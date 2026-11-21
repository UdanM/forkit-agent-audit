import argparse
import hashlib
import json
import re
try:
    import tomllib
except ImportError:
    tomllib = None
import sqlite3
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from secrets import token_urlsafe

from audit_repo import read_archive
from discovery import editor_extensions, local_clients, local_configs
from local_ask import ask_local
from timeline import build_timeline, stable_key

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scan(demo, changed, configs, workspaces=()):
    observations, issues = {}, []
    if demo:
        sources = {"editor": FIXTURES / "editor/mcp.json",
                   "desktop": FIXTURES / "desktop/mcp.json"}
        if changed:
            sources["desktop"] = FIXTURES / "desktop/changed.json"
        sources = list(sources.items())
        commands = [("codex", Path("fixture/codex"), "Fixture PATH")]
    else:
        sources = [(f"Local config {index}", path) for index, path in enumerate(configs, 1)]
        sources.extend(local_configs(workspaces))
        commands = local_clients()

    def add(identity, name, kind, source, **metadata):
        observations[digest(identity)] = {
            "name": name, "kind": kind, "source": source,
            "owner": None, "capabilities": None, **metadata
        }

    for name, path, source in commands:
        locator = path.as_posix() if demo else str(path.resolve())
        key = ["cli", locator, name]
        if digest(key) not in observations:
            add(key, name, "CLI" if source != "Installed app folder" else "App", source)
    if not demo:
        for name, slot, editor in editor_extensions():
            add(["extension", str(slot), name], name, "Extension", editor)
    seen_configs = set()
    for label, path in sources:
        try:
            locator = label if demo else str(path.resolve())
            if locator in seen_configs:
                continue
            seen_configs.add(locator)
            with path.open("rb") as config:
                data = config.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError()
            if path.suffix.lower() == ".toml":
                if tomllib is None:
                    issues.append("Codex config scanning requires Python 3.11 or newer.")
                    continue
                entries = tomllib.loads(data.decode("utf-8-sig")).get("mcp_servers", {})
            else:
                config = json.loads(data.decode("utf-8-sig"))
                if not isinstance(config, dict):
                    raise ValueError()
                entries = config.get("mcpServers", config.get("servers", {}))
            if not isinstance(entries, dict):
                raise ValueError()
            for name, value in entries.items():
                if not isinstance(value, dict):
                    issues.append(f"{label} contains an unreadable server entry.")
                    continue
                has_command = isinstance(value.get("command"), str) and bool(value["command"])
                has_url = isinstance(value.get("url"), str) and bool(value["url"])
                transport = "stdio" if has_command and not has_url else None
                if has_url and not has_command:
                    transport = "HTTP-based"
                disabled = value.get("disabled")
                enabled = not disabled if isinstance(disabled, bool) else None
                if isinstance(value.get("enabled"), bool):
                    enabled = value["enabled"]
                add(["mcp", locator, name], name, "MCP", label,
                    transport=transport, enabled=enabled)
        except (OSError, ValueError, KeyError, TypeError):
            issues.append(f"{label} could not be read as an MCP config.")
    return observations, issues


