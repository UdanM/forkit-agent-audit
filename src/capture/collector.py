"""HTTP collector: remote agents (e.g. n8n in the cloud) POST their tool actions here.

    python -m src.capture.collector --port 8765 --log data/agents/captured_events.jsonl --token <shared>

POST /events with a JSON event (or a list of events) in the runtime-event schema. Every event is
re-built through ``validate_event``: unknown fields are dropped, argument values are reduced to
their key names, targets are redacted, and the server assigns its own event_id. The optional
shared token is compared in constant time; it is read from AUTODISCOVER_COLLECTOR_TOKEN if not
passed. GET /health answers ``ok``. Stdlib only.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .events import append_event, validate_event

MAX_BODY = 256 * 1024
MAX_EVENTS = 100


def make_handler(log: Path, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoDiscoverCollector/1"

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:  # quiet: never echo request bodies
            return

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, {"status": "ok"})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/events":
                return self._reply(404, {"error": "not found"})
            if token:
                sent = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not hmac.compare_digest(sent.encode(), token.encode()):
                    return self._reply(401, {"error": "bad token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                return self._reply(413 if length > MAX_BODY else 400, {"error": "bad body size"})
            try:
                payload = json.loads(self.rfile.read(length))
                items = payload if isinstance(payload, list) else [payload]
                if len(items) > MAX_EVENTS:
                    raise ValueError(f"at most {MAX_EVENTS} events per request")
                events = [validate_event(e) for e in items]
            except ValueError as exc:
                return self._reply(400, {"error": str(exc)[:200]})
            for ev in events:
                append_event(log, ev)
            self._reply(201, {"stored": [e["event_id"] for e in events]})

    return Handler


def serve(port: int, log: Path, token: str | None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(log, token))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="collector")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--log", default="data/agents/captured_events.jsonl")
    p.add_argument("--token", default=os.environ.get("AUTODISCOVER_COLLECTOR_TOKEN"))
    a = p.parse_args(argv)
    srv = serve(a.port, Path(a.log), a.token, a.host)
    print(f"collector listening on http://{a.host}:{a.port}/events", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
