"""Live provenance map as rendered in the UI (HTML flow-chart table)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.live_map_html import live_map_html
from tests.test_provenance_view import CLAUDE, CONFIGS, CURSOR, engine, full_demo_flow, snapshot, ws  # noqa: F401


def rows(page: str) -> list[str]:
    return re.findall(r"<tr[^>]*>(.*?)</tr>", page.split("<tbody>", 1)[1], flags=re.S)


def test_empty_store_gives_no_map(ws: Path) -> None:
    assert live_map_html(ws / "data") is None
    assert live_map_html(ws / "data", candidates=[]) is None
    assert not (ws / "data").exists()


def test_candidates_are_not_issued_yet(ws: Path) -> None:
    cands = engine(ws).discover_tools(CONFIGS)
    page = live_map_html(ws / "data", candidates=cands)
    assert page.count("Not issued yet") == 3
    assert "Integrity check passed" not in page


def test_full_flow_plain_language_rows(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    before = snapshot(ws / "data")
    page = live_map_html(eng.data_dir, candidates=cands)
    assert snapshot(ws / "data") == before                                   # read-only

    assert page.count("Integrity check passed") == 3
    assert page.count("Another tool has the same name. Kept as a separate passport.") == 2
    assert "Out of expected behavior" in page and "No approval on file" in page
    assert "Config changed since issued (1)" in page
    assert "POL-005</b> Critical" in page and "POL-001</b> High" in page
    for emoji in ("🔒", "🚨", "—"):
        assert emoji not in page

    # One row per event: evt-0002 and its incident share a row; evt-0003 sits on the cursor postgres row.
    body = rows(page)
    inc_row = next(r for r in body if "evt-0002" in r)
    assert "Incident" in inc_row and "No approval on file" in inc_row
    pg_row = next(r for r in body if "evt-0003" in r)
    assert ids[(CURSOR, "postgres-mcp")][:12] in pg_row
    assert sum("<b></b>" in r for r in body) == len(body)                    # every row has an arrow


def test_focus_and_collapse(ws: Path) -> None:
    eng, ids, cands = full_demo_flow(ws)
    fs = ids[(CLAUDE, "filesystem-mcp")]
    page = live_map_html(eng.data_dir, candidates=cands, focus=fs)
    assert "filesystem-mcp" in page and "postgres-mcp" not in page
    assert live_map_html(eng.data_dir, focus="0" * 64) is None
    small = live_map_html(eng.data_dir, candidates=cands, max_tools=2)
    assert "2 linked events" in small and "1 without approval" in small and "evt-0002" not in small


def test_hostile_values_are_escaped_and_secrets_absent(ws: Path) -> None:
    hostile = '<img src=x onerror=alert(1)>"&'
    cfg = ws / "fixtures" / "odd"
    cfg.mkdir()
    (cfg / "mcp.json").write_text(json.dumps({"mcpServers": {hostile: {"command": "<script>", "args": []}}}))
    eng, ids, cands = full_demo_flow(ws)
    (cand,) = eng.discover_tools(["fixtures/odd/mcp.json"])
    eng.mint_passport(cand)
    page = live_map_html(eng.data_dir, candidates=cands)
    assert "<img" not in page and "<script>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;&quot;&amp;" in page
    for secret in ("FAKE_s3cr3t_DO_NOT_USE_9f3k", "FAKE_devpass_DO_NOT_USE_7x2q", "postgresql://"):
        assert secret not in page


def test_lifecycle_is_complete_for_empty_and_full_store(ws: Path) -> None:
    from src.live_map_html import lifecycle_html
    empty = lifecycle_html(ws / "data")
    for needle in ("Client config", "Provenance hash", "compute_id()", "0 issued", "0 events linked", "0 incidents",
                   "joined by passport_id"):
        assert needle in empty, needle
    assert not (ws / "data").exists()
    eng, ids, cands = full_demo_flow(ws)
    full = lifecycle_html(eng.data_dir)
    assert "3 issued" in full and "1 incident<" in full and "1 config change<" in full
    assert "without approval" in full
