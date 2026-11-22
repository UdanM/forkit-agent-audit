"""Which machine a tool was discovered on: a sidecar record, never identity.

The machine name is context, not an owner. ``owner`` stays UNKNOWN, and the
name is kept out of every passport_id / compute_id input, so the same config
gives the same ID on every machine. Records live in
``<store>/discovered_on.json``, keyed by passport_id, next to (never inside)
``passports.json``. Demo fixtures are never attributed to the machine running
the demo.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import UNKNOWN

__all__ = ["DEMO_LABEL", "FILE_NAME", "machine_name", "record_discovery", "load_discovered_on", "discovered_on_label"]

FILE_NAME = "discovered_on.json"
DEMO_LABEL = "Demo fixtures (not a real machine)"
_MAX = 64


def machine_name(raw: str | None = None) -> str:
    """Short host name of this computer, or UNKNOWN if the OS gives none."""
    try:
        name = socket.gethostname() if raw is None else raw
    except OSError:
        return UNKNOWN
    name = "".join(ch for ch in str(name or "") if ch.isprintable()).strip().split(".")[0][:_MAX]
    return name or UNKNOWN


def load_discovered_on(store_dir: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(store_dir) / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_discovery(store_dir: str | Path, passport_ids: Iterable[str], machine: str,
                     now: str | None = None) -> dict[str, dict[str, Any]]:
    """Note that these passport_ids were seen on ``machine``. Keeps first_seen; atomic write."""
    ts = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = Path(store_dir)
    data = load_discovered_on(store)
    for pid in passport_ids:
        rec = data.get(pid) if isinstance(data.get(pid), dict) else {}
        machines = sorted(set(rec.get("machines", [])) | {machine})
        data[pid] = {"machines": machines, "first_seen": rec.get("first_seen", ts), "last_seen": ts}
    store.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=store, prefix=".discovered_on.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, store / FILE_NAME)
    return data


def discovered_on_label(store_dir: str | Path, passport_id: str, demo: bool) -> str:
    """Text for the UI. Demo data says so; real data shows the recorded machine(s) or UNKNOWN."""
    if demo:
        return DEMO_LABEL
    rec = load_discovered_on(store_dir).get(passport_id)
    machines = rec.get("machines") if isinstance(rec, dict) else None
    return ", ".join(machines) if machines else UNKNOWN
