"""
Visual provenance & lifecycle flow (Upgrade 4).

Graphviz is not required: DOT output is checked structurally (balanced
quotes and braces, generated node IDs) plus content assertions. All tests run
under tmp_path and verify that building a diagram writes nothing.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from src.engine import AutoDiscoverEngine, derive_passport_id
from src.provenance_view import dot_escape, lifecycle_dot, live_map_dot, load_store

REPO = Path(__file__).resolve().parent.parent
CLAUDE = "fixtures/claude_desktop_config.json"
CURSOR = "fixtures/cursor_mcp.json"
CONFIGS = [CLAUDE, CURSOR]


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    return tmp_path


def engine(ws: Path, data: str = "data") -> AutoDiscoverEngine:
    return AutoDiscoverEngine(data_dir=ws / data, base_dir=ws, approvals_path=ws / "fixtures" / "approvals.json")


def full_demo_flow(ws: Path) -> tuple[AutoDiscoverEngine, dict, list[dict]]:
    eng = engine(ws)
    ids = {(c["source_path"], c["tool_name"]): eng.mint_passport(c) for c in eng.discover_tools(CONFIGS)}
    shutil.copy(ws / "fixtures" / "claude_desktop_config_v2.json", ws / CLAUDE)
    cands = eng.discover_tools(CONFIGS)
    lines = (ws / "fixtures" / "runtime_events.jsonl").read_text().splitlines()
    ev = {e["event_id"]: e for e in map(json.loads, filter(None, lines))}
    eng.link_event(ev["evt-0001"], ids[(CLAUDE, "filesystem-mcp")], user_consent=True)
    eng.link_event(ev["evt-0002"], ids[(CLAUDE, "filesystem-mcp")], user_consent=True)
    eng.link_event(ev["evt-0003"], ids[(CURSOR, "postgres-mcp")], user_consent=True)
    eng.evaluate_policies(cands)
    return eng, ids, cands


def assert_valid_dot(dot: str) -> None:
    """Structural DOT check: every quoted string closes, braces balance."""
    assert dot.startswith("digraph ")
    depth, in_str, i = 0, False, 0
    while i < len(dot):
        ch = dot[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            elif ch == "\n":
                raise AssertionError("raw newline inside a quoted DOT string")
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            assert depth >= 0
        i += 1
    assert not in_str and depth == 0
    # Node IDs are generated identifiers, never config text.
    for stmt in re.findall(r"^\s*([^\s\[\"]+)\s*\[", dot, flags=re.MULTILINE):
        assert re.fullmatch(r"(graph|node|edge|[a-z]+[0-9a-z_]*)", stmt), stmt


def snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


# ── Empty / fresh store ────────────────────────────────────────────────────────

def test_empty_store_gives_full_lifecycle_and_no_live_map(ws: Path) -> None:
    store = ws / "data"                                   # does not exist yet
    dot = lifecycle_dot(store)
    assert_valid_dot(dot)
    for needle in ("Client config", "Provenance hash", "compute_id", "rank=same", "0 confirmed", "0 consented",
                   "0 out-of-expected-behavior", "never rewritten"):
        assert needle in dot, needle
    assert live_map_dot(store) is None
    assert live_map_dot(store, candidates=[]) is None
    assert not store.exists()


def test_candidates_only_are_drawn_dashed(ws: Path) -> None:
    cands = engine(ws).discover_tools(CONFIGS)
    dot = live_map_dot(ws / "data", candidates=cands)
    assert_valid_dot(dot)
    assert dot.count("not minted yet") == 3
    assert "Passport " not in dot


# ── Live map content ───────────────────────────────────────────────────────────

def test_live_map_keeps_same_name_tools_in_separate_config_clusters(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    dot = live_map_dot(eng.data_dir, candidates=cands)
    assert_valid_dot(dot)

    clusters = re.findall(r"subgraph cluster_\d+ \{(.*?)\n  \}", dot, flags=re.DOTALL)
    assert len(clusters) == 2
    claude = next(c for c in clusters if "claude_desktop_config.json" in c)
    cursor = next(c for c in clusters if "cursor_mcp.json" in c)
    assert "postgres-mcp" in claude and "postgres-mcp" in cursor
    assert dot.count("same name, different provenance") == 2

    pg = [ids[(CLAUDE, "postgres-mcp")], ids[(CURSOR, "postgres-mcp")]]
    for pid in pg:
        assert f"Passport {pid[:12]}" in dot
    assert dot.count("integrity PASS") == 3


def test_events_link_to_the_right_passport_with_incident_and_missing_approval(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    dot = live_map_dot(eng.data_dir, candidates=cands)
    node = {pid: m for m, pid in re.findall(r'^  (n\d+) \[label="Passport ([0-9a-f]{12})\\n', dot, flags=re.M)}
    fs_node = node[ids[(CLAUDE, "filesystem-mcp")][:12]]
    pg_node = node[ids[(CURSOR, "postgres-mcp")][:12]]

    def event_node(event_id: str) -> str:
        return re.search(rf'^  (n\d+_e\d+) \[label="{event_id}\\n', dot, flags=re.M).group(1)

    for event_id, owner in (("evt-0001", fs_node), ("evt-0002", fs_node), ("evt-0003", pg_node)):
        e = event_node(event_id)
        assert re.search(rf"^  {owner} -> {e} \[", dot, flags=re.M), event_id

    e2 = event_node("evt-0002")
    assert re.search(rf"^  {e2} -> {e2}_inc \[", dot, flags=re.M)
    assert "Incident\\nOut of expected behavior" in dot
    e2_line = next(line for line in dot.splitlines() if line.startswith(f"  {e2} ["))
    assert "MISSING_APPROVAL" in e2_line and "#fde6e6" in e2_line      # red styling
    e1_line = next(line for line in dot.splitlines() if line.startswith(f"  {event_node('evt-0001')} ["))
    assert "APPROVED" in e1_line and "#e3f6e8" in e1_line              # green styling

    assert "config drift × 1" in dot
    assert "POL-005 CRITICAL" in dot and "POL-001 HIGH" in dot


def test_focus_restricts_to_one_passport(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    fs = ids[(CLAUDE, "filesystem-mcp")]
    dot = live_map_dot(eng.data_dir, candidates=cands, focus=fs)
    assert_valid_dot(dot)
    assert "postgres-mcp" not in dot and "filesystem-mcp" in dot
    assert live_map_dot(eng.data_dir, focus="0" * 64) is None


def test_large_inventory_collapses_events(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    dot = live_map_dot(eng.data_dir, candidates=cands, max_tools=2)
    assert_valid_dot(dot)
    assert "2 linked event(s)" in dot and "1 MISSING_APPROVAL" in dot
    assert "evt-0002" not in dot


def test_tampered_passport_shows_integrity_fail(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    path = eng.data_dir / "passports.json"
    stored = json.loads(path.read_text())
    stored[ids[(CURSOR, "postgres-mcp")]]["command"] = "curl"
    path.write_text(json.dumps(stored))
    assert live_map_dot(eng.data_dir).count("integrity FAIL") == 1


# ── Safety: escaping, secrets, isolation, read-only ────────────────────────────

@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ('evil" ]; x -> y [label="', 'evil\\" ]; x -> y [label=\\"'),
        ("C:\\Users\\x\\mcp.json", "C:\\\\Users\\\\x\\\\mcp.json"),
        ("line1\nline2", "line1\\nline2"),
        ("tab\there\x00", "tab here "),
    ],
)
def test_dot_escape(raw: str, escaped: str) -> None:
    assert dot_escape(raw) == escaped


def test_hostile_tool_names_and_paths_cannot_break_dot(ws: Path) -> None:
    hostile = 'pwn"} digraph x {\\n\n'
    cfg = ws / "fixtures" / "we ird#dir ü"           # legal on Windows too (no quotes/backslashes)
    cfg.mkdir()
    (cfg / "mcp.json").write_text(json.dumps({"mcpServers": {hostile: {"command": 'np"x\\', "args": []}}}))
    eng = engine(ws)
    rel = "fixtures/we ird#dir ü/mcp.json"
    (cand,) = eng.discover_tools([rel])
    eng.mint_passport(cand)
    assert derive_passport_id(cand["source_path"], hostile, 'np"x\\') == cand["passport_id"]
    dot = live_map_dot(eng.data_dir, candidates=[cand])
    assert_valid_dot(dot)
    assert_valid_dot(lifecycle_dot(eng.data_dir))


def test_no_secrets_or_usernames_in_diagrams(ws: Path, tmp_path: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    text = lifecycle_dot(eng.data_dir) + live_map_dot(eng.data_dir, candidates=cands)
    for secret in ("FAKE_s3cr3t_DO_NOT_USE_9f3k", "FAKE_devpass_DO_NOT_USE_7x2q", "postgresql://"):
        assert secret not in text

    # Real-machine store: labels use the ~-masked source paths only.
    home = tmp_path / "home" / "jury-demo-user"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"fs": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", str(home / "Docs")]}}}))
    local = engine(ws, "data/local")
    summary = local.scan_local_machine("linux", home, {})
    local.mint_passport(summary["candidates"][0])
    real = live_map_dot(local.data_dir, candidates=summary["candidates"])
    assert "~/.cursor/mcp.json" in real
    assert "jury-demo-user" not in real


def test_store_dir_isolation_and_read_only(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    local = engine(ws, "data/local")
    (only,) = local.discover_tools([CURSOR])
    local.mint_passport(only)

    before = snapshot(ws / "data")
    demo_map = live_map_dot(ws / "data")
    local_map = live_map_dot(ws / "data" / "local")
    lifecycle_dot(ws / "data")
    lifecycle_dot(ws / "data" / "local")
    load_store(ws / "data" / "local")
    assert snapshot(ws / "data") == before

    assert "evt-0002" in demo_map and "evt-0002" not in local_map
    assert local_map.count("Passport ") == 1
    assert "1 confirmed" in lifecycle_dot(ws / "data" / "local")
    assert "3 confirmed" in lifecycle_dot(ws / "data")
