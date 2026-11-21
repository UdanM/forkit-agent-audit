"""
Installed AI clients (merge M2): CLIs on PATH, desktop apps, editor
extensions. Client lists ported from UdanM/forkit-agent-audit @ 3f5b2aa.
All lookups are injected, so nothing depends on the host.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.engine import (
    CLI_TOOL,
    DESKTOP_APP,
    EDITOR_EXTENSION,
    UNKNOWN,
    AutoDiscoverEngine,
    derive_passport_id,
    passport_id_of,
)
from src.installed import scan_installed_clients

USERNAME = "jury-demo-user"


def home_of(tmp_path: Path) -> Path:
    home = tmp_path / "Users" / USERNAME
    home.mkdir(parents=True)
    return home


def fake_which(home: Path, names: dict[str, str]):
    def which(cmd: str) -> str | None:
        rel = names.get(cmd)
        return str(home / rel) if rel else None
    return which


def ext(home: Path, editor: str, folder: str, package: object) -> None:
    d = home / editor / "extensions" / folder
    d.mkdir(parents=True)
    (d / "package.json").write_text(package if isinstance(package, str) else json.dumps(package))


def test_clean_machine_finds_nothing(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    out = scan_installed_clients("linux", home, {}, which=lambda c: None)
    assert out["candidates"] == []


def test_cli_on_path_is_a_username_free_cli_passport(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    which = fake_which(home, {"claude": ".local/bin/claude", "ollama": ".local/bin/ollama"})
    cands = {c["tool_name"]: c for c in scan_installed_clients("linux", home, {}, which=which)["candidates"]}
    assert set(cands) == {"claude", "ollama"}
    c = cands["claude"]
    assert c["passport_type"] == CLI_TOOL
    assert c["source_path"] == "~/.local/bin/claude"
    assert c["owner"] == UNKNOWN and c["version"] == UNKNOWN and c["access_mode"] == UNKNOWN
    assert passport_id_of(c) == c["passport_id"]
    assert USERNAME not in json.dumps(cands)


def test_cli_never_merges_with_same_named_mcp_server(tmp_path: Path) -> None:
    mcp = derive_passport_id("~/.local/bin/claude", "claude", "claude")
    cli = derive_passport_id("~/.local/bin/claude", "claude", "claude", CLI_TOOL)
    assert mcp != cli


def test_unknown_passport_type_is_refused() -> None:
    try:
        derive_passport_id("x", "y", "z", "made_up")
    except ValueError:
        return
    raise AssertionError("made-up passport_type accepted")


def test_cli_id_is_machine_independent(tmp_path: Path) -> None:
    ids = []
    for who in ("a", "b"):
        home = home_of(tmp_path / who)
        which = fake_which(home, {"codex": ".local/bin/codex"})
        ids.append(scan_installed_clients("linux", home, {}, which=which)["candidates"][0]["passport_id"])
    assert ids[0] == ids[1]


def test_windows_and_macos_desktop_apps(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    local = home / "AppData" / "Local"
    (local / "Programs" / "Cursor").mkdir(parents=True)
    (local / "Programs" / "Cursor" / "Cursor.exe").write_bytes(b"")
    win = scan_installed_clients("win32", home, {"LOCALAPPDATA": str(local)}, which=lambda c: None)
    (cursor,) = win["candidates"]
    assert cursor["passport_type"] == DESKTOP_APP
    assert cursor["source_path"] == "%LOCALAPPDATA%/Programs/Cursor/Cursor.exe"

    apps = tmp_path / "Applications"
    (apps / "Claude.app").mkdir(parents=True)
    mac = scan_installed_clients("darwin", home, {"AUTODISCOVER_APPLICATIONS_DIR": str(apps)}, which=lambda c: None)
    assert [(c["tool_name"], c["source_path"]) for c in mac["candidates"]] == [("Claude", "/Applications/Claude.app")]


def test_windows_without_localappdata_skips_apps(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    assert scan_installed_clients("win32", home, {}, which=lambda c: None)["candidates"] == []


def test_extension_exact_match_newest_version_and_declared_facts(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    ext(home, ".vscode", "github.copilot-1.9.0", {"version": "1.9.0", "publisher": "GitHub"})
    ext(home, ".vscode", "github.copilot-1.10.2", {"version": "1.10.2", "publisher": "GitHub"})
    ext(home, ".vscode", "github.copilot-chat-0.20.0", {"version": "0.20.0", "publisher": "GitHub"})
    cands = {c["tool_name"]: c for c in scan_installed_clients("linux", home, {}, which=lambda c: None)["candidates"]}
    assert set(cands) == {"GitHub Copilot", "GitHub Copilot Chat"}
    cop = cands["GitHub Copilot"]
    assert cop["passport_type"] == EDITOR_EXTENSION
    assert cop["source_path"] == "~/.vscode/extensions/github.copilot"
    assert cop["version"] == "1.10.2" and cop["publisher"] == "GitHub"
    assert cop["owner"] == UNKNOWN


def test_bad_package_json_gives_unknown_not_a_crash(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    ext(home, ".cursor", "continue.continue-0.9.1", "{ not json")
    (cand,) = scan_installed_clients("linux", home, {}, which=lambda c: None)["candidates"]
    assert cand["version"] == UNKNOWN and cand["publisher"] == UNKNOWN
    assert cand["client"] == "cursor"


def test_extension_upgrade_is_drift_on_same_passport(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    ext(home, ".vscode", "anthropic.claude-code-2.0.0", {"version": "2.0.0", "publisher": "Anthropic"})
    eng = AutoDiscoverEngine(tmp_path / "data", approvals_path=tmp_path / "none.json")
    first = next(c for c in eng.scan_local_machine("linux", home, {}, include_clients=True,
                                                   which=lambda c: None)["candidates"])
    eng.mint_passport(first)
    ext(home, ".vscode", "anthropic.claude-code-2.1.0", {"version": "2.1.0", "publisher": "Anthropic"})
    second = next(c for c in eng.scan_local_machine("linux", home, {}, include_clients=True,
                                                    which=lambda c: None)["candidates"])
    assert second["passport_id"] == first["passport_id"]
    assert second["metadata_changed"] is True
    assert "version: '2.0.0' -> '2.1.0'" in second["diff"]


def test_installed_passports_pass_audit_integrity(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    which = fake_which(home, {"gemini": ".local/bin/gemini"})
    eng = AutoDiscoverEngine(tmp_path / "data", approvals_path=tmp_path / "none.json")
    summary = eng.scan_local_machine("linux", home, {}, include_clients=True, which=which)
    assert summary["installed_counts"][CLI_TOOL] == 1
    for c in summary["candidates"]:
        eng.mint_passport(c)
    report = eng.build_audit_report(candidates=summary["candidates"])
    assert report["integrity"]["status"] == "PASS"


def test_scan_without_flag_has_no_clients(tmp_path: Path) -> None:
    home = home_of(tmp_path)
    which = fake_which(home, {"gemini": ".local/bin/gemini"})
    eng = AutoDiscoverEngine(tmp_path / "data", approvals_path=tmp_path / "none.json")
    assert eng.scan_local_machine("linux", home, {}, which=which)["candidates"] == []
