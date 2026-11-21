"""
Wider discovery (merge M1): Claude Code, Windsurf, Codex TOML, VS Code
Insiders and profiles, per-workspace configs, and observed transport and
enabled state. Breadth ported from UdanM/forkit-agent-audit @ 3f5b2aa.

Every test uses a fake home under tmp_path with an injected platform and env.
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
    workspace_config_locations,
)

USERNAME = "jury-demo-user"
SECRET = "FAKE_wide_DO_NOT_USE_z7"


def machine(tmp_path: Path, platform: str = "linux") -> tuple[Path, dict[str, str]]:
    home = tmp_path / "Users" / USERNAME
    home.mkdir(parents=True)
    env: dict[str, str] = {}
    if platform == "win32":
        env = {"APPDATA": str(home / "AppData" / "Roaming"), "USERPROFILE": str(home)}
    return home, env


def write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def loc(platform: str, home: Path, env: dict, client: str) -> dict:
    return next(x for x in known_config_locations(platform, home, env) if x["client"] == client)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_new_clients_have_username_free_labels(tmp_path: Path, platform: str) -> None:
    home, env = machine(tmp_path, platform)
    clients = {x["client"]: x for x in known_config_locations(platform, home, env)}
    assert {"claude-code", "windsurf", "codex", "vscode-insiders"} <= set(clients)
    for x in clients.values():
        assert USERNAME not in x["label"]
    assert clients["codex"]["label"].endswith("/.codex/config.toml")


def test_codex_home_override_is_labelled_by_token(tmp_path: Path) -> None:
    home, _ = machine(tmp_path)
    env = {"CODEX_HOME": str(home / "custom-codex")}
    codex = loc("linux", home, env, "codex")
    assert codex["label"] == "%CODEX_HOME%/config.toml"
    assert codex["path"] == home / "custom-codex" / "config.toml"


def test_codex_toml_is_parsed_and_redacted(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "codex")["path"], f'''
model = "o4"
[mcp_servers.docs]
command = "npx"
args = ["-y", "docs-mcp"]
env = {{ API_KEY = "{SECRET}" }}
enabled = false
''')
    summary = scan_local_machine("linux", home, env)
    (cand,) = summary["candidates"]
    assert cand["tool_name"] == "docs" and cand["client"] == "codex"
    assert cand["env"] == {"API_KEY": REDACTED}
    assert cand["transport"] == "stdio"
    assert cand["enabled"] is False
    assert SECRET not in json.dumps(summary)


def test_invalid_toml_is_reported_not_raised(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "codex")["path"], "[mcp_servers.x\ncommand = ")
    row = next(r for r in scan_local_machine("linux", home, env)["locations"] if r["client"] == "codex")
    assert row["status"] == "invalid_json"
    assert USERNAME not in json.dumps(row)


def test_claude_code_user_and_project_servers_stay_separate(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "claude-code")["path"], {
        "mcpServers": {"search": {"command": "npx", "args": ["search-mcp"]}},
        "projects": {
            str(home / "code" / "app"): {"mcpServers": {"search": {"command": "npx", "args": ["search-mcp"]}}},
            str(home / "code" / "empty"): {"mcpServers": {}},
        },
    })
    cands = scan_local_machine("linux", home, env)["candidates"]
    assert [c["source_path"] for c in cands] == [
        "~/.claude.json", "~/.claude.json#projects/~/code/app"]
    assert len({c["passport_id"] for c in cands}) == 2, "same name in two scopes must not merge"
    assert USERNAME not in json.dumps(cands)


def test_windsurf_server_url_is_http_and_redacted(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "windsurf")["path"], {
        "mcpServers": {"remote": {"serverUrl": f"https://mcp.example/sse?token={SECRET}"}}})
    (cand,) = scan_local_machine("linux", home, env)["candidates"]
    assert cand["transport"] == "http"
    assert SECRET not in cand["url"]
    assert cand["enabled"] == UNKNOWN


def test_vscode_profiles_are_discovered(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    user = loc("linux", home, env, "vscode")["path"].parent
    write(user / "profiles" / "a1b2c3" / "mcp.json", {"servers": {"git": {"type": "stdio", "command": "uvx"}}})
    prof = loc("linux", home, env, "vscode-profile")
    assert prof["label"] == "~/.config/Code/User/profiles/a1b2c3/mcp.json"
    (cand,) = scan_local_machine("linux", home, env)["candidates"]
    assert cand["client"] == "vscode-profile" and cand["transport"] == "stdio"


def test_workspace_configs_use_folder_name_and_never_merge(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    ws1 = tmp_path / "a" / "proj"
    ws2 = tmp_path / "b" / "proj"
    for ws in (ws1, ws2):
        write(ws / ".mcp.json", {"mcpServers": {"fs": {"command": "npx"}}})
    labels = [x["label"] for x in workspace_config_locations([ws1, ws2])]
    assert "<workspace:proj>/.mcp.json" in labels and "<workspace:proj#2>/.mcp.json" in labels
    cands = scan_local_machine("linux", home, env, workspaces=[ws1, ws2])["candidates"]
    assert len(cands) == 2 and len({c["passport_id"] for c in cands}) == 2
    assert all(str(tmp_path) not in json.dumps(c) for c in cands)


def test_workspace_id_is_machine_independent(tmp_path: Path) -> None:
    ids = []
    for where in ("machine-a", "machine-b"):
        home, env = machine(tmp_path / where)
        ws = tmp_path / where / "checkout" / "proj"
        write(ws / ".vscode" / "mcp.json", {"servers": {"db": {"command": "npx"}}})
        ids.append(scan_local_machine("linux", home, env, workspaces=[ws])["candidates"][0]["passport_id"])
    assert ids[0] == ids[1]


def test_disabling_a_server_is_drift_on_the_same_passport(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    cfg = loc("linux", home, env, "cursor")["path"]
    write(cfg, {"mcpServers": {"fs": {"command": "npx", "disabled": False}}})
    eng = AutoDiscoverEngine(tmp_path / "data", approvals_path=tmp_path / "none.json")
    (first,) = eng.scan_local_machine("linux", home, env)["candidates"]
    eng.mint_passport(first)
    write(cfg, {"mcpServers": {"fs": {"command": "npx", "disabled": True}}})
    (second,) = eng.scan_local_machine("linux", home, env)["candidates"]
    assert second["passport_id"] == first["passport_id"]
    assert second["metadata_changed"] is True
    assert "enabled: True -> False" in second["diff"]


def test_configs_without_enabled_flag_keep_their_old_config_hash(tmp_path: Path) -> None:
    from forkit.domain.hashing import HashEngine
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "cursor")["path"], {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}})
    (cand,) = scan_local_machine("linux", home, env)["candidates"]
    legacy = HashEngine.hash_config({"command": "npx", "args": ["x"], "env": {}, "url": None, "headers": None})
    assert cand["config_hash"] == legacy


def test_oversized_config_is_refused(tmp_path: Path) -> None:
    home, env = machine(tmp_path)
    write(loc("linux", home, env, "cursor")["path"], '{"mcpServers": {}, "pad": "' + "x" * (1024 * 1024) + '"}')
    row = next(r for r in scan_local_machine("linux", home, env)["locations"] if r["client"] == "cursor")
    assert row["status"] == "invalid_format"
