"""
Ambiguity-safe event attribution (merge M3). Ported idea from
UdanM/forkit-agent-audit @ 3f5b2aa: an event links only when its own source
evidence matches exactly one passport; otherwise it goes to a review queue,
unassigned.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.engine import AutoDiscoverEngine, build_audit_bundle, render_audit_markdown

REPO = Path(__file__).resolve().parent.parent
CONFIGS = ["fixtures/claude_desktop_config.json", "fixtures/cursor_mcp.json"]


@pytest.fixture()
def eng(tmp_path: Path) -> AutoDiscoverEngine:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    e = AutoDiscoverEngine(tmp_path / "data", base_dir=tmp_path, approvals_path=tmp_path / "fixtures" / "approvals.json")
    for c in e.discover_tools(CONFIGS):
        e.mint_passport(c)
    return e


@pytest.fixture()
def events() -> dict[str, dict]:
    lines = (REPO / "fixtures" / "runtime_events.jsonl").read_text().splitlines()
    return {x["event_id"]: x for x in map(json.loads, filter(None, lines))}


def pid_of(eng: AutoDiscoverEngine, name: str, cfg: str) -> str:
    return next(k for k, p in eng.passports().items() if p["tool_name"] == name and p["source_path"] == cfg)


def test_full_source_is_unique(eng, events) -> None:
    att = eng.attribution(events["evt-0001"])
    assert att["status"] == "unique"
    assert att["candidates"] == [pid_of(eng, "filesystem-mcp", CONFIGS[0])]
    link = eng.link_event(events["evt-0001"], att["candidates"][0], user_consent=True)
    assert link["attribution"] == "unique"


def test_shared_name_without_config_is_ambiguous_and_refused(eng, events) -> None:
    ev = events["evt-0004"]
    att = eng.attribution(ev)
    assert att["status"] == "ambiguous" and len(att["candidates"]) == 2
    for pid in att["candidates"]:
        with pytest.raises(ValueError, match="2 passports match"):
            eng.link_event(ev, pid, user_consent=True)
    assert eng.linked_events() == []


def test_no_source_context_is_refused(eng, events) -> None:
    ev = events["evt-0005"]
    assert eng.attribution(ev)["status"] == "no_source_context"
    with pytest.raises(ValueError, match="guess"):
        eng.link_event(ev, pid_of(eng, "filesystem-mcp", CONFIGS[0]), user_consent=True)


def test_unknown_strings_count_as_missing(eng) -> None:
    ev = {"event_id": "x", "source": {"server_name": "UNKNOWN", "config_path": "UNKNOWN"}}
    assert eng.attribution(ev)["status"] == "no_source_context"


def test_config_only_with_one_registered_server_still_needs_review(eng) -> None:
    ev = {"event_id": "y", "source": {"config_path": CONFIGS[1]}, "tool_call": "pg.query"}
    assert eng.attribution(ev)["status"] == "insufficient_source"
    ev2 = {"event_id": "z", "source": {"config_path": CONFIGS[0]}, "tool_call": "fs.read_file"}
    assert eng.attribution(ev2)["status"] == "ambiguous"


def test_unminted_source_is_no_match(tmp_path: Path, events) -> None:
    e = AutoDiscoverEngine(tmp_path / "d", base_dir=tmp_path, approvals_path=tmp_path / "none.json")
    assert e.attribution(events["evt-0001"])["status"] == "no_match"


def test_review_queue_needs_consent_is_idempotent_and_refuses_unique(eng, events) -> None:
    with pytest.raises(PermissionError):
        eng.record_for_review(events["evt-0004"], user_consent=False)
    first = eng.record_for_review(events["evt-0004"], user_consent=True)
    again = eng.record_for_review(events["evt-0004"], user_consent=True)
    assert first == again and len(eng.review_queue()) == 1
    assert first["actor"] == "UNKNOWN" and first["status"] == "NEEDS_REVIEW"
    assert len(first["candidate_passport_ids"]) == 2
    with pytest.raises(ValueError, match="Link it instead"):
        eng.record_for_review(events["evt-0001"], user_consent=True)


def test_review_queue_is_in_audit_report_and_bundle(eng, events) -> None:
    eng.record_for_review(events["evt-0005"], user_consent=True)
    report = eng.build_audit_report()
    assert report["review_queue"][0]["attribution"] == "no_source_context"
    assert "Review queue" in render_audit_markdown(report)
    bundle = build_audit_bundle(report, eng.data_dir)
    import io
    import zipfile
    assert "store/review_queue.json" in zipfile.ZipFile(io.BytesIO(bundle)).namelist()
