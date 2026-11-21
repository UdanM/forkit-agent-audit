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
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
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
    "workspace_config_locations",
    "load_policies",
    "category_evidence",
    "evaluate_policies",
    "raw_secret_values_from_configs",
    "report_sha256",
    "render_audit_markdown",
    "build_audit_bundle",
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


_TRANSPORTS = {"stdio", "http", "sse", "streamable-http", "streamablehttp", "ws", "websocket"}


def _transport(spec: dict[str, Any]) -> str:
    """Observed transport: declared ``type``/``transport``, else inferred from
    ``command`` (stdio) or ``url`` (http). Neither -> UNKNOWN."""
    for key in ("type", "transport"):
        val = spec.get(key)
        if isinstance(val, str) and val.lower() in _TRANSPORTS:
            return val.lower()
    has_cmd = isinstance(spec.get("command"), str) and bool(spec["command"])
    has_url = isinstance(spec.get("url") or spec.get("serverUrl"), str)
    if has_cmd and not has_url:
        return "stdio"
    if has_url and not has_cmd:
        return "http"
    return UNKNOWN


def _enabled(spec: dict[str, Any]) -> bool | str:
    """True/False only when the config says so explicitly; otherwise UNKNOWN."""
    if isinstance(spec.get("enabled"), bool):
        return spec["enabled"]
    if isinstance(spec.get("disabled"), bool):
        return not spec["disabled"]
    return UNKNOWN


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
            "transport": _transport(spec),
            "enabled": _enabled(spec),
        }
        url = spec.get("url") or spec.get("serverUrl")  # serverUrl: Windsurf
        if url:
            record["url"] = mask(_redact_string(str(url)))
        if spec.get("headers"):
            record["headers"] = _redact_mapping(spec["headers"])
        record["provenance_hash"] = _provenance_hash(source_path, tool_name, command)
        hashed = {k: record.get(k) for k in ("command", "args", "env", "url", "headers")}
        if record["enabled"] != UNKNOWN:
            # Only an explicit enabled/disabled flag feeds drift, so configs
            # without one keep the config_hash they always had.
            hashed["enabled"] = record["enabled"]
        record["config_hash"] = HashEngine.hash_config(hashed)
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
        ("vscode-insiders", "%APPDATA%", ("Code - Insiders", "User", "mcp.json")),
        ("claude-code", "%USERPROFILE%", (".claude.json",)),
        ("windsurf", "%USERPROFILE%", (".codeium", "windsurf", "mcp_config.json")),
        ("codex", "%CODEX_HOME%", ("config.toml",)),
    ],
    "macos": [
        ("claude-desktop", "~", ("Library", "Application Support", "Claude", "claude_desktop_config.json")),
        ("cursor", "~", (".cursor", "mcp.json")),
        ("vscode", "~", ("Library", "Application Support", "Code", "User", "mcp.json")),
        ("vscode-insiders", "~", ("Library", "Application Support", "Code - Insiders", "User", "mcp.json")),
        ("claude-code", "~", (".claude.json",)),
        ("windsurf", "~", (".codeium", "windsurf", "mcp_config.json")),
        ("codex", "%CODEX_HOME%", ("config.toml",)),
    ],
    "linux": [
        ("claude-desktop", "~", (".config", "Claude", "claude_desktop_config.json")),
        ("cursor", "~", (".cursor", "mcp.json")),
        ("vscode", "~", (".config", "Code", "User", "mcp.json")),
        ("vscode-insiders", "~", (".config", "Code - Insiders", "User", "mcp.json")),
        ("claude-code", "~", (".claude.json",)),
        ("windsurf", "~", (".codeium", "windsurf", "mcp_config.json")),
        ("codex", "%CODEX_HOME%", ("config.toml",)),
    ],
}

# Project-level configs looked for inside each workspace folder the user names.
# Discovery breadth (Claude Code, Windsurf, Codex, VS Code profiles, workspace
# configs) ported from UdanM/forkit-agent-audit @ 3f5b2aa (discovery.py).
_WORKSPACE_CONFIGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("vscode", (".vscode", "mcp.json")),
    ("cursor", (".cursor", "mcp.json")),
    ("claude-code", (".mcp.json",)),
    ("codex", (".codex", "config.toml")),
)

