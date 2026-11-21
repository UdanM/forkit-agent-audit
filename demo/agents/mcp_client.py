"""Minimal MCP stdio client: launches a server exactly as an MCP config declares it.

Stdlib only. Enough of the protocol for a scripted agent: initialize, tools/list, tools/call.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class MCPClient:
    def __init__(self, command: str, args: list[str], cwd: str | Path) -> None:
        if command in ("python", "python3"):
            command = sys.executable          # same interpreter as the agent, on any OS
        self.proc = subprocess.Popen([command, *args], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self._id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "autodiscover-demo-agent", "version": "1"}})
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @classmethod
    def from_config(cls, config_file: str | Path, server: str, cwd: str | Path) -> MCPClient:
        spec = json.loads(Path(config_file).read_text(encoding="utf-8"))["mcpServers"][server]
        return cls(spec["command"], list(spec.get("args", [])), cwd)

    def _write(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        self._write({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server closed during {method}")
            msg = json.loads(line)
            if msg.get("id") == self._id and "method" not in msg:
                return msg

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Returns {'ok': bool, 'text': str} without raising on tool errors."""
        msg = self.request("tools/call", {"name": tool, "arguments": arguments})
        if "error" in msg:
            return {"ok": False, "text": str(msg["error"].get("message", ""))}
        res = msg.get("result", {})
        text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict))
        return {"ok": not res.get("isError", False), "text": text}

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
