"""Delegated-helper pilot (merge M6): real subprocesses, independent observer.
Ported from UdanM/forkit-agent-audit @ 3f5b2aa (agent_pilot.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo.agents.pilot import run_pilot
from src.ask import answer_from_records
from src.engine import load_policies
from src.timeline import timeline_from_files

REPO = Path(__file__).resolve().parent.parent


def test_pilot_observes_two_processes_and_the_helper_write(tmp_path: Path) -> None:
    res = run_pilot(tmp_path / "pilot")
    tl = timeline_from_files(res["observer_log"], res["reported_log"],
                             rules=load_policies(REPO / "fixtures" / "policies.json"), namespace="pilot")
    assert tl["rejected"] == {"observer": [], "self_reported": []}
    runs = {r["run_id"]: r for r in tl["runs"]}
    assert set(runs) == {res["parent_run"], res["helper_run"]}
    helper = runs[res["helper_run"]]
    assert helper["delegated_by"] == res["parent_run"] and helper["access"] == "read_only"
    assert isinstance(helper["pid"], int) and helper["pid"] != runs[res["parent_run"]]["pid"]
    writes = {a["path"]: a for a in tl["actions"]}
    assert any(a["path"] == "reports/draft.md" and a["operation"] == "create"
               and a["actor"] == "Report agent" for a in tl["actions"])
    assert writes["reports/summary.md"]["actor"] == "Report helper"
    assert writes["reports/summary.md"]["initiated_by"] == res["parent_run"]
    assert [a["actor"] for a in tl["actions"]] == ["Report agent", "Maya Chen", "Report helper", "Maya Chen"]
    assert all(a["simulated"] and a["run_id"] is None for a in tl["actions"] if a["actor_type"] == "human")
    assert "Maya Chen" in answer_from_records("Who was the human in the workflow?", {"timeline": tl})["answer"]
    assert {"POL-007", "POL-008", "POL-009", "POL-015"} <= {f["rule_id"] for f in tl["findings"]}
    verdicts = {r["label"]: r["verdict"] for r in tl["self_reported"] if r["kind"] == "run_started"}
    assert verdicts == {"Report agent": "confirmed", "Read-only finisher": "disagrees with observer"}
    # Diffs are hashed by the timeline; the raw patch text stays only in the pilot's own log.
    assert "Finalized by helper" not in json.dumps(tl, default=str)


def test_pilot_refuses_a_foreign_non_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("mine")
    with pytest.raises(ValueError, match="new or empty"):
        run_pilot(tmp_path)


def test_pilot_can_rerun_in_its_own_folder(tmp_path: Path) -> None:
    run_pilot(tmp_path / "p")
    res = run_pilot(tmp_path / "p")
    assert res["helper_run"]
