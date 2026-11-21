"""
One-click compliance & audit bundle export (Upgrade 3).

Every test works under tmp_path; nothing is written to the repo's data/.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from src.engine import (
    INCIDENT,
    MISSING_APPROVAL,
    AutoDiscoverEngine,
    build_audit_bundle,
    raw_secret_values_from_configs,
    render_audit_markdown,
    report_sha256,
)

REPO = Path(__file__).resolve().parent.parent
CLAUDE = "fixtures/claude_desktop_config.json"
CURSOR = "fixtures/cursor_mcp.json"
CONFIGS = [CLAUDE, CURSOR]


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "fixtures", tmp_path / "fixtures")
    return tmp_path


def engine(ws: Path, data: str = "data") -> AutoDiscoverEngine:
    return AutoDiscoverEngine(data_dir=ws / data, base_dir=ws, approvals_path=ws / "fixtures" / "approvals.json")


def raw_values(ws: Path) -> list[str]:
    return raw_secret_values_from_configs([ws / c for c in CONFIGS])


def full_demo_flow(ws: Path) -> tuple[AutoDiscoverEngine, list[dict]]:
    eng = engine(ws)
    ids = {(c["source_path"], c["tool_name"]): eng.mint_passport(c) for c in eng.discover_tools(CONFIGS)}
    shutil.copy(ws / "fixtures" / "claude_desktop_config_v2.json", ws / CLAUDE)
    cands = eng.discover_tools(CONFIGS)
    lines = (ws / "fixtures" / "runtime_events.jsonl").read_text().splitlines()
    ev = {e["event_id"]: e for e in map(json.loads, filter(None, lines))}
    eng.link_event(ev["evt-0001"], ids[(CLAUDE, "filesystem-mcp")], user_consent=True)
    eng.link_event(ev["evt-0002"], ids[(CLAUDE, "filesystem-mcp")], user_consent=True)
    eng.link_event(ev["evt-0003"], ids[(CURSOR, "postgres-mcp")], user_consent=True)
    eng.evaluate_policies(cands)
    return eng, cands


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.glob("*"))} if d.exists() else {}


# ── Report content ─────────────────────────────────────────────────────────────

def test_report_covers_every_section_after_full_demo(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    report = eng.build_audit_report(cands, raw_values(ws))
    s = report["summary"]
    assert s["passports"] == 3
    assert s["metadata_changes"] == 1
    assert s["linked_events"] == 3
    assert s["missing_approval_links"] == 2
    assert s["incidents"] == 1
    assert {f["rule_id"] for f in report["policy_findings"]} == {"POL-001", "POL-002", "POL-003", "POL-004", "POL-005"}
    assert s["policy_findings_by_severity"]["critical"] == 1
    assert {a["state"] for a in report["approval_coverage"]} == {"approved", "pending", "missing"}
    assert len(report["missing_approval_link_ids"]) == 2
    assert report["integrity"]["status"] == "PASS"
    assert report["secret_scan"]["status"] == "PASS"
    assert report["secret_scan"]["raw_values_checked"] == 2
    assert report["generated_at"].endswith("+00:00")


def test_markdown_shows_governance_flags(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    md = render_audit_markdown(eng.build_audit_report(cands, raw_values(ws)))
    for needle in (MISSING_APPROVAL, INCIDENT, "OUT_OF_EXPECTED_BEHAVIOR", "POL-001", "POL-002", "POL-005",
                   "Passport integrity:** PASS", "Secret scan:** PASS"):
        assert needle in md, needle


# ── Empty / fresh-reset safety ─────────────────────────────────────────────────

def test_empty_store_exports_cleanly_with_zero_counts(ws: Path) -> None:
    eng = engine(ws)                       # data/ does not even exist yet
    report = eng.build_audit_report()
    assert all(v == 0 for k, v in report["summary"].items() if k != "policy_findings_by_severity")
    assert report["summary"]["policy_findings_by_severity"] == {}
    assert report["integrity"] == {
        "status": "PASS", "passports_file_present": False, "passports_file_sha256": None,
        "passports_checked": 0, "mismatches": [],
    }
    assert report["secret_scan"]["status"] == "PASS"
    assert report["secret_scan"]["files_scanned"] == []
    md = render_audit_markdown(report)
    assert "| Confirmed passports | 0 |" in md
    files = unzip(build_audit_bundle(report, eng.data_dir))
    assert not any(name.startswith("store/") for name in files)
    assert not (ws / "data").exists()


# ── Integrity ──────────────────────────────────────────────────────────────────

def test_integrity_detects_tampered_passport(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    path = ws / "data" / "passports.json"
    stored = json.loads(path.read_text())
    victim = next(iter(stored))
    stored[victim]["command"] = "curl"     # provenance no longer matches the stored ID
    path.write_text(json.dumps(stored))

    integ = eng.build_audit_report(cands, raw_values(ws))["integrity"]
    assert integ["status"] == "FAIL"
    assert integ["mismatches"] == [{"passport_id": victim, "reason": "id_does_not_match_provenance"}]
    assert integ["passports_file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


# ── Leak-safe failure ──────────────────────────────────────────────────────────

def test_planted_leak_fails_scan_without_echoing_the_secret(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    secret = next(v for v in raw_values(ws) if "analytics_admin" in v)
    path = ws / "data" / "passports.json"
    stored = json.loads(path.read_text())
    next(iter(stored.values()))["env"]["POSTGRES_URL"] = secret       # simulate a redaction bug
    path.write_text(json.dumps(stored))

    report = eng.build_audit_report(cands, raw_values(ws))
    scan = report["secret_scan"]
    assert scan["status"] == "FAIL"
    assert {"file": "passports.json", "kinds": ["raw_value", "url_password"]} in scan["leaks"]

    with pytest.raises(PermissionError) as blocked:
        build_audit_bundle(report, eng.data_dir)

    password = secret.split(":")[2].split("@")[0]
    for text in (json.dumps(report), render_audit_markdown(report), str(blocked.value)):
        assert secret not in text
        assert password not in text
    assert "passports.json" in str(blocked.value)


def test_token_pattern_leak_is_detected(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    path = ws / "data" / "linked_events.json"
    links = json.loads(path.read_text())
    links[0]["target"] = token
    path.write_text(json.dumps(links))
    report = eng.build_audit_report(cands, [])
    assert report["secret_scan"]["status"] == "FAIL"
    assert token not in json.dumps(report)


# ── Canonical report_sha256 ────────────────────────────────────────────────────

def test_report_sha256_is_independently_verifiable_from_report_json(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    report = eng.build_audit_report(cands, raw_values(ws))
    loaded = json.loads(unzip(build_audit_bundle(report, eng.data_dir))["report.json"])

    claimed = loaded.pop("report_sha256")
    recomputed = hashlib.sha256(json.dumps(loaded, sort_keys=True).encode("utf-8")).hexdigest()
    assert claimed == recomputed == report_sha256(report)

    loaded["summary"]["incidents"] = 0                    # any edit breaks the hash
    assert hashlib.sha256(json.dumps(loaded, sort_keys=True).encode("utf-8")).hexdigest() != claimed


# ── Bundle ─────────────────────────────────────────────────────────────────────

def test_bundle_contents_match_sha256sums_with_posix_paths(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    report = eng.build_audit_report(cands, raw_values(ws))
    files = unzip(build_audit_bundle(report, eng.data_dir, inputs=[eng.approvals_path, eng.policies_path]))

    assert set(files) == {
        "report.json", "report.md", "SHA256SUMS",
        "store/passports.json", "store/metadata_changes.json", "store/linked_events.json",
        "store/incidents.json", "store/policy_findings.json",
        "inputs/approvals.json", "inputs/policies.json",
    }
    assert all("\\" not in name for name in files)
    sums = files["SHA256SUMS"].decode("utf-8").splitlines()
    assert len(sums) == len(files) - 1
    for line in sums:
        digest, name = line.split("  ", 1)
        assert "\\" not in name
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert files["store/passports.json"] == (ws / "data" / "passports.json").read_bytes()


def test_export_is_read_only(ws: Path) -> None:
    eng, cands = full_demo_flow(ws)
    before = snapshot(ws / "data")
    report = eng.build_audit_report(cands, raw_values(ws))
    render_audit_markdown(report)
    build_audit_bundle(report, eng.data_dir, inputs=[eng.approvals_path, eng.policies_path])
    assert snapshot(ws / "data") == before


def test_real_mode_export_reads_only_the_local_store(ws: Path) -> None:
    full_demo_flow(ws)                                     # populate the demo store
    local = engine(ws, "data/local")
    cands = local.discover_tools([CURSOR])
    local.mint_passport(cands[0])

    report = local.build_audit_report(cands, raw_values(ws), mode="real-local-machine")
    assert report["mode"] == "real-local-machine"
    assert report["summary"]["passports"] == 1
    assert report["summary"]["linked_events"] == 0 and report["summary"]["incidents"] == 0
    files = unzip(build_audit_bundle(report, local.data_dir))
    assert files["store/passports.json"] == (ws / "data" / "local" / "passports.json").read_bytes()
    assert "store/linked_events.json" not in files
