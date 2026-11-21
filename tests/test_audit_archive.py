"""
Append-only Git audit archive (merge M5). Idea ported from
UdanM/forkit-agent-audit @ 3f5b2aa (audit_repo.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src.audit_archive import (
    FILE,
    REF,
    ArchiveError,
    append_records,
    collect_records,
    git_available,
    init_archive,
    verify_archive,
)
from src.engine import AutoDiscoverEngine

pytestmark = pytest.mark.skipif(not git_available(), reason="git not installed")
REPO = Path(__file__).resolve().parent.parent
CONFIGS = ["fixtures/claude_desktop_config.json", "fixtures/cursor_mcp.json"]
WHEN = "2026-11-21T10:00:00+00:00"


@pytest.fixture()
def eng(tmp_path: Path) -> AutoDiscoverEngine:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    e = AutoDiscoverEngine(tmp_path / "data", base_dir=tmp_path, approvals_path=tmp_path / "fixtures" / "approvals.json")
    for c in e.discover_tools(CONFIGS):
        e.mint_passport(c)
    events = [json.loads(x) for x in (tmp_path / "fixtures" / "runtime_events.jsonl").read_text().splitlines() if x]
    fs = next(k for k, p in e.passports().items() if p["tool_name"] == "filesystem-mcp")
    e.link_event(events[1], fs, user_consent=True)          # evt-0002 -> link + incident
    e.record_for_review(events[3], user_consent=True)        # evt-0004 -> review queue
    return e


def git(repo: Path, *args: str, data: bytes | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL="x@x", GIT_COMMITTER_NAME="x", GIT_COMMITTER_EMAIL="x@x",
               GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "--git-dir", str(repo), *args], input=data, capture_output=True,
                          check=True, env=env).stdout.decode().strip()


def test_collects_links_incidents_and_review_items(eng) -> None:
    kinds = sorted(r["kind"] for r in collect_records(eng.data_dir))
    assert kinds == ["event_link", "incident", "review_item"]


def test_archive_is_append_only_and_idempotent(eng, tmp_path: Path) -> None:
    repo = tmp_path / "archive.git"
    first = eng.archive_to_git(repo)
    assert first["added"] == 3 and first["verify"]["status"] == "PASS"
    again = eng.archive_to_git(repo)
    assert again["added"] == 0 and again["commit"] is None
    eng.activity_timeline("demo", tmp_path / "fixtures" / "activity" / "observer.jsonl")
    third = eng.archive_to_git(repo)
    assert third["added"] == 10  # one per activity rule that fired (no self-report log here)
    status = eng.archive_status(repo)
    assert status == {**status, "status": "PASS", "commits": 2, "records": 13}
    assert git(repo, "log", "--format=%an <%ae>", REF).splitlines() == ["AutoDiscover <autodiscover@localhost>"] * 2


def test_rewritten_history_fails_verification_and_blocks_writes(eng, tmp_path: Path) -> None:
    repo = tmp_path / "archive.git"
    eng.archive_to_git(repo)
    # An attacker rewrites the only commit with an edited record.
    content = git(repo, "show", f"{REF}:{FILE}").replace("MISSING_APPROVAL", "APPROVED")
    blob = git(repo, "hash-object", "-w", "--stdin", data=(content + "\n").encode())
    tree = git(repo, "mktree", data=f"100644 blob {blob}\t{FILE}\n".encode())
    fake = git(repo, "commit-tree", tree, "-m", "rewrite")
    git(repo, "update-ref", REF, fake)
    status = eng.archive_status(repo)
    assert status["status"] == "FAIL"
    assert "history replaced" in status["problems"][0]
    with pytest.raises(ArchiveError, match="failed verification"):
        eng.archive_to_git(repo)


def test_in_place_edit_of_earlier_record_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "a.git"
    append_records(repo, [{"kind": "k", "key": "1", "record": {"v": 1}}], when=WHEN)
    append_records(repo, [{"kind": "k", "key": "2", "record": {"v": 2}}], when=WHEN)
    tip = git(repo, "rev-parse", REF)
    content = git(repo, "show", f"{REF}:{FILE}").replace('"v":1', '"v":9')
    blob = git(repo, "hash-object", "-w", "--stdin", data=(content + "\n").encode())
    tree = git(repo, "mktree", data=f"100644 blob {blob}\t{FILE}\n".encode())
    git(repo, "update-ref", REF, git(repo, "commit-tree", tree, "-p", tip, "-m", "edit"))
    v = verify_archive(repo)
    assert v["status"] == "FAIL" and "earlier records changed" in v["problems"][0]


def test_leaky_batch_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="possible secret"):
        append_records(tmp_path / "a.git", [{"kind": "k", "key": "1", "record": {"t": "sk-abcdefghijklmnop"}}],
                       when=WHEN, leak_check=lambda t: ["token_prefix"] if "sk-" in t else [])
    assert verify_archive(tmp_path / "a.git")["commits"] == 0


def test_refuses_foreign_repo_and_non_empty_folder(tmp_path: Path) -> None:
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "--bare", "-q", str(other)], check=True)
    with pytest.raises(ArchiveError, match="not an AutoDiscover audit archive"):
        init_archive(other)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x")
    with pytest.raises(ArchiveError, match="new or empty folder"):
        init_archive(busy)


def test_missing_archive_reports_missing(tmp_path: Path) -> None:
    assert verify_archive(tmp_path / "nope")["status"] == "MISSING"
