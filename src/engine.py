"""
AutoDiscover engine
===================

Zero-touch discovery of local MCP servers into a metadata-only passport
inventory, built on Forkit Core's identity primitives.

Guardrails enforced here
------------------------
1. Honesty: fields we cannot prove (owner, version, access mode, client) are
   stored as the literal string "UNKNOWN". Nothing is guessed.
2. Metadata-only: every env value, header value, URL password and secret-looking
   argument is replaced with "[REDACTED]" before anything is hashed or stored.
3. No false merges: a passport's identity includes the config file it came from
   and the command it runs, so two servers that share a name stay separate.
4. Local-first: confirmed passports live in ``data/passports.json``. Event links,
   incidents and metadata changes are separate JSON records joined by
   ``passport_id``. A confirmed passport is never rewritten.

Identity
--------
    provenance_hash = HashEngine.hash_dict({"source_path", "tool_name", "command"})
    passport_id     = forkit.domain.identity.compute_id(
                          passport_type="mcp_server", name=tool_name,
                          version="UNKNOWN", creator_name="UNKNOWN",
                          creator_org=None, artifact_hash=provenance_hash)

``compute_id`` is called directly (not through ``AgentPassport``) because the
schema's ``validate_version`` rejects "UNKNOWN". Arguments and env keys are
deliberately *not* identity inputs. They feed ``config_hash``, so editing a
server's arguments shows up as a metadata change on the same passport and does
not create a new tool.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from forkit.domain.hashing import HashEngine
from forkit.domain.identity import compute_id

__all__ = [
    "UNKNOWN",
    "REDACTED",
    "PASSPORT_TYPE",
    "APPROVED",
    "MISSING_APPROVAL",
    "INCIDENT",
    "discover_tools",
    "derive_passport_id",
    "AutoDiscoverEngine",
]

UNKNOWN = "UNKNOWN"
REDACTED = "[REDACTED]"
PASSPORT_TYPE = "mcp_server"
APPROVED = "APPROVED"
MISSING_APPROVAL = "MISSING_APPROVAL"
INCIDENT = "INCIDENT"
READ_ONLY = "read-only"

# Known config file names → client. Anything else is UNKNOWN.
_KNOWN_CLIENTS: dict[str, str] = {
    "claude_desktop_config.json": "claude-desktop",
    "cursor_mcp.json": "cursor",
}

# ── Redaction ──────────────────────────────────────────────────────────────────

_SECRET_NAME = re.compile(
    r"(token|secret|passw(or)?d|pwd|api[_-]?key|apikey|auth|credential|private[_-]?key|"
    r"access[_-]?key|session|cookie|bearer|_url$|^url$|dsn|conn(ection)?[_-]?str)",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@\s]+)@")
_KNOWN_TOKEN = re.compile(r"^(sk-|sk_live_|ghp_|gho_|github_pat_|xox[abp]-|AKIA|AIza|glpat-)")
_SECRET_QUERY = re.compile(r"([?&](?:token|key|api_key|apikey|password|secret|sig)=)[^&\s]+", re.IGNORECASE)


def _redact_string(value: str) -> str:
    """Strip credentials embedded in a single string (URLs, known token shapes)."""
    if _KNOWN_TOKEN.match(value):
        return REDACTED
    value = _URL_USERINFO.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", value)
    return _SECRET_QUERY.sub(lambda m: f"{m.group(1)}{REDACTED}", value)


def _redact_args(args: list[Any]) -> list[str]:
    """Redact secret-bearing CLI arguments while keeping their structure."""
    out: list[str] = []
    redact_next = False
    for raw in args:
        arg = str(raw)
        if redact_next:
            out.append(REDACTED)
            redact_next = False
            continue
        if arg.startswith("-") and "=" in arg:
            flag, _, val = arg.partition("=")
            out.append(f"{flag}={REDACTED}" if _SECRET_NAME.search(flag) else f"{flag}={_redact_string(val)}")
            continue
        if arg.startswith("-") and _SECRET_NAME.search(arg):
            out.append(arg)
            redact_next = True
            continue
        # KEY=value pairs, e.g. `-e API_KEY=...` for docker
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", arg)
        if m and _SECRET_NAME.search(m.group(1)):
            out.append(f"{m.group(1)}={REDACTED}")
            continue
        out.append(_redact_string(arg))
    return out


def _redact_mapping(mapping: dict[str, Any] | None) -> dict[str, str]:
    """Keep the keys of env/headers, never the values."""
    return {str(k): REDACTED for k in sorted((mapping or {}).keys())}


# ── Behaviour evidence ─────────────────────────────────────────────────────────

_RO_MOUNT = re.compile(r"(^|,)(ro|readonly)(=true)?(,|$)", re.IGNORECASE)
_RO_FLAGS = {"--read-only", "--readonly", "--ro"}
_MUTATING_VERBS = ("write", "delete", "remove", "move", "rename", "create", "edit", "update", "insert", "drop", "truncate", "alter")


def _access_mode(args: list[str]) -> str:
    """Return "read-only" only when the config proves it, else UNKNOWN.

    Evidence: a bind/volume mount flagged ``ro``/``readonly``, or an explicit
    read-only flag. Absence of evidence is UNKNOWN, never "read-write".
    """
    for a in args:
        if a.lower() in _RO_FLAGS:
            return READ_ONLY
        if ("type=bind" in a or "type=volume" in a or "src=" in a) and _RO_MOUNT.search(a):
            return READ_ONLY
        if re.match(r"^[^:]+:[^:]+:ro$", a):  # docker -v host:container:ro
            return READ_ONLY
    return UNKNOWN


def _is_mutating(tool_call: str) -> bool:
    action = tool_call.rsplit(".", 1)[-1].lower()
    return action.startswith(_MUTATING_VERBS)


# ── Discovery (pure, no I/O besides reading the config) ────────────────────────

def _source_path(config_path: Path, base_dir: Path) -> str:
    """Stable POSIX path relative to base_dir, so IDs match across machines."""
    resolved = config_path.resolve()
    try:
        rel = resolved.relative_to(base_dir.resolve())
    except ValueError:
        rel = resolved
    return str(PurePosixPath(*rel.parts)) if not rel.is_absolute() else resolved.as_posix()


def _provenance_hash(source_path: str, tool_name: str, command: str) -> str:
    return HashEngine.hash_dict({"source_path": source_path, "tool_name": tool_name, "command": command})


def derive_passport_id(source_path: str, tool_name: str, command: str) -> str:
    """Deterministic passport ID from provenance. Same inputs → same ID, always."""
    return compute_id(
        passport_type=PASSPORT_TYPE,
        name=tool_name,
        version=UNKNOWN,
        creator_name=UNKNOWN,
        creator_org=None,
        artifact_hash=_provenance_hash(source_path, tool_name, command),
    )


def discover_tools(config_paths: list[str | Path], base_dir: str | Path = ".") -> list[dict[str, Any]]:
    """Parse MCP config files into redacted, metadata-only candidate records."""
    base = Path(base_dir)
    candidates: list[dict[str, Any]] = []
    for cfg in config_paths:
        cfg_path = Path(cfg) if Path(cfg).is_absolute() else base / cfg
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        source_path = _source_path(cfg_path, base)
        client = _KNOWN_CLIENTS.get(cfg_path.name, UNKNOWN)
        for tool_name, spec in sorted((data.get("mcpServers") or {}).items()):
            spec = spec or {}
            command = str(spec.get("command") or UNKNOWN)
            args = _redact_args(list(spec.get("args") or []))
            env = _redact_mapping(spec.get("env"))
            record: dict[str, Any] = {
                "passport_id": derive_passport_id(source_path, tool_name, command),
                "passport_type": PASSPORT_TYPE,
                "tool_name": tool_name,
                "source_path": source_path,
                "client": client,
                "command": command,
                "args": args,
                "env": env,
                "owner": str(spec["owner"]) if spec.get("owner") else UNKNOWN,
                "version": str(spec["version"]) if spec.get("version") else UNKNOWN,
                "access_mode": _access_mode(args),
            }
            if spec.get("url"):
                record["url"] = _redact_string(str(spec["url"]))
            if spec.get("headers"):
                record["headers"] = _redact_mapping(spec["headers"])
            record["provenance_hash"] = _provenance_hash(source_path, tool_name, command)
            record["config_hash"] = HashEngine.hash_config(
                {k: record.get(k) for k in ("command", "args", "env", "url", "headers")}
            )
            candidates.append(record)
    return candidates


# ── Human-readable diff ────────────────────────────────────────────────────────

def _diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("command", "url", "access_mode"):
        if old.get(key) != new.get(key):
            lines.append(f"{key}: {old.get(key, UNKNOWN)!r} -> {new.get(key, UNKNOWN)!r}")
    a, b = old.get("args", []), new.get("args", [])
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                o = a[i1 + k] if i1 + k < i2 else None
                n = b[j1 + k] if j1 + k < j2 else None
                if o is None:
                    lines.append(f"args[{j1 + k}] added: {n!r}")
                elif n is None:
                    lines.append(f"args[{i1 + k}] removed: {o!r}")
                else:
                    lines.append(f"args[{i1 + k}] changed: {o!r} -> {n!r}")
        elif tag == "delete":
            lines.extend(f"args[{i}] removed: {a[i]!r}" for i in range(i1, i2))
        elif tag == "insert":
            lines.extend(f"args[{j}] added: {b[j]!r}" for j in range(j1, j2))
    for key in ("env", "headers"):
        ok, nk = set(old.get(key) or {}), set(new.get(key) or {})
        lines.extend(f"{key} key added: {k!r}" for k in sorted(nk - ok))
        lines.extend(f"{key} key removed: {k!r}" for k in sorted(ok - nk))
    return lines


# ── Storage helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    """Atomic write so a crash never leaves a half-written source of truth."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# ── Engine ─────────────────────────────────────────────────────────────────────

