"""Live capture: event schema, MCP tap and HTTP collector. Synthetic data only, all in tmp_path."""
from __future__ import annotations

import argparse
import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from src.capture.collector import MAX_EVENTS, serve
from src.capture.events import append_event, build_event, mask_home, read_events, validate_event
from src.capture.mcp_tap import DENIED_CODE, Tap, parse


def _ev(**kw):
    base = dict(origin="local-tap", client="claude-desktop", config_path="configs/c.json",
                server_name="files-local", tool_call="mcp.read_text_file", result="success")
    base.update(kw)
    return build_event(**base)


def test_build_event_keeps_metadata_only():
    ev = _ev(arguments={"path": "/home/alice/reports/q3.md", "content": "SECRET BODY"}, home="/home/alice")
    assert ev["target"] == "~/reports/q3.md"
    assert ev["argument_keys"] == ["content", "path"]
    assert "SECRET BODY" not in json.dumps(ev)
    assert ev["approval_id"] is None and ev["runtime_consent"] is None
    assert ev["event_id"].startswith("cap-")


def test_build_event_redacts_secret_target_and_rejects_bad_values():
    ev = _ev(target="postgres://admin:hunter2@db.example/app")
    assert "hunter2" not in json.dumps(ev)
    with pytest.raises(ValueError):
        _ev(result="maybe")
    with pytest.raises(ValueError):
        _ev(origin="somewhere")


def test_missing_target_is_unknown():
    assert _ev(arguments={})["target"] == "UNKNOWN"
    assert mask_home("/etc/hosts", "/home/alice") == "/etc/hosts"
    assert _ev(arguments={"path": "/srv/app/data/q3.md"}, base="/srv/app")["target"] == "data/q3.md"


def test_validate_event_drops_raw_fields_and_spoofed_origin():
    posted = {"event_id": "chosen-by-sender", "tool_call": "n8n.write_file", "result": "success",
              "source": {"client": "n8n", "config_path": "configs/n8n_mcp.json", "server_name": "flow"},
              "arguments": {"fileName": "out.md", "data": "raw file content"},
              "capture": {"origin": "local-tap"}, "approval_id": "appr-fake", "runtime_consent": True,
              "output": "raw output"}
    ev = validate_event(posted)
    text = json.dumps(ev)
    assert ev["event_id"] != "chosen-by-sender"
    assert "raw file content" not in text and "raw output" not in text
    assert ev["approval_id"] is None and ev["runtime_consent"] is None
    assert ev["capture"]["origin"] == "cloud-collector"
    assert validate_event({**posted, "capture": {"origin": "cloud-n8n"}})["capture"]["origin"] == "cloud-n8n"
    with pytest.raises(ValueError):
        validate_event(["not", "a", "dict"])


def test_append_and_read_events_skip_bad_lines(tmp_path):
    log = tmp_path / "events.jsonl"
    append_event(log, _ev())
    with log.open("a") as fh:
        fh.write("not json\n")
    append_event(log, _ev(result="denied"))
    assert [e["result"] for e in read_events(log)] == ["success", "denied"]
    assert read_events(tmp_path / "missing.jsonl") == []


def _tap(tmp_path, read_only=True):
    ns = argparse.Namespace(client="claude-desktop", config="configs/c.json", server="files-local",
                            log=tmp_path / "ev.jsonl", agent="test-agent", read_only=read_only,
                            base=str(tmp_path))
    out = io.BytesIO()
    return Tap(ns, out), out


def _line(msg):
    return (json.dumps(msg) + "\n").encode()


def test_tap_records_success_and_error(tmp_path):
    tap, _ = _tap(tmp_path)
    call = _line({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "read_text_file", "arguments": {"path": "q3.md"}}})
    assert tap.from_client(call) == call
    tap.from_server(_line({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "x"}]}}))
    tap.from_client(_line({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_text_file"}}))
    tap.from_server(_line({"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": []}}))
    evs = read_events(tmp_path / "ev.jsonl")
    assert [(e["tool_call"], e["result"]) for e in evs] == [("mcp.read_text_file", "success"),
                                                            ("mcp.read_text_file", "error")]
    assert evs[0]["capture"] == {**evs[0]["capture"], "origin": "local-tap", "agent": "test-agent"}


def test_tap_denies_mutating_call_on_read_only(tmp_path):
    tap, out = _tap(tmp_path)
    call = _line({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                  "params": {"name": "write_file", "arguments": {"path": "x.md", "content": "body"}}})
    assert tap.from_client(call) is None
    reply = json.loads(out.getvalue())
    assert reply["id"] == 7 and reply["error"]["code"] == DENIED_CODE
    (ev,) = read_events(tmp_path / "ev.jsonl")
    assert ev["result"] == "denied" and ev["tool_call"] == "mcp.write_file"


def test_tap_forwards_writes_when_not_read_only_and_passes_other_traffic(tmp_path):
    tap, out = _tap(tmp_path, read_only=False)
    call = _line({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "write_file"}})
    assert tap.from_client(call) == call and out.getvalue() == b""
    for raw in (b"garbage\n", _line({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})):
        assert tap.from_client(raw) == raw
    assert read_events(tmp_path / "ev.jsonl") == []


def test_tap_parse_requires_server_command():
    ns, cmd = parse(["--client", "c", "--config", "f", "--server", "s", "--read-only", "--", "npx", "srv"])
    assert ns.read_only and cmd == ["npx", "srv"]
    with pytest.raises(SystemExit):
        parse(["--client", "c", "--config", "f", "--server", "s"])
    with pytest.raises(SystemExit):
        parse(["--client", "c", "--config", "f", "--server", "s", "--"])


@pytest.fixture
def collector(tmp_path):
    log = tmp_path / "collector.jsonl"
    srv = serve(0, log, "s3cret-token")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", log
    srv.shutdown()
    srv.server_close()


def _post(url, body, token="s3cret-token"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url + "/events", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_collector_stores_validated_events(collector):
    url, log = collector
    with urllib.request.urlopen(url + "/health", timeout=5) as r:
        assert json.loads(r.read()) == {"status": "ok"}
    ev = {"tool_call": "n8n.read_file", "result": "success", "capture": {"origin": "cloud-n8n"},
          "source": {"client": "n8n", "config_path": "configs/n8n_mcp.json", "server_name": "flow"}}
    code, body = _post(url, [ev, ev])
    assert code == 201 and len(body["stored"]) == 2
    assert [e["capture"]["origin"] for e in read_events(log)] == ["cloud-n8n", "cloud-n8n"]


def test_collector_rejects_bad_token_body_and_batch(collector):
    url, log = collector
    ev = {"tool_call": "n8n.read_file", "result": "success"}
    assert _post(url, ev, token="wrong")[0] == 401
    assert _post(url, b"{not json")[0] == 400
    assert _post(url, {**ev, "result": "maybe"})[0] == 400
    assert _post(url, [ev] * (MAX_EVENTS + 1))[0] == 400
    assert read_events(log) == []
