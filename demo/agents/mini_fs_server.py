"""Offline stand-in for @modelcontextprotocol/server-filesystem (stdio MCP, stdlib only).

Used by the tests, and by the scenario runner when ``npx`` is not available. It speaks the same
protocol and tool names as the real server for the four tools the demo agent uses, and refuses any
path outside the allowed directory.

    python -m demo.agents.mini_fs_server <allowed_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS = ("list_allowed_directories", "list_directory", "read_text_file", "write_file")


def _inside(root: Path, raw: str) -> Path:
    p = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if p != root and root not in p.parents:
        raise PermissionError(f"Access denied - path outside allowed directories: {raw}")
    return p


def handle(root: Path, msg: dict[str, Any]) -> dict[str, Any] | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None                                   # notification
    if method == "initialize":
        result: dict[str, Any] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "mini-fs", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": t, "inputSchema": {"type": "object"}} for t in TOOLS]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name == "list_allowed_directories":
                text = f"Allowed directories:\n{root}"
            elif name == "list_directory":
                text = "\n".join(f"[{'DIR' if c.is_dir() else 'FILE'}] {c.name}"
                                 for c in sorted(_inside(root, args.get("path", ".")).iterdir()))
            elif name == "read_text_file":
                text = _inside(root, args["path"]).read_text(encoding="utf-8")
            elif name == "write_file":
                target = _inside(root, args["path"])
                target.write_text(str(args.get("content", "")), encoding="utf-8")
                text = f"Successfully wrote to {args['path']}"
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown tool {name}"}}
            result = {"content": [{"type": "text", "text": text}]}
        except (OSError, KeyError, PermissionError) as exc:
            result = {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]).resolve()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(root, msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
