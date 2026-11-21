"""Archive append-only collector records in a separate bare Git repository."""

import argparse
import hashlib
import json
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

REF = "refs/heads/main"
MARKER = "forkit-audit-v1"


def read_archive(repo, sample_log=None):
    """Read the newest complete rows without modifying the repository."""
    records = deque(maxlen=250)
    total = 0

    def load(stream):
        nonlocal total
        for line in stream:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("Expected an audit record object.")
            records.append(record)
            total += 1

    source = "Audit repository" if repo else "Sample audit records"
    try:
        if repo:
            with subprocess.Popen(["git", "-C", str(repo), "show", f"{REF}:events.jsonl"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                load(process.stdout)
                if process.wait():
                    raise ValueError("Could not read the audit repository.")
        elif sample_log:
            with sample_log.open("rb") as stream:
                load(stream)
        else:
            return None
    except (OSError, ValueError):
        return {"source": source, "records": [], "total": 0,
                "error": "Could not load the audit records. Check that the repository has a main branch with events.jsonl."}
    return {"source": source, "records": list(reversed(records)), "total": total}


def git(repo, *args, data=None):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Forkit Audit",
         "-c", "user.email=audit@forkit.invalid", *args],
        input=data, stdout=subprocess.PIPE, check=True,
    ).stdout


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def checkpoint(repo, log, *, from_now=False):
    repo = repo.resolve()
    log = log.resolve()
    if log.is_relative_to(repo):
        raise ValueError("Keep the collector log outside the audit repository.")
    raw = log.read_bytes()
    complete = raw[:raw.rfind(b"\n") + 1]
    if repo.exists():
        if not (repo / MARKER).is_file():
            raise ValueError("Choose a new directory or an existing Forkit audit repository.")
        if from_now:
            raise ValueError("Choose a new directory to start a fresh recording.")
    else:
        repo.mkdir(parents=True)
        git(repo, "init", "--bare", "--initial-branch=main", "--quiet")
        (repo / MARKER).touch(exist_ok=False)

    head = git(repo, "for-each-ref", "--format=%(objectname)", REF).decode().strip()
    if head:
        saved = json.loads(git(repo, "show", f"{head}:checkpoint.json"))
        previous = git(repo, "show", f"{head}:events.jsonl")
    else:
        saved = {"bytes": len(complete) if from_now else 0}
        saved["sha256"] = sha256(complete[:saved["bytes"]])
        previous = b""

    offset = saved["bytes"]
    if len(complete) < offset or sha256(complete[:offset]) != saved["sha256"]:
        raise ValueError("Collector history changed. The archived records were kept.")
    new = complete[offset:]
    records = new.split(b"\n")[:-1]
    for index, line in enumerate(records, 1):
        try:
            if not isinstance(json.loads(line), dict):
                raise ValueError()
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"Invalid collector JSON object in new row {index}.") from error
    if head and not records:
        return 0

    metadata = {"bytes": len(complete), "sha256": sha256(complete),
                "recorded_at": datetime.now(timezone.utc).isoformat(), "added": len(records)}
    tree = ""
    for name, content in (("checkpoint.json", json.dumps(metadata, indent=2).encode() + b"\n"),
                          ("events.jsonl", previous + new)):
        blob = git(repo, "hash-object", "-w", "--stdin", data=content).decode().strip()
        tree += f"100644 blob {blob}\t{name}\n"
    tree_id = git(repo, "mktree", data=tree.encode()).decode().strip()
    parents = ("-p", head) if head else ()
    commit = git(repo, "commit-tree", tree_id, *parents,
                 "-m", f"Record {len(records)} collector events").decode().strip()
    git(repo, "update-ref", REF, commit, head or "0" * len(commit))
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("collector_log", type=Path)
    parser.add_argument("--from-now", action="store_true",
                        help="Start a fresh repository after the already completed collector records")
    args = parser.parse_args()
    try:
        count = checkpoint(args.repository, args.collector_log, from_now=args.from_now)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Recorded {count} new events.")


if __name__ == "__main__":
    main()
