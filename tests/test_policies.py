"""
Policy-as-code guardrails (Upgrade 2).

All tests run against a copy of fixtures/ under tmp_path, so no runtime
artifact is ever written to the repo's data/ directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.engine import (
    UNKNOWN,
    AutoDiscoverEngine,
    category_evidence,
    evaluate_policies,
    load_policies,
)

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


def rules_by_tool(cands: list[dict]) -> dict[tuple[str, str], set[str]]:
    return {(c["source_path"], c["tool_name"]): {f["rule_id"] for f in c["policy_findings"]} for c in cands}


def events(ws: Path) -> dict[str, dict]:
    lines = (ws / "fixtures" / "runtime_events.jsonl").read_text().splitlines()
    return {e["event_id"]: e for e in map(json.loads, filter(None, lines))}


# ── Rule outcomes on the fixtures ──────────────────────────────────────────────

def test_database_servers_without_approval_trigger_pol001(ws: Path) -> None:
    flags = rules_by_tool(engine(ws).discover_tools(CONFIGS))
    assert "POL-001" in flags[(CLAUDE, "postgres-mcp")]    # approval pending
    assert "POL-001" in flags[(CURSOR, "postgres-mcp")]    # no approval record
    assert "POL-001" not in flags[(CLAUDE, "filesystem-mcp")]

    cursor_pg = next(c for c in engine(ws).discover_tools([CURSOR]))
    pol1 = next(f for f in cursor_pg["policy_findings"] if f["rule_id"] == "POL-001")
    assert pol1["severity"] == "high"
    assert pol1["evidence"] == {
        "category": "database",
        "category_evidence": "@modelcontextprotocol/server-postgres",
        "approval": "missing",
        "approval_id": UNKNOWN,
    }


def test_unknown_owner_and_version_flag_missing_attestation(ws: Path) -> None:
    flags = rules_by_tool(engine(ws).discover_tools(CONFIGS))
    assert all({"POL-003", "POL-004"} <= f for f in flags.values())

    cfg = ws / "fixtures" / "attested.json"
    cfg.write_text(json.dumps({"mcpServers": {"notes": {
        "command": "npx", "args": ["-y", "notes-mcp"], "owner": "platform-team", "version": "1.4.0",
    }}}))
    (attested,) = engine(ws).discover_tools(["fixtures/attested.json"])
    assert attested["owner"] == "platform-team"
    assert {f["rule_id"] for f in attested["policy_findings"]} == set()


def test_access_mode_downgrade_triggers_pol002(ws: Path) -> None:
    eng = engine(ws)
    for c in eng.discover_tools(CONFIGS):
        eng.mint_passport(c)
    shutil.copy(ws / "fixtures" / "claude_desktop_config_v2.json", ws / CLAUDE)

    flags = rules_by_tool(eng.discover_tools(CONFIGS))
    assert "POL-002" in flags[(CLAUDE, "filesystem-mcp")]
    assert "POL-002" not in flags[(CLAUDE, "postgres-mcp")]
    fs = next(c for c in eng.discover_tools([CLAUDE]) if c["tool_name"] == "filesystem-mcp")
    pol2 = next(f for f in fs["policy_findings"] if f["rule_id"] == "POL-002")
    assert pol2["evidence"]["access_mode_before"] == "read-only"
    assert pol2["evidence"]["access_mode_after"] == UNKNOWN


def test_unapproved_write_event_triggers_pol005_and_approved_read_does_not(ws: Path) -> None:
    eng = engine(ws)
    ids = {(c["source_path"], c["tool_name"]): eng.mint_passport(c) for c in eng.discover_tools(CONFIGS)}
    fs = ids[(CLAUDE, "filesystem-mcp")]
    ev = events(ws)

    write = eng.link_event(ev["evt-0002"], fs, user_consent=True)
    read = eng.link_event(ev["evt-0001"], fs, user_consent=True)
    query = eng.link_event(ev["evt-0003"], ids[(CURSOR, "postgres-mcp")], user_consent=True)
    assert write["policy_flags"] == ["POL-005"]
    assert read["policy_flags"] == []
    assert query["policy_flags"] == []      # unapproved, but not a mutating action

    findings = eng.evaluate_policies()
    pol5 = [f for f in findings if f["rule_id"] == "POL-005"]
    assert len(pol5) == 1
    assert pol5[0]["evidence"]["tool_call"] == "fs.write_file"
    assert pol5[0]["severity"] == "critical"


# ── Zero guessing ──────────────────────────────────────────────────────────────

def test_category_requires_explicit_evidence() -> None:
    no_evidence = {"command": "npx", "args": ["-y", "@acme/internal-mcp", "/data/postgres-backups"]}
    assert category_evidence(no_evidence) == (UNKNOWN, None)          # path words are not evidence
    assert category_evidence({"command": UNKNOWN, "args": []}) == (UNKNOWN, None)
    assert category_evidence({"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "~/x.db"]})[0] == "database"
    assert category_evidence({"command": "docker", "args": ["run", "-i", "mcp/postgres"]})[0] == "database"


def test_tool_with_unknown_category_never_triggers_pol001(ws: Path) -> None:
    cfg = ws / "fixtures" / "mystery.json"
    cfg.write_text(json.dumps({"mcpServers": {"mystery-db": {"command": "./run.sh", "args": ["--port", "9000"]}}}))
    (cand,) = engine(ws).discover_tools(["fixtures/mystery.json"])
    assert "POL-001" not in {f["rule_id"] for f in cand["policy_findings"]}


# ── Strict loader ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rule_patch", "message"),
    [
        ({"when": {"python_eval": "True"}}, "not allowed"),
        ({"when": {"action_mutating": True}}, "not allowed"),           # event condition on a passport rule
        ({"severity": "urgent"}, "severity"),
        ({"when": {"category_in": ["llm"]}}, "category_in"),
        ({"when": {"field_is_unknown": "api_key"}}, "field_is_unknown"),
        ({"when": {}}, "non-empty"),
    ],
)
def test_malformed_policy_file_raises(tmp_path: Path, rule_patch: dict, message: str) -> None:
    rule = {"rule_id": "X-1", "title": "t", "severity": "low", "applies_to": "passport",
            "when": {"field_is_unknown": "owner"}, "explanation": "e", **rule_patch}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"rules": [rule]}))
    with pytest.raises(ValueError, match=message):
        load_policies(path)


def test_shipped_policy_file_is_valid() -> None:
    rules = load_policies(REPO / "fixtures" / "policies.json")
    assert [r["rule_id"] for r in rules] == [f"POL-{i:03d}" for i in range(1, 19)]
    assert {r["applies_to"] for r in rules[5:]} == {"activity"}


def test_missing_policy_file_means_no_findings(ws: Path) -> None:
    (ws / "fixtures" / "policies.json").unlink()
    assert all(c["policy_findings"] == [] for c in engine(ws).discover_tools(CONFIGS))


# ── Persistence, isolation, immutability ───────────────────────────────────────

def test_findings_persist_per_store_without_duplicates_or_passport_writes(ws: Path) -> None:
    demo = engine(ws)
    cands = demo.discover_tools(CONFIGS)
    for c in cands:
        demo.mint_passport(c)
    passports = ws / "data" / "passports.json"
    snapshot = passports.read_bytes()

    first = demo.evaluate_policies(cands)
    second = demo.evaluate_policies(demo.discover_tools(CONFIGS))
    saved = json.loads((ws / "data" / "policy_findings.json").read_text())
    assert len(saved) == len(first) == len(second)
    assert len({f["finding_id"] for f in saved}) == len(saved)
    assert [f["first_detected_at"] for f in first] == [f["first_detected_at"] for f in second]
    assert passports.read_bytes() == snapshot

    # A second store (real-machine mode) is fully isolated from the demo store.
    local = engine(ws, "data/local")
    local.evaluate_policies(local.discover_tools([CURSOR]))
    assert (ws / "data" / "local" / "policy_findings.json").exists()
    assert json.loads((ws / "data" / "policy_findings.json").read_text()) == saved
    assert not (ws / "data" / "local" / "passports.json").exists()


def test_explicit_store_dir_is_honoured(ws: Path) -> None:
    eng = engine(ws)
    eng.evaluate_policies(eng.discover_tools(CONFIGS), store_dir=ws / "elsewhere")
    assert (ws / "elsewhere" / "policy_findings.json").exists()
    assert not (ws / "data" / "policy_findings.json").exists()


def test_pure_evaluation_writes_nothing(ws: Path) -> None:
    eng = engine(ws)
    cands = eng.discover_tools(CONFIGS)
    before = sorted(p.name for p in (ws / "data").glob("*")) if (ws / "data").exists() else []
    out = evaluate_policies(eng.policies(), cands, {}, eng.approvals(), [])
    after = sorted(p.name for p in (ws / "data").glob("*")) if (ws / "data").exists() else []
    assert out and before == after
