"""
Activity timeline (merge M4)
============================

Turns an observer's activity log (JSON Lines) into runs, agents, delegation
chains and file actions, and raises integrity findings through the same
policy-as-code file as every other rule (``applies_to: "activity"``).

The evidence model is ported from UdanM/forkit-agent-audit @ 3f5b2aa
(``timeline.py`` and ``docs/scaling.md``), re-implemented on AutoDiscover's
guardrails:

- **Identity needs observer evidence.** An agent ID exists only when a
  ``run_started`` row from the observer carries ``deployment``, ``agent_id`` and
  ``instance_id``. A name, a path or an agent's own report never creates one.
- **Unknown stays UNKNOWN.** An action whose run is missing or unobserved has
  actor ``UNKNOWN``; it is never assigned by similarity.
- **Self-reports are evidence, not truth.** Rows an agent reports about itself
  are compared with the observer and flagged when they disagree.
- **Rules are data.** Each check below is a fixed, allowlisted detector; whether
  it raises a finding, its severity and wording live in ``fixtures/policies.json``.
- **Tamper evidence.** A log checkpoint ``LENGTH:SHA256`` over a saved prefix
  proves later reads still start with the same bytes.
- **Metadata-only.** Diffs and file contents are never kept; paths must be
  relative to the workspace.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

UNKNOWN = "UNKNOWN"
KINDS = ("run_started", "file_write", "tool_call")
OPERATIONS = ("create", "write", "rename", "delete")
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10_000
CONFIG_FILE_NAMES = {"mcp.json", ".mcp.json", "mcp_config.json", "claude_desktop_config.json", ".claude.json",
                     "config.toml"}

# Detectors available to activity rules. Each is fixed code; rules pick them by name.
ACTIVITY_CHECKS = {
    "sequence_gap": "Observer numbering has a gap or a repeat",
    "clock_drift": "Event time and observer time disagree",
    "protected_write": "A file marked protected was changed",
    "approval_missing": "A file change has missing or denied approval",
    "read_only_write": "A run declared read-only changed a file",
    "config_file_write": "A tool configuration file was changed",
    "unknown_actor": "A file change has no observed run",
    "access_escalation": "The same agent went from read-only to write access",
    "identity_relabel": "The same agent identity changed its name or location",
    "onboarding_missing": "Activity from an agent with no confirmed onboarding",
    "self_report_mismatch": "An agent's own report disagrees with the observer",
    "run_reuse": "One run reference was started twice",
    "log_rewritten": "The log no longer matches its saved checkpoint",
}
DEFAULT_MAX_SKEW_SECONDS = 300

_ID = re.compile(r"^[\w.:@/-]{1,128}$")


# ── Parsing ────────────────────────────────────────────────────────────────────

def _text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("expected text")
    s = str(value)
    if not s or len(s) > limit or any(ord(c) < 32 for c in s):
        raise ValueError("text is empty, too long or has control characters")
    return s


def _ident(value: Any) -> str | None:
    s = _text(value, 128)
    if s is not None and not _ID.match(s):
        raise ValueError("identifier has unexpected characters")
    return s


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("time must be an ISO-8601 string")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("time must carry a timezone")
    return t


def _workspace_path(value: Any) -> str:
    s = _text(value, 260)
    if s is None:
        raise ValueError("path is required")
    p = PurePosixPath(s.replace("\\", "/"))
    if p.is_absolute() or re.match(r"^[A-Za-z]:", s) or ".." in p.parts:
        raise ValueError("path must be relative to the workspace")
    return p.as_posix()


def parse_row(raw: Any) -> dict[str, Any]:
    """Validate one activity row. Raises ValueError with a short reason."""
    if not isinstance(raw, dict):
        raise ValueError("row is not an object")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    row: dict[str, Any] = {
        "id": _ident(raw.get("id")),
        "kind": kind,
        "time": _time(raw.get("time")),
        "run_id": _ident(raw.get("run_id")),
        "deployment": _ident(raw.get("deployment")),
    }
    if row["id"] is None:
        raise ValueError("id is required")
    if "sequence" in raw:
        if type(raw["sequence"]) is not int or raw["sequence"] < 0:
            raise ValueError("sequence must be a non-negative integer")
        row["sequence"] = raw["sequence"]
    if raw.get("observed_at") is not None:
        row["observed_at"] = _time(raw["observed_at"])
    for key in ("approval", "protected", "onboarded"):
        if key in raw:
            if raw[key] is not None and not isinstance(raw[key], bool):
                raise ValueError(f"{key} must be true, false or null")
            row[key] = raw[key]
    if kind == "run_started":
        if row["run_id"] is None:
            raise ValueError("run_started needs run_id")
        for key in ("agent_id", "instance_id", "parent_id"):
            row[key] = _ident(raw.get(key))
        for key in ("label", "location", "tool"):
            row[key] = _text(raw.get(key))
        access = raw.get("access")
        if access not in (None, "read_only", "write"):
            raise ValueError("access must be read_only or write")
        row["access"] = access
        if raw.get("pid") is not None:
            if type(raw["pid"]) is not int:
                raise ValueError("pid must be an integer")
            row["pid"] = raw["pid"]
    elif kind == "file_write":
        row["path"] = _workspace_path(raw.get("path"))
        op = raw.get("operation", "write")
        if op not in OPERATIONS:
            raise ValueError(f"operation must be one of {OPERATIONS}")
        row["operation"] = op
        row["initiator_run_id"] = _ident(raw.get("initiator_run_id"))
        # Metadata only: a diff is reduced to its size and hash, never stored.
        patch = raw.get("patch")
        if isinstance(patch, str):
            row["patch_sha256"] = hashlib.sha256(patch.encode("utf-8")).hexdigest()
            row["patch_bytes"] = len(patch.encode("utf-8"))
    else:  # tool_call
        row["tool_call"] = _text(raw.get("tool_call"))
        row["target"] = _text(raw.get("target"), 260)
    return row


def parse_log(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse JSON Lines bytes. Returns (rows, rejected). Never raises for bad lines.

    An incomplete last line (no trailing newline) is left for the next read.
    """
    if len(data) > MAX_LOG_BYTES:
        data = data[:MAX_LOG_BYTES]
    complete = data if data.endswith(b"\n") else data[: data.rfind(b"\n") + 1]
    rows, rejected, seen = [], [], set()
    for n, line in enumerate(complete.splitlines(), 1):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS:
            rejected.append({"line": n, "reason": f"more than {MAX_ROWS} rows"})
            break
        try:
            row = parse_row(json.loads(line))
        except (ValueError, TypeError) as exc:  # json.JSONDecodeError is a ValueError
            rejected.append({"line": n, "reason": str(exc).split("\n")[0][:120]})
            continue
        if row["id"] in seen:
            rejected.append({"line": n, "reason": "duplicate id"})
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows, rejected