MAX_CONFIG_BYTES = 1024 * 1024


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
    userprofile = Path(env["USERPROFILE"]) if env.get("USERPROFILE") else home_path
    bases = {
        "~": home_path,
        "%APPDATA%": Path(env["APPDATA"]) if env.get("APPDATA") else None,
        "%USERPROFILE%": userprofile,
    }
    out = []
    for client, token, parts in _LOCATIONS[plat]:
        if token == "%CODEX_HOME%":
            if env.get("CODEX_HOME"):
                base: Path | None = Path(env["CODEX_HOME"])
            else:
                # Default Codex home: ~/.codex. Keep the label username-free.
                token = "%USERPROFILE%" if plat == "windows" else "~"
                parts = (".codex", *parts)
                base = userprofile if plat == "windows" else home_path
        else:
            base = bases[token]
        out.append({
            "client": client,
            "label": "/".join((token, *parts)),
            "path": base.joinpath(*parts) if base is not None else None,
        })
    # VS Code profiles keep their own mcp.json under User/profiles/<id>/.
    for loc in list(out):
        if loc["client"] in ("vscode", "vscode-insiders") and loc["path"] is not None:
            profiles = loc["path"].parent / "profiles"
            try:
                found = sorted(profiles.glob("*/mcp.json")) if profiles.is_dir() else []
            except OSError:
                found = []
            prefix = loc["label"].rsplit("/", 1)[0]
            for prof in found:
                out.append({
                    "client": f"{loc['client']}-profile",
                    "label": f"{prefix}/profiles/{prof.parent.name}/mcp.json",
                    "path": prof,
                })
    return out


def workspace_config_locations(workspaces: list[str | Path] | None) -> list[dict[str, Any]]:
    """Project-level MCP configs inside the given workspace folders.

    Labels use the workspace folder name, never its absolute path, so IDs do not
    depend on where the project is checked out. Two workspaces with the same
    folder name get an index suffix so their tools are never merged.
    """
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for ws in workspaces or []:
        root = Path(ws)
        name = root.name or "workspace"
        seen[name] = seen.get(name, 0) + 1
        tag = name if seen[name] == 1 else f"{name}#{seen[name]}"
        for client, parts in _WORKSPACE_CONFIGS:
            out.append({
                "client": client,
                "label": "/".join((f"<workspace:{tag}>", *parts)),
                "path": root.joinpath(*parts),
            })
    return out


def _load_config(path: Path) -> Any:
    """Read one config file as data (JSON, or TOML for Codex). Never executes it."""
    with path.open("rb") as fh:
        raw = fh.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("config file is larger than 1 MiB")
    text = raw.decode("utf-8-sig")
    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ImportError as exc:  # pragma: no cover - Python < 3.11
            raise ValueError("TOML configs need Python 3.11+") from exc
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise json.JSONDecodeError(str(exc).split("(")[0].strip(), text, 0) from exc
        servers = data.get("mcp_servers") if isinstance(data, dict) else None
        return {"mcpServers": servers if isinstance(servers, dict) else {}}
    return json.loads(text)


def _candidates_from_location(data: Any, label: str, client: str, home: str | None) -> list[dict[str, Any]]:
    """Candidates for one location. Claude Code's ~/.claude.json also holds
    per-project servers under ``projects.<path>.mcpServers``; each project is
    its own source (``label#projects/<masked path>``) so same-named servers in
    different projects stay separate tools."""
    found = _candidates_from_config(data, label, client, home=home)
    if client == "claude-code" and isinstance(data.get("projects"), dict):
        for proj_path, proj in sorted(data["projects"].items()):
            if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict) and proj["mcpServers"]:
                masked = _mask_home(str(proj_path), home) if home else str(proj_path)
                masked = masked.replace("\\", "/")
                found += _candidates_from_config(
                    {"mcpServers": proj["mcpServers"]}, f"{label}#projects/{masked}", client, home=home
                )
    return found


