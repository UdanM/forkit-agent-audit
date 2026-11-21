"""
Installed AI clients (merge M2)
===============================

Read-only discovery of AI tools installed on this machine, alongside the MCP
servers in their configs: command-line agents on PATH, desktop apps in their
standard install folders, and AI editor extensions.

The client lists are ported from UdanM/forkit-agent-audit @ 3f5b2aa
(``discovery.py``). Identity follows AutoDiscover's rules:

- Each kind has its own ``passport_type`` (``cli_tool``, ``desktop_app``,
  ``editor_extension``), so a CLI called ``claude`` never merges with an MCP
  server called ``claude``.
- ``source_path`` is the install location with the home folder masked as ``~``
  (or ``%LOCALAPPDATA%`` on Windows), so IDs carry no username.
- An extension's location excludes its version folder: upgrading Copilot is
  drift on the same passport, not a new tool.
- Owner stays UNKNOWN. An extension's own ``package.json`` declares its version
  and publisher, so those are recorded as declared facts (never ID inputs).
- Nothing is executed: PATH lookups resolve file names, and package.json is
  read as data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from forkit.domain.hashing import HashEngine

from .engine import (
    CLI_TOOL,
    DESKTOP_APP,
    EDITOR_EXTENSION,
    UNKNOWN,
    _mask_home,
    _normalize_platform,
    derive_passport_id,
)

CLI_COMMANDS = ("claude", "codex", "gemini", "ollama", "aider", "opencode", "cursor-agent",
                "goose", "kiro", "q", "copilot", "llm")

# (app name, path parts under the base). Base is %LOCALAPPDATA% on Windows,
# /Applications or ~/Applications on macOS.
DESKTOP_APPS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "windows": [
        ("Cursor", ("Programs", "Cursor", "Cursor.exe")),
        ("Windsurf", ("Programs", "Windsurf", "Windsurf.exe")),
        ("LM Studio", ("Programs", "LM Studio", "LM Studio.exe")),
        ("Ollama", ("Programs", "Ollama", "ollama app.exe")),
        ("Claude", ("AnthropicClaude", "claude.exe")),
    ],
    "macos": [
        ("Cursor", ("Cursor.app",)),
        ("Windsurf", ("Windsurf.app",)),
        ("LM Studio", ("LM Studio.app",)),
        ("Ollama", ("Ollama.app",)),
        ("Claude", ("Claude.app",)),
        ("ChatGPT", ("ChatGPT.app",)),
    ],
    "linux": [],
}

EDITOR_EXTENSIONS = {
    "github.copilot": "GitHub Copilot",
    "github.copilot-chat": "GitHub Copilot Chat",
    "anthropic.claude-code": "Claude Code",
    "openai.chatgpt": "Codex",
    "rooveterinaryinc.roo-cline": "Roo Code",
    "saoudrizwan.claude-dev": "Cline",
    "continue.continue": "Continue",
    "google.geminicodeassist": "Gemini Code Assist",
}
EDITORS = (".vscode", ".vscode-insiders", ".cursor", ".windsurf")
_MAX_PACKAGE_BYTES = 256 * 1024


def _record(passport_type: str, name: str, source_path: str, command: str, client: str,
            version: str = UNKNOWN, publisher: str = UNKNOWN) -> dict[str, Any]:
    record: dict[str, Any] = {
        "passport_id": derive_passport_id(source_path, name, command, passport_type),
        "passport_type": passport_type,
        "tool_name": name,
        "source_path": source_path,
        "client": client,
        "command": command,
        "args": [],
        "env": {},
        "owner": UNKNOWN,
        "version": version,
        "publisher": publisher,
        "access_mode": UNKNOWN,
    }
    record["provenance_hash"] = HashEngine.hash_dict(
        {"source_path": source_path, "tool_name": name, "command": command})
    record["config_hash"] = HashEngine.hash_config({"command": command, "version": version})
    return record


def _declared(package: Path) -> tuple[str, str]:
    """(version, publisher) from an extension's package.json, else UNKNOWN."""
    try:
        with package.open("rb") as fh:
            raw = fh.read(_MAX_PACKAGE_BYTES + 1)
        if len(raw) > _MAX_PACKAGE_BYTES:
            return UNKNOWN, UNKNOWN
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError):
        return UNKNOWN, UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN, UNKNOWN
    version = data.get("version")
    publisher = data.get("publisher")
    ok = re.compile(r"^[\w.+-]{1,64}$")
    return (version if isinstance(version, str) and ok.match(version) else UNKNOWN,
            publisher if isinstance(publisher, str) and ok.match(publisher) else UNKNOWN)


def _version_key(folder: Path, prefix: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", folder.name[len(prefix):])
    return tuple(int(n) for n in nums[:4])


def scan_installed_clients(
    platform: str | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
    which: Callable[..., str | None] | None = None,
) -> dict[str, Any]:
    """Read-only scan for installed AI clients. Never raises for a bad file.

    ``which`` is injectable (defaults to ``shutil.which`` over ``env['PATH']``)
    so tests never depend on what the host has installed.
    """
    plat = _normalize_platform(platform)
    env = dict(os.environ if env is None else env)
    home_path = Path(home) if home is not None else Path.home()
    home_str = str(home_path)
    lookup = which or (lambda cmd: shutil.which(cmd, path=env.get("PATH")))
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(rec: dict[str, Any]) -> None:
        if rec["passport_id"] not in seen:
            seen.add(rec["passport_id"])
            found.append(rec)

    for cmd in CLI_COMMANDS:
        try:
            hit = lookup(cmd)
        except OSError:
            hit = None
        if hit:
            location = _mask_home(str(hit), home_str).replace("\\", "/")
            add(_record(CLI_TOOL, cmd, location, cmd, "PATH"))

    bases: list[tuple[str, Path | None]] = []
    if plat == "windows":
        local = env.get("LOCALAPPDATA")
        bases.append(("%LOCALAPPDATA%", Path(local) if local else None))
    elif plat == "macos":
        bases += [("/Applications", Path(env.get("AUTODISCOVER_APPLICATIONS_DIR", "/Applications"))),
                  ("~/Applications", home_path / "Applications")]
    for token, base in bases:
        if base is None:
            continue
        for name, parts in DESKTOP_APPS[plat]:
            path = base.joinpath(*parts)
            try:
                present = path.exists()
            except OSError:
                present = False
            if present:
                location = "/".join((token, *parts))
                add(_record(DESKTOP_APP, name, location, location, "install folder"))

    for editor in EDITORS:
        root = home_path / editor / "extensions"
        try:
            folders = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
        except OSError:
            folders = []
        for ident, label in EDITOR_EXTENSIONS.items():
            # Exact identifier match: "github.copilot-1.2.3" but not "github.copilot-chat-0.1".
            pattern = re.compile(rf"^{re.escape(ident)}-\d")
            matches = [f for f in folders if pattern.match(f.name.lower()) and (f / "package.json").is_file()]
            if not matches:
                continue
            newest = max(matches, key=lambda f: _version_key(f, ident))
            version, publisher = _declared(newest / "package.json")
            location = f"~/{editor}/extensions/{ident}"
            add(_record(EDITOR_EXTENSION, label, location, ident, editor.lstrip("."), version, publisher))

    counts = {t: sum(r["passport_type"] == t for r in found) for t in (CLI_TOOL, DESKTOP_APP, EDITOR_EXTENSION)}
    return {"platform": plat, "counts": counts, "candidates": found}
