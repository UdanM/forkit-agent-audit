"""Delegated-helper pilot: real subprocesses, watched by an independent observer.

    python -m demo.agents.pilot data/agents/pilot

Ported from UdanM/forkit-agent-audit @ 3f5b2aa (``agent_pilot.py``) and wired
into AutoDiscover's activity timeline (tab 06) and rules POL-006..POL-018.

What happens, with synthetic files only:

1. The observer (this process) starts a *report agent* worker as a separate OS
   process and records ``run_started`` with its PID, agent identity and
   declared access (``write``).
2. The worker drafts a report, then asks to delegate: it prints
   ``delegate:finalize``.
3. A simulated human reviewer adds a note to the draft.
4. The observer starts a *helper* worker as a second process, with parent
   ``run_id`` and declared access ``read_only``. The helper still edits the
   protected summary file, as a misbehaving helper would.
5. The simulated reviewer logs the handoff in the draft. Around each step,
   the observer hashes every file in the pilot workspace and
   records what really changed (path, operation, diff hash and size). It never
   relies on what a worker says it did.
6. Each worker also writes its own report (``agent_reported.jsonl``). The
   helper describes itself as "read-only finisher", which the timeline flags
   as disagreeing with the observer.

The pilot only sees changes to its own workspace around each worker. It does
not enforce OS permissions and is not a tamper-proof host monitor.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MARKER = ".autodiscover-pilot"
DEPLOYMENT = "local-pilot"
AGENT_ID = "report-workflow"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _snapshot(root: Path) -> dict[str, tuple[str, str]]:
    """{relative path: (sha256, text)} for every regular file in the workspace."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            out[p.relative_to(root).as_posix()] = (hashlib.sha256(data).hexdigest(),
                                                   data.decode("utf-8", "replace"))
    return out


def worker(workspace: Path, role: str, reported_log: Path, run_id: str) -> None:
    """The agent side. Runs in its own process; writes its own self-report."""
    def report(kind: str, **fields: Any) -> None:
        with reported_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": uuid.uuid4().hex[:16], "time": _now(), "kind": kind,
                                 "run_id": run_id, "deployment": DEPLOYMENT, **fields}) + "\n")

    reports = workspace / "reports"
    if role == "parent":
        report("run_started", label="Report agent")
        (reports / "draft.md").write_text("# Q3 draft\n\nRevenue grew 4%.\n", encoding="utf-8")
        report("file_write", path="reports/draft.md", operation="create")
        print("delegate:finalize", flush=True)
    else:
        report("run_started", label="Read-only finisher")   # claims to be read-only
        summary = reports / "summary.md"
        summary.write_text(summary.read_text(encoding="utf-8") + "Finalized by helper.\n", encoding="utf-8")
        # It does not report the write.


def run_pilot(out: Path) -> dict[str, Any]:
    """Run the pilot into ``out`` (a new or pilot-owned folder). Returns paths and run IDs."""
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not (out / MARKER).exists():
        if any(out.iterdir()):
            raise ValueError("Choose a new or empty folder for the pilot.")
        (out / MARKER).touch()
    workspace = out / "workspace"
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports" / "summary.md").write_text("# Q3 summary\n\nSynthetic report.\n", encoding="utf-8")
    observer_log, reported_log = out / "observer.jsonl", out / "agent_reported.jsonl"
    for log in (observer_log, reported_log):
        if log.is_symlink():
            raise ValueError("Pilot logs must be regular files.")
        log.write_text("", encoding="utf-8")
    seq = 0

    def observe(kind: str, run_id: str | None, **fields: Any) -> None:
        nonlocal seq
        seq += 1
        row = {"id": f"obs-{seq:04d}", "time": _now(), "kind": kind, "run_id": run_id,
               "deployment": DEPLOYMENT, "sequence": seq, **fields}
        with observer_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def record_changes(before: dict[str, tuple[str, str]], run_id: str | None, **evidence: Any) -> None:
        after = _snapshot(workspace)
        for path in sorted(set(before) | set(after)):
            if before.get(path, ("",))[0] == after.get(path, ("",))[0]:
                continue
            op = "create" if path not in before else "delete" if path not in after else "write"
            patch = "".join(difflib.unified_diff(
                before.get(path, ("", ""))[1].splitlines(keepends=True),
                after.get(path, ("", ""))[1].splitlines(keepends=True), f"a/{path}", f"b/{path}"))
            observe("file_write", run_id, path=path, operation=op, patch=patch,
                    protected=path == "reports/summary.md", **evidence)

    def launch(role: str, parent: str | None) -> tuple[str, str]:
        run_id = f"{role}-{uuid.uuid4().hex[:8]}"
        access = "write" if role == "parent" else "read_only"
        before = _snapshot(workspace)
        cmd = [sys.executable, "-m", "demo.agents.pilot", "--worker", str(workspace), role, str(reported_log), run_id]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, cwd=Path(__file__).resolve().parents[2]) as proc:
            observe("run_started", run_id, agent_id=AGENT_ID, instance_id=role, parent_id=parent, pid=proc.pid,
                    access=access, onboarded=True, tool="pilot-worker",
                    label="Report agent" if role == "parent" else "Report helper", location=f"{DEPLOYMENT}/workspace")
            output, _ = proc.communicate(timeout=30)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
        record_changes(before, run_id, initiator_run_id=parent, approval=role == "parent")
        return run_id, output.strip()

    def simulate_review(note: str) -> None:
        before = _snapshot(workspace)
        with (workspace / "reports" / "draft.md").open("a", encoding="utf-8") as draft:
            draft.write("\n" + note + "\n")
        record_changes(before, None, actor_type="human", human_id="maya-reviewer",
                       human_label="Maya Chen", simulated=True, approval=True)

    parent, request = launch("parent", None)
    simulate_review("Reviewer note: verify the revenue figure before finalizing.")
    helper = None
    if request == "delegate:finalize":
        helper, _ = launch("helper", parent)
        simulate_review("Reviewer note: send the protected edit to governance review.")
    return {"observer_log": observer_log, "reported_log": reported_log, "parent_run": parent, "helper_run": helper}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--worker"]:
        worker(Path(args[1]), args[2], Path(args[3]), args[4])
        return 0
    p = argparse.ArgumentParser(prog="pilot", description=__doc__.splitlines()[0])
    p.add_argument("out", type=Path, help="New or empty folder for the pilot workspace and logs")
    ns = p.parse_args(args)
    res = run_pilot(ns.out)
    print(f"Observer log: {res['observer_log']}\nAgent self-report: {res['reported_log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