def _safe_error(exc: BaseException) -> str:
    """Error text without the file path (which would contain the username)."""
    detail = getattr(exc, "strerror", None) or (exc.msg if isinstance(exc, json.JSONDecodeError) else "")
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def scan_local_machine(
    platform: str | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
    workspaces: list[str | Path] | None = None,
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
    for loc in known_config_locations(platform, home, env) + workspace_config_locations(workspaces):
        row = {"client": loc["client"], "location": loc["label"], "status": "missing", "servers": 0, "error": None}
        path: Path | None = loc["path"]
        try:
            if path is None:
                row["status"] = "unresolved"
            elif path.exists():
                data = _load_config(path)
                found = _candidates_from_location(data, loc["label"], loc["client"], home_str)
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
    for key in ("command", "url", "access_mode", "transport", "enabled"):
        if key in ("transport", "enabled") and (key not in old or key not in new):
            continue  # records minted before these fields existed
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


# ── Audit export helpers ───────────────────────────────────────────────────────

STORE_FILES = ("passports.json", "metadata_changes.json", "linked_events.json", "incidents.json",
               "policy_findings.json")
_LEAK_PATTERNS: dict[str, re.Pattern[str]] = {
    "url_password": re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s\"]+:(?!\[REDACTED\]@)[^@\s\"]+@"),
    "token_prefix": re.compile(
        r"\b(sk-[A-Za-z0-9_-]{8,}|sk_live_[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|"
        r"xox[abp]-[\w-]{10,}|AKIA[0-9A-Z]{16}|AIza[\w-]{30,}|glpat-[\w-]{20,})"
    ),
}


def raw_secret_values_from_configs(paths: list[str | Path]) -> list[str]:
    """Raw env/header values from MCP configs, for in-memory leak checks only.

    Unreadable or malformed files are skipped. The values must never be
    stored or displayed; callers only compare against them.
    """
    values: list[str] = []
    for path in paths:
        try:
            data = _load_config(Path(path))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        blocks = [data.get("mcpServers") or data.get("servers") or {}]
        if isinstance(data.get("projects"), dict):  # Claude Code per-project servers
            blocks += [p.get("mcpServers") or {} for p in data["projects"].values() if isinstance(p, dict)]
        specs = [spec for b in blocks if isinstance(b, dict) for spec in b.values()]
        for spec in specs:
            if isinstance(spec, dict):
                for key in ("env", "headers"):
                    mapping = spec.get(key)
                    if isinstance(mapping, dict):
                        values += [str(v) for v in mapping.values() if v not in (None, "")]
    return sorted(set(values))


def _leak_kinds(text: str, raw_values: list[str]) -> list[str]:
    kinds = []
    # Check both the raw form and its JSON-escaped form (quotes, backslashes, unicode).
    if any(v and (v in text or json.dumps(v)[1:-1] in text) for v in raw_values):
        kinds.append("raw_value")
    kinds += [name for name, pat in _LEAK_PATTERNS.items() if pat.search(text)]
    return kinds


def _scrub(obj: Any, raw_values: list[str]) -> Any:
    """Deep-redact raw values and secret patterns (used only when a leak is found)."""
    if isinstance(obj, str):
        for v in raw_values:
            if v:
                obj = obj.replace(v, REDACTED)
        obj = _redact_string(obj)
        return _LEAK_PATTERNS["token_prefix"].sub(REDACTED, obj)
    if isinstance(obj, list):
        return [_scrub(x, raw_values) for x in obj]
    if isinstance(obj, dict):
        return {_scrub(k, raw_values): _scrub(v, raw_values) for k, v in obj.items()}
    return obj


