"""Run an isolated report workflow and observe its real subprocess file changes."""

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from timeline import timestamp

SCENARIOS = ("approved", "delegated")
MARKER = ".forkit-agent-pilot"
TOOL = "Forkit report workflow"


def target_file(root, scenario):
    target = root / "workspace" / scenario / "status.txt"
    if not target.resolve().is_relative_to(root) or target.is_symlink():
        raise ValueError("The pilot file must stay inside its test workspace.")
    return target


def worker(root, scenario, role):
    if not (root / MARKER).is_file():
        raise ValueError("This directory has not been initialized by the pilot.")
    target = target_file(root, scenario)
    if scenario == "delegated" and role == "parent":
        if target.read_text(encoding="utf-8").startswith("Draft"):
            print("delegate:finalize")
        return
    if target.read_text(encoding="utf-8").startswith("Draft"):
        target.write_bytes(b"Ready for review\n")


def run_pilot(root, deployment, scenario):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("Choose a new or empty directory for the pilot.")
        marker.touch()
    target = target_file(root, scenario)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"Draft report\n")
    log = root / "observer.jsonl"
    if log.is_symlink():
        raise ValueError("The pilot log must be a regular file.")
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        def observe(kind, run_id, **fields):
            entry = {"id": uuid4().hex, "time": timestamp(), "kind": kind, "run_id": run_id,
                     "deployment": deployment, **fields}
            stream.write(json.dumps(entry) + "\n")
            stream.flush()

        def launch(role, parent=None):
            run_id = uuid4().hex
            before = target.read_text(encoding="utf-8").splitlines(keepends=True)
            access = "read_only" if scenario == "delegated" else "write"
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), scenario, role]
            with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
                observe("run_started", run_id, tool=TOOL, agent_id="report-workflow", instance_id=role,
                        label="Report agent" if role == "parent" else "Report helper",
                        parent_id=parent, pid=process.pid, access=access, onboarded=True,
                        location=f"{deployment}/pilot")
                output, _ = process.communicate(timeout=15)
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
            after = target.read_text(encoding="utf-8").splitlines(keepends=True)
            if after != before:
                path = f"{deployment}/{scenario}/{target.name}"
                patch = "".join(difflib.unified_diff(before, after, f"a/{path}", f"b/{path}"))
                observe("file_write", run_id, path=path, operation="write", patch=patch,
                        approval=scenario == "approved", protected=scenario == "delegated",
                        initiator_run_id=parent, pid=process.pid, observer_pid=os.getpid(),
                        source="Pilot subprocess observer")
            return run_id, output.strip()

        parent, request = launch("parent")
        if request == "delegate:finalize" and scenario == "delegated":
            launch("helper", parent)


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        root, scenario, role = sys.argv[2:]
        if scenario not in SCENARIOS or role not in ("parent", "helper"):
            raise SystemExit("Unknown pilot request.")
        worker(Path(root).resolve(), scenario, role)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--deployment", required=True, choices=("local-windows", "cloud-linux"))
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()
    run_pilot(args.output, args.deployment, args.scenario)
    print(f"{args.deployment}: {args.scenario} completed; observer.jsonl recorded the result.")


if __name__ == "__main__":
    main()
