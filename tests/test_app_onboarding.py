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
    _button(at, "Start the demo over").click().run()
