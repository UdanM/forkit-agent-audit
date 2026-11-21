"""Tab 06 feed and findings HTML (pitch refresh). Layout idea from UdanM/forkit-agent-audit."""

from pathlib import Path

from src.engine import load_policies
from src.timeline import timeline_from_files
from src.timeline_html import activity_feed_html, findings_html

REPO = Path(__file__).resolve().parent.parent


def tl() -> dict:
    return timeline_from_files(REPO / "fixtures" / "activity" / "observer.jsonl",
                               REPO / "fixtures" / "activity" / "agent_reported.jsonl",
                               rules=load_policies(REPO / "fixtures" / "policies.json"))


def test_feed_marks_violations_and_unknown_actors() -> None:
    out = activity_feed_html(tl())
    assert out.count("<li") == 6
    assert '<li class="bad">' in out and "read-only run wrote" in out and "protected file" in out
    assert "delegated by Report assistant" in out
    assert 'class="who unk">UNKNOWN' in out and ".." not in out.split("</style>")[1]
    assert "File changes per actor" in out


def test_findings_list_and_escaping() -> None:
    out = findings_html(tl()["findings"])
    assert "POL-007" in out and "Read-only run changed a file" in out
    evil = [{"severity": "high", "rule_id": "<x>", "title": "<script>", "explanation": "&", "record_ids": ["a"]}]
    assert "<script>" not in findings_html(evil).split("</style>")[1]
