"""Build and store captured runtime events in the same schema as fixtures/runtime_events.jsonl.

Metadata only: the tool name, a redacted target, the result and the time. Argument
*values* and tool output are never stored; only the argument key names are kept.
Fields the capture point cannot know (approval, runtime consent) stay null and are
resolved later by AutoDiscoverEngine.link_event against approvals.json.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..engine import UNKNOWN, _redact_string

ORIGINS = ("local-tap", "cloud-n8n", "cloud-collector")
RESULTS = ("success", "error", "denied")
TARGET_KEYS = ("path", "source", "destination", "file", "filePath", "fileName", "url", "uri", "table", "query_target")
_MAX = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(value: Any, limit: int = _MAX) -> str:
    text = "".join(ch for ch in str(value) if ch.isprintable())[:limit]
    return _redact_string(text) if text else UNKNOWN


def mask_home(value: str, home: str | None = None) -> str:
    """Replace the user's home directory with '~' so usernames never reach the event log."""
    home = home if home is not None else str(Path.home())
    if home and home not in ("/", "") and value.startswith(home):
        return "~" + value[len(home):]
    return value


def relative_to_base(value: str, base: str | None) -> str:
    """A path inside ``base`` (the agent's working folder) is stored relative to it, machine-independent."""
    if base and value.startswith(base.rstrip("/\\") + os.sep):
        return value[len(base.rstrip("/\\")) + 1:].replace("\\", "/")
    return value


def target_from_args(arguments: Mapping[str, Any] | None, home: str | None = None,
                     base: str | None = None) -> str:
    """The first path-like argument, redacted and home-masked; UNKNOWN if there is none."""
    for key in TARGET_KEYS:
        if arguments and isinstance(arguments.get(key), str) and arguments[key]:
            return _clean(mask_home(relative_to_base(arguments[key], base), home))
    return UNKNOWN


def build_event(
    *,
    origin: str,
    client: str,
    config_path: str,
    server_name: str,
    tool_call: str,
    result: str,
    arguments: Mapping[str, Any] | None = None,
    agent: str | None = None,
    target: str | None = None,
    timestamp: str | None = None,
    home: str | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}")
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}")
    return {
        "event_id": "cap-" + uuid.uuid4().hex[:12],
        "timestamp": timestamp or _now(),
        "source": {"client": _clean(client), "config_path": _clean(config_path), "server_name": _clean(server_name)},
        "tool_call": _clean(tool_call),
        "target": _clean(target) if target else target_from_args(arguments, home, base),
        "result": result,
        "argument_keys": sorted(str(k) for k in (arguments or {}))[:20],
        "approval_id": None,          # never claimed by the capture point; checked against approvals.json
        "runtime_consent": None,      # UNKNOWN until a person links the event with consent
        "capture": {"origin": origin, "agent": _clean(agent) if agent else UNKNOWN, "captured_at": _now()},
    }


REMOTE_ORIGINS = ("cloud-n8n", "cloud-collector")


def validate_event(event: Any, allowed_origins: tuple[str, ...] = REMOTE_ORIGINS) -> dict[str, Any]:
    """Re-build a posted event through build_event, so a remote sender cannot inject raw fields.

    A sender may only claim an origin from ``allowed_origins``; anything else (for example a remote
    sender pretending to be the local tap) is recorded as ``cloud-collector``.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    src = event.get("source") if isinstance(event.get("source"), dict) else {}
    cap = event.get("capture") if isinstance(event.get("capture"), dict) else {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else None
    origin = str(cap.get("origin") or event.get("origin") or "")
    clean = build_event(
        origin=origin if origin in allowed_origins else "cloud-collector",
        client=src.get("client") or UNKNOWN,
        config_path=src.get("config_path") or UNKNOWN,
        server_name=src.get("server_name") or UNKNOWN,
        tool_call=event.get("tool_call") or UNKNOWN,
        result=str(event.get("result") or ""),
        arguments=args,
        agent=cap.get("agent") or event.get("agent"),
        target=event.get("target") if isinstance(event.get("target"), str) and not args else None,
        timestamp=event.get("timestamp") if isinstance(event.get("timestamp"), str) else None,
    )
    if args is None and isinstance(event.get("argument_keys"), list):
        clean["argument_keys"] = sorted(_clean(k, 64) for k in event["argument_keys"])[:20]
    return clean


def append_event(path: str | Path, event: Mapping[str, Any]) -> None:
    """Append one JSON line. O_APPEND keeps concurrent single-line writes from interleaving."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def read_events(path: str | Path) -> list[dict[str, Any]]:
    """All captured events; unreadable lines are skipped, never fatal."""
    out: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("event_id"):
            out.append(ev)
    return out
