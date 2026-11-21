"""Launchers (merge M8): double-click on Windows, one command on macOS/Linux."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_windows_launcher_uses_crlf_and_starts_the_app() -> None:
    raw = (REPO / "Start AutoDiscover.cmd").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode()
    assert 'cd /d "%~dp0"' in text and "streamlit run app.py" in text


def test_unix_launcher_starts_the_app() -> None:
    text = (REPO / "start-autodiscover.sh").read_text()
    assert text.startswith("#!/usr/bin/env sh") and "streamlit run app.py" in text
