"""First-time-viewer flow: one-click demo setup, plain labels, and linking (UI smoke test).

Skipped where Streamlit is not installed (CI installs only the package extras).
"""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_one_click_setup_then_link_shows_plain_labels():
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    _button(at, "Start the demo over").click().run()

    assert any(b.label == "Set up the demo in one click" for b in at.button)
    _button(at, "Set up the demo in one click").click().run()
    assert not at.exception
    assert sum(b.label == "Registered ✓" for b in at.button) == 3
    at.selectbox(key="ev_select").set_value("evt-0002").run()
    at.checkbox(key="consent_evt-0002").check().run()
    _button(at, "Link action to tool").click().run()
    text = " ".join(m.value for m in at.markdown)
    assert not at.exception
    assert "NO APPROVAL ON FILE" in text and "Incident recorded" in text
    at.selectbox(key="ev_select").set_value("evt-0004").run()
    assert _button(at, "Link action to tool").disabled
    assert any("Ambiguous source" in message.value for message in at.warning)
    _button(at, "Start the demo over").click().run()


def test_ask_follows_an_agent_and_identifies_human_edits():
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    _button(at, "Start the demo over").click().run()

    def ask(question):
        at.text_input(key="ask_q").set_value(question).run()
        _button(at, "Ask").click().run()
        assert not at.exception
        return at.session_state["ask_result"]

    assert "Report helper" in ask("Tell me about the Report helper")["answer"]
    parent = ask("Who is the parent of this subagent?")
    assert "Report assistant" in parent["answer"] and len(parent["citations"]) == 2
    _button(at, "Open Activity timeline").click().run()
    assert not at.exception and at.session_state["active_tab"] == "Activity timeline"
    assert "Maya Chen (simulated human)" in ask("Who was the human in the workflow?")["answer"]
    _button(at, "Save log checkpoint").click().run()
    assert "Report assistant" in ask("Who is the parent of Report helper?")["answer"]
    _button(at, "Simulate log tampering").click().run()
    assert not any("The recorded parent of" in message.value for message in at.markdown)
    assert "failed its checkpoint" in ask("Who was the human in the workflow?")["answer"]
    _button(at, "Start the demo over").click().run()
