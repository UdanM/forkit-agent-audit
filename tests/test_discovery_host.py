"""'Discovered on' machine name: sidecar context only, never identity or owner."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from src.discovery_host import (
    DEMO_LABEL,
    FILE_NAME,
    discovered_on_label,
    load_discovered_on,
    machine_name,
    record_discovery,
)
from src.engine import UNKNOWN
from src.live_map_html import live_map_html
from tests.test_provenance_view import CONFIGS, engine

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    return tmp_path


def real_scan(ws: Path, host: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list[dict]]:
    """Real-machine scan of a fake home on a given host name, recorded like the app does."""
    monkeypatch.setattr(socket, "gethostname", lambda: host)
    home = ws / "home" / "someone"
    (home / ".cursor").mkdir(parents=True, exist_ok=True)
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"]}}}))
    eng = engine(ws, "data/local")
    cands = eng.scan_local_machine("linux", home, {})["candidates"]
    record_discovery(eng.data_dir, [c["passport_id"] for c in cands], machine_name())
    return eng, cands


def test_machine_name_is_short_printable_or_unknown() -> None:
    assert machine_name("Erens-Laptop.local") == "Erens-Laptop"
    assert machine_name("bad\x00name\n") == "badname"
    assert machine_name("") == UNKNOWN
    assert len(machine_name("a" * 300)) == 64


def test_host_never_changes_ids_or_owner(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    eng_a, cands_a = real_scan(ws, "machine-alpha", monkeypatch)
    ids_a = [c["passport_id"] for c in cands_a]
    eng_b, cands_b = real_scan(ws, "machine-beta", monkeypatch)
    assert [c["passport_id"] for c in cands_b] == ids_a          # deterministic across machines
    assert all(c["owner"] == UNKNOWN for c in cands_a + cands_b)  # a host name is not an owner

    pid = eng_b.mint_passport(cands_b[0])
    stored = (eng_b.data_dir / "passports.json").read_text()
    assert "machine-alpha" not in stored and "machine-beta" not in stored
    assert load_discovered_on(eng_b.data_dir)[pid]["machines"] == ["machine-alpha", "machine-beta"]
    assert discovered_on_label(eng_b.data_dir, pid, demo=False) == "machine-alpha, machine-beta"


def test_recording_does_not_touch_passports_and_keeps_first_seen(ws: Path) -> None:
    eng = engine(ws)
    (cand, *_) = eng.discover_tools(CONFIGS)
    eng.mint_passport(cand)
    before = (eng.data_dir / "passports.json").read_bytes()
    record_discovery(eng.data_dir, [cand["passport_id"]], "host-1", now="2026-11-20T10:00:00Z")
    record_discovery(eng.data_dir, [cand["passport_id"]], "host-1", now="2026-11-21T10:00:00Z")
    assert (eng.data_dir / "passports.json").read_bytes() == before
    rec = load_discovered_on(eng.data_dir)[cand["passport_id"]]
    assert rec == {"machines": ["host-1"], "first_seen": "2026-11-20T10:00:00Z", "last_seen": "2026-11-21T10:00:00Z"}


def test_demo_mode_never_shows_a_host(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "sandbox-host")
    eng = engine(ws)
    cands = eng.discover_tools(CONFIGS)
    pid = cands[0]["passport_id"]
    assert discovered_on_label(eng.data_dir, pid, demo=True) == DEMO_LABEL
    assert discovered_on_label(eng.data_dir, pid, demo=False) == UNKNOWN  # nothing recorded: never guessed
    assert not (eng.data_dir / FILE_NAME).exists()


def test_corrupt_sidecar_is_ignored_and_label_is_escaped_in_map(ws: Path) -> None:
    eng = engine(ws)
    cands = eng.discover_tools(CONFIGS)
    eng.data_dir.mkdir(parents=True, exist_ok=True)
    (eng.data_dir / FILE_NAME).write_text("{not json")
    assert load_discovered_on(eng.data_dir) == {}
    page = live_map_html(eng.data_dir, candidates=cands, found_on={cands[0]["passport_id"]: "<b>host</b>"})
    assert "Found on: &lt;b&gt;host&lt;/b&gt;" in page
