import subprocess
import tempfile
import unittest
from pathlib import Path

from audit_repo import read_archive
from git_audit import import_commits
from timeline import git_snapshot


class GitAuditTests(unittest.TestCase):
    def test_preserves_diffs_without_inventing_an_actor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture",
                                "-c", "user.email=fixture@example.invalid", *args],
                               check=True, stdout=subprocess.DEVNULL)
            git("init", "-q")
            file = source / "notes.txt"
            for value in ("before", "after"):
                file.write_text(value + "\n", encoding="utf-8")
                git("add", "notes.txt")
                git("commit", "-qm", value)
            archive = root / "archive.git"
            feed = root / "git.jsonl"
            self.assertEqual(import_commits(source, archive, feed), 2)
            self.assertEqual(import_commits(source, archive, feed), 0)
            record = read_archive(archive)["records"][0]
            self.assertIsNone(record["run_id"])
            self.assertEqual(record["paths"], ["notes.txt"])
            self.assertIn("-before\n+after", record["patch"])
            safe = root / "metadata.git"
            safe_feed = root / "metadata.jsonl"
            self.assertEqual(import_commits(source, safe, safe_feed, metadata_only=True), 2)
            safe_record = read_archive(safe)["records"][0]
            self.assertEqual(safe_record["changes"], [{"path": "notes.txt", "status": "M"}])
            self.assertNotIn("patch", safe_record)
            self.assertNotIn("after", safe_feed.read_text())
            file.write_text("private third revision\n", encoding="utf-8")
            git("add", "notes.txt")
            git("commit", "-qm", "third")
            self.assertEqual(import_commits(source, safe, safe_feed), 1)
            self.assertNotIn("private third revision", safe_feed.read_text())
            self.assertNotIn("patch", read_archive(safe)["records"][0])
            (source / ".gitignore").write_text("cache/\n", encoding="utf-8")
            (source / "cache").mkdir()
            (source / "cache/data.txt").touch()
            self.assertEqual(set(git_snapshot(source)), {".gitignore", "notes.txt"})
