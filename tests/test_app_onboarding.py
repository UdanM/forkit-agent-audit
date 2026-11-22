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
    # Lineage: the incident names its parent passport and the settings file that declared the tool.
    parent = text.split("Parent passport", 1)[1].split("</div>", 1)[0]
    assert "filesystem-mcp" in parent and "fixtures/claude_desktop_config.json" in parent
    assert "In plain words" in text and "1 INCIDENT" in text          # Map summary
    _button(at, "Save log checkpoint").click().run()
    _button(at, "Simulate log tampering").click().run()
    tamper_text = " ".join(m.value for m in at.markdown)
    assert "Someone rewrote the log" in tamper_text and "The timeline is withheld" in tamper_text
    _button(at, "Restore original log").click().run()
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
    timeline = next(tab for tab in at.tabs if tab.label == "Activity timeline")
    assert any("2 file changes</b> attributed to people (2 simulated)" in item.value for item in timeline.markdown)
    evidence = next(panel for panel in timeline.expander if panel.label.startswith("Technical details"))
    runs, actions, *_ = evidence.dataframe
    assert "record" in runs.value.columns and "record" in actions.value.columns
    assert set(actions.value["actor_type"]) >= {"agent", "human"}
    _button(at, "Save log checkpoint").click().run()
    assert "Report assistant" in ask("Who is the parent of Report helper?")["answer"]
    _button(at, "Simulate log tampering").click().run()
    assert not any("The recorded parent of" in message.value for message in at.markdown)
    assert "failed its checkpoint" in ask("Who was the human in the workflow?")["answer"]
    _button(at, "Start the demo over").click().run()


def test_prompt_injection_tab_shows_both_runs_and_cites_the_flagged_write(monkeypatch):
    monkeypatch.delenv("AUTODISCOVER_PROMPT_PAIR_LOG", raising=False)
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception
    tab = next(tab for tab in at.tabs if tab.label == "Testing")
    assert all(head.value != "Live prompt injection demo" for head in tab.subheader)
    assert {metric.value for metric in tab.metric} == {"Same prompt", "Overlapping runs"}
    assert {head.value for head in tab.subheader} >= {"Local PC · control", "Cloud VM · with attachment"}
    assert any("Possible indirect prompt injection" in warning.value for warning in tab.warning)
    assert any("scratch/off-task.txt" in code.value for code in tab.code)
    assert any("attachment-replay-cloud-vm-edit-2" in caption.value for caption in tab.caption)
    assert tab.text_area(key="prompt_pair_draft").value == ""
    assert any("Staged for the Cloud VM" in item.value for item in tab.caption)
    assert not any("Live test results" in head.value for head in tab.subheader)
