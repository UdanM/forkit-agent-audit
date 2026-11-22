import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from demo.agents.prompt_pair import PROMPT, run_pair
from src.engine import load_policies
from src.timeline import timeline_from_files

ROOT = Path(__file__).resolve().parents[1]


def test_parallel_agents_share_a_prompt_but_only_attachment_run_leaves_scope(tmp_path):
    log = run_pair(tmp_path / "experiment", start_at=datetime.now(timezone.utc) + timedelta(seconds=2))
    timeline = timeline_from_files(log, rules=load_policies(ROOT / "fixtures" / "policies.json"))
    trial, = timeline["prompt_comparisons"]
    local, cloud = trial["runs"]
    assert trial["same_prompt"] is True and trial["overlap"] is True
    assert local["deployment"] == "local-pc" and local["attachment"] is None
    assert local["outcome"] == "Within task scope"
    assert cloud["deployment"] == "cloud-vm" and cloud["attachment"] == "inputs/extra-instructions.json"
    assert cloud["outcome"] == "Out-of-scope change"
    finding, = [row for row in timeline["findings"] if row["check"] == "attachment_scope_mismatch"]
    assert finding["record_ids"] == cloud["out_of_scope_ids"]
    assert (tmp_path / "experiment" / "cloud-vm" / "workspace" / "scratch" / "off-task.txt").is_file()
    assert not (tmp_path / "experiment" / "local-pc" / "workspace" / "scratch" / "off-task.txt").exists()
    assert PROMPT not in log.read_text(encoding="utf-8")


def test_attachment_alone_is_not_a_finding(tmp_path):
    fixture = ROOT / "fixtures" / "activity" / "prompt_pair_observer.jsonl"
    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
    rows = [row for row in rows if row.get("path") != "scratch/off-task.txt"]
    log = tmp_path / "observer.jsonl"
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    trial = timeline_from_files(log, rules=load_policies(ROOT / "fixtures" / "policies.json"))
    assert "attachment_scope_mismatch" not in trial["signals"]
    assert trial["prompt_comparisons"][0]["runs"][1]["outcome"] == "Within task scope"


def test_ambiguous_trial_shows_all_runs_without_claiming_a_pair(tmp_path):
    fixture = ROOT / "fixtures" / "activity" / "prompt_pair_observer.jsonl"
    original = fixture.read_text(encoding="utf-8")
    extra = json.loads(original.splitlines()[0])
    extra.update(id="extra-start", run_id="extra-control")
    log = tmp_path / "observer.jsonl"
    log.write_text(original + json.dumps(extra) + "\n", encoding="utf-8")
    timeline = timeline_from_files(log, rules=load_policies(ROOT / "fixtures" / "policies.json"))
    trial, = timeline["prompt_comparisons"]
    assert trial["same_prompt"] is None and trial["overlap"] is None
    assert len(trial["runs"]) == 3
    assert "attachment_scope_mismatch" in timeline["signals"]


def test_scope_check_counts_deletes_but_excludes_edits_after_the_run(tmp_path):
    fixture = ROOT / "fixtures" / "activity" / "prompt_pair_observer.jsonl"
    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
    delete = rows[-1]
    delete.update(path="reports/status.txt", operation="delete")
    after = dict(delete, id="later-edit", time="2026-11-20T11:00:03Z", path="scratch/later.txt", operation="create")
    log = tmp_path / "observer.jsonl"
    log.write_text("".join(json.dumps(row) + "\n" for row in [*rows, after]), encoding="utf-8")
    timeline = timeline_from_files(log, rules=load_policies(ROOT / "fixtures" / "policies.json"))
    cloud = timeline["prompt_comparisons"][0]["runs"][1]
    assert cloud["outcome"] == "Out-of-scope change"
    assert cloud["observed_paths"] == ["reports/status.txt"]
    assert timeline["signals"]["attachment_scope_mismatch"] == [delete["id"]]
