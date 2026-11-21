"""Read-only discovery rules for known local AI clients."""

import os
import shutil
from pathlib import Path

COMMANDS = ("codex", "claude", "ollama", "gemini", "aider", "opencode",
            "cursor", "goose", "kiro", "amazon-q", "copilot")
WINDOWS_APPS = {
    "cursor": ("Programs/Cursor/Cursor.exe",),
    "windsurf": ("Programs/Windsurf/Windsurf.exe",),
    "lm-studio": ("Programs/LM Studio/LM Studio.exe",),
    "ollama": ("Programs/Ollama/ollama.exe",),
}
EDITOR_EXTENSIONS = {
    "github.copilot": "GitHub Copilot",
    "anthropic.claude-code": "Claude Code",
    "openai.chatgpt": "Codex",
    "rooveterinaryinc.roo-cline": "Roo Code",
    "saoudrizwan.claude-dev": "Cline",
    "continue.continue": "Continue",
}


def local_clients():
    for name in COMMANDS:
        command = shutil.which(name)
        if command:
            yield name, Path(command), "Local PATH"
    local = Path.home() / "AppData/Local" if os.name == "nt" else None
    if local:
        for name, candidates in WINDOWS_APPS.items():
            for relative in candidates:
                path = local / relative
                if path.is_file():
                    yield name, path, "Installed app folder"


def editor_extensions():
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        root = Path.home() / editor / "extensions"
        for identifier, name in EDITOR_EXTENSIONS.items():
            if any(path.is_file() for path in root.glob(identifier + "-*/package.json")):
                yield name, root / identifier, editor.removeprefix(".")


def local_configs(workspaces):
    home = Path.home()
    roaming = os.environ.get("APPDATA")
    candidates = [
        ("Cursor", home / ".cursor/mcp.json"),
        ("Claude Code", home / ".claude.json"),
        ("Windsurf", home / ".codeium/windsurf/mcp_config.json"),
        ("Codex", Path(os.environ.get("CODEX_HOME") or home / ".codex") / "config.toml"),
    ]
    if roaming:
        for client in ("Code", "Code - Insiders"):
            root = Path(roaming) / client / "User"
            candidates.append((client, root / "mcp.json"))
            candidates.extend((client + " profile", path)
                              for path in sorted((root / "profiles").glob("*/mcp.json")))
        candidates.append(("Claude Desktop", Path(roaming) / "Claude/claude_desktop_config.json"))
    for index, root in enumerate(workspaces, 1):
        candidates.extend((f"Workspace {index} · {name}", root / relative) for name, relative in (
            ("VS Code", ".vscode/mcp.json"), ("Cursor", ".cursor/mcp.json"),
            ("Claude Code", ".mcp.json"), ("Codex", ".codex/config.toml"),
        ))
    return [(name, path) for name, path in candidates if path.is_file()]
