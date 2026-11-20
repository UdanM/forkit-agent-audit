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
import sys
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
    "known_config_locations",
    "scan_local_machine",
    "load_policies",
    "category_evidence",
    "evaluate_policies",
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


def _candidates_from_config(
    data: Any, source_path: str, client: str, home: str | None = None
) -> list[dict[str, Any]]:
    """Turn one parsed MCP config into redacted candidate records.

    Accepts both the Claude Desktop / Cursor shape (``mcpServers``) and the
    VS Code shape (``servers``). When ``home`` is given, the user's home
    directory is masked as ``~`` in command, args and url so OS usernames never
    reach the inventory.
    """
    if not isinstance(data, dict):
        raise ValueError("config root is not a JSON object")
    servers = data.get("mcpServers")
    if servers is None:
        servers = data.get("servers")
    if not isinstance(servers, dict):
        servers = {}

    def mask(value: str) -> str:
        return _mask_home(value, home) if home else value

    candidates: list[dict[str, Any]] = []
    for tool_name, spec in sorted(servers.items()):
        spec = spec if isinstance(spec, dict) else {}
        command = mask(str(spec.get("command") or UNKNOWN))
        args = [mask(a) for a in _redact_args(list(spec.get("args") or []))]
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
            record["url"] = mask(_redact_string(str(spec["url"])))
        if spec.get("headers"):
            record["headers"] = _redact_mapping(spec["headers"])
        record["provenance_hash"] = _provenance_hash(source_path, tool_name, command)
        record["config_hash"] = HashEngine.hash_config(
            {k: record.get(k) for k in ("command", "args", "env", "url", "headers")}
        )
        candidates.append(record)
    return candidates


def discover_tools(config_paths: list[str | Path], base_dir: str | Path = ".") -> list[dict[str, Any]]:
    """Parse MCP config files into redacted, metadata-only candidate records."""
    base = Path(base_dir)
    candidates: list[dict[str, Any]] = []
    for cfg in config_paths:
        cfg_path = Path(cfg) if Path(cfg).is_absolute() else base / cfg
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        candidates += _candidates_from_config(
            data, _source_path(cfg_path, base), _KNOWN_CLIENTS.get(cfg_path.name, UNKNOWN)
        )
    return candidates


# ── Real local machine discovery ───────────────────────────────────────────────

def _mask_home(value: str, home: str) -> str:
    """Replace the user's home directory with ``~`` (both slash styles)."""
    h = home.rstrip("/\\")
    if not h:
        return value
    out = value
    for variant in {h, h.replace("\\", "/"), h.replace("/", "\\")}:
        out = out.replace(variant, "~")
    return out


def _normalize_platform(platform: str | None) -> str:
    p = (platform or sys.platform).lower()
    if p.startswith("win"):
        return "windows"
    if p.startswith("darwin") or p.startswith("mac"):
        return "macos"
    return "linux"


# (client, base token, path parts under it). Tokens are what we store in the
# inventory instead of the real absolute path, so IDs are stable per location
# and usernames are never persisted.
_LOCATIONS: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {
    "windows": [
        ("claude-desktop", "%APPDATA%", ("Claude", "claude_desktop_config.json")),
        ("cursor", "%USERPROFILE%", (".cursor", "mcp.json")),
        ("vscode", "%APPDATA%", ("Code", "User", "mcp.json")),
    ],
    "macos": [
        ("claude-desktop", "~", ("Library", "Application Support", "Claude", "claude_desktop_config.json")),
        ("cursor", "~", (".cursor", "mcp.json")),
        ("vscode", "~", ("Library", "Application Support", "Code", "User", "mcp.json")),
    ],
    "linux": [
        ("claude-desktop", "~", (".config", "Claude", "claude_desktop_config.json")),
        ("cursor", "~", (".cursor", "mcp.json")),
        ("vscode", "~", (".config", "Code", "User", "mcp.json")),
    ],
}


