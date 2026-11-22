"""Run the interactive prompt comparison through a private, loopback-only VM endpoint."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from demo.agents.prompt_pair import (
    APPROVED_OUTPUT,
    EXTRA_OUTPUT,
    read_attachment,
    run_arm,
    validate_prompt,
)

MAX_MESSAGE = 24 * 1024


def _execute(deployment: str, trial: str, start: datetime, prompt: str,
             attachment: bytes | None = None) -> dict[str, str | None]:
    with TemporaryDirectory(prefix="forkit-prompt-run-") as directory:
        root = Path(directory)
        log = run_arm(root, deployment, trial=trial, start_at=start, prompt=prompt, attachment=attachment)
        workspace = root / "workspace"
        extra = workspace / EXTRA_OUTPUT
        return {"observer": log.read_text(encoding="utf-8"),
                "report": (workspace / APPROVED_OUTPUT).read_text(encoding="utf-8").strip(),
                "extra": extra.read_text(encoding="utf-8").strip() if extra.is_file() else None}


def make_vm_server(port: int, token: str) -> ThreadingHTTPServer:
    if not token or not token.isascii():
        raise ValueError("A private ASCII token is required for the VM listener.")
    authorization = f"Bearer {token}".encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            if self.path != "/run":
                self._json(404, {"error": "Unknown route"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), authorization):
                self._json(401, {"error": "Unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_MESSAGE:
                    raise ValueError("Invalid payload size")
                self.connection.settimeout(10)
                job = json.loads(self.rfile.read(size))
                trial, prompt = job["trial"], job["prompt"]
                if not re.fullmatch(r"live-[a-f0-9]{32}", trial):
                    raise ValueError("Invalid trial")
                start = datetime.fromisoformat(job["start_at"])
                if start.utcoffset() is None or start > datetime.now(timezone.utc) + timedelta(seconds=6):
                    raise ValueError("Invalid start time")
                validate_prompt(prompt)
                attachment = base64.b64decode(job["attachment"], validate=True)
                read_attachment(attachment)
            except (ValueError, TypeError, KeyError, OSError, RecursionError):
                self._json(400, {"error": "Invalid pilot request"})
                return
            try:
                result = _execute("cloud-vm", trial, start, prompt, attachment)
            except (OSError, ValueError, subprocess.SubprocessError):
                self._json(500, {"error": "The VM worker failed"})
                return
            self._json(200, result)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _request_vm(port: int, token: str, job: dict[str, str]) -> dict[str, Any]:
    # The dashboard uses the same parser to check the receipt and render the evidence.
    from src.timeline import parse_log

    connection = HTTPConnection("127.0.0.1", port, timeout=25)
    try:
        connection.request("POST", "/run", json.dumps(job),
                           {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("The VM declined the request")
        payload = response.read(MAX_MESSAGE + 1)
        if len(payload) > MAX_MESSAGE:
            raise ValueError("Oversize VM response")
        result = json.loads(payload)
        if (not isinstance(result["report"], str) or not isinstance(result["observer"], str)
                or (result.get("extra") is not None and not isinstance(result["extra"], str))):
            raise ValueError("Invalid observer response")
        rows, rejected = parse_log(result["observer"].encode())
        starts = [row for row in rows if row["kind"] == "run_started"]
        if rejected or len(starts) != 1 or any(
            row["deployment"] != "cloud-vm" or row["run_id"] != f"{job['trial']}-cloud-vm" for row in rows
        ):
            raise ValueError("Invalid VM observer records")
        task = starts[0].get("task", {})
        if (task.get("id") != job["trial"]
                or task.get("prompt_sha256") != hashlib.sha256(job["prompt"].encode()).hexdigest()
                or task.get("attachment_sha256") != hashlib.sha256(base64.b64decode(job["attachment"])).hexdigest()):
            raise ValueError("VM observer records do not match this send")
        return result
    finally:
        connection.close()


def run_live_pair(log: Path, port: int, token: str, prompt: str, attachment: bytes) -> dict[str, Any]:
    validate_prompt(prompt)
    read_attachment(attachment)
    trial = f"live-{uuid4().hex}"
    start = datetime.now(timezone.utc) + timedelta(seconds=1)
    job = {"trial": trial, "start_at": start.isoformat(), "prompt": prompt,
           "attachment": base64.b64encode(attachment).decode("ascii")}
    results = {}
    cloud_error = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        local = pool.submit(_execute, "local-pc", trial, start, prompt)
        cloud = pool.submit(_request_vm, port, token, job)
        results["local-pc"] = local.result()
        try:
            results["cloud-vm"] = cloud.result()
        except (OSError, HTTPException, ValueError, KeyError, TypeError):
            cloud_error = "The Cloud VM did not return a result. Check the private connection and try again."
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(result["observer"] for result in results.values()), encoding="utf-8")
    return {"reports": {host: result["report"] for host, result in results.items()},
            "extras": {host: result["extra"] for host, result in results.items() if result.get("extra")},
            "cloud_error": cloud_error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    with make_vm_server(args.port, args.token_file.read_text(encoding="utf-8").strip()) as vm:
        vm.serve_forever()
