import io
import json
import zipfile

import pytest

from src.engine import AutoDiscoverEngine, build_audit_bundle
from src.timeline import UNKNOWN, build_timeline, make_checkpoint, parse_row, timeline_from_files


def record(id, kind, run_id, deployment="a", minute=0, **extra):
    return {"id": id, "kind": kind, "run_id": run_id, "deployment": deployment,
            "time": f"2026-11-20T10:{minute:02d}:00Z", **extra}


def test_timeline_scopes_runs_by_deployment_and_start_time():
    events = [
        record("win", "run_started", "shared", label="Windows", access="read_only"),
        record("write", "file_write", "shared", "b", minute=1, path="notes.txt"),
        record("cloud", "run_started", "shared", "b", minute=2, label="Cloud"),
        record("cloud-after", "file_write", "shared", "b", minute=3, path="notes.txt"),
        record("write", "file_write", "shared", minute=3, path="notes.txt"),
    ]
    timeline = build_timeline([parse_row(event) for event in events])
    assert timeline["actions"][0]["actor"] == UNKNOWN
    assert {row["actor"] for row in timeline["actions"][1:]} == {"Cloud", "Windows"}
    assert len({row["record_id"] for row in timeline["actions"]}) == 3
    windows_write = next(row["record_id"] for row in timeline["actions"] if row["actor"] == "Windows")
    assert timeline["signals"]["read_only_write"] == [windows_write]
    assert len({run["run_id"] for run in timeline["runs"]}) == 2


def test_unchecked_start_cannot_attribute_checkpointed_write(tmp_path):
    path = tmp_path / "observer.jsonl"
    saved = (json.dumps(record("action", "file_write", "r", minute=1, path="notes.txt")) + "\n").encode()
    start = (json.dumps(record("start", "run_started", "r", label="Claimant")) + "\n").encode()
    path.write_bytes(saved + start)
    assert timeline_from_files(path, checkpoint=make_checkpoint(saved))["actions"][0]["actor"] == UNKNOWN
    path.write_bytes(saved.replace(b"notes.txt", b"draft.txt") + start)
    timeline = timeline_from_files(path, checkpoint=make_checkpoint(saved))
    assert timeline["checkpoint"] == "rewritten" and timeline["actions"] == []

    engine = AutoDiscoverEngine(tmp_path / "store", base_dir=tmp_path)
    path.write_bytes(saved)
    pin = engine.save_log_checkpoint("lab", path)
    path.write_bytes(saved.replace(b"notes.txt", b"draft.txt"))
    with pytest.raises(ValueError, match="saved evidence changed"):
        engine.save_log_checkpoint("lab", path)
    assert engine.log_checkpoints()["lab"]["checkpoint"] == pin


def test_self_report_needs_a_matching_observation():
    start = parse_row(record("start", "run_started", "r", label="Same name", agent_id="observer"))
    report = parse_row(record("claim", "tool_call", "r", minute=1, tool_call="mcp.read"))
    assert build_timeline([start], [report])["self_reported"][0]["verdict"] == "not observed"
    claimant = parse_row(record("claim", "run_started", "r", label="Same name", agent_id="self"))
    assert build_timeline([start], [claimant])["self_reported"][0]["verdict"] == "disagrees with observer"


def test_partial_source_cannot_link_and_export_omits_full_config(tmp_path):
    for name in ("one.json", "two.json"):
        (tmp_path / name).write_text(json.dumps({"mcpServers": {"shared": {
            "command": "node", "args": ["--payload", "opaque-only-for-test"],
            "url": "https://example.invalid/mcp?access_token=opaque-token-for-test"
        }}}))
    engine = AutoDiscoverEngine(tmp_path / "store", base_dir=tmp_path)
    candidates = engine.discover_tools(["one.json", "two.json"])
    pid = engine.mint_passport(candidates[0])
    event = {"event_id": "partial", "source": {"server_name": "shared", "body": {"content": "test body"}},
             "target": {"content": "test payload"}}
    assert engine.attribution(event)["status"] != "unique"
    with pytest.raises(ValueError):
        engine.link_event(event, pid, user_consent=True)
    queued = engine.record_for_review(event, user_consent=True)
    assert queued["source"] == {"server_name": "shared"} and queued["target"] == UNKNOWN
    invalid_source = {"event_id": {"body": "some payload"},
                      "source": {"config_path": "https://example.invalid/?access_token=opaque-token-for-test"}}
    queued = engine.record_for_review(invalid_source, user_consent=True)
    assert queued["event_id"] == UNKNOWN
    assert "opaque-token-for-test" not in json.dumps(queued)

    config_path = tmp_path / "one.json"
    edited = json.loads(config_path.read_text())
    edited["mcpServers"]["shared"]["args"][1] = "new-opaque-value"
    config_path.write_text(json.dumps(edited))
    candidates = engine.discover_tools(["one.json", "two.json"])
    report = engine.build_audit_report(candidates=candidates)
    assert "config_hash" in report["passports"][0]
    assert not {"args", "command", "env", "headers", "url"} & report["passports"][0].keys()
    assert report["metadata_changes"][0]["diff"] == ["args changed"]
    bundle = build_audit_bundle(report, engine.data_dir)
    with zipfile.ZipFile(io.BytesIO(bundle)) as files:
        for name in files.namelist():
            contents = files.read(name)
            assert b"opaque-only-for-test" not in contents
            assert b"opaque-token-for-test" not in contents
            assert b"new-opaque-value" not in contents


def test_selected_passport_must_be_the_unique_mcp_candidate(tmp_path, monkeypatch):
    engine = AutoDiscoverEngine(tmp_path / "store", base_dir=tmp_path)
    identity = {"tool_name": "shared", "source_path": "one.json"}
    monkeypatch.setattr(engine, "passports", lambda: {
        "mcp": {**identity, "passport_type": "mcp_server"},
        "cli": {**identity, "passport_type": "cli"},
    })
    event = {"event_id": "known", "source": {"server_name": "shared", "config_path": "one.json"}}
    assert engine.attribution(event)["candidates"] == ["mcp"]
    with pytest.raises(ValueError, match="uniquely matched"):
        engine.link_event(event, "cli", user_consent=True)
