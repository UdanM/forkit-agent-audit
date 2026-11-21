"""Import committed Git changes into a Forkit audit archive."""

import argparse
import base64
import json
import subprocess
from pathlib import Path

from audit_repo import checkpoint

METADATA_MARKER = "forkit-metadata-only-v1"


def source_git(source, *args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={source}", "-C", str(source), *args]
    )


def import_commits(source, archive, feed, *, metadata_only=False):
    source, archive, feed = source.resolve(), archive.resolve(), feed.resolve()
    if archive.is_relative_to(source) or feed.is_relative_to(source):
        raise ValueError("Keep the archive and feed outside the monitored checkout.")
    metadata_only = metadata_only or (archive / METADATA_MARKER).is_file()
    tip = source_git(source, "rev-parse", "HEAD").decode().strip()
    commits = source_git(source, "rev-list", "--topo-order", "--reverse", tip).decode().splitlines()
    if feed.exists():
        existing = feed.read_bytes()
        if existing and not existing.endswith(b"\n"):
            raise ValueError("The Git collector feed ends in an incomplete record.")
        rows = [json.loads(line) for line in existing.splitlines()]
        contains_patches = any("patch" in row or "patch_bytes_base64" in row for row in rows)
        if metadata_only and contains_patches:
            raise ValueError("Use a new feed and archive for metadata-only recording.")
        metadata_only = metadata_only or bool(rows and not contains_patches)
        recorded = {row["commit"] for row in rows}
        if not recorded.issubset(commits):
            raise ValueError("The source history changed. Use a separate archive for the new history.")
        commits = [commit for commit in commits if commit not in recorded]

    status_options = ("--no-ext-diff", "--no-textconv", "--no-renames",
                      "--root", "--diff-merges=first-parent")
    with feed.open("ab") as stream:
        for commit in commits:
            fields = source_git(source, "show", "--format=", "--name-status", "-z",
                                *status_options, commit).split(b"\0")
            changes = [{"path": path.decode("utf-8", "replace"), "status": status.decode("ascii")}
                       for status, path in zip(fields[::2], fields[1::2])]
            files = [change["path"] for change in changes]
            record = {"id": commit, "commit": commit,
                      "time": source_git(source, "show", "-s", "--format=%cI", commit).decode().strip(),
                      "kind": "git_commit", "operation": "Committed changes", "source": "Git commit history",
                      "run_id": None, "path": files[0] if len(files) == 1 else None,
                      "paths": files, "changes": changes}
            if not metadata_only:
                patch = source_git(source, "show", "--format=", "--binary", *status_options, commit)
                try:
                    record["patch"] = patch.decode("utf-8")
                except UnicodeDecodeError:
                    record["patch"] = patch.decode("utf-8", "replace")
                    record["patch_bytes_base64"] = base64.b64encode(patch).decode("ascii")
            stream.write(json.dumps(record).encode() + b"\n")
    added = checkpoint(archive, feed)
    if metadata_only:
        (archive / METADATA_MARKER).touch()
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("feed", type=Path)
    parser.add_argument("--metadata-only", action="store_true", help="Archive paths and status without file contents")
    args = parser.parse_args()
    try:
        added = import_commits(args.source, args.archive, args.feed, metadata_only=args.metadata_only)
        print(f"Archived {added} Git commits.")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
