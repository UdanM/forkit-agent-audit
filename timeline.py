"""Local timeline evidence, independent of a registered tool's installation identity."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_FOLDER_ENTRIES = 10_000
MAX_RECORDS = 250
OPERATIONS = {
    "write": "Edited a file", "delete": "Deleted a file",
    "rename": "Renamed a file", "create": "Created a file"
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def stable_key(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]


def bounded_read(path):
    if not path.is_file():
        raise OSError("Expected a regular log file.")
    with path.open("rb") as file:
        data = file.read(MAX_LOG_BYTES + 1)
    if len(data) > MAX_LOG_BYTES:
        raise ValueError("This log exceeds the 4 MiB prototype limit.")
    return data


def checkpoint_bytes(data):
    return f"{len(data)}:{hashlib.sha256(data).hexdigest()}"


def checkpoint_length(checkpoint, data):
    length, expected = checkpoint.split(":")
    length = int(length)
    if (length > len(data) or (length and data[length - 1:length] != b"\n")
            or hashlib.sha256(data[:length]).hexdigest() != expected.lower()):
        raise ValueError("Previously recorded log bytes have changed or are missing.")
    return length


def field(value, *, optional=False, limit=160):
    if value is None and optional:
        return None
    if (not isinstance(value, str) or not 0 < len(value) <= limit
            or value != value.strip() or not value.isprintable()):
        raise ValueError("Invalid text field")
    return value


def relative_path(value):
    path = PurePosixPath(field(value, limit=512).replace("\\", "/"))
    if not path.parts or path.is_absolute() or ".." in path.parts or ":" in str(path):
        raise ValueError("Expected a relative path")
    return str(path)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def parse_time(value):
    time = datetime.fromisoformat(field(value, limit=64).replace("Z", "+00:00"))
    if time.utcoffset() is None:
        raise ValueError("Expected a timestamp with an offset")
    return time.astimezone(timezone.utc)


def parse_event(line):
    item = json.loads(line, object_pairs_hook=unique_object)
    if not isinstance(item, dict):
        raise ValueError("Expected an event object")
    event = {"id": field(item.get("id")), "time": parse_time(item.get("time")),
             "run_id": field(item.get("run_id"), optional=True), "kind": item.get("kind"),
             "deployment": field(item.get("deployment"), optional=True)}
    if type(item.get("pid")) is int and item["pid"] > 0:
        event["pid"] = item["pid"]
    if item.get("observed_at") is not None:
        event["observed_at"] = parse_time(item["observed_at"])
    if item.get("sequence") is not None:
        sequence = item["sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("Expected a nonnegative sequence number")
        event["sequence"] = sequence
    for key in ("approval", "onboarded", "protected"):
        if key in item:
            if item[key] is not None and type(item[key]) is not bool:
                raise ValueError("Expected true, false, or null")
            event[key] = item[key]
    if event["kind"] == "run_started":
        if not event["run_id"]:
            raise ValueError("Expected a run reference")
        event.update(tool=field(item.get("tool"), optional=True),
                     label=field(item.get("label"), optional=True),
                     parent_id=field(item.get("parent_id"), optional=True))
        for key in ("agent_id", "instance_id", "access", "location"):
            event[key] = field(item.get(key), optional=True)
        if event["access"] not in (None, "read_only", "write"):
            raise ValueError("Expected read_only or write access")
    elif event["kind"] == "file_write":
        operation = item.get("operation", "write")
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise ValueError("Unknown operation")
        event.update(path=relative_path(item.get("path")), operation=operation,
                     initiator=field(item.get("initiator_run_id"), optional=True))
    else:
        raise ValueError("Unsupported event type")
    return event


def review_case(case_id, title, status, explanation, record_ids=()):
    return {"id": case_id, "title": title, "status": status,
            "explanation": explanation, "record_ids": list(record_ids)}


def agent_identity(event, namespace):
    identity = [event.get(key) for key in ("deployment", "agent_id", "instance_id")]
    return "agent_" + stable_key(namespace, *identity) if all(identity) else None


def record_id(event, namespace):
    deployment = event["deployment"]
    return (stable_key(namespace, deployment, "event", event["id"]) if deployment
            else stable_key(namespace, "event", event["id"]))


def observer_findings(active, attributed, namespace):
    findings = defaultdict(list)
    latest = {}
    sequences = defaultdict(list)
    for event, _ in sorted(active, key=lambda row: row[0].get("observed_at", row[0]["time"])):
        event_id = record_id(event, namespace)
        if "sequence" in event:
            sequences[event["deployment"]].append((event["sequence"], event_id))
        observed = event.get("observed_at")
        if observed and abs((event["time"] - observed).total_seconds()) > 300:
            findings["time"].append(event_id)
        run = attributed.get(event_id)
        if event["kind"] == "run_started" and run and run["agent_id"]:
            identity = run["agent_id"]
            previous = latest.get(identity, {})
            for field_name, flag in (("label", "rename"), ("location", "move")):
                if previous.get(field_name) and event.get(field_name) and previous[field_name] != event[field_name]:
                    findings[flag].append(event_id)
            if previous.get("access") == "read_only" and event.get("access") == "write":
                findings["access"].append(event_id)
            latest[identity] = {**previous, **{key: value for key, value in event.items() if value is not None}}
        if event["kind"] == "file_write":
            if event.get("protected") is True:
                findings["protected"].append(event_id)
            if "approval" in event and event["approval"] is not True:
                findings["approval"].append(event_id)
            if run and run["access"] == "read_only":
                findings["read-only"].append(event_id)
            if PurePosixPath(event["path"]).name in {"mcp.json", ".mcp.json", "mcp_config.json",
                                                     "claude_desktop_config.json"} or event["path"].endswith(".codex/config.toml"):
                findings["config"].append(event_id)
        if "onboarded" in event and event["onboarded"] is not True:
            findings["onboarding"].append(event_id)
    for series in sequences.values():
        series.sort()
        for first, second in zip(series, series[1:]):
            if second[0] != first[0] + 1:
                findings["gap"].append(second[1])
    descriptions = {
        "time": ("Event and observer clocks disagree", "Check time synchronization or delayed delivery before drawing a conclusion."),
        "rename": ("An agent name changed", "The observer supplied the same deployment and instance identity for both names."),
        "move": ("An agent location changed", "The observer supplied the same identity before and after the move."),
        "access": ("An agent gained write access", "The observer recorded a change from read-only to write access."),
        "protected": ("A protected file was changed", "The observer marked this file as protected. Check whether the change was expected."),
        "approval": ("A file change lacks approval", "The observer marked action approval as missing or denied."),
        "read-only": ("A read-only run was linked to a file change", "Review the recorded access and action before attributing fault."),
        "onboarding": ("Activity with no confirmed onboarding", "The observer reported missing or denied onboarding approval."),
        "gap": ("Activity log has a gap or repeated sequence", "The numbering is incomplete or repeated. Check collector coverage before relying on this timeline."),
        "config": ("A tool configuration file was changed", "Review the change to see whether tool connections or capabilities were altered."),
    }
    return [review_case(namespace + "-" + key, descriptions[key][0], "Needs review",
                        descriptions[key][1], ids) for key, ids in findings.items()]


def collect_stream(name, namespace, read, checkpoint, self_reported, registered):
    source = {"name": name, "integrity": "No saved checkpoint",
              "explanation": "The log is read without writing to it. Its origin and completeness are unverified."}
    if self_reported:
        source["explanation"] = "The agent supplied this account of its activity. It may omit or misstate actions."

    try:
        data = read()
    except (OSError, ValueError) as error:
        reason = str(error) if isinstance(error, ValueError) else "Forkit could not read this log."
        source.update(integrity="Log unavailable", explanation=reason)
        return [], [], [review_case(namespace + "-unavailable", "Activity source unavailable",
                                   "Needs review", name + ". " + reason)], source

    verified = 0
    if checkpoint:
        try:
            verified = checkpoint_length(checkpoint, data)
        except ValueError:
            reason = "The log differs from a previously saved copy. Records from this copy were left out."
            source.update(integrity="Saved checkpoint mismatch", explanation=reason)
            return [], [], [review_case(namespace + "-changed", "A log changed after it was saved",
                                       "Needs review", name + ". " + reason)], source
        source.update(integrity="Saved prefix matches" if verified < len(data) else "Saved checkpoint matches",
                      explanation="Saved bytes match. Any newer lines are unchecked. "
                                  "This check cannot establish who wrote the log or what events are missing.")

    events, conflicts, errors = {}, set(), 0
    offset = 0
    # A producer can append while Forkit reads. Only complete lines become evidence.
    for line in data.splitlines(keepends=True):
        offset += len(line)
        if not line.strip():
            continue
        if not line.endswith(b"\n"):
            errors += 1
            continue
        try:
            event = parse_event(line)
        except (ValueError, TypeError, OverflowError, RecursionError):
            errors += 1
            continue
        event_id = record_id(event, namespace)
        previous = events.get(event_id)
        if previous is not None:
            if previous[0] != event:
                conflicts.add(event_id)
            continue
        events[event_id] = (event, offset <= verified)

    cases = []
    if conflicts:
        cases.append(review_case(namespace + "-conflict", "One event ID has conflicting details",
                                 "Needs review", f"{name} supplied {len(conflicts)} conflicting event IDs. "
                                 "Those records were left out."))
    if errors:
        cases.append(review_case(namespace + "-parse", "Some activity records could not be read",
                                 "Needs review", f"{name} has {errors} invalid or incomplete lines. "
                                 "Those lines were left out."))

    active = [entry for key, entry in events.items() if key not in conflicts]
    starts = defaultdict(list)
    for event, checked in active:
        if event["kind"] == "run_started":
            starts[(event["deployment"], event["run_id"])].append((event, checked))
    unique = {key: rows[0] for key, rows in starts.items() if len(rows) == 1}
    run_ids = {key: stable_key(namespace, key if key[0] else key[1], start["id"], start["time"].isoformat())
               for key, (start, _) in unique.items()}
    ambiguous = len(starts) - len(unique)
    if ambiguous:
        cases.append(review_case(namespace + "-ambiguous", "A run reference was reused",
                                 "Needs review", f"{name} has {ambiguous} run references with multiple starts. "
                                 "Actions using those references were left unassigned."))

    runs = {}
    unresolved_parents = 0

    def eligible(key, event, checked):
        start, start_checked = unique.get(key, (None, False))
        return start if start and start["time"] <= event["time"] and (not checked or start_checked) else None

    for key, (event, checked) in unique.items():
        parent_key = (event["deployment"], event["parent_id"])
        parent = eligible(parent_key, event, checked)
        valid_parent = parent is not None and parent["time"] < event["time"]
        if event["parent_id"] and not valid_parent:
            unresolved_parents += 1
        runs[key] = {"id": run_ids[key], "tool_id": registered.get(event["tool"]),
                     "reported_tool": event["tool"], "deployment": event["deployment"],
                     "agent_id": None if self_reported else agent_identity(event, namespace),
                     "access": event["access"],
                     "started_at": event["time"].isoformat(),
                     "label": event["label"] or f"Run {key[1]}", "source": name,
                     "parent_id": run_ids[parent_key] if valid_parent else None,
                     "reported_id": key[1]}

    if unresolved_parents:
        cases.append(review_case(namespace + "-parent", "A parent run could not be established",
                                 "Needs review", f"{name} has {unresolved_parents} parent references that "
                                 "were missing, ambiguous, or did not start before their child."))

    records = []
    attributed = {}
    for event, checked in active:
        key = (event["deployment"], event["run_id"])
        run = runs.get(key) if eligible(key, event, checked) else None
        kind = event["kind"]
        initiator_key = (event["deployment"], event.get("initiator"))
        identifier = record_id(event, namespace)
        title = "Started a run" if kind == "run_started" else OPERATIONS[event["operation"]]
        check_note = (" This line was in the saved checkpoint." if checked else
                      " This line was added after the saved checkpoint." if checkpoint else "")
        if run:
            attributed[identifier] = run
            confidence = "Reported by agent" if self_reported else "Matched within this log"
            explanation = ("The agent supplied the run reference." if self_reported else
                           "The log references a unique run start in the same source and deployment.")
        else:
            confidence = "Who acted is unknown"
            explanation = "No eligible run start matched this source, deployment and checkpoint status."
        records.append({"id": identifier,
                        "time": event["time"].isoformat(), "kind": kind, "source": name,
                        "deployment": event["deployment"],
                        "summary": title, "path": event.get("path"),
                        "run_id": run["id"] if run else None, "tool_id": run["tool_id"] if run else None,
                        "agent_id": run["agent_id"] if run else None,
                        "initiator_run_id": run_ids[initiator_key] if eligible(initiator_key, event, checked) else None,
                        "approval": event.get("approval"),
                        "approval_reported": "approval" in event,
                        "pid": event.get("pid"),
                        "confidence": confidence, "explanation": explanation + check_note})
    if not self_reported:
        cases.extend(observer_findings(active, attributed, namespace))
    return records, list(runs.values()), cases, source


def snapshot(root):
    result, pending, visited = {}, [root], 0
    if is_link(root.lstat()):
        raise ValueError("The watched folder is a link. Choose the actual folder.")
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_FOLDER_ENTRIES:
                    raise ValueError("This folder exceeds the 10,000-entry prototype limit.")
                info = entry.stat(follow_symlinks=False)
                if is_link(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    result[Path(entry.path).relative_to(root).as_posix()] = [info.st_size, info.st_mtime_ns]
    return result


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def git_snapshot(root):
    root = root.resolve()
    paths = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root),
         "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        stderr=subprocess.DEVNULL,
    ).split(b"\0")
    paths = set(filter(None, paths))
    if len(paths) > MAX_FOLDER_ENTRIES:
        raise ValueError("Git reported more than 10,000 eligible files.")
    result = {}
    for raw in paths:
        relative = os.fsdecode(raw)
        path = root / relative
        try:
            if not path.resolve().is_relative_to(root):
                continue
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not is_link(info) and stat.S_ISREG(info.st_mode):
            result[relative] = [info.st_size, info.st_mtime_ns]
    return result


def compare_snapshot(root, name, namespace, previous, scan=snapshot):
    try:
        current = scan(root)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        reason = str(error) if isinstance(error, ValueError) else "The complete folder could not be read."
        return previous, [], {"name": name, "integrity": "Folder check incomplete",
                              "explanation": reason + " The previous baseline was kept."}
    time = timestamp()
    if previous is None:
        return current, [], {"name": name, "integrity": "First folder check saved",
                            "explanation": "Refresh after a change to compare against this baseline."}

    records = []
    for path in sorted(current.keys() | previous.keys()):
        if previous.get(path) == current.get(path):
            continue
        summary = ("File appeared" if path not in previous else "File disappeared" if path not in current
                   else "File metadata changed")
        records.append({"id": stable_key(namespace, time, path, summary), "time": time,
                        "kind": "snapshot", "source": name, "summary": summary, "path": path,
                        "run_id": None, "tool_id": None, "confidence": "Who acted is unknown",
                        "explanation": "Folder checks found a difference. The time shown is when Forkit saw it."})
    return current, records, {"name": name, "integrity": "Folder checks compared",
                             "explanation": "Shows net file changes between checks. The checks cannot identify who acted."}


def build_timeline(*, demo, fixtures, tools, audit_log, checkpoint, agent_log, watch_dirs, watch_git,
                   previous_snapshots, snapshot_history):
    records, runs, cases, sources = [], [], [], []
    registered = {tool["id"]: tool["id"] for tool in tools.values() if tool["id"]}
    streams = []
    if demo:
        registered.update({tool["metadata"]["source"]: tool["id"]
                           for tool in tools.values() if tool["id"]})
        fixture = fixtures / "timeline.jsonl"
        streams.append(("Fixture observer", "demo-observer", lambda: bounded_read(fixture), None, False))
        reported = fixtures / "timeline-reported.jsonl"
        streams.append(("Fixture agent report", "demo-report", lambda: bounded_read(reported), None, True))
        clean = (b'{"id":"saved","time":"2026-11-20T17:42:01Z","kind":"file_write",'
                 b'"path":"notes.txt"}\n')
        changed = clean.replace(b"notes.txt", b"other.txt")
        streams.append(("Fixture changed log", "demo-tampered", lambda: changed,
                        checkpoint_bytes(clean), True))
    if audit_log:
        streams.append(("Configured audit log", stable_key("observer", str(audit_log.absolute())),
                        lambda: bounded_read(audit_log), checkpoint, False))
    if agent_log:
        streams.append(("Agent report", stable_key("self-report", str(agent_log.absolute())),
                        lambda: bounded_read(agent_log), None, True))

    for name, namespace, reader, pin, self_reported in streams:
        found, run_list, flags, source = collect_stream(name, namespace, reader, pin, self_reported, registered)
        records.extend(found)
        runs.extend(run_list)
        cases.extend(flags)
        sources.append(source)

    snapshots = dict(previous_snapshots)
    history = list(snapshot_history)
    folders = [("Watched folder", root, snapshot) for root in watch_dirs]
    folders.extend(("Watched Git checkout", root, git_snapshot) for root in watch_git)
    for index, (label, root, scan) in enumerate(folders, 1):
        namespace = stable_key(label, str(root.absolute()))
        name = f"{label} {index}"
        current, changes, source = compare_snapshot(root, name, namespace, snapshots.get(namespace), scan)
        if current is not None:
            snapshots[namespace] = current
        history.extend(changes)
        sources.append(source)
        if source["integrity"] == "Folder check incomplete":
            cases.append(review_case(namespace + "-folder-incomplete", "A watched folder could not be checked",
                                     "Needs review", name + ". " + source["explanation"]))
    history = sorted(history, key=lambda item: (item["time"], item["id"]))[-MAX_RECORDS:]
    records.extend(history)
    records.sort(key=lambda item: (item["time"], item["id"]), reverse=True)
    if len(records) > MAX_RECORDS:
        cases.append(review_case("preview-limit", "Some older records are outside this view", "Display limit",
                                 f"The timeline shows the newest {MAX_RECORDS} of {len(records)} records."))
        records = records[:MAX_RECORDS]

    runs_by_id = {run["id"]: run for run in runs}
    delegated = [run for run in runs if run["parent_id"] and run["tool_id"]
                 and runs_by_id[run["parent_id"]]["tool_id"] == run["tool_id"]]
    if delegated:
        children = {run["id"] for run in delegated}
        cases.append(review_case("delegation", "One tool was used by a parent and a helper run", "Kept separate",
                                 "The source reports distinct runs and a parent link. They share the same registered tool.",
                                 [item["id"] for item in records if item["run_id"] in children]))

    unknown = [item["id"] for item in records if item["run_id"] is None]
    if unknown:
        cases.append(review_case("unknown", "Some file changes have no identified run", "Unknown",
                                 "Similar names, folders, and timing alone cannot identify who made a change.", unknown))

    by_path = defaultdict(set)
    for item in records:
        if item["path"] and item["run_id"]:
            by_path[(item["source"], item.get("deployment"), item["path"])].add(item["run_id"])
    shared = {key for key, run_ids in by_path.items() if len(run_ids) > 1}
    if shared:
        cases.append(review_case("shared-file", "Different runs touched the same file", "Kept separate",
                                 "The log names different runs for that path. The shared file does not merge them.",
                                 [item["id"] for item in records
                                  if (item["source"], item.get("deployment"), item["path"]) in shared]))

    run_claims = defaultdict(list)
    for run in runs:
        run_claims[run["reported_id"]].append(run["id"])
    reused = {run_id for ids in run_claims.values() if len(ids) > 1 for run_id in ids}
    if reused:
        cases.append(review_case("cross-source-id", "Two sources used the same run reference", "Unknown",
                                 "Sources can reuse the same reference. More evidence is needed to link their accounts.",
                                 [item["id"] for item in records if item["run_id"] in reused]))

    relevant_runs = {item["run_id"] for item in records if item["run_id"]}
    relevant_runs.update(item["initiator_run_id"] for item in records if item.get("initiator_run_id"))
    for run_id in list(relevant_runs):
        parent = runs_by_id[run_id]["parent_id"]
        if parent:
            relevant_runs.add(parent)
    agents = {}
    for run in sorted(runs, key=lambda item: item["started_at"]):
        if run["agent_id"]:
            agents[run["agent_id"]] = {"id": run["agent_id"], "label": run["label"],
                                       "source": run["source"], "last_seen": run["started_at"]}
    runs = [run for run in runs if run["id"] in relevant_runs]
    return {"records": records, "runs": runs, "cases": cases, "sources": sources,
            "agents": list(agents.values())}, snapshots, history


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print a SHA-256 checkpoint for a JSONL audit log.")
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    try:
        data = bounded_read(args.log)
        if data and not data.endswith(b"\n"):
            parser.error("The log must end on a complete newline.")
        print(checkpoint_bytes(data))
    except (OSError, ValueError) as error:
        parser.error(str(error))