def report_sha256(report: dict[str, Any]) -> str:
    """SHA-256 of json.dumps(report minus 'report_sha256', sort_keys=True), UTF-8."""
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_None._\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def render_audit_markdown(report: dict[str, Any]) -> str:
    """Human-readable rendering of a build_audit_report() result."""
    s, integ, scan = report["summary"], report["integrity"], report["secret_scan"]
    sev = ", ".join(f"{k}: {v}" for k, v in s["policy_findings_by_severity"].items()) or "none"
    lines = [
        "# AutoDiscover Governance Audit Report",
        "",
        f"- **Report ID:** `{report['report_id']}`",
        f"- **Generated at (UTC):** {report['generated_at']}",
        f"- **Mode:** {report['mode']} · **Platform:** {report['platform']}",
        f"- **Report SHA-256:** `{report['report_sha256']}` (canonical JSON, sort_keys=True, excluding this field)",
        "",
        "## Verdict",
        "",
        f"- **Passport integrity:** {integ['status']} ({integ['passports_checked']} checked, "
        f"{len(integ['mismatches'])} mismatched)",
        f"- **Secret scan:** {scan['status']} ({len(scan['files_scanned'])} files scanned, "
        f"{scan['raw_values_checked']} raw secret values checked, {len(scan['leaks'])} leaks)",
        "",
        "## Summary",
        "",
        _md_table(["Metric", "Count"], [
            ["Confirmed passports", s["passports"]],
            ["Metadata changes", s["metadata_changes"]],
            ["Consented event links", s["linked_events"]],
            ["Links with MISSING_APPROVAL", s["missing_approval_links"]],
            ["Incidents", s["incidents"]],
            ["Policy findings", f"{s['policy_findings']} ({sev})"],
        ]),
        "## Confirmed passports",
        "",
        _md_table(["Passport ID", "Tool", "Source", "Client", "Owner", "Version", "Access mode"], [
            [f"`{p['passport_id'][:12]}…`", p.get("tool_name"), p.get("source_path"), p.get("client"),
             p.get("owner"), p.get("version"), p.get("access_mode")] for p in report["passports"]
        ]),
        "## Approval coverage",
        "",
        _md_table(["Tool", "Source", "Approval", "Approval ID"], [
            [a["tool_name"], a["source_path"], a["state"], a["approval_id"]] for a in report["approval_coverage"]
        ]),
        "## Metadata changes",
        "",
        _md_table(["Tool", "Status", "Detected", "Diff"], [
            [c.get("tool_name"), c.get("status"), c.get("detected_at"), "; ".join(c.get("diff", []))]
            for c in report["metadata_changes"]
        ]),
        "## Consented event links",
        "",
        _md_table(["Event", "Tool call", "Timestamp", "Source", "Approval", "Flags", "Policy"], [
            [l_.get("event_id"), l_.get("tool_call"), l_.get("timestamp"),
             (l_.get("source") or {}).get("config_path", UNKNOWN) if isinstance(l_.get("source"), dict) else UNKNOWN,
             l_.get("approval_status"), ", ".join(l_.get("flags", [])) or "—",
             ", ".join(l_.get("policy_flags", [])) or "—"] for l_ in report["linked_events"]
        ]),
        "## Incidents",
        "",
        _md_table(["Incident", "Type", "Tool", "Action", "Result", "Timestamp", "Approval"], [
            [i.get("incident_id"), i.get("type"), i.get("tool_name"), i.get("tool_call"), i.get("result"),
             i.get("timestamp"), i.get("approval_status")] for i in report["incidents"]
        ]),
        "## Policy findings",
        "",
        _md_table(["Rule", "Severity", "Title", "Tool", "Evidence"], [
            [f["rule_id"], f["severity"], f["title"], f["tool_name"],
             ", ".join(f"{k}={v}" for k, v in f["evidence"].items())] for f in report["policy_findings"]
        ]),
        "## Integrity mismatches",
        "",
        _md_table(["Passport ID", "Reason"], [[m["passport_id"], m["reason"]] for m in integ["mismatches"]]),
        "## Secret scan",
        "",
        f"Patterns checked: {', '.join(scan['pattern_checks'])}. Raw secret values are compared in memory only "
        "and never written to this report.",
        "",
        _md_table(["File", "Leak kinds"], [[x["file"], ", ".join(x["kinds"])] for x in scan["leaks"]]),
    ]
    return "\n".join(lines)


