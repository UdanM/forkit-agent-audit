"""
AutoDiscover acceptance tests.

Each test works in an isolated copy of fixtures/ under tmp_path, so the
repo's own fixtures and data/ directory are never touched. Source paths are
stored relative to the base dir, so passport IDs here are identical to the
IDs referenced in fixtures/approvals.json.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.engine import (
    APPROVED,
    INCIDENT,
    MISSING_APPROVAL,
    REDACTED,
    UNKNOWN,
    AutoDiscoverEngine,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
CLAUDE = "fixtures/claude_desktop_config.json"
CURSOR = "fixtures/cursor_mcp.json"
CONFIGS = [CLAUDE, CURSOR]
FAKE_SECRETS = ("FAKE_s3cr3t_DO_NOT_USE_9f3k", "FAKE_devpass_DO_NOT_USE_7x2q")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    return tmp_path


def make_engine(ws: Path) -> AutoDiscoverEngine:
    return AutoDiscoverEngine(
        data_dir=ws / "data",
        base_dir=ws,
        approvals_path=ws / "fixtures" / "approvals.json",
    )


def onboard_all(engine: AutoDiscoverEngine) -> dict[tuple[str, str], str]:
    """Discover and single-click confirm every candidate."""
    return {
        (c["source_path"], c["tool_name"]): engine.mint_passport(c)
        for c in engine.discover_tools(CONFIGS)
    }


def load_events(ws: Path) -> dict[str, dict]:
    lines = (ws / "fixtures" / "runtime_events.jsonl").read_text().splitlines()
    return {e["event_id"]: e for e in map(json.loads, filter(None, lines))}


# ── Test 1: Restart recognition ────────────────────────────────────────────────

def test_restart_recognition_yields_same_passport_ids(workspace: Path) -> None:
    first = onboard_all(make_engine(workspace))
    stored_before = (workspace / "data" / "passports.json").read_bytes()

    # Simulated restart: drop every in-memory object, build a fresh engine.
    fresh = make_engine(workspace)
    rescanned = fresh.discover_tools(CONFIGS)

    assert {(c["source_path"], c["tool_name"]): c["passport_id"] for c in rescanned} == first
    assert all(c["known"] and not c["metadata_changed"] for c in rescanned)

    # Confirming again is idempotent: no duplicates, no rewrite.
    for c in rescanned:
        assert fresh.mint_passport(c) == first[(c["source_path"], c["tool_name"])]
    assert len(fresh.passports()) == 3
    assert (workspace / "data" / "passports.json").read_bytes() == stored_before


# ── Test 2: No false merges ────────────────────────────────────────────────────

def test_same_name_tools_from_different_configs_stay_distinct(workspace: Path) -> None:
    engine = make_engine(workspace)
    ids = onboard_all(engine)

    claude_pg = ids[(CLAUDE, "postgres-mcp")]
    cursor_pg = ids[(CURSOR, "postgres-mcp")]
    assert claude_pg != cursor_pg

    postgres = [p for p in engine.passports().values() if p["tool_name"] == "postgres-mcp"]
    assert len(postgres) == 2
    assert {p["source_path"] for p in postgres} == {CLAUDE, CURSOR}
    assert {p["client"] for p in postgres} == {"claude-desktop", "cursor"}


# ── Test 3: Consented event linking & missing approval ─────────────────────────

def test_consented_linking_preserves_source_and_flags_missing_approval(workspace: Path) -> None:
    engine = make_engine(workspace)
    ids = onboard_all(engine)
    events = load_events(workspace)
    passports_file = workspace / "data" / "passports.json"
    snapshot = passports_file.read_bytes()

    # Without consent: blocked, nothing written.
    with pytest.raises(PermissionError):
        engine.link_event(events["evt-0003"], ids[(CURSOR, "postgres-mcp")], user_consent=False)
    assert engine.linked_events() == []

    # With consent: tool has no approval record at all → MISSING_APPROVAL.
    link = engine.link_event(events["evt-0003"], ids[(CURSOR, "postgres-mcp")], user_consent=True)
    assert link["source"] == events["evt-0003"]["source"]
    assert link["timestamp"] == events["evt-0003"]["timestamp"] == "2026-11-20T09:30:12Z"
    assert link["approval_status"] == MISSING_APPROVAL
    assert MISSING_APPROVAL in link["flags"]
    assert link["approval_id"] == UNKNOWN

    # Approved read on the approved tool → APPROVED, checked against approvals.json.
    ok = engine.link_event(events["evt-0001"], ids[(CLAUDE, "filesystem-mcp")], user_consent=True)
    assert ok["approval_status"] == APPROVED
    assert ok["approval_id"] == "appr-0001"
    assert ok["flags"] == []

    # Links live in their own file; the core passport record is byte-identical.
    assert len(json.loads((workspace / "data" / "linked_events.json").read_text())) == 2
    assert passports_file.read_bytes() == snapshot

    # An event cannot be linked to the wrong tool.
    with pytest.raises(ValueError):
        engine.link_event(events["evt-0001"], ids[(CLAUDE, "postgres-mcp")], user_consent=True)


# ── Test 4 (stretch): Out-of-expected-behaviour incident ───────────────────────

def test_write_from_read_only_tool_records_incident(workspace: Path) -> None:
    engine = make_engine(workspace)
    ids = onboard_all(engine)
    fs_id = ids[(CLAUDE, "filesystem-mcp")]
    assert engine.passports()[fs_id]["access_mode"] == "read-only"

    link = engine.link_event(load_events(workspace)["evt-0002"], fs_id, user_consent=True)

    assert INCIDENT in link["flags"]
    assert MISSING_APPROVAL in link["flags"]  # fs.write_file is outside appr-0001's scope
    incidents = json.loads((workspace / "data" / "incidents.json").read_text())
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["type"] == "OUT_OF_EXPECTED_BEHAVIOR"
    assert inc["passport_id"] == fs_id
    assert inc["tool_call"] == "fs.write_file"
    assert inc["event_id"] == "evt-0002"
    assert inc["timestamp"] == "2026-11-20T09:21:47Z"
    assert link["incident_id"] == inc["incident_id"]

    # Re-linking the same event does not duplicate the incident.
    engine.link_event(load_events(workspace)["evt-0002"], fs_id, user_consent=True)
    assert len(engine.incidents()) == 1


# ── Supporting guardrail checks ────────────────────────────────────────────────

def test_secrets_never_reach_disk_and_unknowns_stay_unknown(workspace: Path) -> None:
    engine = make_engine(workspace)
    onboard_all(engine)
    for event in load_events(workspace).values():
        if "config_path" not in event["source"]:
            continue  # unattributable fixtures (evt-0004/0005) are covered in test_attribution.py
        pid = next(
            p["passport_id"] for p in engine.passports().values()
            if p["tool_name"] == event["source"]["server_name"]
            and p["source_path"] == event["source"]["config_path"]
        )
        engine.link_event(event, pid, user_consent=True)

    for f in (workspace / "data").glob("*.json"):
        text = f.read_text()
        for secret in FAKE_SECRETS:
            assert secret not in text, f"secret leaked into {f.name}"
        assert "postgresql://" not in text

    for p in engine.passports().values():
        assert p["owner"] == UNKNOWN
        assert p["version"] == UNKNOWN
        assert all(v == REDACTED for v in p["env"].values())


def test_config_change_flags_metadata_diff_without_mutating_passport(workspace: Path) -> None:
    engine = make_engine(workspace)
    ids = onboard_all(engine)
    original = engine.passports()[ids[(CLAUDE, "filesystem-mcp")]]

    # The user edits Claude Desktop's config in place (v2 contents).
    shutil.copy(workspace / "fixtures" / "claude_desktop_config_v2.json", workspace / CLAUDE)
    after = {c["tool_name"]: c for c in make_engine(workspace).discover_tools([CLAUDE])}

    fs = after["filesystem-mcp"]
    assert fs["passport_id"] == original["passport_id"]  # same tool, not a new one
    assert fs["metadata_changed"] is True
    assert fs["access_mode"] == UNKNOWN  # ",ro" evidence is gone; we do not guess "read-write"
    assert any("removed" in d or "changed" in d for d in fs["diff"])
    assert any("finance" in d for d in fs["diff"])
    assert any(d.startswith("access_mode:") for d in fs["diff"])
    assert after["postgres-mcp"]["metadata_changed"] is False

    assert engine.passports()[original["passport_id"]] == original
    changes = engine.metadata_changes()
    assert len(changes) == 1 and changes[0]["status"] == "PENDING_REVIEW"
