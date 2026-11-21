"""End-to-end: the scripted agent runs through the tap against the stdlib MCP server (no npx, no n8n)."""
from __future__ import annotations

import json

import demo.agents.run_scenarios as rs
from src.capture.events import read_events


def test_local_scenarios_capture_link_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "find_n8n", lambda explicit=None: None)
    monkeypatch.setenv("AUTODISCOVER_CAPTURE_LOG", "unused")
    monkeypatch.setenv("PYTHONPATH", "")
    summary = rs.run_all(tmp_path, fs_server="mini", consent=True, say=lambda _msg: None)
    out = tmp_path / "data" / "agents"

    status = {s["id"]: s["status"] for s in summary["scenarios"]}
    assert status == {"A": "ran", "B": "ran", "C": "ran", "D": "skipped", "E": "ran", "F": "ran"}
    assert {"POL-007", "POL-008", "POL-009", "POL-015"} <= set(summary["pilot"]["findings"])

    events = {e["event_id"]: e for e in read_events(out / "captured_events.jsonl")}
    by_id = {s["id"]: [events[i] for i in s["event_ids"]] for s in summary["scenarios"]}
    assert [(e["tool_call"], e["result"]) for e in by_id["A"]] == [
        ("mcp.list_allowed_directories", "success"), ("mcp.read_text_file", "success")]
    assert ("mcp.write_file", "denied") in [(e["tool_call"], e["result"]) for e in by_id["B"]]
    assert ("mcp.write_file", "success") in [(e["tool_call"], e["result"]) for e in by_id["C"]]
    assert ("mcp.write_file", "success") in [(e["tool_call"], e["result"]) for e in by_id["E"]]
    assert not (out / "workspace" / "reports" / "summary.md").read_text().startswith("#")  # E really wrote
    assert all(e["capture"]["origin"] == "local-tap" for e in events.values())
    assert "Summary drafted" not in (out / "captured_events.jsonl").read_text()  # no file content stored

    links = json.loads((out / "linked_events.json").read_text())
    assert len(links) == len(events)
    writes = [link for link in links if link["tool_call"] == "mcp.write_file"]
    assert all(link["approval_status"] == "MISSING_APPROVAL" and "POL-005" in link["policy_flags"] for link in writes)
    assert len(json.loads((out / "incidents.json").read_text())) == 2          # B (denied) and E (after drift)
    assert summary["cross_link"]["refused"] is True

    passports = json.loads((out / "passports.json").read_text())
    assert len(passports) == 3                                                 # drift did not mint a new tool
    changes = json.loads((out / "metadata_changes.json").read_text())
    assert len(changes) == 1 and changes[0]["source_path"] == "configs/claude_desktop_config.json"
    rules = {f["rule_id"] for f in json.loads((out / "policy_findings.json").read_text())}
    assert {"POL-002", "POL-005"} <= rules
    assert json.loads((out / "scenarios.json").read_text())["events"] == len(events)


def test_mini_server_refuses_paths_outside_its_folder(tmp_path):
    from demo.agents.mini_fs_server import handle
    (tmp_path / "ok.md").write_text("synthetic")
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    ok = handle(tmp_path, {**call, "params": {"name": "read_text_file", "arguments": {"path": "ok.md"}}})
    assert ok["result"]["content"][0]["text"] == "synthetic"
    bad = handle(tmp_path, {**call, "params": {"name": "read_text_file", "arguments": {"path": "../x"}}})
    assert bad["result"]["isError"] is True
    assert handle(tmp_path, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_n8n_env_allows_the_workflows_env_reads_on_n8n_2(tmp_path):
    """n8n 2.x denies $env by default; the subprocess must opt out, and every $env the
    workflow reads must be set by us, not inherited from the host."""
    import re

    env = rs.n8n_env(tmp_path, tmp_path / "out", "http://127.0.0.1:1/events", "tok")
    assert env["N8N_BLOCK_ENV_ACCESS_IN_NODE"] == "false"
    flow = (rs.DEMO / "n8n" / "report_flow.json").read_text(encoding="utf-8")
    used = set(re.findall(r"\$env\.([A-Z0-9_]+)", flow))
    assert used and used <= {k for k in env if k.startswith(("AUTODISCOVER_", "N8N_"))}


def test_run_n8n_passes_that_env_to_the_subprocess(tmp_path):
    import sys

    import pytest

    if sys.platform == "win32":
        pytest.skip("uses a POSIX shell script as a stand-in for n8n")
    fake = tmp_path / "n8n"
    fake.write_text('#!/bin/sh\n[ "$N8N_BLOCK_ENV_ACCESS_IN_NODE" = "false" ] || '
                    '{ echo "access to env vars denied"; exit 1; }\n', encoding="utf-8")
    fake.chmod(0o755)
    ok, msg = rs.run_n8n(str(fake), tmp_path, tmp_path / "out", "http://127.0.0.1:1/events", "tok")
    assert ok, msg