class Inventory:
    def __init__(self, database, demo, configs=(), *, audit_log=None,
                 checkpoint=None, agent_log=None, watch_dirs=(), watch_git=(), workspaces=(),
                 audit_repo=None, ask_model=None):
        self.db = sqlite3.connect(database)
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        self.demo = demo
        self.workspaces = workspaces
        self.audit_repo = audit_repo
        self.ask_model = ask_model
        self.configs = [Path(path) for path in configs]
        self.events = json.loads((FIXTURES / "events.json").read_text()) if demo else []
        self.timeline_options = {
            "demo": demo, "fixtures": FIXTURES, "audit_log": audit_log,
            "agent_log": agent_log, "checkpoint": checkpoint,
            "watch_dirs": list(dict.fromkeys(path.absolute() for path in watch_dirs)),
            "watch_git": list(dict.fromkeys(path.absolute() for path in watch_git))
        }
        self.timeline_key = "timeline_" + stable_key(
            demo, str(audit_log), str(agent_log), checkpoint,
            [str(path) for path in self.timeline_options["watch_dirs"]],
            [str(path) for path in self.timeline_options["watch_git"]]
        )

    def get(self, key, default):
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def discover(self, changed=False):
        observations, issues = scan(self.demo, changed, self.configs, self.workspaces)
        tools = self.get("tools", {})
        for key, tool in tools.items():
            tool["seen"] = key in observations
        for key, metadata in observations.items():
            existing = tools.get(key, {})
            before = existing.get("metadata", metadata)
            previous = dict(existing.get("changes", {}))
            for field in metadata:
                if field != "source" and before.get(field) != metadata[field]:
                    previous[field] = {"before": before.get(field), "after": metadata[field]}
            tools[key] = {"id": existing.get("id"), "metadata": metadata,
                          "changes": previous, "seen": True}
        with self.db:
            self.put("tools", tools)
            self.put("scan", {"timestamp": now(), "issues": issues})
        return self.state()

    def candidates(self, event, tools):
        return [key for key, tool in tools.items()
                if tool["seen"] and tool["metadata"]["kind"] == "MCP"
                and tool["metadata"]["name"] == event["name"]
                and (event["config"] is None or tool["metadata"]["source"] == event["config"])]

    def state(self):
        tools = self.get("tools", {})
        events = [{**event, "candidates": self.candidates(event, tools)}
                  for event in self.events]
        return {"tools": [{"key": key, **tool} for key, tool in tools.items()],
                "events": events, "links": self.get("links", {}),
                "incidents": self.get("incidents", {}), "scan": self.get("scan", None),
                "timeline": self.get(self.timeline_key, None),
                "archive": read_archive(self.audit_repo, FIXTURES / "audit-repo.jsonl" if self.demo else None),
                "ask_model": bool(self.ask_model),
                "timeline_configured": self.demo or any(self.timeline_options[key] for key in
                    ("audit_log", "agent_log", "watch_dirs", "watch_git")),
                "demo": self.demo}

    def refresh_timeline(self):
        timeline, snapshots, history = build_timeline(
            tools=self.get("tools", {}),
            previous_snapshots=self.get(self.timeline_key + "_snapshots", {}),
            snapshot_history=self.get(self.timeline_key + "_history", []),
            **self.timeline_options,
        )
        previous = self.get(self.timeline_key, {}) or {}
        known = {agent["id"]: agent for agent in previous.get("agents", [])}
        for agent in timeline["agents"]:
            if agent["last_seen"] >= known.get(agent["id"], {}).get("last_seen", ""):
                known[agent["id"]] = agent
        timeline["agents"] = sorted(known.values(), key=lambda agent: agent["last_seen"], reverse=True)
        with self.db:
            self.put(self.timeline_key, timeline)
            self.put(self.timeline_key + "_snapshots", snapshots)
            self.put(self.timeline_key + "_history", history)
        return self.state()

    def mutate(self, action, request):
        if action == "ask" and self.ask_model:
            return {"model_answer": ask_local(self.ask_model, request)}
        if action == "timeline":
            return self.refresh_timeline()
        if action in ("scan", "changed"):
            if action == "changed" and not self.demo:
                raise ValueError("Fixture changes require demo mode.")
            return self.discover(action == "changed")
        tools = self.get("tools", {})
        key = request.get("key")
        if action == "register" and isinstance(key, str) and key in tools and tools[key]["seen"]:
            tools[key]["id"] = "tool_" + key
            with self.db:
                self.put("tools", tools)
            return self.state()
        event = next((event for event in self.events if event["id"] == request.get("event")), None)
        if action == "link" and event:
            if request.get("consent") is not True:
                raise ValueError("Linking requires reviewer consent.")
            if (event["config"] is None or not isinstance(key, str)
                    or self.candidates(event, tools) != [key] or not tools[key]["id"]):
                raise ValueError("A unique evidence match and a registered tool are required.")
            links = self.get("links", {})
            links.setdefault(event["id"], {"tool_id": tools[key]["id"], "consented_at": now(),
                                           "source": event["source"], "timestamp": event["timestamp"],
                                           "approval": event["approval"], "matched_config": event["config"]})
            with self.db:
                self.put("links", links)
            return self.state()
        if action == "incident" and event and event.get("expected"):
            incidents = self.get("incidents", {})
            incidents.setdefault(event["id"], {"recorded_at": now(),
                                               **{field: event[field] for field in
                                                  ("source", "timestamp", "action", "expected", "approval")}})
            with self.db:
                self.put("incidents", incidents)
            return self.state()
        raise ValueError("Action or candidate unavailable.")

    def export(self):
        return {"schema_version": 1, "scan": self.get("scan", None),
                "tools": list(self.get("tools", {}).values())}


