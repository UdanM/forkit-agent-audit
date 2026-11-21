"""Scripted LangGraph agent (no LLM) that works through a real MCP filesystem server.

The graph has fixed steps: list the folder, read the report, write a summary. It connects to the
server exactly as the client config says, so every call passes through the AutoDiscover tap and is
captured. Requires `langgraph` (pip install -e ".[langgraph]").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from demo.agents.mcp_client import MCPClient


class State(TypedDict, total=False):
    steps: list[dict[str, Any]]


def build_graph(client: MCPClient, read_path: str | None, write_path: str | None):
    def call(state: State, tool: str, args: dict[str, Any]) -> State:
        out = client.call(tool, args)
        return {"steps": [*state.get("steps", []), {"tool": tool, "ok": out["ok"], "text": out["text"][:120]}]}

    g = StateGraph(State)
    g.add_node("list", lambda s: call(s, "list_allowed_directories", {}))
    g.add_node("read", lambda s: call(s, "read_text_file", {"path": read_path}) if read_path else s)
    g.add_node("write", lambda s: call(s, "write_file", {
        "path": write_path, "content": "Summary drafted by the demo agent (synthetic)."}) if write_path else s)
    g.set_entry_point("list")
    g.add_edge("list", "read")
    g.add_edge("read", "write")
    g.add_edge("write", END)
    return g.compile()


def run(config_file: str | Path, server: str, cwd: str | Path, read_path: str | None = None,
        write_path: str | None = None) -> list[dict[str, Any]]:
    # The filesystem server resolves relative paths against its allowed directory, so send absolute ones.
    read_path = str(Path(cwd, read_path).resolve()) if read_path else None
    write_path = str(Path(cwd, write_path).resolve()) if write_path else None
    client = MCPClient.from_config(config_file, server, cwd)
    try:
        return build_graph(client, read_path, write_path).invoke({"steps": []})["steps"]
    finally:
        client.close()


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[2]
    for step in run(sys.argv[1], sys.argv[2], root, *sys.argv[3:5]):
        print(step)
