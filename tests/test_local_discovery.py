"""
Real local machine discovery (Upgrade 1).

Every test points the scanner at a fake home under tmp_path and injects the
platform and environment, so the suite behaves identically on Linux, macOS
and Windows hosts and never reads the developer's real configs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine import (
    REDACTED,
    UNKNOWN,
    AutoDiscoverEngine,
    known_config_locations,
    scan_local_machine,
)

SECRET = "FAKE_local_DO_NOT_USE_q8w2"
USERNAME = "jury-demo-user"


def fake_machine(tmp_path: Path, platform: str) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "Users" / USERNAME
    home.mkdir(parents=True)
    env = {}
    if platform == "win32":
        env = {"APPDATA": str(home / "AppData" / "Roaming"), "USERPROFILE": str(home)}
    return home, env


def write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def claude_config(home: Path) -> dict:
    return {
        "mcpServers": {
            "postgres-mcp": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-postgres"],
                "env": {"POSTGRES_URL": f"postgresql://admin:{SECRET}@db.example:5432/prod"},
            },
            "filesystem-mcp": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", str(home / "Documents")],
            },
        }
    }


# ── Path resolution ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", {
            "claude-desktop": ("%APPDATA%/Claude/claude_desktop_config.json",
                               ("AppData", "Roaming", "Claude", "claude_desktop_config.json")),
            "cursor": ("%USERPROFILE%/.cursor/mcp.json", (".cursor", "mcp.json")),
            "vscode": ("%APPDATA%/Code/User/mcp.json", ("AppData", "Roaming", "Code", "User", "mcp.json")),
        }),
        ("darwin", {
            "claude-desktop": ("~/Library/Application Support/Claude/claude_desktop_config.json",
                               ("Library", "Application Support", "Claude", "claude_desktop_config.json")),
            "cursor": ("~/.cursor/mcp.json", (".cursor", "mcp.json")),
            "vscode": ("~/Library/Application Support/Code/User/mcp.json",
                       ("Library", "Application Support", "Code", "User", "mcp.json")),
        }),
        ("linux", {
            "claude-desktop": ("~/.config/Claude/claude_desktop_config.json",
                               (".config", "Claude", "claude_desktop_config.json")),
            "cursor": ("~/.cursor/mcp.json", (".cursor", "mcp.json")),
            "vscode": ("~/.config/Code/User/mcp.json", (".config", "Code", "User", "mcp.json")),
        }),
    ],
)
def test_platform_paths_resolve_under_injected_home(tmp_path: Path, platform: str, expected: dict) -> None:
    home, env = fake_machine(tmp_path, platform)
    locs = {loc["client"]: loc for loc in known_config_locations(platform, home, env)}
    assert set(expected) <= set(locs)
    for client, (label, parts) in expected.items():
        assert locs[client]["label"] == label
        assert locs[client]["path"] == home.joinpath(*parts)
        assert USERNAME not in locs[client]["label"]


def test_windows_without_appdata_is_unresolved_not_a_crash(tmp_path: Path) -> None:
    home, _ = fake_machine(tmp_path, "linux")
    summary = scan_local_machine("win32", home, env={})
    status = {r["client"]: r["status"] for r in summary["locations"]}
    assert {k: status[k] for k in ("claude-desktop", "cursor", "vscode")} == {
        "claude-desktop": "unresolved", "cursor": "missing", "vscode": "unresolved"}
    assert status["vscode-insiders"] == "unresolved"
    assert status["claude-code"] == "missing"


# ── Scanning ───────────────────────────────────────────────────────────────────

def test_clean_machine_returns_empty_summary(tmp_path: Path) -> None:
    home, env = fake_machine(tmp_path, "linux")
    summary = scan_local_machine("linux", home, env)
    assert summary["platform"] == "linux"
    assert summary["configs_found"] == 0
    assert summary["candidates"] == []
    assert {r["status"] for r in summary["locations"]} == {"missing"}


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_planted_config_is_redacted_honest_and_username_free(tmp_path: Path, platform: str) -> None:
    home, env = fake_machine(tmp_path, platform)
    claude = next(loc for loc in known_config_locations(platform, home, env) if loc["client"] == "claude-desktop")
    write(claude["path"], claude_config(home))

    summary = scan_local_machine(platform, home, env)
    assert summary["configs_found"] == 1
    cands = {c["tool_name"]: c for c in summary["candidates"]}
    assert set(cands) == {"postgres-mcp", "filesystem-mcp"}
    assert cands["postgres-mcp"]["env"] == {"POSTGRES_URL": REDACTED}
    assert all(c["owner"] == UNKNOWN and c["version"] == UNKNOWN for c in cands.values())
    assert all(c["client"] == "claude-desktop" for c in cands.values())
    assert cands["filesystem-mcp"]["args"][-1] in ("~/Documents", "~\\Documents")

    dumped = json.dumps(summary)
    assert SECRET not in dumped
    assert USERNAME not in dumped

    # Deterministic: a second scan yields the same passport IDs.
    again = scan_local_machine(platform, home, env)
    assert [c["passport_id"] for c in again["candidates"]] == [c["passport_id"] for c in summary["candidates"]]


def test_vscode_servers_key_is_parsed(tmp_path: Path) -> None:
    home, env = fake_machine(tmp_path, "linux")
    vscode = next(loc for loc in known_config_locations("linux", home, env) if loc["client"] == "vscode")
    write(vscode["path"], {
        "inputs": [],
        "servers": {
            "github-mcp": {"type": "http", "url": "https://api.example.com/mcp?token=abc123",
                           "headers": {"Authorization": "Bearer secret"}},
            "memory": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
        },
    })
    cands = {c["tool_name"]: c for c in scan_local_machine("linux", home, env)["candidates"]}
    assert set(cands) == {"github-mcp", "memory"}
    assert cands["github-mcp"]["command"] == UNKNOWN
    assert cands["github-mcp"]["url"] == f"https://api.example.com/mcp?token={REDACTED}"
    assert cands["github-mcp"]["headers"] == {"Authorization": REDACTED}
    assert cands["memory"]["client"] == "vscode"


def test_corrupt_and_unreadable_files_are_reported_not_raised(tmp_path: Path, monkeypatch) -> None:
    home, env = fake_machine(tmp_path, "linux")
    locs = {loc["client"]: loc["path"] for loc in known_config_locations("linux", home, env)}
    write(locs["claude-desktop"], "{ this is not json")
    locs["cursor"].mkdir(parents=True)               # a directory where a file should be
    write(locs["vscode"], {"servers": {"ok": {"command": "npx"}}})

    # Simulate an OS-level read failure (e.g. permissions) identically on every OS.
    real_open = Path.open

    def flaky(self: Path, *a, **kw):
        if self == locs["vscode"]:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", flaky)
    summary = scan_local_machine("linux", home, env)
    status = {r["client"]: r for r in summary["locations"]}
    assert status["claude-desktop"]["status"] == "invalid_json"
    assert status["cursor"]["status"] == "unreadable"
    assert status["vscode"]["status"] == "unreadable"
    assert status["vscode"]["error"] == "PermissionError: Permission denied"
    assert summary["candidates"] == []
    assert USERNAME not in json.dumps(summary)


def test_non_object_config_is_invalid_format(tmp_path: Path) -> None:
    home, env = fake_machine(tmp_path, "linux")
    cursor = next(loc for loc in known_config_locations("linux", home, env) if loc["client"] == "cursor")
    write(cursor["path"], ["not", "an", "object"])
    row = next(r for r in scan_local_machine("linux", home, env)["locations"] if r["client"] == "cursor")
    assert row["status"] == "invalid_format"


# ── Isolated registry ──────────────────────────────────────────────────────────

def test_real_mode_minting_writes_only_to_isolated_store(tmp_path: Path) -> None:
    home, env = fake_machine(tmp_path, "darwin")
    claude = next(loc for loc in known_config_locations("darwin", home, env) if loc["client"] == "claude-desktop")
    write(claude["path"], claude_config(home))

    demo_dir = tmp_path / "data"
    demo_passports = demo_dir / "passports.json"
    write(demo_passports, {"demo-passport": {"tool_name": "untouched"}})
    before = demo_passports.read_bytes()

    local = AutoDiscoverEngine(data_dir=demo_dir / "local", base_dir=tmp_path)
    summary = local.scan_local_machine("darwin", home, env)
    ids = {local.mint_passport(c) for c in summary["candidates"]}

    assert demo_passports.read_bytes() == before
    stored = json.loads((demo_dir / "local" / "passports.json").read_text())
    assert set(stored) == ids and len(ids) == 2
    assert all(p["source_path"].startswith("~/Library/Application Support/Claude/") for p in stored.values())

    # Restart recognition holds in real mode too.
    rescanned = AutoDiscoverEngine(data_dir=demo_dir / "local", base_dir=tmp_path).scan_local_machine("darwin", home, env)
    assert all(c["known"] and not c["metadata_changed"] for c in rescanned["candidates"])