def serve(inventory, port, open_browser=False):
    token = token_urlsafe(32)
    sample = inventory if inventory.demo else Inventory(":memory:", True)

    class Handler(BaseHTTPRequestHandler):
        def send(self, body, kind="application/json", status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

        def route(self):
            if self.path.startswith("/demo/"):
                return sample, self.path.removeprefix("/demo"), "/demo"
            return inventory, self.path, ""

        def do_GET(self):
            if self.headers.get("Host") != address:
                return self.send({"error": "Invalid host."}, status=403)
            active, path, prefix = self.route()
            if path == "/":
                page = ((ROOT / "index.html").read_bytes()
                        .replace(b"__TOKEN__", token.encode())
                        .replace(b"__BASE__", prefix.encode()))
                return self.send(page, "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send(active.state())
            if path == "/inventory.json":
                return self.send(active.export(), filename="forkit-inventory.json")
            return self.send({"error": "Page unavailable."}, status=404)

        def do_POST(self):
            if (self.headers.get("Host") != address
                    or self.headers.get("Origin") != f"http://{address}"
                    or self.headers.get("X-Forkit-Token") != token):
                return self.send({"error": "Request rejected."}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object.")
                active, path, _ = self.route()
                result = active.mutate(path.removeprefix("/api/"), payload)
            except (ValueError, UnicodeError) as error:
                return self.send({"error": str(error)}, status=400)
            return self.send(result)

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        address = f"127.0.0.1:{server.server_port}"
        print(f"Forkit AI running at http://{address}", flush=True)
        if open_browser:
            webbrowser.open(f"http://{address}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forkit AI local discovery prototype")
    parser.add_argument("--live", action="store_true", help="Scan local PATH and supplied MCP configs")
    parser.add_argument("--workspace", type=Path, action="append", default=[],
                        help="Look for known project MCP configs in this folder")
    parser.add_argument("--config", action="append", default=[], help="JSON file with mcpServers")
    parser.add_argument("--audit-log", type=Path, help="Activity JSONL from a configured observer")
    parser.add_argument("--audit-repo", type=Path, help="Read committed events from this local audit repository")
    parser.add_argument("--ask-model", help="Optional already-installed local Ollama model for Ask Forkit")
    parser.add_argument("--audit-checkpoint", help="Saved audit log prefix in LENGTH:SHA256 form")
    parser.add_argument("--agent-log", type=Path, help="Activity JSONL reported by an agent")
    parser.add_argument("--watch-dir", type=Path, action="append", default=[],
                        help="Compare net file changes in this folder on timeline refresh")
    parser.add_argument("--watch-git", type=Path, action="append", default=[],
                        help="Compare tracked and unignored files in a Git checkout")
    parser.add_argument("--db", type=Path, help="Path to the local SQLite state")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the browser after binding")
    args = parser.parse_args()
    if (args.config or args.workspace) and not args.live:
        parser.error("--config and --workspace require --live")
    if not args.live and (args.audit_log or args.agent_log or args.watch_dir or args.watch_git):
        parser.error("Live timeline sources require --live")
    if args.audit_checkpoint and (
        not args.audit_log or not re.fullmatch(r"[0-9]{1,10}:[0-9a-fA-F]{64}", args.audit_checkpoint)
    ):
        parser.error("--audit-checkpoint requires --audit-log and a LENGTH:SHA256 value")
    database = args.db or Path(".forkit-live.sqlite3" if args.live else ".forkit-demo.sqlite3")
    inventory = Inventory(database, not args.live, args.config, audit_log=args.audit_log,
                          checkpoint=args.audit_checkpoint, agent_log=args.agent_log,
                          watch_dirs=args.watch_dir, watch_git=args.watch_git,
                          workspaces=args.workspace, audit_repo=args.audit_repo, ask_model=args.ask_model)
    serve(inventory, args.port, args.open)
