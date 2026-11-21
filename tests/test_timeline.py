"""
Activity timeline (merge M4). Evidence model ported from
UdanM/forkit-agent-audit @ 3f5b2aa (timeline.py, docs/scaling.md).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.engine import AutoDiscoverEngine, load_policies, render_audit_markdown
from src.timeline import (
    UNKNOWN,
    build_timeline,
    make_checkpoint,
    parse_log,
    parse_row,
    timeline_from_files,
    verify_checkpoint,
)

REPO = Path(__file__).resolve().parent.parent
OBS = REPO / "fixtures" / "activity" / "observer.jsonl"
REP = REPO / "fixtures" / "activity" / "agent_reported.jsonl"


@pytest.fixture()
def tl() -> dict:
    return timeline_from_files(OBS, REP, rules=load_policies(REPO / "fixtures" / "policies.json"), namespace="demo")


def rows(*dicts: dict) -> list[dict]:
    return [parse_row(d) for d in dicts]


def run(i: str, run_id: str, t: str = "2026-11-20T10:00:00Z", **kw) -> dict:
    return {"id": i, "time": t, "kind": "run_started", "run_id": run_id, **kw}


def write(i: str, run_id: str | None, path: str, t: str = "2026-11-20T10:01:00Z", **kw) -> dict:
    return {"id": i, "time": t, "kind": "file_write", "run_id": run_id, "path": path, **kw}


def test_fixture_parses_cleanly_and_every_check_fires_where_expected(tl) -> None:
    assert tl["rejected"] == {"observer": [], "self_reported": []}
    assert tl["signals"] == {
        "access_escalation": ["act-010"], "approval_missing": ["act-005"], "clock_drift": ["act-007"],
        "config_file_write": ["act-006"], "identity_relabel": ["act-010"], "onboarding_missing": ["act-011"],
        "protected_write": ["act-005"], "read_only_write": ["act-005"],
        "self_report_mismatch": ["rep-001", "rep-003"], "sequence_gap": ["act-007"],
        "unknown_actor": ["act-008", "act-009"],
    }
    assert [f["rule_id"] for f in tl["findings"]][:4] == ["POL-007", "POL-008", "POL-009", "POL-011"]


def test_delegation_chain_and_initiator(tl) -> None:
    runs = {r["run_id"]: r for r in tl["runs"]}
    assert runs["desktop-helper"]["delegated_by"] == "desktop-main"
    assert runs["desktop-main"]["delegated_by"] is None
    act = next(a for a in tl["actions"] if a["record_id"] == "act-005")
    assert act["initiated_by"] == "desktop-main" and act["actor"] == "Report helper"


def test_same_identity_across_runs_is_one_agent(tl) -> None:
    helper = next(a for a in tl["agents"] if a["instance_id"] == "helper")
    assert helper["runs"] == ["desktop-helper", "desktop-helper-2"]
    assert helper["labels"] == ["Report helper", "Report finisher"]
    assert helper["access"] == ["read_only", "write"]


def test_unknown_actor_is_never_guessed(tl) -> None:
    acts = {a["record_id"]: a for a in tl["actions"]}
    assert acts["act-008"]["actor"] == UNKNOWN and acts["act-008"]["actor_reason"] == "run was never observed starting"
    assert acts["act-009"]["actor"] == UNKNOWN and acts["act-009"]["actor_reason"] == "no run reference"


def test_shared_file_overlap(tl) -> None:
    assert tl["shared_files"] == {"reports/q3/summary.md": ["Code assistant", "Report assistant", "Report helper"]}


def test_identity_needs_all_three_observer_fields() -> None:
    t = build_timeline(rows(run("a", "r1", deployment="d", agent_id="x")))
    assert t["runs"][0]["agent"] == UNKNOWN and t["agents"] == []


def test_self_report_cannot_create_a_run_or_agent() -> None:
    t = build_timeline([], rows(run("s", "r9", deployment="d", agent_id="x", instance_id="i")))
    assert t["runs"] == [] and t["agents"] == []
    assert t["self_reported"][0]["verdict"] == "not observed"


def test_run_reuse_makes_relationship_unknown() -> None:
    t = build_timeline(rows(run("a", "r1"), run("b", "r1", t="2026-11-20T10:05:00Z")))
    assert t["signals"]["run_reuse"] == ["b"]
    assert t["runs"][0]["relationship"] == UNKNOWN


def test_unresolved_parent_is_unknown() -> None:
    t = build_timeline(rows(run("a", "child", parent_id="ghost")))
    assert t["runs"][0]["delegated_by"] == UNKNOWN


@pytest.mark.parametrize("bad", [
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "file_write", "run_id": "r", "path": "/etc/passwd"},
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "file_write", "run_id": "r", "path": "../../secret"},
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "file_write", "run_id": "r", "path": "C:\\Users\\a"},
    {"id": "x", "time": "2026-11-20T10:00:00", "kind": "run_started", "run_id": "r"},
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "shell", "run_id": "r"},
    {"id": "x y", "time": "2026-11-20T10:00:00Z", "kind": "run_started", "run_id": "r"},
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "run_started", "run_id": "r", "access": "admin"},
    {"id": "x", "time": "2026-11-20T10:00:00Z", "kind": "run_started", "run_id": "r", "sequence": True},
])
def test_bad_rows_are_rejected(bad: dict) -> None:
    with pytest.raises(ValueError):
        parse_row(bad)


def test_bad_lines_are_reported_not_raised_and_partial_last_line_waits() -> None:
    good = json.dumps(run("a", "r1")).encode()
    data = good + b"\n{not json\n" + good + b"\n" + b'{"id": "partial"'
    parsed, rejected = parse_log(data)
    assert len(parsed) == 1
    assert [r["line"] for r in rejected] == [2, 3]
    assert rejected[1]["reason"] == "duplicate id"


def test_patch_is_reduced_to_hash_and_size() -> None:
    t = build_timeline(rows(run("a", "r1"), write("b", "r1", "x.md", patch="+secret line\n")))
    act = t["actions"][0]
    assert "patch" not in act and act["patch_bytes"] == 13 and len(act["patch_sha256"]) == 64
    assert "secret line" not in json.dumps(t, default=str)


def test_checkpoint_states() -> None:
    log = b'{"a":1}\n{"b":2}\n'
    cp = make_checkpoint(log)
    assert verify_checkpoint(log, cp) == "intact"
    assert verify_checkpoint(log + b'{"c":3}\n', cp) == "intact"
    assert verify_checkpoint(log[:5], cp) == "truncated"
    assert verify_checkpoint(log.replace(b"1", b"9"), cp) == "rewritten"
    assert verify_checkpoint(log, "nope") == "invalid"
    assert verify_checkpoint(log, None) == "none"
    assert make_checkpoint(log + b'{"partial"') == cp


def test_engine_detects_a_rewritten_log_and_persists_findings(tmp_path: Path) -> None:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    log = tmp_path / "fixtures" / "activity" / "observer.jsonl"
    eng = AutoDiscoverEngine(tmp_path / "data", base_dir=tmp_path, approvals_path=tmp_path / "fixtures" / "approvals.json")
    assert eng.save_log_checkpoint("demo", log)
    assert eng.activity_timeline("demo", log)["checkpoint"] == "intact"
    before = log.read_bytes()
    log.write_bytes(before.replace(b'"protected": true', b'"protected": null'))
    tl = eng.activity_timeline("demo", log)
    assert tl["checkpoint"] == "rewritten"
    assert tl["findings"][0]["rule_id"] == "POL-006" and tl["findings"][0]["severity"] == "critical"
    stored = eng.activity_findings()["demo"]
    assert stored["checkpoint"] == "rewritten"
    report = eng.build_audit_report()
    assert report["activity"]["demo"]["findings"][0]["rule_id"] == "POL-006"
    assert "Activity timeline findings" in render_audit_markdown(report)
    assert log.read_bytes() != before  # engine never "repairs" the log


def test_clock_skew_threshold_comes_from_the_policy_file(tmp_path: Path) -> None:
    pol = json.loads((REPO / "fixtures" / "policies.json").read_text())
    for r in pol["rules"]:
        if r["when"].get("check") == "clock_drift":
            r["when"]["max_skew_seconds"] = 3600
    (tmp_path / "p.json").write_text(json.dumps(pol))
    t = timeline_from_files(OBS, rules=load_policies(tmp_path / "p.json"))
    assert "clock_drift" not in t["signals"]


def test_unknown_check_in_policy_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "p.json").write_text(json.dumps({"rules": [{
        "rule_id": "X", "title": "t", "severity": "low", "applies_to": "activity",
        "when": {"check": "rm -rf"}, "explanation": "e"}]}))
    with pytest.raises(ValueError, match="check must be one of"):
        load_policies(tmp_path / "p.json")