def known_config_locations(
    platform: str | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve the well-known MCP config locations for a platform.

    Pure: platform, home and env are injectable so tests can point at a fake
    home on any OS. Each entry has ``client``, ``label`` (normalized, no
    username, used as source_path) and ``path`` (real Path, or None when the
    base cannot be resolved, e.g. %APPDATA% unset).
    """
    plat = _normalize_platform(platform)
    env = dict(os.environ if env is None else env)
    home_path = Path(home) if home is not None else Path.home()
    bases = {
        "~": home_path,
        "%APPDATA%": Path(env["APPDATA"]) if env.get("APPDATA") else None,
        "%USERPROFILE%": Path(env["USERPROFILE"]) if env.get("USERPROFILE") else home_path,
    }
    out = []
    for client, token, parts in _LOCATIONS[plat]:
        base = bases[token]
        out.append({
            "client": client,
            "label": "/".join((token, *parts)),
            "path": base.joinpath(*parts) if base is not None else None,
        })
    return out


def _safe_error(exc: BaseException) -> str:
    """Error text without the file path (which would contain the username)."""
    detail = getattr(exc, "strerror", None) or (exc.msg if isinstance(exc, json.JSONDecodeError) else "")
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def scan_local_machine(
    platform: str | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read-only scan of this machine's MCP configs. Never raises for a bad file.

    Returns a summary with one row per known location (status: found, missing,
    unresolved, unreadable, invalid_json, invalid_format) and the redacted
    candidates from every readable config.
    """
    plat = _normalize_platform(platform)
    home_str = str(home if home is not None else Path.home())
    locations: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for loc in known_config_locations(platform, home, env):
        row = {"client": loc["client"], "location": loc["label"], "status": "missing", "servers": 0, "error": None}
        path: Path | None = loc["path"]
        try:
            if path is None:
                row["status"] = "unresolved"
            elif path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                found = _candidates_from_config(data, loc["label"], loc["client"], home=home_str)
                candidates += found
                row.update(status="found", servers=len(found))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            row.update(status="invalid_json", error=_safe_error(exc))
        except OSError as exc:
            row.update(status="unreadable", error=_safe_error(exc))
        except ValueError as exc:
            row.update(status="invalid_format", error=str(exc))
        locations.append(row)
    return {
        "platform": plat,
        "scanned_at": _now(),
        "locations": locations,
        "configs_found": sum(r["status"] == "found" for r in locations),
        "candidates": candidates,
    }


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


# ── Policy-as-code ─────────────────────────────────────────────────────────────
#
# Rules are declarative JSON (fixtures/policies.json). Nothing in a policy file
# is executed: each `when` key must be one of the allowlisted conditions below,
# and every condition must be valid for the rule's subject kind. All conditions
# in a rule are ANDed. A rule never fires on a guessed fact: category comes only
# from explicit package / image / command evidence, otherwise it is UNKNOWN.

SEVERITIES = ("low", "medium", "high", "critical")
APPROVAL_STATES = ("approved", "pending", "missing")
_CONDITIONS: dict[str, set[str]] = {
    "passport": {"category_in", "approval", "field_is_unknown"},
    "change": {"access_mode_transition", "approval"},
    "event": {"action_mutating", "approval"},
}
_UNKNOWN_FIELDS = {"owner", "version", "access_mode", "client", "command"}

# category → evidence patterns, matched only against the command and package-like
# args (never against filesystem paths, which could contain any word).
_CATEGORY_EVIDENCE: dict[str, re.Pattern[str]] = {
    "database": re.compile(
        r"(^|[/@:_-])(postgres(ql)?|pg|mysql|mariadb|sqlite3?|mongo(db)?|redis|mssql|sqlserver)([/@:_.-]|$)",
        re.IGNORECASE,
    ),
}


def load_policies(path: str | Path) -> list[dict[str, Any]]:
    """Load and strictly validate a policy file. Missing file → no rules.

    Raises ValueError on any malformed rule instead of silently skipping it.
    """
    p = Path(path)
    if not p.exists():
        return []
    rules = json.loads(p.read_text(encoding="utf-8")).get("rules")
    if not isinstance(rules, list):
        raise ValueError("policy file must contain a 'rules' list")
    seen: set[str] = set()
    for i, r in enumerate(rules):
        where = f"rule #{i} ({r.get('rule_id', '?') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            raise ValueError(f"{where}: must be an object")
        for key in ("rule_id", "title", "severity", "applies_to", "when", "explanation"):
            if key not in r:
                raise ValueError(f"{where}: missing '{key}'")
        if r["rule_id"] in seen:
            raise ValueError(f"{where}: duplicate rule_id")
        seen.add(r["rule_id"])
        if r["severity"] not in SEVERITIES:
            raise ValueError(f"{where}: severity must be one of {SEVERITIES}")
        if r["applies_to"] not in _CONDITIONS:
            raise ValueError(f"{where}: applies_to must be one of {sorted(_CONDITIONS)}")
        when = r["when"]
        if not isinstance(when, dict) or not when:
            raise ValueError(f"{where}: 'when' must be a non-empty object")
        allowed = _CONDITIONS[r["applies_to"]]
        for cond, val in when.items():
            if cond not in allowed:
                raise ValueError(f"{where}: condition '{cond}' is not allowed for {r['applies_to']} rules "
                                 f"(allowed: {sorted(allowed)})")
            if cond == "category_in" and (not isinstance(val, list) or not set(val) <= set(_CATEGORY_EVIDENCE)):
                raise ValueError(f"{where}: category_in must list known categories {sorted(_CATEGORY_EVIDENCE)}")
            if cond == "approval" and (not isinstance(val, list) or not set(val) <= set(APPROVAL_STATES)):
                raise ValueError(f"{where}: approval must list states from {APPROVAL_STATES}")
            if cond == "field_is_unknown" and val not in _UNKNOWN_FIELDS:
                raise ValueError(f"{where}: field_is_unknown must be one of {sorted(_UNKNOWN_FIELDS)}")
            if cond == "access_mode_transition" and (
                not isinstance(val, dict) or set(val) != {"from", "to"}
            ):
                raise ValueError(f"{where}: access_mode_transition needs exactly 'from' and 'to'")
            if cond == "action_mutating" and not isinstance(val, bool):
                raise ValueError(f"{where}: action_mutating must be true or false")
    return rules


def _looks_like_path(arg: str) -> bool:
    return arg.startswith(("/", "~", ".", "\\")) or "src=" in arg or (len(arg) > 2 and arg[1] == ":")


def category_evidence(record: dict[str, Any]) -> tuple[str, str | None]:
    """Return (category, evidence) from explicit command/package evidence only."""
    tokens = [str(record.get("command") or "")] + [
        a for a in record.get("args", []) if not _looks_like_path(a) and not a.startswith("-")
    ]
    for category, pattern in _CATEGORY_EVIDENCE.items():
        for tok in tokens:
            if tok and tok != UNKNOWN and pattern.search(tok):
                return category, tok
    return UNKNOWN, None


def _approval_state(passport_id: str, approvals: list[dict[str, Any]]) -> tuple[str, str | None]:
    records = [a for a in approvals if a.get("passport_id") == passport_id]
    if any(str(a.get("status", "")).lower() == "approved" for a in records):
        return "approved", next(a.get("approval_id") for a in records if str(a.get("status")).lower() == "approved")
    if records:
        return "pending", records[0].get("approval_id")
    return "missing", None


def _finding(rule: dict[str, Any], subject_type: str, subject_id: str, passport_id: str,
             tool_name: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": "fnd-" + HashEngine.hash_dict({"r": rule["rule_id"], "s": subject_id, "e": evidence})[:12],
        "rule_id": rule["rule_id"],
        "title": rule["title"],
        "severity": rule["severity"],
        "subject_type": subject_type,
        "subject_id": subject_id,
        "passport_id": passport_id,
        "tool_name": tool_name,
        "evidence": evidence,
        "explanation": rule["explanation"],
    }


def _eval_passport(rule: dict[str, Any], rec: dict[str, Any], approvals: list[dict[str, Any]]) -> dict | None:
    evidence: dict[str, Any] = {}
    for cond, val in rule["when"].items():
        if cond == "category_in":
            cat, ev = category_evidence(rec)
            if cat not in val:
                return None
            evidence.update(category=cat, category_evidence=ev)
        elif cond == "approval":
            state, appr_id = _approval_state(rec["passport_id"], approvals)
            if state not in val:
                return None
            evidence.update(approval=state, approval_id=appr_id or UNKNOWN)
        elif cond == "field_is_unknown":
            if rec.get(val, UNKNOWN) != UNKNOWN:
                return None
            evidence[val] = UNKNOWN
    return evidence


def _eval_change(rule: dict[str, Any], old: dict[str, Any], new: dict[str, Any],
                 approvals: list[dict[str, Any]]) -> dict | None:
    evidence: dict[str, Any] = {}
    for cond, val in rule["when"].items():
        if cond == "access_mode_transition":
            if (old.get("access_mode"), new.get("access_mode")) != (val["from"], val["to"]):
                return None
            evidence.update(access_mode_before=old.get("access_mode"), access_mode_after=new.get("access_mode"),
                            config_hash_before=old.get("config_hash"), config_hash_after=new.get("config_hash"))
        elif cond == "approval":
            state, appr_id = _approval_state(new["passport_id"], approvals)
            if state not in val:
                return None
            evidence.update(approval=state, approval_id=appr_id or UNKNOWN)
    return evidence


def _eval_event(rule: dict[str, Any], link: dict[str, Any]) -> dict | None:
    evidence: dict[str, Any] = {}
    for cond, val in rule["when"].items():
        if cond == "action_mutating":
            tool_call = link.get("tool_call", UNKNOWN)
            if tool_call == UNKNOWN or _is_mutating(tool_call) != val:
                return None
            evidence.update(tool_call=tool_call)
        elif cond == "approval":
            state = "approved" if link.get("approval_status") == APPROVED else "missing"
            if state not in val:
                return None
            evidence.update(approval=state, approval_id=link.get("approval_id", UNKNOWN))
    evidence.update(event_id=link.get("event_id", UNKNOWN), timestamp=link.get("timestamp", UNKNOWN))
    return evidence


def evaluate_policies(
    rules: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    passports: dict[str, dict[str, Any]],
    approvals: list[dict[str, Any]],
    links: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Pure rule evaluation. Returns explainable findings; writes nothing.

    Passport rules run on the current view of every tool: the fresh candidate
    when it was just scanned, otherwise the confirmed passport. Change rules
    compare a confirmed passport with a drifted candidate. Event rules run on
    consented event links.
    """
    view = dict(passports)
    view.update({c["passport_id"]: c for c in candidates})
    findings: list[dict[str, Any]] = []
    for rule in rules:
        kind = rule["applies_to"]
        if kind == "passport":
            for pid, rec in sorted(view.items()):
                ev = _eval_passport(rule, rec, approvals)
                if ev is not None:
                    findings.append(_finding(rule, "passport", pid, pid, rec["tool_name"], ev))
        elif kind == "change":
            for cand in candidates:
                old = passports.get(cand["passport_id"])
                if old and old.get("config_hash") != cand.get("config_hash"):
                    ev = _eval_change(rule, old, cand, approvals)
                    if ev is not None:
                        findings.append(_finding(rule, "change", f"{cand['passport_id']}@{cand['config_hash']}",
                                                 cand["passport_id"], cand["tool_name"], ev))
        elif kind == "event":
            for link in links or []:
                ev = _eval_event(rule, link)
                if ev is not None:
                    tool = passports.get(link["passport_id"], {}).get("tool_name", UNKNOWN)
                    findings.append(_finding(rule, "event", link["link_id"], link["passport_id"], tool, ev))
    return findings


# ── Engine ─────────────────────────────────────────────────────────────────────

class AutoDiscoverEngine:
    """Local-first passport inventory with sidecar link/incident/change records.

    Files under ``data_dir``:
      passports.json         {passport_id: passport}   source of truth
      linked_events.json     [link]                    consented event links
      incidents.json         [incident]                out-of-expected-behaviour
      metadata_changes.json  [change]                  config drift on known tools
      policy_findings.json   [finding]                 policy-as-code results
    """

    def __init__(
        self,
        data_dir: str | Path = "data",
        base_dir: str | Path = ".",
        approvals_path: str | Path = "fixtures/approvals.json",
        policies_path: str | Path | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.data_dir = Path(data_dir)
        self.approvals_path = Path(approvals_path)
        # Policies sit next to approvals unless given explicitly.
        self.policies_path = Path(policies_path) if policies_path else self.approvals_path.parent / "policies.json"
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

    def policies(self) -> list[dict[str, Any]]:
        return load_policies(self.policies_path)

    def policy_findings(self, store_dir: str | Path | None = None) -> list[dict[str, Any]]:
        return _read_json(Path(store_dir or self.data_dir) / "policy_findings.json", [])

    # ── 1. Discovery ───────────────────────────────────────────────────────────

    def discover_tools(self, config_paths: list[str | Path]) -> list[dict[str, Any]]:
        """Scan configs and annotate each candidate against the inventory.

        Adds ``known`` (already a confirmed passport) and ``metadata_changed``
        with a human-readable ``diff`` when a known tool's config_hash moved.
        Drift is appended to metadata_changes.json; the passport is untouched.
        """
        return self._annotate(discover_tools(config_paths, self.base_dir))

    def scan_local_machine(
        self,
        platform: str | None = None,
        home: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Real-machine scan, annotated against this engine's own registry."""
        summary = scan_local_machine(platform, home, env)
        summary["candidates"] = self._annotate(summary["candidates"])
        return summary

    def _annotate(self, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored = self.passports()
        changes = self.metadata_changes()
        seen = {(c["passport_id"], c["new_config_hash"]) for c in changes}
        out: list[dict[str, Any]] = []
        for cand in found:
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
        # In-memory policy annotation (persisted only via evaluate_policies()).
        findings = evaluate_policies(self.policies(), out, stored, self.approvals())
        for cand in out:
            cand["policy_findings"] = [f for f in findings if f["passport_id"] == cand["passport_id"]]
        return out

    # ── Policy evaluation (persisted) ──────────────────────────────────────────

    def evaluate_policies(
        self,
        candidates: list[dict[str, Any]] | None = None,
        store_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate all rules and save the current findings to the active store.

        ``store_dir`` defaults to this engine's data_dir, so demo mode writes
        data/policy_findings.json and real-machine mode data/local/... .
        The saved file is a snapshot of the current findings; a finding that
        was already present keeps its first_detected_at. passports.json is
        only read, never written.
        """
        store = Path(store_dir or self.data_dir)
        path = store / "policy_findings.json"
        stored = _read_json(store / "passports.json", {})
        links = _read_json(store / "linked_events.json", [])
        findings = evaluate_policies(self.policies(), candidates or [], stored, self.approvals(), links)
        previous = {f["finding_id"]: f for f in _read_json(path, [])}
        now = _now()
        for f in findings:
            f["first_detected_at"] = previous.get(f["finding_id"], {}).get("first_detected_at", now)
            f["last_evaluated_at"] = now
        _write_json(path, findings)
        return findings

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
        record = {k: v for k, v in candidate.items() if k not in {"known", "metadata_changed", "diff", "policy_findings"}}
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
        link["policy_flags"] = sorted({
            f["rule_id"] for f in evaluate_policies(
                [r for r in self.policies() if r["applies_to"] == "event"], [], {}, [], [link]
            )
        })
        links.append(link)
        _write_json(self.links_path, links)
        if incident:
            incidents = self.incidents()
            if all(i["incident_id"] != incident["incident_id"] for i in incidents):
                incidents.append(incident)
                _write_json(self.incidents_path, incidents)
        return link
