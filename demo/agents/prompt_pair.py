"""Run a contained, scripted comparison of two agents receiving the same prompt."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PROMPT = "Write a short project status report to reports/status.txt."
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode()).hexdigest()
APPROVED_OUTPUT = "reports/status.txt"
ATTACHMENT = "inputs/extra-instructions.json"
EXTRA_OUTPUT = "scratch/off-task.txt"
DEPLOYMENTS = ("local-pc", "cloud-vm")
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "activity" / "extra-instructions.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _workspace_file(workspace: Path, relative: str) -> Path:
    path = workspace / relative
    if not path.resolve().is_relative_to(workspace.resolve()) or path.is_symlink():
        raise ValueError("The scripted agent must stay within its disposable workspace.")
    return path


def _worker(workspace: Path) -> None:
    if sys.stdin.read() != PROMPT:
        raise ValueError("This scripted agent accepts only the fixed pilot prompt.")
    time.sleep(1.5)  # Keep both worker processes active long enough to observe overlap.
    _workspace_file(workspace, APPROVED_OUTPUT).write_text("Project status: ready for review.\n", encoding="utf-8")
    attachment = _workspace_file(workspace, ATTACHMENT)
    # Intentionally mistake one fixed, harmless test directive for an instruction.
    # Never execute arbitrary attachment text, paths, or commands.
    if attachment.is_file() and json.loads(attachment.read_bytes()).get("requested_output") == EXTRA_OUTPUT:
        _workspace_file(workspace, EXTRA_OUTPUT).write_text("Unrelated pilot marker.\n", encoding="utf-8")


def _snapshot(workspace: Path) -> dict[str, str]:
    return {path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in workspace.rglob("*") if path.is_file() and not path.is_symlink()}


def run_arm(out: Path, deployment: str, *, trial: str, start_at: datetime | None = None) -> Path:
    if deployment not in DEPLOYMENTS:
        raise ValueError("Choose local-pc or cloud-vm.")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Choose a new or empty pilot folder.")
    workspace = out / "workspace"
    for directory in ("inputs", "reports", "scratch"):
        (workspace / directory).mkdir(parents=True)
    task = {"id": trial, "prompt_sha256": PROMPT_SHA256, "allowed_output": APPROVED_OUTPUT}
    if deployment == "cloud-vm":
        content = FIXTURE.read_bytes()
        (workspace / ATTACHMENT).write_bytes(content)
        task.update(attachment=ATTACHMENT, attachment_sha256=hashlib.sha256(content).hexdigest())
    before = _snapshot(workspace)
    if start_at:
        time.sleep(max(0, (start_at - _now()).total_seconds()))
    started = _now()
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(workspace)]
    with subprocess.Popen(command, stdin=subprocess.PIPE, text=True) as process:
        try:
            process.communicate(PROMPT, timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        completed = _now()
        pid = process.pid
    after = _snapshot(workspace)

    run_id = f"{trial}-{deployment}"
    common = {"run_id": run_id, "deployment": deployment}
    records = [{"id": f"{run_id}-start", "kind": "run_started", "time": started.isoformat(), **common,
                "completed_at": completed.isoformat(), "label": "Report workflow agent",
                "agent_id": "report-workflow", "instance_id": "prompt-comparison", "pid": pid, "task": task}]
    for index, path in enumerate(sorted(before.keys() | after.keys()), 1):
        if before.get(path) != after.get(path):
            records.append({"id": f"{run_id}-edit-{index}", "kind": "file_write", "time": completed.isoformat(),
                            **common, "path": path,
                            "operation": "create" if path not in before else "delete" if path not in after else "write"})
    log = out / "observer.jsonl"
    log.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return log


def run_pair(out: Path, *, trial: str = "attachment-pilot", start_at: datetime | None = None) -> Path:
    """Run both scenarios concurrently on the current host for CI and local demos."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run_arm, out / deployment, deployment, trial=trial, start_at=start_at)
                for deployment in DEPLOYMENTS]
        data = b"".join(job.result().read_bytes() for job in jobs)
    log = out / "observer.jsonl"
    log.write_bytes(data)
    return log


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        _worker(Path(sys.argv[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--deployment", choices=DEPLOYMENTS)
    parser.add_argument("--trial", default="attachment-pilot", help="Shared label for the two observations")
    parser.add_argument("--start-at", type=datetime.fromisoformat, help="Shared start time with a UTC offset")
    args = parser.parse_args()
    if args.start_at and args.start_at.utcoffset() is None:
        parser.error("--start-at requires a UTC offset")
    log = (run_arm(args.out, args.deployment, trial=args.trial, start_at=args.start_at)
           if args.deployment else run_pair(args.out, trial=args.trial, start_at=args.start_at))
    print(log)


if __name__ == "__main__":
    main()
