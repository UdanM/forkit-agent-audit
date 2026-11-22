import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import Mock

from demo.agents import prompt_pair_live
from demo.agents.prompt_pair import FIXTURE
from demo.agents.prompt_pair_live import make_vm_server, run_live_pair
from src.engine import load_policies
from src.timeline import timeline_from_files

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-local-only"


def test_live_prompt_and_uploaded_bytes_go_to_both_expected_destinations(tmp_path):
    prompt = "Write a status report about the café opening."
    attachment = FIXTURE.read_bytes() + b"\n"
    with make_vm_server(0, TOKEN) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = run_live_pair(tmp_path / "observer.jsonl", server.server_port, TOKEN, prompt, attachment)
        finally:
            server.shutdown()
            thread.join()

    assert result["cloud_error"] is None
    assert "café opening" in result["reports"]["local-pc"]
    assert result["reports"]["local-pc"] == result["reports"]["cloud-vm"]
    assert result["extras"] == {"cloud-vm": "Unrelated pilot marker."}
    log = tmp_path / "observer.jsonl"
    timeline = timeline_from_files(log, rules=load_policies(ROOT / "fixtures" / "policies.json"))
    pair, = timeline["prompt_comparisons"]
    assert pair["same_prompt"] and pair["overlap"]
    assert pair["runs"][0]["attachment_sha256"] is None
    assert pair["runs"][1]["attachment_sha256"] == hashlib.sha256(attachment).hexdigest()
    assert any(item["rule_id"] == "POL-019" for item in timeline["findings"])
    assert "café opening" not in log.read_text(encoding="utf-8")


def test_success_http_status_without_current_observer_evidence_does_not_confirm_vm_delivery(tmp_path, monkeypatch):
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.return_value = json.dumps({"report": "Old report", "observer": "", "extra": None}).encode()
    monkeypatch.setattr(prompt_pair_live, "HTTPConnection", lambda *_args, **_kwargs: connection)

    log = tmp_path / "observer.jsonl"
    result = run_live_pair(log, 12345, TOKEN, "Write a status report.", FIXTURE.read_bytes())
    assert result["cloud_error"]
    assert set(result["reports"]) == {"local-pc"}
    trial, = timeline_from_files(log)["prompt_comparisons"]
    assert trial["same_prompt"] is None
