"""Ask AutoDiscover (merge M7). Ported from UdanM/forkit-agent-audit @ 3f5b2aa (local_ask.py)."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

import src.ask as ask_mod
from src.ask import answer_from_records, ask, ask_local_model
from src.engine import AutoDiscoverEngine, load_policies
from src.timeline import timeline_from_files

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def store(tmp_path: Path) -> dict:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    e = AutoDiscoverEngine(tmp_path / "data", base_dir=tmp_path, approvals_path=tmp_path / "fixtures" / "approvals.json")
    for c in e.discover_tools(["fixtures/claude_desktop_config.json", "fixtures/cursor_mcp.json"]):
        e.mint_passport(c)
    events = [json.loads(x) for x in (tmp_path / "fixtures" / "runtime_events.jsonl").read_text().splitlines() if x]
    fs = next(k for k, p in e.passports().items() if p["tool_name"] == "filesystem-mcp")
    e.link_event(events[1], fs, user_consent=True)
    e.record_for_review(events[4], user_consent=True)
    tl = timeline_from_files(REPO / "fixtures" / "activity" / "observer.jsonl",
                             REPO / "fixtures" / "activity" / "agent_reported.jsonl",
                             rules=load_policies(REPO / "fixtures" / "policies.json"))
    return {"passports": e.passports(), "links": e.linked_events(), "incidents": e.incidents(),
            "review_queue": e.review_queue(), "policy_findings": [], "timeline": tl}


def test_who_changed_a_file_cites_actions_and_flags_unknown(store) -> None:
    a = answer_from_records("Who changed reports/q3/draft.md?", store)
    assert a["citations"] and all("draft.md" in c["text"] for c in a["citations"])
    assert "UNKNOWN" in a["answer"]


def test_what_needs_review_puts_high_severity_first(store) -> None:
    a = answer_from_records("What needs review?", store)
    assert "not proof of misconduct" in a["answer"]
    assert a["citations"][0]["text"].startswith(("critical", "high"))


def test_tools_and_ids_and_incidents(store) -> None:
    assert answer_from_records("Which tools did you find?", store)["answer"].startswith("3 confirmed")
    assert answer_from_records("What happened in evt-0002?", store)["citations"][0]["id"] == "evt-0002"
    assert answer_from_records("Any incidents?", store)["citations"][0]["kind"] == "incident"
    assert answer_from_records("Show missing approval", store)["citations"]


def test_no_match_does_not_guess(store) -> None:
    assert answer_from_records("zebra quantum", store) == {
        "answer": "No record answers that. AutoDiscover does not guess.", "citations": [], "source": "records"}
    assert answer_from_records("x" * 300, store)["citations"] == []


def test_agent_questions_and_followups_cite_the_right_run(store) -> None:
    profile = answer_from_records("Tell me about the Report helper", store)
    assert "Report helper" in profile["answer"]
    assert "act-002" in {ref["id"] for ref in profile["citations"]}
    parent = answer_from_records("What is the parent agent of this subagent?", store, subject=profile["subject"])
    assert "Report assistant" in parent["answer"]
    assert {ref["id"] for ref in parent["citations"]} == {"act-001", "act-002"}
    actions = answer_from_records("Which file did it change?", store, subject=profile["subject"])
    assert "summary.md" in actions["answer"]
    assert {ref["id"] for ref in actions["citations"]} == {"act-005"}
    assert "read-only" in answer_from_records("What access did the Report helper have?", store)["answer"]
    timing = answer_from_records("When did Report helper write summary.md?", store)
    assert "10:01:30" in timing["answer"] and timing["citations"][0]["id"] == "act-005"
    assert "Report helper" in answer_from_records("Who did the Report assistant delegate to?", store)["answer"]
    same = answer_from_records("Is Report helper the same agent as Report finisher?", store)
    assert "same agent identity" in same["answer"]
    assert {ref["id"] for ref in same["citations"]} == {"act-002", "act-010"}
    assert "not proof of misconduct" in answer_from_records("What needs human review?", store)["answer"]


def test_same_label_needs_clarification_even_with_local_model(store, monkeypatch) -> None:
    timeline = store["timeline"]
    extra = {**next(run for run in timeline["runs"] if run["label"] == "Report helper"),
             "run_id": "separate-run", "agent": "separate-agent", "record_id": "other-start"}
    mixed = {**store, "timeline": {**timeline, "runs": [*timeline["runs"], extra]}}
    def should_not_guess(*args):
        pytest.fail("Ambiguous answers must not be delegated to the model")
    monkeypatch.setattr(ask_mod, "ask_local_model", should_not_guess)
    result = ask("Who is the parent of Report helper?", mixed, model="test-model")
    assert "Which run do you mean?" in result["answer"]
    assert "subject" not in result


def test_human_questions_use_observer_records(store) -> None:
    human = answer_from_records("Who was the human in the workflow?", store)
    assert "Maya Chen" in human["answer"] and "simulated" in human["answer"]
    assert {ref["id"] for ref in human["citations"]} == {"act-012", "act-013"}
    changes = answer_from_records("Which files did Maya Chen change?", store)
    assert "review-notes.md" in changes["answer"]
    assert all("Maya Chen" in ref["text"] for ref in changes["citations"])
    assert "review-notes.md" in answer_from_records("Which approved files did Maya Chen change?", store)["answer"]
    assert not answer_from_records("Which unapproved files did Maya Chen change?", store)["citations"]
    assert "do not identify a human approver" in answer_from_records("Was there human approval?", store)["answer"]
    missing = answer_from_records("Who was the human in the workflow?", {**store, "timeline": None})
    assert "No human" in missing["answer"] and not missing["citations"]
    rejected = answer_from_records("Who was the human in the workflow?",
                                   {**store, "timeline": {"checkpoint": "rewritten"}})
    assert "failed its checkpoint" in rejected["answer"] and not rejected["citations"]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_opener(payload: dict):
    class Opener:
        def open(self, req, timeout=0):
            assert req.full_url == "http://127.0.0.1:11434/api/chat"
            assert "patch" not in req.data.decode()
            return _Resp(json.dumps({"message": {"content": json.dumps(payload)}}).encode())
    return lambda *a: Opener()


def test_local_model_answer_kept_only_with_valid_citations(store, monkeypatch) -> None:
    cites = answer_from_records("What needs review?", store)["citations"]
    monkeypatch.setattr(ask_mod, "build_opener", _fake_opener({"answer": "Two high findings.", "citations": [1, 2]}))
    good = ask_local_model("m", "What needs review?", cites)
    assert good["answer"] == "Two high findings." and good["citations"] == cites[:2]
    monkeypatch.setattr(ask_mod, "build_opener", _fake_opener({"answer": "x", "citations": [99]}))
    assert ask_local_model("m", "q", cites) is None
    fallback = ask("What needs review?", store, model="m")
    assert fallback["source"] == "records"


def test_model_unreachable_falls_back(store, monkeypatch) -> None:
    def boom(*a):
        class Opener:
            def open(self, *a, **k):
                raise OSError("refused")
        return Opener()
    monkeypatch.setattr(ask_mod, "build_opener", boom)
    assert ask("Which tools did you find?", store, model="m")["source"] == "records"
