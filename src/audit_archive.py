"""
Append-only Git audit archive (merge M5)
========================================

Keeps every governance record (event links, incidents, review queue items,
drift records, activity findings) in a separate bare Git repository, one
commit per batch, so a reviewer can see when each record first appeared and
prove nothing was rewritten later.

Idea ported from UdanM/forkit-agent-audit @ 3f5b2aa (``audit_repo.py``),
re-implemented on AutoDiscover's guardrails:

- **Append-only.** Each commit's ``records.jsonl`` must start with its parent's
  bytes. ``verify_archive`` walks the whole history and fails on any rewrite.
- **Anchored.** The engine remembers the last head it wrote. If the branch is
  later replaced by a consistent but different history, the anchor is no
  longer an ancestor and verification fails.
- **Compare-and-swap.** ``update-ref`` is given the expected old head, so two
  writers can never silently overwrite each other.
- **Metadata-only and leak-checked.** Only redacted store records are written,
  and a batch with a raw secret value or token pattern is refused.
- **Plumbing only.** No working tree, no hooks, no user Git config: commits are
  built with ``hash-object``/``mktree``/``commit-tree`` and a fixed identity.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

REF = "refs/heads/main"
FILE = "records.jsonl"
MARKER = "autodiscover-audit-v1"

# store file -> (record kind, key field). Keys make re-archiving idempotent.
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("linked_events.json", "event_link", "link_id"),
    ("incidents.json", "incident", "incident_id"),
    ("review_queue.json", "review_item", "review_id"),
    ("metadata_changes.json", "metadata_change", "passport_id+new_config_hash"),
)


class ArchiveError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


def _git(repo: Path, *args: str, data: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    base_env.update(env or {})
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "--git-dir", str(repo), *args],
        input=data, capture_output=True, env=base_env, timeout=30,
    )
    if proc.returncode:
        raise ArchiveError(proc.stderr.decode("utf-8", "replace").strip()[:200] or f"git {args[0]} failed")
    return proc.stdout


def init_archive(repo: str | Path) -> Path:
    """Create the bare archive repository, or check an existing one is ours."""
    if not git_available():
        raise ArchiveError("Git is not installed, so the audit archive is unavailable.")
    repo = Path(repo)
    if (repo / "HEAD").is_file():
        try:
            marker = _git(repo, "config", "--get", "autodiscover.archive").decode().strip()
        except ArchiveError:
            marker = ""
        if marker != MARKER:
            raise ArchiveError("That folder is a Git repository, but not an AutoDiscover audit archive.")
        return repo
    if repo.exists() and any(repo.iterdir()):
        raise ArchiveError("Choose a new or empty folder for the audit archive.")
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-q", str(repo)], check=True, capture_output=True, timeout=30,
                   env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    _git(repo, "config", "autodiscover.archive", MARKER)
    _git(repo, "symbolic-ref", "HEAD", REF)
    return repo


def head(repo: Path) -> str | None:
    try:
        return _git(repo, "rev-parse", "--verify", "-q", REF).decode().strip() or None
    except ArchiveError:
        return None


def _content(repo: Path, commit: str | None) -> bytes:
    if commit is None:
        return b""
    try:
        return _git(repo, "show", f"{commit}:{FILE}")
    except ArchiveError:
        return b""


def _key(record: dict[str, Any], field: str) -> str | None:
    parts = [record.get(f) for f in field.split("+")]
    return "@".join(str(p) for p in parts) if all(parts) else None


def collect_records(store_dir: str | Path) -> list[dict[str, Any]]:
    """Every archivable record in a store, as ``{kind, key, record}``. Read-only."""
    store = Path(store_dir)
    out: list[dict[str, Any]] = []
    for name, kind, field in SOURCES:
        path = store / name
        try:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError):
            continue
        for rec in rows if isinstance(rows, list) else []:
            key = _key(rec, field) if isinstance(rec, dict) else None
            if key:
                out.append({"kind": kind, "key": key, "record": rec})
    act = store / "activity_findings.json"
    try:
        blocks = json.loads(act.read_text(encoding="utf-8")) if act.is_file() else {}
    except (OSError, ValueError):
        blocks = {}
    for label, block in sorted(blocks.items()) if isinstance(blocks, dict) else []:
        for f in block.get("findings", []) if isinstance(block, dict) else []:
            ids = ",".join(f.get("record_ids", []))
            out.append({"kind": "activity_finding", "key": f"{label}:{f.get('rule_id')}:{ids}",
                        "record": {**f, "log": label, "checkpoint": block.get("checkpoint")}})
    return out


def append_records(
    repo: str | Path,
    records: list[dict[str, Any]],
    *,
    when: str,
    leak_check: Any = None,
) -> dict[str, Any]:
    """Append records whose ``kind:key`` is not archived yet, as one commit.

    ``leak_check(text) -> list[str]`` returns leak kinds; any hit refuses the batch.
    Returns ``{"commit", "added", "total"}``; ``commit`` is None when nothing was new.
    """
    repo = init_archive(repo)
    old = head(repo)
    existing = _content(repo, old)
    seen = set()
    for line in existing.splitlines():
        try:
            row = json.loads(line)
            seen.add(f"{row['kind']}:{row['key']}")
        except (ValueError, KeyError, TypeError):
            raise ArchiveError("The archive contains a malformed row.") from None
    new_lines = []
    for r in records:
        k = f"{r['kind']}:{r['key']}"
        if k in seen:
            continue
        seen.add(k)
        new_lines.append(json.dumps({"kind": r["kind"], "key": r["key"], "archived_at": when,
                                     "record": r["record"]}, sort_keys=True, separators=(",", ":")))
    total = len(existing.splitlines()) + len(new_lines)
    if not new_lines:
        return {"commit": None, "added": 0, "total": total, "head": old}
    batch = ("\n".join(new_lines) + "\n").encode("utf-8")
    if leak_check:
        kinds = leak_check(batch.decode("utf-8"))
        if kinds:
            raise PermissionError(f"Archive refused: possible secret in the batch ({', '.join(sorted(set(kinds)))})")
    blob = _git(repo, "hash-object", "-w", "--stdin", data=existing + batch).decode().strip()
    tree = _git(repo, "mktree", data=f"100644 blob {blob}\t{FILE}\n".encode()).decode().strip()
    ident = {"GIT_AUTHOR_NAME": "AutoDiscover", "GIT_AUTHOR_EMAIL": "autodiscover@localhost",
             "GIT_COMMITTER_NAME": "AutoDiscover", "GIT_COMMITTER_EMAIL": "autodiscover@localhost",
             "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    args = ["commit-tree", tree, "-m", f"{MARKER}: +{len(new_lines)} record(s)"]
    if old:
        args[2:2] = ["-p", old]
    commit = _git(repo, *args, env=ident).decode().strip()
    # Compare-and-swap: fails if another writer moved the branch meanwhile.
    _git(repo, "update-ref", REF, commit, old or "0" * 40)
    return {"commit": commit, "added": len(new_lines), "total": total, "head": commit}


def verify_archive(repo: str | Path, anchor: str | None = None) -> dict[str, Any]:
    """Walk the whole history: every commit must only append. Read-only."""
    repo = Path(repo)
    if not (repo / "HEAD").is_file():
        return {"status": "MISSING", "commits": 0, "records": 0, "head": None, "problems": []}
    tip = head(repo)
    if tip is None:
        return {"status": "PASS", "commits": 0, "records": 0, "head": None, "problems": []}
    problems = []
    commits = _git(repo, "rev-list", "--reverse", "--topo-order", REF).decode().split()
    prev = b""
    for c in commits:
        parents = _git(repo, "rev-list", "--parents", "-n", "1", c).decode().split()[1:]
        if len(parents) > 1:
            problems.append(f"{c[:12]}: merge commit")
        cur = _content(repo, c)
        if not cur.startswith(prev):
            problems.append(f"{c[:12]}: earlier records changed or removed")
        prev = cur
    if anchor:
        try:
            _git(repo, "merge-base", "--is-ancestor", anchor, tip)
        except ArchiveError:
            problems.append("the last head AutoDiscover wrote is no longer in the history (history replaced)")
    return {"status": "FAIL" if problems else "PASS", "commits": len(commits),
            "records": len(prev.splitlines()), "head": tip, "problems": problems}