def build_audit_bundle(
    report: dict[str, Any],
    store_dir: str | Path,
    inputs: list[str | Path] | None = None,
) -> bytes:
    """ZIP bundle: report.json, report.md, store/*, inputs/*, SHA256SUMS.

    Entry names and SHA256SUMS use POSIX '/' paths. Refuses (PermissionError)
    when the report's secret scan failed, so a leak is never redistributed.
    """
    if report["secret_scan"]["status"] != "PASS":
        files = ", ".join(sorted({x["file"] for x in report["secret_scan"]["leaks"]}))
        raise PermissionError(f"Audit bundle blocked: secret leak detected in {files}")
    store = Path(store_dir)
    entries: dict[str, bytes] = {
        "report.json": (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "report.md": render_audit_markdown(report).encode("utf-8"),
    }
    for name in STORE_FILES:
        if (store / name).is_file():
            entries[f"store/{name}"] = (store / name).read_bytes()
    for inp in inputs or []:
        if Path(inp).is_file():
            entries[f"inputs/{Path(inp).name}"] = Path(inp).read_bytes()
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(entries.items()))
    entries["SHA256SUMS"] = sums.encode("utf-8")

    buf = io.BytesIO()
    stamp = datetime.fromisoformat(report["generated_at"]).timetuple()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(PurePosixPath(name).as_posix(), date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


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
        workspaces: list[str | Path] | None = None,
    ) -> dict[str, Any]:
        """Real-machine scan, annotated against this engine's own registry."""
        summary = scan_local_machine(platform, home, env, workspaces)
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

    # ── Audit export (read-only) ───────────────────────────────────────────────

    def build_audit_report(
        self,
        candidates: list[dict[str, Any]] | None = None,
        raw_secret_values: list[str] | None = None,
        mode: str = "demo",
        platform: str | None = None,
    ) -> dict[str, Any]:
        """Timestamped, tamper-evident governance snapshot of this engine's store.

        Read-only: policies are evaluated in memory and nothing is written.
        Works on an empty or freshly reset store (all counts 0). Raw secret
        values are compared in memory and never copied into the report; if a
        leak is found the report content is scrubbed and secret_scan = FAIL.
        """
        store = self.data_dir
        passports = _read_json(self.passports_path, {})
        changes = self.metadata_changes()
        links = self.linked_events()
        incidents = self.incidents()
        approvals = self.approvals()
        findings = evaluate_policies(self.policies(), candidates or [], passports, approvals, links)
        raw = [v for v in (raw_secret_values or []) if v]

        # Integrity: every stored ID must re-derive from its own provenance.
        mismatches = []
        for key, p in sorted(passports.items()):
            try:
                derived = derive_passport_id(p["source_path"], p["tool_name"], p["command"])
            except (KeyError, TypeError):
                mismatches.append({"passport_id": key, "reason": "missing_provenance_fields"})
                continue
            if derived != key or p.get("passport_id") != key:
                mismatches.append({"passport_id": key, "reason": "id_does_not_match_provenance"})

        # Leak scan over every store file (by name only; values never echoed).
        leaks, scanned = [], []
        for name in STORE_FILES:
            f = store / name
            if f.is_file():
                scanned.append(name)
                kinds = _leak_kinds(f.read_text(encoding="utf-8", errors="replace"), raw)
                if kinds:
                    leaks.append({"file": name, "kinds": kinds})

        coverage = []
        for pid, p in sorted(passports.items()):
            state, appr_id = _approval_state(pid, approvals)
            coverage.append({"passport_id": pid, "tool_name": p.get("tool_name", UNKNOWN),
                             "source_path": p.get("source_path", UNKNOWN), "state": state,
                             "approval_id": appr_id or UNKNOWN})
        severity = {s: sum(f["severity"] == s for f in findings) for s in reversed(SEVERITIES)}
        now = _now()
        body: dict[str, Any] = {
            "report_type": "autodiscover.governance_audit",
            "schema_version": 1,
            "report_id": "aud-" + re.sub(r"[^0-9]", "", now)[:14],
            "generated_at": now,
            "mode": mode,
            "platform": _normalize_platform(platform),
            "summary": {
                "passports": len(passports),
                "candidates_unconfirmed": sum(1 for c in candidates or [] if c["passport_id"] not in passports),
                "metadata_changes": len(changes),
                "linked_events": len(links),
                "missing_approval_links": sum(1 for x in links if x.get("approval_status") == MISSING_APPROVAL),
                "incidents": len(incidents),
                "policy_findings": len(findings),
                "policy_findings_by_severity": {k: v for k, v in severity.items() if v},
            },
            "passports": [passports[k] for k in sorted(passports)],
            "approval_coverage": coverage,
            "metadata_changes": changes,
            "linked_events": links,
            "missing_approval_link_ids": [x["link_id"] for x in links if x.get("approval_status") == MISSING_APPROVAL],
            "incidents": incidents,
            "policy_findings": findings,
            "integrity": {
                "status": "PASS" if not mismatches else "FAIL",
                "passports_file_present": self.passports_path.is_file(),
                "passports_file_sha256": (hashlib.sha256(self.passports_path.read_bytes()).hexdigest()
                                          if self.passports_path.is_file() else None),
                "passports_checked": len(passports),
                "mismatches": mismatches,
            },
        }
        # The report itself is part of the leak scan.
        own = _leak_kinds(json.dumps(body, sort_keys=True), raw)
        if own and not leaks:
            leaks.append({"file": "report", "kinds": own})
        if leaks:
            body = _scrub(body, raw)
        body["secret_scan"] = {
            "status": "PASS" if not leaks else "FAIL",
            "files_scanned": scanned,
            "raw_values_checked": len(raw),
            "pattern_checks": sorted(_LEAK_PATTERNS),
            "leaks": leaks,
        }
        body["report_sha256"] = report_sha256(body)
        return body

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
