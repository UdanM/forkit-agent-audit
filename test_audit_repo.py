import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from audit_repo import checkpoint


class AuditRepositoryTests(unittest.TestCase):
    def test_complete_records_and_fresh_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "collector.jsonl"
            event = {"id": "one", "run_id": "agent-1", "path": "hello.txt",
                     "patch": "@@ -1 +1 @@\n-old\n+new\n"}
            first = json.dumps(event).encode() + b"\n"
            log.write_bytes(first)
            repo = root / "archive"
            self.assertEqual(checkpoint(repo, log), 1)
            self.assertEqual(checkpoint(repo, log), 0)
            recorded = subprocess.check_output(["git", "-C", str(repo), "show", "HEAD:events.jsonl"])
            self.assertEqual(json.loads(recorded), event)
            from test_acceptance import running
            with running(root / "dashboard.sqlite3", "--live", "--audit-repo", str(repo)) as call:
                archive = call("api/state")["archive"]
                self.assertEqual(archive["records"], [event])
                self.assertEqual(archive["total"], 1)
            fresh = root / "fresh"
            self.assertEqual(checkpoint(fresh, log, from_now=True), 0)
            log.write_bytes(first + b'{"id":"two","run_id":null}\n')
            self.assertEqual(checkpoint(fresh, log), 1)
            self.assertEqual(subprocess.check_output(["git", "-C", str(fresh), "show", "HEAD:events.jsonl"]), b'{"id":"two","run_id":null}\n')
            log.write_bytes(b'{"id":"changed"}\n')
            with self.assertRaises(ValueError):
                checkpoint(fresh, log)
