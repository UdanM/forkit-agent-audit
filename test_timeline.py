import json
import tempfile
import unittest
from pathlib import Path

from test_acceptance import running
from timeline import checkpoint_bytes, collect_stream


def event(event_id, kind, run_id, **fields):
    return json.dumps({"id": event_id, "time": "2026-11-20T17:42:00Z", "kind": kind,
                       "run_id": run_id, **fields}).encode() + b"\n"


class TimelineTests(unittest.TestCase):
    def test_agent_identity_and_risk_findings_use_observer_evidence(self):
        identity = {"agent_id": "stable-agent", "instance_id": "install-one", "deployment": "lab"}
        old = event("old", "run_started", "run-1", **identity, label="Original",
                    access="read_only", sequence=1, time="2026-11-20T17:41:00Z")
        new = event("new", "run_started", "run-2", **identity, label="Renamed",
                    access="write", sequence=2, time="2026-11-20T17:41:30Z")
        clone = event("clone", "run_started", "run-3", **{**identity, "instance_id": "install-two"},
                      label="Renamed", sequence=3)
        write = event("edit", "file_write", "run-2", path="approved/report.md", sequence=5,
                      deployment="lab", approval=False, protected=True, observed_at="2026-11-20T18:00:00Z")
        data = old + new + clone + write
        records, runs, cases, _ = collect_stream("Example", "observer", lambda: data,
                                                 None, False, {})
        self.assertEqual(runs[0]["agent_id"], runs[1]["agent_id"])
        self.assertNotEqual(runs[0]["agent_id"], runs[2]["agent_id"])
        self.assertTrue({"observer-rename", "observer-access", "observer-time", "observer-gap",
                         "observer-protected", "observer-approval"} <= {case["id"] for case in cases})
        self.assertEqual(records[-1]["approval"], False)
        _, self_reported, _, _ = collect_stream("Agent log", "agent", lambda: data, None, True, {})
        self.assertTrue(all(run["agent_id"] is None for run in self_reported))

    def test_scoped_runs_and_checkpoint_do_not_reassign_actor(self):
        time = "2026-11-20T17:41:00Z"
        pinned = event("action", "file_write", "shared", path="test.txt", deployment="cloud")
        starts = (event("start", "run_started", "shared", time=time, deployment="local", access="read_only")
                  + event("start", "run_started", "shared", time=time, deployment="cloud", access="read_only"))
        extra = event("action", "file_write", "shared", path="test.txt", deployment="local")
        rows, _, cases, _ = collect_stream("Lab", "lab", lambda: pinned + starts + extra,
                                           checkpoint_bytes(pinned), False, {})
        writes = [row for row in rows if row["kind"] == "file_write"]
        self.assertIsNone(writes[0]["run_id"])
        self.assertIsNotNone(writes[1]["run_id"])
        self.assertEqual(next(case for case in cases if case["id"] == "lab-read-only")["record_ids"],
                         [writes[1]["id"]])

    def test_duplicate_replay_conflict_and_reused_run_reference(self):
        start = event("start", "run_started", "editor", tool="tool_123")
        action = event("write", "file_write", "editor", path="reports/summary.md")
        collision = event("write", "file_write", "editor", path="reports/changed.md")
        newer_start = event("second-start", "run_started", "editor", tool="tool_123")
        other_action = event("other-write", "file_write", "editor", path="reports/other.md")
        data = start + start + action + collision + newer_start + other_action + b"{partial"
        records, runs, cases, _ = collect_stream("Example", "one", lambda: data,
                                                  None, False, {"tool_123": "tool_123"})
        self.assertEqual({case["id"] for case in cases}, {"one-conflict", "one-parse", "one-ambiguous"})
        self.assertEqual(len(records), 3)
        self.assertEqual(runs, [])
        records_with_path = [item for item in records if item["path"]]
        self.assertEqual([item["path"] for item in records_with_path], ["reports/other.md"])
        self.assertIsNone(records_with_path[0]["run_id"])

    def test_checkpoint_flags_a_changed_log_but_unchecked_appends_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "audit.jsonl"
            original = event("start", "run_started", "editor")
            appended = event("write", "file_write", "editor", path="reports/summary.md")
            pin = checkpoint_bytes(original).upper()
            log.write_bytes(original + appended)
            with running(root / "timeline.sqlite3", "--live", "--audit-log", str(log),
                         "--audit-checkpoint", pin) as call:
                timeline = call("api/timeline", {})["timeline"]
                self.assertEqual(timeline["sources"][0]["integrity"], "Saved prefix matches")
                record = next(item for item in timeline["records"] if item["path"])
                self.assertIn("added after the saved checkpoint", record["explanation"])
                self.assertEqual(log.read_bytes(), original + appended)
                log.write_bytes(original.replace(b"editor", b"helper") + appended)
                timeline = call("api/timeline", {})["timeline"]
                self.assertEqual(timeline["sources"][0]["integrity"], "Saved checkpoint mismatch")
                self.assertEqual(timeline["records"], [])

    def test_snapshot_keeps_actor_unknown_and_baseline_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watched = root / "watched"
            watched.mkdir()
            db = root / "state.sqlite3"
            switches = ("--live", "--watch-dir", str(watched))
            (watched / "notes.txt").write_text("first revision")
            (watched / "delete.txt").write_text("remove")
            with running(db, *switches) as call:
                timeline = call("api/timeline", {})["timeline"]
                self.assertEqual(timeline["sources"][0]["integrity"], "First folder check saved")
                self.assertEqual(timeline["records"], [])
            (watched / "notes.txt").write_text("second revision, sensitive contents")
            (watched / "new.txt").touch()
            (watched / "delete.txt").unlink()
            with running(db, *switches) as call:
                timeline = call("api/timeline", {})["timeline"]
                changes = {record["path"]: record for record in timeline["records"]}
                self.assertEqual(changes["new.txt"]["summary"], "File appeared")
                self.assertEqual(changes["notes.txt"]["summary"], "File metadata changed")
                self.assertEqual(changes["delete.txt"]["summary"], "File disappeared")
                self.assertTrue(all(record["run_id"] is None for record in changes.values()))
                self.assertNotIn("sensitive contents", json.dumps(timeline))
                self.assertEqual(call("api/timeline", {})["timeline"]["records"], timeline["records"])