# ── Checkpoints ────────────────────────────────────────────────────────────────

def make_checkpoint(data: bytes) -> str:
    """``LENGTH:SHA256`` over the complete lines of a log."""
    complete = data if data.endswith(b"\n") else data[: data.rfind(b"\n") + 1]
    return f"{len(complete)}:{hashlib.sha256(complete).hexdigest()}"


def verify_checkpoint(data: bytes, checkpoint: str | None) -> str:
    """``none``, ``intact``, ``truncated``, ``rewritten`` or ``invalid``."""
    if not checkpoint:
        return "none"
    m = re.fullmatch(r"([0-9]{1,10}):([0-9a-f]{64})", checkpoint)
    if not m:
        return "invalid"
    length, digest = int(m.group(1)), m.group(2)
    if len(data) < length:
        return "truncated"
    return "intact" if hashlib.sha256(data[:length]).hexdigest() == digest else "rewritten"


# ── Building the timeline ──────────────────────────────────────────────────────

def agent_identity(row: dict[str, Any], namespace: str) -> str:
    parts = [row.get("deployment"), row.get("agent_id"), row.get("instance_id")]
    if not all(parts):
        return UNKNOWN
    return "agent_" + hashlib.sha256(json.dumps([namespace, *parts]).encode()).hexdigest()[:24]


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def build_timeline(
    observer_rows: list[dict[str, Any]],
    reported_rows: list[dict[str, Any]] | None = None,
    *,
    namespace: str = "local",
    checkpoint_status: str = "none",
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> dict[str, Any]:
    """Pure: runs, agents, actions and raw check signals. Writes nothing."""
    signals: dict[str, list[str]] = defaultdict(list)

    def flag(check: str, record_id: str) -> None:
        if record_id not in signals[check]:
            signals[check].append(record_id)

    rows = sorted(observer_rows, key=lambda r: (r["time"], r.get("sequence", 0), r["id"]))
    runs: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "run_started":
            continue
        if r["run_id"] in runs:
            flag("run_reuse", r["id"])
            runs[r["run_id"]]["relationship"] = UNKNOWN
            continue
        runs[r["run_id"]] = {
            "run_id": r["run_id"], "record_id": r["id"], "started_at": _iso(r["time"]),
            "label": r.get("label") or UNKNOWN, "tool": r.get("tool") or UNKNOWN,
            "location": r.get("location") or UNKNOWN, "access": r.get("access") or UNKNOWN,
            "deployment": r.get("deployment") or UNKNOWN,
            "agent": agent_identity(r, namespace), "parent_run_id": r.get("parent_id"),
            "pid": r.get("pid"), "onboarded": r.get("onboarded", UNKNOWN), "relationship": "observed",
        }
    for run in runs.values():
        parent = run["parent_run_id"]
        run["delegated_by"] = (parent if parent in runs else UNKNOWN) if parent else None

    # Agents: one per observer identity, with what changed across its runs.
    agents: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "run_started" or runs.get(r["run_id"], {}).get("record_id") != r["id"]:
            continue
        aid = runs[r["run_id"]]["agent"]
        if aid == UNKNOWN:
            continue
        a = agents.setdefault(aid, {"agent": aid, "agent_id": r["agent_id"], "instance_id": r["instance_id"],
                                    "deployment": r["deployment"], "runs": [], "labels": [], "locations": [],
                                    "access": [], "first_seen": _iso(r["time"])})
        if a["labels"] and r.get("label") and r["label"] != a["labels"][-1]:
            flag("identity_relabel", r["id"])
        if a["locations"] and r.get("location") and r["location"] != a["locations"][-1]:
            flag("identity_relabel", r["id"])
        if "read_only" in a["access"] and r.get("access") == "write":
            flag("access_escalation", r["id"])
        a["runs"].append(r["run_id"])
        for key, field in (("labels", "label"), ("locations", "location"), ("access", "access")):
            if r.get(field) and (not a[key] or a[key][-1] != r[field]):
                a[key].append(r[field])
        a["last_seen"] = _iso(r["time"])

    actions: list[dict[str, Any]] = []
    for r in rows:
        if r.get("observed_at") and abs((r["time"] - r["observed_at"]).total_seconds()) > max_skew_seconds:
            flag("clock_drift", r["id"])
        if r.get("onboarded") is False:
            flag("onboarding_missing", r["id"])
        if r["kind"] == "run_started":
            continue
        run = runs.get(r["run_id"]) if r["run_id"] else None
        if run is None:
            actor_reason = "no run reference" if not r["run_id"] else "run was never observed starting"
        else:
            actor_reason = "observed run"
        act = {
            "record_id": r["id"], "time": _iso(r["time"]), "kind": r["kind"],
            "run_id": r["run_id"] if run else None,
            "agent": run["agent"] if run else UNKNOWN,
            "actor": run["label"] if run else UNKNOWN,
            "actor_reason": actor_reason,
            "initiated_by": None,
        }
        if r["kind"] == "file_write":
            act.update(path=r["path"], operation=r["operation"],
                       approval=r.get("approval", UNKNOWN), protected=r.get("protected", UNKNOWN))
            if r.get("patch_sha256"):
                act.update(patch_sha256=r["patch_sha256"], patch_bytes=r["patch_bytes"])
            init = r.get("initiator_run_id")
            if init:
                act["initiated_by"] = init if init in runs else UNKNOWN
            if run is None:
                flag("unknown_actor", r["id"])
            if r.get("protected") is True:
                flag("protected_write", r["id"])
            if "approval" in r and r["approval"] is not True:
                flag("approval_missing", r["id"])
            if run and run["access"] == "read_only":
                flag("read_only_write", r["id"])
            if PurePosixPath(r["path"]).name in CONFIG_FILE_NAMES:
                flag("config_file_write", r["id"])
        else:
            act.update(tool_call=r.get("tool_call") or UNKNOWN, target=r.get("target") or UNKNOWN)
        actions.append(act)

    # Sequence gaps / repeats per deployment.
    by_dep: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        if "sequence" in r:
            by_dep[r.get("deployment") or UNKNOWN].append((r["sequence"], r["id"]))
    for series in by_dep.values():
        series.sort()
        for (a_seq, _), (b_seq, b_id) in zip(series, series[1:], strict=False):
            if b_seq != a_seq + 1:
                flag("sequence_gap", b_id)

    # Self-reports: compared, never trusted.
    reported = []
    for r in sorted(reported_rows or [], key=lambda x: (x["time"], x["id"])):
        run = runs.get(r["run_id"]) if r["run_id"] else None
        verdict = "confirmed"
        if run is None:
            verdict = "not observed"
        elif r["kind"] == "run_started" and r.get("label") and r["label"] != run["label"]:
            verdict = "disagrees with observer"
        elif r["kind"] == "file_write" and not any(
            a["run_id"] == r["run_id"] and a.get("path") == r.get("path") for a in actions
        ):
            verdict = "not observed"
        if verdict != "confirmed":
            flag("self_report_mismatch", r["id"])
        reported.append({"record_id": r["id"], "time": _iso(r["time"]), "kind": r["kind"],
                         "run_id": r["run_id"], "path": r.get("path"), "label": r.get("label"),
                         "verdict": verdict})

    if checkpoint_status in ("rewritten", "truncated"):
        flag("log_rewritten", f"checkpoint:{checkpoint_status}")

    files: dict[str, set[str]] = defaultdict(set)
    for a in actions:
        if a.get("path"):
            files[a["path"]].add(a["actor"])
    shared = {p: sorted(who) for p, who in sorted(files.items()) if len(who) > 1}

    return {
        "namespace": namespace,
        "runs": sorted(runs.values(), key=lambda x: x["started_at"]),
        "agents": sorted(agents.values(), key=lambda x: x["first_seen"]),
        "actions": actions,
        "self_reported": reported,
        "shared_files": shared,
        "signals": {k: v for k, v in sorted(signals.items())},
        "checkpoint": checkpoint_status,
    }


def evaluate_activity(rules: list[dict[str, Any]], timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings for ``applies_to: activity`` rules. One finding per rule that fires."""
    out = []
    for rule in rules:
        if rule.get("applies_to") != "activity":
            continue
        check = rule["when"]["check"]
        ids = timeline["signals"].get(check, [])
        if not ids:
            continue
        out.append({
            "finding_id": f"{rule['rule_id']}@{timeline['namespace']}",
            "rule_id": rule["rule_id"], "title": rule["title"], "severity": rule["severity"],
            "subject_type": "activity", "check": check, "record_ids": ids,
            "explanation": rule["explanation"], "status": "NEEDS_REVIEW",
            "evidence": {"records": len(ids), "check": ACTIVITY_CHECKS[check]},
        })
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(out, key=lambda f: (rank.get(f["severity"], 9), f["rule_id"]))


def read_log(path: str | Path) -> bytes:
    """Bounded read of a log file (first 4 MiB). Missing file -> empty."""
    p = Path(path)
    if not p.is_file():
        return b""
    with p.open("rb") as fh:
        return fh.read(MAX_LOG_BYTES)


def timeline_from_files(
    observer_log: str | Path,
    reported_log: str | Path | None = None,
    *,
    rules: list[dict[str, Any]] | None = None,
    checkpoint: str | None = None,
    namespace: str = "local",
) -> dict[str, Any]:
    """Read logs (never writes to them), build the timeline and its findings."""
    data = read_log(observer_log)
    rows, rejected = parse_log(data)
    rep_rows, rep_rejected = parse_log(read_log(reported_log)) if reported_log else ([], [])
    skew = DEFAULT_MAX_SKEW_SECONDS
    for r in rules or []:
        if r.get("applies_to") == "activity" and r["when"].get("check") == "clock_drift":
            skew = int(r["when"].get("max_skew_seconds", skew))
    status = verify_checkpoint(data, checkpoint)
    tl = build_timeline(rows, rep_rows, namespace=namespace, checkpoint_status=status, max_skew_seconds=skew)
    tl["rejected"] = {"observer": rejected, "self_reported": rep_rejected}
    tl["checkpoint_now"] = make_checkpoint(data) if data else None
    tl["findings"] = evaluate_activity(rules or [], tl)
    return tl
