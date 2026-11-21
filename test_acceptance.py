import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


@contextmanager
def running(db, *options):
    with subprocess.Popen(
        [sys.executable, "app.py", "--port", "0", "--db", str(db), *options],
        cwd=ROOT, stdout=subprocess.PIPE, text=True
    ) as process:
        try:
            address = process.stdout.readline().strip().split()[-1]
            with urlopen(address, timeout=5) as response:
                page = response.read().decode("utf-8")
            token = re.search(r'const token = "([^"]+)";', page)[1]

            def call(path, payload=None):
                request = Request(address + "/" + path, headers={
                    "Origin": address, "Content-Type": "application/json", "X-Forkit-Token": token
                }, data=json.dumps(payload).encode() if payload is not None else None)
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            yield call
        finally:
            process.terminate()


def tool(state, source):
    return next(item for item in state["tools"] if item["metadata"]["source"] == source)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "test.sqlite3"

    def test_restart_recognition_and_metadata_change(self):
        with running(self.db) as call:
            candidate = tool(call("api/scan", {}), "desktop")
            self.assertIsNone(candidate["id"])
            registered = call("api/register", {"key": candidate["key"]})
            tool_id = tool(registered, "desktop")["id"]
        with running(self.db) as call:
            self.assertEqual(tool(call("api/state"), "desktop")["id"], tool_id)
            changed = tool(call("api/changed", {}), "desktop")
            self.assertEqual(changed["id"], tool_id)
            self.assertEqual(changed["changes"]["enabled"], {"before": True, "after": False})

    def test_no_false_merges_or_config_secrets_in_export(self):
        with running(self.db) as call:
            state = call("api/scan", {})
            editor, desktop = tool(state, "editor"), tool(state, "desktop")
            self.assertEqual(editor["metadata"]["name"], desktop["metadata"]["name"])
            call("api/register", {"key": editor["key"]})
            state = call("api/register", {"key": desktop["key"]})
            self.assertNotEqual(tool(state, "editor")["id"], tool(state, "desktop")["id"])
            export = call("inventory.json")
            self.assertEqual(len(export["tools"]), 3)
            for secret in ("fixture-secret-do-not-export", "--demo", "example.invalid", "fixture-search"):
                self.assertNotIn(secret, json.dumps(export))

    def test_consent_and_correct_event_link(self):
        with running(self.db) as call:
            state = call("api/scan", {})
            editor, desktop = tool(state, "editor"), tool(state, "desktop")
            call("api/register", {"key": desktop["key"]})
            registered = call("api/register", {"key": editor["key"]})
            tool_id = tool(registered, "editor")["id"]
            for event, key, consent in [
                ("evt-104", editor["key"], False), ("evt-104", desktop["key"], True),
                ("evt-105", editor["key"], True), ("evt-106", editor["key"], True)
            ]:
                with self.assertRaises(HTTPError) as error:
                    call("api/link", {"event": event, "key": key, "consent": consent})
                self.assertEqual(error.exception.code, 400)
            state = call("api/link", {"event": "evt-104", "key": editor["key"], "consent": True})
            self.assertEqual(set(state["links"]), {"evt-104"})
            link = state["links"]["evt-104"]
            self.assertEqual(link["tool_id"], tool_id)
            self.assertEqual(link["source"], "Editor audit fixture")
            self.assertEqual(link["timestamp"], "2026-11-20T17:42:00Z")
            self.assertIsNone(link["approval"])
            self.assertTrue(link["consented_at"])
            incident = call("api/incident", {"event": "evt-106"})["incidents"]["evt-106"]
            self.assertEqual(incident["expected"], "Read-only workspace search")
        with running(self.db) as call:
            self.assertEqual(call("api/state")["links"]["evt-104"], link)

    def test_timeline_keeps_helper_and_cross_source_claims_separate(self):
        with running(self.db) as call:
            self.assertIsNone(call("api/state")["timeline"])
            state = call("api/scan", {})
            for source in ("editor", "desktop"):
                state = call("api/register", {"key": tool(state, source)["key"]})
            timeline = call("api/timeline", {})["timeline"]
            runs = {run["label"]: run for run in timeline["runs"]}
            parent = runs["Editor run"]
            helper = runs["Editor helper run"]
            report = runs["Helper as reported by the agent"]
            self.assertEqual(parent["tool_id"], tool(state, "editor")["id"])
            self.assertEqual(helper["tool_id"], parent["tool_id"])
            self.assertEqual(helper["parent_id"], parent["id"])
            self.assertEqual(len({parent["id"], helper["id"], report["id"]}), 3)
            self.assertIsNone(report["parent_id"])
            cases = {case["id"] for case in timeline["cases"]}
            self.assertTrue({"delegation", "shared-file", "unknown", "cross-source-id",
                             "demo-tampered-changed"} <= cases)
            self.assertTrue(any(record["path"] and record["run_id"] is None
                                for record in timeline["records"]))
            self.assertNotIn("reports/", json.dumps(call("inventory.json")))
        with running(self.db) as call:
            self.assertEqual(call("api/state")["timeline"], timeline)

    def test_live_discovery_in_seeded_environment_and_separate_demo(self):
        root = self.db.parent
        commands = root / "bin"
        commands.mkdir()
        suffix = ".cmd" if os.name == "nt" else ""
        for name in ("codex", "ollama", "gemini", "aider", "opencode"):
            path = commands / (name + suffix)
            path.touch()
            path.chmod(0o755)
        workspace = root / "project"
        (workspace / ".vscode").mkdir(parents=True)
        (workspace / ".vscode/mcp.json").write_text(
            '{"servers":{"shared":{"command":"fixture"}}}', encoding="utf-8")
        (workspace / ".mcp.json").write_text(
            '{"mcpServers":{"shared":{"url":"https://example.invalid"}}}', encoding="utf-8")
        with patch.dict(os.environ, {"PATH": str(commands)}):
            with running(self.db, "--live", "--workspace", str(workspace)) as call:
                live = call("api/scan", {})
                self.assertFalse(live["timeline_configured"])
                tools = [item["metadata"]["name"] for item in live["tools"]]
                self.assertTrue({"codex", "ollama", "gemini", "aider", "opencode"} <= set(tools))
                self.assertEqual(tools.count("shared"), 2)
                demo = call("demo/api/scan", {})
                self.assertTrue(demo["demo"])
                self.assertEqual(len(call("demo/api/timeline", {})["timeline"]["records"]), 12)
                self.assertEqual(call("api/state"), live)


if __name__ == "__main__":
    unittest.main()
