import json
import tempfile
import unittest
from pathlib import Path

from agent_pilot import run_pilot
from audit_repo import checkpoint, read_archive
from timeline import collect_stream


class AgentPilotTests(unittest.TestCase):
    def test_real_workers_leave_auditable_diffs_and_separate_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "observer.jsonl"
            for scenario in ("approved", "delegated"):
                run_pilot(root, "test-local", scenario)
            self.assertTrue((root / "workspace/approved/status.txt").is_file())
            archived = root / "changes.git"
            self.assertGreater(checkpoint(archived, log), 0)
            archive = read_archive(archived)
            self.assertTrue(any("+Ready for review" in item.get("patch", "") for item in archive["records"]))
            records, runs, cases, _ = collect_stream("Pilot", "pilot", log.read_bytes, None, False, {})
            parents = [run for run in runs if run["label"] == "Report agent"]
            helper = next(run for run in runs if run["label"] == "Report helper")
            self.assertEqual(len({run["id"] for run in parents}), 2)
            self.assertEqual(len({run["agent_id"] for run in parents}), 1)
            self.assertNotEqual(helper["agent_id"], parents[0]["agent_id"])
            self.assertTrue(any(row["initiator_run_id"] for row in records))
            self.assertTrue({"pilot-read-only", "pilot-protected", "pilot-approval"} <= {case["id"] for case in cases})
            self.assertTrue(all(isinstance(row.get("pid"), int) for row in
                                [json.loads(line) for line in log.read_text().splitlines()]
                                if row["kind"] == "run_started"))