class AutoDiscoverEngine:
    """Local-first passport inventory with sidecar link/incident/change records.

    Files under ``data_dir``:
      passports.json         {passport_id: passport}   source of truth
      linked_events.json     [link]                    consented event links
      incidents.json         [incident]                out-of-expected-behaviour
      metadata_changes.json  [change]                  config drift on known tools
    """

    def __init__(
        self,
        data_dir: str | Path = "data",
        base_dir: str | Path = ".",
        approvals_path: str | Path = "fixtures/approvals.json",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.data_dir = Path(data_dir)
        self.approvals_path = Path(approvals_path)
        self.passports_path = self.data_dir / "passports.json"
        self.links_path = self.data_dir / "linked_events.json"
        self.incidents_path = self.data_dir / "incidents.json"
        self.changes_path = self.data_dir / "metadata_changes.json"

    # ── Reads ──────────────────────────────────────────────────────────────────

    def passports(self) -> dict[str, dict[str, Any]]:
        return _read_json(self.passports_path, {})

    def linked_events(self) -> list[dict[str, Any]]:
        return _read_json(self.links_path, [])

    def incidents(self) -> list[dict[str, Any]]:
        return _read_json(self.incidents_path, [])

    def metadata_changes(self) -> list[dict[str, Any]]:
        return _read_json(self.changes_path, [])

    def approvals(self) -> list[dict[str, Any]]:
        return _read_json(self.approvals_path, {"approvals": []}).get("approvals", [])

    # ── 1. Discovery ───────────────────────────────────────────────────────────

    def discover_tools(self, config_paths: list[str | Path]) -> list[dict[str, Any]]:
        """Scan configs and annotate each candidate against the inventory.

        Adds ``known`` (already a confirmed passport) and ``metadata_changed``
        with a human-readable ``diff`` when a known tool's config_hash moved.
        Drift is appended to metadata_changes.json; the passport is untouched.
        """
        stored = self.passports()
        changes = self.metadata_changes()
        seen = {(c["passport_id"], c["new_config_hash"]) for c in changes}
        out: list[dict[str, Any]] = []
        for cand in discover_tools(config_paths, self.base_dir):
            existing = stored.get(cand["passport_id"])
            cand["known"] = existing is not None
            cand["metadata_changed"] = bool(existing and existing["config_hash"] != cand["config_hash"])
            cand["diff"] = _diff(existing, cand) if cand["metadata_changed"] else []
            if cand["metadata_changed"] and (cand["passport_id"], cand["config_hash"]) not in seen:
                changes.append({
                    "passport_id": cand["passport_id"],
                    "tool_name": cand["tool_name"],
                    "source_path": cand["source_path"],
                    "old_config_hash": existing["config_hash"],
                    "new_config_hash": cand["config_hash"],
                    "diff": cand["diff"],
                    "detected_at": _now(),
                    "status": "PENDING_REVIEW",
                })
                seen.add((cand["passport_id"], cand["config_hash"]))
                _write_json(self.changes_path, changes)
            out.append(cand)
        return out

    # ── 2. Minting ─────────────────────────────────────────────────────────────

    def mint_passport(self, candidate: dict[str, Any]) -> str:
        """Confirm a candidate (the single-click action). Idempotent.

        Re-derives the ID from provenance rather than trusting the candidate,
        and never overwrites an existing confirmed passport.
        """
        pid = derive_passport_id(candidate["source_path"], candidate["tool_name"], candidate["command"])
        if pid != candidate.get("passport_id"):
            raise ValueError("candidate passport_id does not match its provenance")
        stored = self.passports()
        if pid in stored:
            return pid
        record = {k: v for k, v in candidate.items() if k not in {"known", "metadata_changed", "diff"}}
        record["status"] = "CONFIRMED"
        record["confirmed_at"] = _now()
        stored[pid] = record
        _write_json(self.passports_path, stored)
        return pid

    # ── 3. Consented event linking ─────────────────────────────────────────────

    def _approval_for(self, passport_id: str, tool_call: str) -> dict[str, Any] | None:
        for a in self.approvals():
            if (
                a.get("passport_id") == passport_id
                and str(a.get("status", "")).lower() == "approved"
                and (tool_call in a.get("approved_actions", []) or "*" in a.get("approved_actions", []))
            ):
                return a
        return None

    def link_event(self, event: dict[str, Any], passport_id: str, user_consent: bool) -> dict[str, Any]:
        """Link a runtime event to a confirmed passport, with explicit consent.

        Raises PermissionError without consent, KeyError for an unknown
        passport, and ValueError if the event's source names another tool.
        Writes only to linked_events.json / incidents.json.
        """
        if user_consent is not True:
            raise PermissionError("Event linking requires explicit user_consent=True")
        passport = self.passports().get(passport_id)
        if passport is None:
            raise KeyError(f"No confirmed passport with id {passport_id}")

        source = event.get("source") or {}
        claimed = (source.get("server_name"), source.get("config_path"))
        if claimed[0] is not None and claimed[0] != passport["tool_name"]:
            raise ValueError(f"Event is from {claimed[0]!r}, passport is {passport['tool_name']!r}")
        if claimed[1] is not None and claimed[1] != passport["source_path"]:
            raise ValueError(f"Event is from {claimed[1]!r}, passport is {passport['source_path']!r}")

        event_id = event.get("event_id") or UNKNOWN
        links = self.linked_events()
        for existing in links:
            if existing["event_id"] == event_id and existing["passport_id"] == passport_id and event_id != UNKNOWN:
                return existing

        tool_call = event.get("tool_call") or UNKNOWN
        approval = self._approval_for(passport_id, tool_call)
        runtime_consent = event.get("runtime_consent") or {}
        flags: list[str] = [] if approval else [MISSING_APPROVAL]

        incident = None
        if tool_call != UNKNOWN and _is_mutating(tool_call) and passport.get("access_mode") == READ_ONLY:
            flags.append(INCIDENT)
            incident = {
                "incident_id": "inc-" + HashEngine.hash_dict({"e": event_id, "p": passport_id})[:12],
                "type": "OUT_OF_EXPECTED_BEHAVIOR",
                "passport_id": passport_id,
                "tool_name": passport["tool_name"],
                "event_id": event_id,
                "timestamp": event.get("timestamp") or UNKNOWN,
                "source": source or UNKNOWN,
                "tool_call": tool_call,
                "target": event.get("target") or UNKNOWN,
                "result": event.get("result") or UNKNOWN,
                "expected_behavior": f"{READ_ONLY} (evidence in passport args)",
                "approval_status": APPROVED if approval else MISSING_APPROVAL,
                "detected_at": _now(),
            }

        link = {
            "link_id": "lnk-" + HashEngine.hash_dict({"e": event_id, "p": passport_id})[:12],
            "event_id": event_id,
            "passport_id": passport_id,
            "source": source or UNKNOWN,
            "timestamp": event.get("timestamp") or UNKNOWN,
            "tool_call": tool_call,
            "target": event.get("target") or UNKNOWN,
            "result": event.get("result") or UNKNOWN,
            "approval_status": APPROVED if approval else MISSING_APPROVAL,
            "approval_id": approval["approval_id"] if approval else UNKNOWN,
            "event_claimed_approval_id": event.get("approval_id") or UNKNOWN,
            "runtime_consent_id": runtime_consent.get("consent_id") or UNKNOWN,
            "link_consent": True,
            "linked_at": _now(),
            "flags": flags,
            "incident_id": incident["incident_id"] if incident else None,
        }
        links.append(link)
        _write_json(self.links_path, links)
        if incident:
            incidents = self.incidents()
            if all(i["incident_id"] != incident["incident_id"] for i in incidents):
                incidents.append(incident)
                _write_json(self.incidents_path, incidents)
        return link
