"""MCP stdio tap: sits between an MCP client and a real MCP server and records every tool call.

Put it in front of any stdio MCP server in a client config:

    "command": "python",
    "args": ["-m", "src.capture.mcp_tap", "--client", "claude-desktop",
             "--config", "demo/agents/configs/claude_desktop_config.json", "--server", "files-local",
             "--log", "data/agents/captured_events.jsonl", "--read-only",
             "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "demo/agents/workspace"]

Messages are passed through unchanged. For each ``tools/call`` the tap writes one metadata-only
event (tool, redacted target, result, time). With ``--read-only`` it refuses mutating calls itself
and answers the client with a JSON-RPC error, so the read-only promise in the config is enforced,
not just declared; the refused call is still recorded (result ``denied``).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from typing import Any, BinaryIO

from ..engine import _is_mutating
from .events import append_event, build_event

DENIED_CODE = -32001


class Tap:
    def __init__(self, args: argparse.Namespace, out: BinaryIO) -> None:
        self.args = args
        self.out = out
        self.pending: dict[Any, tuple[str, dict[str, Any]]] = {}
        self.lock = threading.Lock()

    def _send(self, message: dict[str, Any] | bytes) -> None:
        data = message if isinstance(message, bytes) else (json.dumps(message) + "\n").encode("utf-8")
        with self.lock:
            self.out.write(data)
            self.out.flush()

    def _record(self, tool: str, arguments: dict[str, Any], result: str) -> None:
        a = self.args
        append_event(a.log, build_event(
            origin="local-tap", client=a.client, config_path=a.config, server_name=a.server,
            tool_call=f"mcp.{tool}", result=result, arguments=arguments, agent=a.agent,
            base=getattr(a, "base", None) or os.getcwd(),
        ))

    def from_client(self, line: bytes) -> bytes | None:
        """Inspect one client→server line. Returns the bytes to forward, or None if answered here."""
        try:
            msg = json.loads(line)
        except ValueError:
            return line
        if not isinstance(msg, dict) or msg.get("method") != "tools/call":
            return line
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        tool = str(params.get("name") or "UNKNOWN")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if self.args.read_only and _is_mutating(tool):
            self._record(tool, arguments, "denied")
            self._send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                "code": DENIED_CODE, "message": f"Blocked by AutoDiscover tap: {tool} is not allowed on a read-only server"}})
            return None
        if "id" in msg:
            self.pending[msg["id"]] = (tool, arguments)
        return line

    def from_server(self, line: bytes) -> bytes:
        try:
            msg = json.loads(line)
        except ValueError:
            return line
        if isinstance(msg, dict) and "id" in msg and msg["id"] in self.pending and "method" not in msg:
            tool, arguments = self.pending.pop(msg["id"])
            res = msg.get("result")
            ok = "error" not in msg and not (isinstance(res, dict) and res.get("isError"))
            self._record(tool, arguments, "success" if ok else "error")
        return line


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--" not in argv:
        raise SystemExit("usage: mcp_tap [options] -- <server command ...>")
    i = argv.index("--")
    p = argparse.ArgumentParser(prog="mcp_tap")
    p.add_argument("--client", required=True)
    p.add_argument("--config", required=True, help="config file path as AutoDiscover stores it (source_path)")
    p.add_argument("--server", required=True, help="server name in that config")
    p.add_argument("--log", default=os.environ.get("AUTODISCOVER_CAPTURE_LOG", "data/agents/captured_events.jsonl"))
    p.add_argument("--agent", default=None)
    p.add_argument("--read-only", action="store_true")
    ns = p.parse_args(argv[:i])
    if not argv[i + 1:]:
        raise SystemExit("mcp_tap: missing server command after --")
    return ns, argv[i + 1:]


def main(argv: list[str] | None = None) -> int:
    ns, command = parse(list(sys.argv[1:] if argv is None else argv))
    if command[0] in ("python", "python3"):
        command = [sys.executable, *command[1:]]     # same interpreter as the tap, on any OS
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    tap = Tap(ns, sys.stdout.buffer)

    def pump_server() -> None:
        assert child.stdout is not None
        for line in iter(child.stdout.readline, b""):
            tap._send(tap.from_server(line))

    t = threading.Thread(target=pump_server, daemon=True)
    t.start()
    assert child.stdin is not None
    try:
        for line in iter(sys.stdin.buffer.readline, b""):
            fwd = tap.from_client(line)
            if fwd is not None:
                child.stdin.write(fwd)
                child.stdin.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
        code = child.wait()
        t.join(timeout=2)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
