"""
AutoDiscover — live demo UI.

    streamlit run app.py

Journey: Discover → Match → Suggest → Review → Link.

How the v2 toggle works
-----------------------
Discovery scans a *live config workspace* (``data/live_configs/fixtures/``) that
is restored from ``fixtures/`` on reset. "Load v2 Config Update" copies the v2
contents over the live ``claude_desktop_config.json``, which simulates the user
editing that file in place. Because the file path is unchanged, filesystem-mcp
keeps the same passport_id and the engine flags a metadata change on it, exactly
as in ``tests/test_acceptance.py``. The repo's own fixtures are never modified.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

import streamlit as st

from src.engine import (
    APPROVED,
    INCIDENT,
    MISSING_APPROVAL,
    REDACTED,
    UNKNOWN,
    AutoDiscoverEngine,
    build_audit_bundle,
    known_config_locations,
    raw_secret_values_from_configs,
    render_audit_markdown,
)
from src.provenance_view import lifecycle_dot, live_map_dot

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DATA = ROOT / "data"
LIVE = DATA / "live_configs"                 # base_dir for discovery
LOCAL_DATA = DATA / "local"                  # isolated registry for real-machine scans
LIVE_FIXTURES = LIVE / "fixtures"
CLAUDE = "fixtures/claude_desktop_config.json"
CURSOR = "fixtures/cursor_mcp.json"
CONFIGS = [CLAUDE, CURSOR]
EVENTS_FILE = FIXTURES / "runtime_events.jsonl"
APPROVALS_FILE = FIXTURES / "approvals.json"

st.set_page_config(page_title="AutoDiscover · Forkit Passports", page_icon="🛂", layout="wide")

# ── Styling: warm editorial theme (ivory canvas, serif headings, terracotta accent, TUM-blue info) ──
#
# One status palette for every tab, the sidebar and the provenance map:
#   PASS / APPROVED / CONFIRMED  green    UNKNOWN        amber
#   CRITICAL / HIGH / MISSING_*  red      MEDIUM / drift orange
#   LOW / CANDIDATE / neutral    slate    [REDACTED]     blue

st.markdown(
    """
<style>
:root {
  --ad-accent:#c96442; --ad-accent-dark:#a94f31; --ad-blue:#3070b3; --ad-ink:#141413; --ad-muted:#5e5d59;
  --ad-line:#e3dfd3; --ad-soft:#f0eee6; --ad-card:#fffefb;
  --ad-serif: 'Tiempos Text', Georgia, 'Iowan Old Style', 'Palatino Linotype', serif;
}
.block-container, [data-testid="stMainBlockContainer"] { padding: 1.1rem 2.5rem 2rem 2.5rem; max-width: 1560px; }
header[data-testid="stHeader"] { height: 2.25rem; background: transparent; }
h3 { margin-top: 0.2rem !important; }

/* Hero */
.hero { display:flex; flex-wrap:wrap; align-items:center; gap:8px 20px; padding:0 0 10px 0;
        border-bottom:1px solid var(--ad-line); margin-bottom:4px; }
.hero-eyebrow { font-size:0.72rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase;
                color:var(--ad-accent); margin-bottom:2px; }
.hero-title { font-family:var(--ad-serif); font-size:1.75rem; font-weight:500; color:var(--ad-ink);
              letter-spacing:-0.015em; line-height:1.15; }
.hero-title .accent { color:var(--ad-accent); font-style:italic; }
.pills { display:flex; flex-wrap:wrap; gap:6px; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:4px 12px; border-radius:999px;
        font-weight:650; font-size:0.86rem; color:var(--ad-ink); background:var(--ad-card);
        border:1px solid var(--ad-line); }
.pill::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--pill-dot); }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 2px; border-bottom:1px solid var(--ad-line); }
.stTabs [data-baseweb="tab"] { padding: 10px 16px; border-radius:8px 8px 0 0; }
.stTabs [data-baseweb="tab"] p { font-size:1.02rem; font-weight:650; }
.stTabs [data-baseweb="tab"]:hover { background: var(--ad-soft); }
.stTabs [aria-selected="true"] p { color: var(--ad-ink); }
h1, h2, h3 { font-family: var(--ad-serif) !important; font-weight: 500 !important; letter-spacing:-0.01em; }

/* Status badges */
.badge { display:inline-block; padding:3px 10px; border-radius:999px; font-weight:700;
         font-size:0.84rem; margin:2px 4px 2px 0; letter-spacing:0.02em; line-height:1.35; }
.b-ok       { background:#e3f6e8; color:#0f5a2a; border:1px solid #5fb87e; }
.b-unknown  { background:#fff4d6; color:#6e4a00; border:1px solid #d9a93a; }
.b-bad      { background:#fde6e6; color:#9b1419; border:1px solid #e06c70; }
.b-warn     { background:#ffe9d6; color:#843800; border:1px solid #ea9450; }
.b-slate    { background:#eceef1; color:#3d4654; border:1px solid #b3bac5; }
.b-neutral  { background:#efede6; color:#4a4943; border:1px solid #cdc7b8; }
.b-redacted { background:#e6eef7; color:#1f4e80; border:1px solid #8fb0d6; }

/* Cards */
.card { border:1px solid var(--ad-line); border-radius:12px; padding:14px 18px; margin-bottom:12px;
        background:var(--ad-card); box-shadow:0 1px 2px rgba(20,20,19,0.04);
        transition:border-color .15s ease, box-shadow .15s ease; }
.card:hover { border-color:#cfc6b4; box-shadow:0 3px 10px rgba(20,20,19,0.07); }
.card h4 { margin:0 0 6px 0; font-size:1.12rem; font-weight:650; color:var(--ad-ink); }
.card.merge-warn { border:2px solid #ea9450; background:#fffaf5; }
.card.drift      { border:2px solid #ea9450; border-left-width:6px; background:#fffaf5; }
.card.incident   { border:2px solid #d62828; border-left-width:6px; background:#fff5f5; }
.card.audit      { border:1px solid #b9cde4; border-left:6px solid var(--ad-blue); background:#f7f9fc; }
.muted { color:var(--ad-muted); }
.mono { font-family: Consolas, 'SF Mono', Menlo, ui-monospace, monospace; font-size:0.86rem; }
.kv { margin:3px 0; }
.kv b { display:inline-block; min-width:155px; color:var(--ad-muted); font-weight:600; }
.diff-del { color:#9b1419; } .diff-add { color:#0f5a2a; }

/* Buttons */
.stButton button, .stDownloadButton button { font-weight:650; transition: box-shadow .15s ease, transform .05s ease; }
.stButton button:hover, .stDownloadButton button:hover { box-shadow:0 2px 8px rgba(201,100,66,0.22); }
.stButton button:active, .stDownloadButton button:active { transform: translateY(1px); }

/* Sidebar */
[data-testid="stSidebarUserContent"] { padding-top: 0.25rem; }
[data-testid="stSidebarHeader"] { height: 2.25rem; padding-top: 0.75rem; padding-bottom: 0; }
section[data-testid="stSidebar"] hr { margin: 0.7rem 0 !important; border-color: var(--ad-line); }
.sb-brand { font-family:var(--ad-serif); font-size:1.45rem; font-weight:500; color:var(--ad-ink); line-height:1.2; }
.sb-tag { color:var(--ad-muted); font-size:0.82rem; margin-top:2px; }
.sb-section { font-size:0.72rem; font-weight:750; letter-spacing:0.06em; text-transform:uppercase;
              color:var(--ad-accent); margin:2px 0 8px 0; }
.tiles { display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-bottom:8px; }
.tile { background:var(--ad-card); border:1px solid var(--ad-line); border-radius:8px; padding:6px 8px; text-align:center; }
.tile .n { font-family:var(--ad-serif); font-size:1.65rem; font-weight:500; color:var(--ad-ink); line-height:1.15; }
.tile .l { font-size:0.72rem; color:var(--ad-muted); font-weight:600; line-height:1.2; }
.tile.alert { border-color:#e06c70; background:#fff5f5; }
.tile.alert .n { color:#9b1419; }
.sb-status { font-size:0.86rem; margin:2px 0; }
.sb-status b { display:inline-block; min-width:92px; color:var(--ad-muted); font-weight:600; }
</style>
""",
    unsafe_allow_html=True,
)


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="badge b-{kind}">{html.escape(text)}</span>'


def status_badge(status: str) -> str:
    """PASS green, FAIL red: the same colours as every other status pill."""
    return badge(status, "ok" if status == "PASS" else "bad")


def value_badge(value: Any) -> str:
    """Render UNKNOWN / REDACTED explicitly; everything else as mono text."""
    if value == UNKNOWN:
        return badge("UNKNOWN", "unknown")
    if value == REDACTED:
        return badge("[REDACTED]", "redacted")
    if value == "read-only":
        return badge("read-only", "ok")
    return f'<span class="mono">{html.escape(str(value))}</span>'


def sidebar_section(title: str) -> None:
    st.markdown(f'<div class="sb-section">{html.escape(title)}</div>', unsafe_allow_html=True)


DEMO_HINTS = {
    1: "Click **Scan**, point at the [REDACTED] card and the disk check, then open ②.",
    2: "Point at the SAME NAME and UNKNOWN badges, mint all three, then click **🔄 Simulate Restart** in the sidebar.",
    3: "Toggle **📝 Load v2 Config Update** in the sidebar, show the diff and why access mode is UNKNOWN, then open ④.",
    4: "Pick evt-0002 and click Link without consent (blocked), then tick consent and link (MISSING_APPROVAL + INCIDENT). "
       "Finish with evt-0003 on the filesystem-mcp passport (blocked), then open ⑤.",
    5: "Close the demo here: walk config → provenance hash → passport → events/incidents, all joined by passport_id.",
}


def real_mode() -> bool:
    return st.session_state.get("mode") == "real"


def demo_hint(step: int) -> None:
    if real_mode():
        return
    st.caption(f"🎬 Next demo step: {DEMO_HINTS[step]}")


SEVERITY_KIND = {"critical": "bad", "high": "bad", "medium": "warn", "low": "slate"}


def rule_badges(findings: list[dict[str, Any]]) -> str:
    return "".join(
        badge(f'{f["rule_id"]} · {f["severity"].upper()}', SEVERITY_KIND.get(f["severity"], "neutral"))
        for f in sorted(findings, key=lambda f: f["rule_id"])
    )


def refresh_policies() -> None:
    """Re-evaluate policy-as-code rules into the ACTIVE store (data/ or data/local/)."""
    engine().evaluate_policies(st.session_state.get("candidates") or [])


def short(pid: str) -> str:
    return pid[:12] + "…"


# ── Workspace / state helpers ──────────────────────────────────────────────────

def restore_live_configs(v2: bool) -> None:
    """Rebuild the live config workspace from fixtures/ (v1 or v2 Claude config)."""
    LIVE_FIXTURES.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "cursor_mcp.json", LIVE_FIXTURES / "cursor_mcp.json")
    src = "claude_desktop_config_v2.json" if v2 else "claude_desktop_config.json"
    shutil.copy(FIXTURES / src, LIVE_FIXTURES / "claude_desktop_config.json")


def engine() -> AutoDiscoverEngine:
    if real_mode():
        return AutoDiscoverEngine(data_dir=LOCAL_DATA, base_dir=ROOT, approvals_path=APPROVALS_FILE)
    return AutoDiscoverEngine(data_dir=DATA, base_dir=LIVE, approvals_path=APPROVALS_FILE)


def run_discovery() -> None:
    if real_mode():
        summary = engine().scan_local_machine()     # read-only; never raises for a bad file
        st.session_state.scan_summary = summary
        st.session_state.candidates = summary["candidates"]
    else:
        st.session_state.candidates = engine().discover_tools(CONFIGS)
    st.session_state.scanned = True
    refresh_policies()


def init_state() -> None:
    defaults = {
        "candidates": [],
        "scanned": False,
        "v2_active": False,
        "last_link": None,
        "last_error": None,
        "restart_proof": None,
        "flash": None,
        "mode": "demo",
        "scan_summary": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    if not LIVE_FIXTURES.exists():
        restore_live_configs(st.session_state.v2_active)


def load_events() -> list[dict[str, Any]]:
    return [json.loads(line) for line in EVENTS_FILE.read_text().splitlines() if line.strip()]


def scanned_config_paths() -> list[Path]:
    if real_mode():
        return [loc["path"] for loc in known_config_locations() if loc["path"] is not None]
    return [LIVE / cfg for cfg in CONFIGS]


def raw_secret_values() -> list[str]:
    """Raw env/header values from the scanned configs, held in memory only to prove none hit disk."""
    return raw_secret_values_from_configs(scanned_config_paths())


def leaked_secrets() -> list[str]:
    secrets = raw_secret_values()
    leaks = []
    for f in engine().data_dir.glob("*.json"):
        text = f.read_text()
        leaks += [f.name for s in secrets if s and s in text]
    return leaks


init_state()
eng = engine()

# ── Sidebar: demo superpowers ──────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="sb-brand">🛂 AutoDiscover</div>'
                '<div class="sb-tag">Zero-touch AI onboarding &amp; passports · built on Forkit Core '
                '<span class="mono">compute_id</span></div>', unsafe_allow_html=True)
    st.divider()

    # ── Section 1: Discovery Mode ──
    sidebar_section("Discovery Mode")
    mode_labels = {"demo": "🎭 Demo fixtures (default)", "real": "💻 Scan Real Local Machine"}
    picked = st.radio("Discovery mode", list(mode_labels), format_func=mode_labels.get,
                      index=list(mode_labels).index(st.session_state.mode), label_visibility="collapsed")
    if picked != st.session_state.mode:
        tab = st.session_state.get("active_tab")
        st.session_state.clear()
        st.session_state.mode = picked
        if tab:
            st.session_state.active_tab = tab
        restore_live_configs(v2=False)
        st.session_state.flash = (
            "info",
            "Real-machine mode: read-only scan of this computer's MCP configs. Results go to an isolated "
            "registry in `data/local/`. Usernames are masked as `~` and secrets are redacted."
            if picked == "real" else "Back to the deterministic demo fixtures.",
        )
        st.rerun()

    # ── Section 2: Demo Controls ──
    st.divider()
    sidebar_section("Demo Controls")
    if real_mode():
        if st.button("🧹 Clear Local Scan Registry", width="stretch", type="primary"):
            for f in LOCAL_DATA.glob("*.json"):
                f.unlink()
            st.session_state.clear()
            st.session_state.mode = "real"
            st.session_state.flash = ("success", "Local scan registry in `data/local/` cleared.")
            st.rerun()
    elif st.button("🧹 1-Click Reset Demo State", width="stretch", type="primary"):
        for f in DATA.glob("*.json"):
            f.unlink()
        shutil.rmtree(LIVE, ignore_errors=True)
        st.session_state.clear()
        restore_live_configs(v2=False)
        st.session_state.flash = ("success", "Demo reset: registry, links, incidents and changes cleared.")
        st.rerun()

    if st.button("🔄 Simulate Restart", width="stretch"):
        before = {c["passport_id"] for c in st.session_state.candidates}
        v2 = st.session_state.v2_active
        mode = st.session_state.mode
        tab = st.session_state.get("active_tab")
        st.session_state.clear()                       # wipe ALL in-memory state
        st.session_state.v2_active = v2                # config on disk did not change
        st.session_state.mode = mode
        if tab:
            st.session_state.active_tab = tab          # UI nicety only, not engine state
        run_discovery()                                # re-scan against saved registry
        after = {c["passport_id"] for c in st.session_state.candidates}
        registry = engine().passports()
        st.session_state.restart_proof = {
            "before": sorted(before),
            "after": sorted(after),
            "registry_count": len(registry),
            "recognized": sorted(after & set(registry)),
        }
        st.rerun()

    v2_toggle = False if real_mode() else st.toggle(
        "📝 Load v2 Config Update",
        value=st.session_state.v2_active,
        help="Simulates editing claude_desktop_config.json in place with the v2 contents.",
    )
    if not real_mode() and v2_toggle != st.session_state.v2_active:
        st.session_state.v2_active = v2_toggle
        restore_live_configs(v2=v2_toggle)
        run_discovery()
        fs_minted = any(
            c["tool_name"] == "filesystem-mcp" and c["source_path"] == CLAUDE and c["known"]
            for c in st.session_state.candidates
        )
        if v2_toggle and not fs_minted:
            st.session_state.flash = (
                "warning",
                "v2 config loaded, but filesystem-mcp has no passport yet, so there is nothing to diff against. "
                "For the demo: turn v2 off, mint filesystem-mcp in ②, then turn v2 on again.",
            )
        else:
            st.session_state.flash = (
                "warning" if v2_toggle else "info",
                "v2 config loaded: filesystem-mcp args changed on disk. See ③."
                if v2_toggle else "Reverted to the original v1 config.",
            )
        st.rerun()

    if real_mode():
        st.caption("Config mode: **real local machine**")
    else:
        st.caption(f"Config mode: **{'v2 (edited)' if st.session_state.v2_active else 'v1 (original)'}**")

    # ── Section 3: Live Governance Metrics & Audit Export (read-only snapshot) ──
    st.divider()
    sidebar_section("Live Governance Metrics & Audit Export")
    passports = eng.passports()
    n_incidents = len(eng.incidents())
    st.markdown(
        '<div class="tiles">'
        f'<div class="tile"><div class="n">{len(passports)}</div><div class="l">Confirmed passports</div></div>'
        f'<div class="tile"><div class="n">{len(eng.linked_events())}</div><div class="l">Linked events</div></div>'
        f'<div class="tile{" alert" if n_incidents else ""}"><div class="n">{n_incidents}</div>'
        '<div class="l">Incidents</div></div></div>',
        unsafe_allow_html=True,
    )
    audit = eng.build_audit_report(
        candidates=st.session_state.candidates,
        raw_secret_values=raw_secret_values(),
        mode="real-local-machine" if real_mode() else "demo",
    )
    integ, scan = audit["integrity"], audit["secret_scan"]
    st.markdown(
        f'<div class="sb-status"><b>Integrity</b>{status_badge(integ["status"])}'
        f'<span class="muted">{integ["passports_checked"]} passports</span></div>'
        f'<div class="sb-status"><b>Secret scan</b>{status_badge(scan["status"])}'
        f'<span class="muted">{len(scan["files_scanned"])} files</span></div>',
        unsafe_allow_html=True,
    )
    st.caption("Source of truth: `data/local/passports.json`" if real_mode()
               else "Source of truth: `data/passports.json`")
    stamp = audit["generated_at"][:19].replace("-", "").replace(":", "") + "Z"
    base = f"autodiscover-audit-{'local' if real_mode() else 'demo'}-{stamp}"
    try:
        bundle = build_audit_bundle(audit, eng.data_dir, inputs=[APPROVALS_FILE, eng.policies_path])
    except PermissionError as exc:
        st.error(f"🚫 {exc}. Fix the leak, then the download returns.")
    else:
        st.download_button("📦 Download Governance Audit Report", bundle, file_name=f"{base}.zip",
                           mime="application/zip", width="stretch", type="primary")
        c_json, c_md = st.columns(2)
        c_json.download_button("JSON", json.dumps(audit, indent=2, sort_keys=True), file_name=f"{base}.json",
                               mime="application/json", width="stretch")
        c_md.download_button("Markdown", render_audit_markdown(audit), file_name=f"{base}.md",
                             mime="text/markdown", width="stretch")

# ── Header ─────────────────────────────────────────────────────────────────────

PRINCIPLES = [("Metadata-only", "#3070b3"), ("Unknown stays UNKNOWN", "#d9a93a"),
              ("No false merges", "#ea9450"), ("Consent-gated linking", "#2f9e5a")]
st.markdown(
    '<div class="hero"><div><div class="hero-eyebrow">Forkit AI Hackathon · AI governance</div>'
    '<div class="hero-title">AutoDiscover: <span class="accent">Zero-Touch AI '
    'Onboarding &amp; Passports</span></div></div><div class="pills">'
    + "".join(f'<span class="pill" style="--pill-dot:{dot}">{html.escape(t)}</span>' for t, dot in PRINCIPLES)
    + "</div></div>",
    unsafe_allow_html=True,
)

if st.session_state.flash:
    kind, msg = st.session_state.flash
    getattr(st, kind)(msg)
    st.session_state.flash = None

proof = st.session_state.restart_proof
if proof and proof["registry_count"] == 0:
    st.info("Restart simulated, but the registry is empty so there is nothing to recognize yet. "
            "Scan in ①, mint in ②, then click **🔄 Simulate Restart** again.")
elif proof:
    same = proof["after"] == proof["recognized"] and (not proof["before"] or proof["before"] == proof["after"])
    keys = [(p["source_path"], p["tool_name"]) for p in eng.passports().values()]
    dupes = len(keys) - len(set(keys))
    if same and dupes == 0:
        st.success(
            f"✅ RESTART RECOGNIZED: in-memory state wiped, re-scan matched "
            f"{len(proof['recognized'])}/{len(proof['after'])} tools to existing passports. "
            f"Same passport_ids, {proof['registry_count']} passports in registry, 0 duplicates."
        )
    else:
        st.warning(
            f"Restart re-scan: {len(proof['recognized'])}/{len(proof['after'])} tools already have passports "
            f"({dupes} duplicates). Mint the rest in ② to see them recognized next time."
        )
    with st.expander("Passport IDs before vs. after restart"):
        st.json({"before_restart": proof["before"], "after_restart": proof["after"]})

# Keyed, stateful tabs: clicking a button inside a tab keeps you on that tab.
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["① Discover", "② Identity & Honesty Review", "③ Metadata Diff & Governance", "④ Consented Linking & Incidents",
     "⑤ Provenance Map"],
    key="active_tab",
    on_change="rerun",
)

candidates: list[dict[str, Any]] = st.session_state.candidates

# ── ① Discover ─────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Step 1 · Local Discovery (Metadata-Only)")
    demo_hint(1)
    if real_mode():
        st.caption("Read-only scan of the well-known Claude Desktop, Cursor and VS Code MCP config locations "
                   "on this computer. Nothing is written to those files.")
        scan_label = "💻 Scan Real Local Machine"
    else:
        st.caption("Scans local MCP configs: " + ", ".join(f"`{c}`" for c in CONFIGS))
        scan_label = "🔍 Scan Local MCP Configs"
    if st.button(scan_label, type="primary"):
        run_discovery()
        st.session_state.restart_proof = None
        st.rerun()

    summary = st.session_state.scan_summary
    if real_mode() and st.session_state.scanned and summary:
        st.markdown(f"**Locations checked** · platform `{summary['platform']}` · "
                    f"{summary['configs_found']} config file(s) found")
        st.dataframe([{
            "client": r["client"], "location": r["location"], "status": r["status"],
            "servers": r["servers"], "note": r["error"] or "",
        } for r in summary["locations"]], width="stretch", hide_index=True)
        if not candidates:
            st.info("No MCP servers found on this machine. That's a clean result, not an error: none of the "
                    "known config files exist or contain servers. If you run this in a cloud container, "
                    "launch the app on your own computer instead.")

    if not st.session_state.scanned:
        st.info(f"Click **{scan_label}** to start.")
    elif candidates:
        rows = [{
            "tool_name": c["tool_name"],
            "client": c["client"],
            "source_path": c["source_path"],
            "command": c["command"],
            "args": " ".join(c["args"]),
            "env": ", ".join(f'{k}="{v}"' for k, v in c["env"].items()) or "—",
            "owner": c["owner"],
            "version": c["version"],
            "access_mode": c["access_mode"],
            "config_hash": c["config_hash"][:12] + "…",
        } for c in candidates]
        st.dataframe(rows, width="stretch", hide_index=True)

        redacted = [(c, k) for c in candidates for k, v in c["env"].items() if v == REDACTED]
        if redacted:
            items = "".join(
                f'<div class="kv"><b>{html.escape(c["tool_name"])}</b>'
                f'<span class="mono">{html.escape(k)}: </span>{badge("[REDACTED]", "redacted")}'
                f' <span class="mono">({html.escape(c["client"])})</span></div>'
                for c, k in redacted
            )
            st.markdown(
                f'<div class="card"><h4>🔒 Secrets stripped at discovery</h4>{items}'
                f'<div class="muted" style="margin-top:8px">Raw values are never hashed, stored, or displayed. '
                f'Only the env <i>key names</i> are kept.</div></div>',
                unsafe_allow_html=True,
            )
        leaks = leaked_secrets()
        if leaks:
            st.error(f"Secret value found in: {', '.join(sorted(set(leaks)))}")
        else:
            store = "data/local/*.json" if real_mode() else "data/*.json"
            st.success(f"Disk check: 0 of {len(raw_secret_values())} raw secret values appear anywhere in `{store}`.")

# ── ② Identity & Honesty Review ────────────────────────────────────────────────

with tab2:
    st.subheader("Step 2 · Identity & Honesty Review")
    demo_hint(2)
    st.caption(
        "passport_id = compute_id(passport_type='mcp_server', name, version='UNKNOWN', "
        "creator_name='UNKNOWN', artifact_hash=sha256(source_path + tool_name + command))"
    )
    if not candidates:
        st.info("Run discovery in Step 1 first.")
    else:
        name_sources: dict[str, set[str]] = {}
        for c in candidates:
            name_sources.setdefault(c["tool_name"], set()).add(c["source_path"])
        passports = eng.passports()
        if st.session_state.v2_active and any(
            c["source_path"] == CLAUDE and c["passport_id"] not in passports for c in candidates
        ):
            st.warning("You are on the edited **v2** config. Minting now records v2 as the baseline, so ③ will "
                       "have no diff to show. For the demo, turn v2 off in the sidebar first and mint on v1.")

        cols = st.columns(len(candidates))
        for col, c in zip(cols, candidates, strict=True):
            with col:
                collision = len(name_sources[c["tool_name"]]) > 1
                minted = c["passport_id"] in passports
                status = badge("CONFIRMED", "ok") if minted else badge("CANDIDATE", "neutral")
                warn = (
                    f'<div style="margin:6px 0">{badge("⚠ SAME NAME — DO NOT MERGE: Different Provenance", "warn")}</div>'
                    if collision else ""
                )
                changed = badge("METADATA CHANGED", "bad") if c.get("metadata_changed") else ""
                st.markdown(
                    f'<div class="card {"merge-warn" if collision else ""}">'
                    f'<h4>{html.escape(c["tool_name"])}</h4>{status}{changed}{warn}'
                    f'<div style="margin:4px 0">{rule_badges(c.get("policy_findings", []))}</div>'
                    f'<div class="kv"><b>client</b>{value_badge(c["client"])}</div>'
                    f'<div class="kv"><b>source_path</b>{value_badge(c["source_path"])}</div>'
                    f'<div class="kv"><b>command</b>{value_badge(c["command"])}</div>'
                    f'<div class="kv"><b>owner</b>{value_badge(c["owner"])}</div>'
                    f'<div class="kv"><b>version</b>{value_badge(c["version"])}</div>'
                    f'<div class="kv"><b>access_mode</b>{value_badge(c["access_mode"])}</div>'
                    f'<div class="kv"><b>passport_id</b><span class="mono">{short(c["passport_id"])}</span></div>'
                    f'<div class="kv"><b>provenance</b><span class="mono">{c["provenance_hash"][:12]}…</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if minted:
                    st.button("✅ Passport minted", key=f"mint_{c['passport_id']}", disabled=True,
                              width="stretch")
                elif st.button("🪪 Single-Click Mint Stable ID", key=f"mint_{c['passport_id']}",
                               type="primary", width="stretch"):
                    pid = eng.mint_passport(c)
                    refresh_policies()
                    st.session_state.restart_proof = None  # stale once the registry changes
                    st.session_state.flash = ("success", f"Minted {c['tool_name']} → {pid}")
                    st.rerun()

        if any(len(s) > 1 for s in name_sources.values()):
            pg = [c for c in candidates if len(name_sources[c["tool_name"]]) > 1]
            st.warning(
                "**No false merge.** "
                + " vs. ".join(f"`{c['tool_name']}` @ `{c['source_path']}` → `{short(c['passport_id'])}`" for c in pg)
                + ". Same name, different provenance, so they get different passport IDs."
            )

# ── ③ Metadata Diff & Governance ───────────────────────────────────────────────

with tab3:
    st.subheader("Step 3 · Metadata Diff & Governance Alerts")
    demo_hint(3)
    changed = [c for c in candidates if c.get("metadata_changed")]
    if real_mode():
        if not changed:
            st.info("No config drift on confirmed passports. To see it, mint a tool in ②, edit that tool's "
                    "args in your MCP config, then re-scan in ①.")
    elif not st.session_state.scanned:
        st.info("Run discovery in ① and mint `filesystem-mcp` in ② first.")
    elif not st.session_state.v2_active:
        st.info("Toggle **📝 Load v2 Config Update** in the sidebar to simulate an in-place config edit.")
    elif not changed:
        st.info("v2 is loaded, but no confirmed passport changed. Mint `filesystem-mcp` in Step 2 first, "
                "then toggle v2 off and on again.")
    for c in changed:
        stored = eng.passports()[c["passport_id"]]
        lines = []
        for d in c["diff"]:
            cls = "diff-add" if " added" in d else "diff-del" if (" removed" in d or "changed" in d) else ""
            lines.append(f'<div class="mono {cls}">• {html.escape(d)}</div>')
        reason = ""
        if stored.get("access_mode") != c["access_mode"] and c["access_mode"] == UNKNOWN:
            reason = (
                f'<div style="margin-top:10px"><b>Why access_mode is now {badge("UNKNOWN", "unknown")}:</b> '
                "the read-only evidence (<span class='mono'>,ro</span> on the bind mount) is gone from the config. "
                "We will not assume <i>read-write</i> either, so it stays UNKNOWN until a human reviews it.</div>"
            )
        st.markdown(
            f'<div class="card drift">'
            f'<h4>⚠ METADATA CHANGED · {html.escape(c["tool_name"])} '
            f'<span class="mono">({short(c["passport_id"])})</span></h4>'
            f'{badge("Same passport_id", "ok")}{badge("PENDING_REVIEW", "warn")}'
            f'<div class="kv"><b>config_hash</b><span class="mono">{stored["config_hash"][:12]}… → '
            f'{c["config_hash"][:12]}…</span></div>'
            f'<div style="margin-top:8px">{"".join(lines)}</div>{reason}'
            f'<div class="muted" style="margin-top:10px">The confirmed passport is unchanged. Drift is recorded in '
            f'<span class="mono">data/metadata_changes.json</span>.</div></div>',
            unsafe_allow_html=True,
        )
    log = eng.metadata_changes()
    if log:
        with st.expander(f"metadata_changes.json ({len(log)})"):
            st.json(log)

    st.markdown("#### 🛡️ Policy-as-code findings")
    st.caption("Declarative rules from `fixtures/policies.json`. Each finding lists the exact facts it used; "
               "missing facts are never guessed.")
    findings = eng.policy_findings()
    if not findings:
        st.info("No findings yet. Scan in ① to evaluate the rules.")
    else:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        counts = {sev: sum(f["severity"] == sev for f in findings) for sev in order}
        st.markdown(" ".join(badge(f"{n} {sev.upper()}", SEVERITY_KIND[sev]) for sev, n in counts.items() if n),
                    unsafe_allow_html=True)
        for f in sorted(findings, key=lambda f: (order.get(f["severity"], 9), f["rule_id"], f["subject_id"])):
            ev = ", ".join(f"{k}={v}" for k, v in f["evidence"].items())
            st.markdown(
                f'<div class="card"><h4>{rule_badges([f])} {html.escape(f["title"])}</h4>'
                f'<div class="kv"><b>Tool</b><span class="mono">{html.escape(f["tool_name"])} '
                f'({short(f["passport_id"])}) · {html.escape(f["subject_type"])}</span></div>'
                f'<div class="kv"><b>Evidence</b><span class="mono">{html.escape(ev)}</span></div>'
                f'<div class="muted" style="margin-top:4px">{html.escape(f["explanation"])}</div></div>',
                unsafe_allow_html=True,
            )

# ── ④ Consented Linking & Incidents ────────────────────────────────────────────

with tab4:
    st.subheader("Step 4 · Consented Event Linking & Incident Log")
    demo_hint(4)
    events = load_events()
    passports = eng.passports()
    if real_mode():
        st.info("Event linking runs on the demo's runtime event fixtures, whose sources point at the fixture "
                "configs. Switch to **🎭 Demo fixtures** in the sidebar to link events.")
    elif not passports:
        st.info("No passports yet. Scan in ① and mint at least `filesystem-mcp` in ② before linking events.")
    else:
        left, right = st.columns([1, 1])
        with left:
            ev_labels = {
                e["event_id"]: f'{e["event_id"]} · {e["source"]["server_name"]} · {e["tool_call"]} · {e["result"]}'
                for e in events
            }
            ev_id = st.selectbox("Runtime event", list(ev_labels), format_func=ev_labels.get, key="ev_select")
            event = next(e for e in events if e["event_id"] == ev_id)
            st.json(event, expanded=False)
        with right:
            pids = list(passports)
            labels = {p: f'{passports[p]["tool_name"]} @ {passports[p]["source_path"]} · {short(p)}' for p in pids}
            match = next(
                (p for p in pids if passports[p]["tool_name"] == event["source"]["server_name"]
                 and passports[p]["source_path"] == event["source"]["config_path"]),
                None,
            )
            if match is None:
                st.warning(
                    f"The passport for this event's source (`{event['source']['server_name']}` @ "
                    f"`{event['source']['config_path']}`) is not minted yet. Mint it in ② to link this event. "
                    "Linking to another passport will be blocked as a source mismatch."
                )
                match = pids[0]
            target = st.selectbox("Target passport_id", pids, index=pids.index(match),
                                  format_func=labels.get, key=f"target_{ev_id}")
            consent = st.checkbox("I grant explicit consent to link this event", key=f"consent_{ev_id}")
            if st.button("🔗 Link Event", type="primary", width="stretch"):
                try:
                    st.session_state.last_link = eng.link_event(event, target, user_consent=consent)
                    refresh_policies()
                    st.session_state.last_error = None
                except (PermissionError, ValueError, KeyError) as exc:
                    st.session_state.last_link = None
                    msg = exc.args[0] if exc.args else str(exc)
                    st.session_state.last_error = f"{type(exc).__name__}: {msg}"
                st.rerun()

        if st.session_state.last_error:
            st.error(f"🚫 Link blocked by engine. {st.session_state.last_error}")

        link = st.session_state.last_link
        if link:
            appr = badge(APPROVED, "ok") if link["approval_status"] == APPROVED else badge(MISSING_APPROVAL, "bad")
            src = link["source"] if isinstance(link["source"], dict) else {}
            st.markdown(
                f'<div class="card audit"><h4>🧾 Audit record · {html.escape(link["link_id"])}</h4>'
                f'<div class="kv"><b>Event</b><span class="mono">{html.escape(link["event_id"])} · '
                f'{html.escape(link["tool_call"])} → {html.escape(str(link["target"]))} ({html.escape(link["result"])})</span></div>'
                f'<div class="kv"><b>Source</b><span class="mono">{html.escape(src.get("client", UNKNOWN))} · '
                f'{html.escape(src.get("config_path", UNKNOWN))} · {html.escape(src.get("server_name", UNKNOWN))}</span></div>'
                f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(link["timestamp"])}</span></div>'
                f'<div class="kv"><b>Passport</b><span class="mono">{short(link["passport_id"])}</span></div>'
                f'<div class="kv"><b>Approval</b>{appr} {value_badge(link["approval_id"])} '
                f'<span class="muted">(verified against fixtures/approvals.json)</span></div>'
                f'<div class="kv"><b>Runtime consent</b>{value_badge(link["runtime_consent_id"])}</div>'
                f'<div class="kv"><b>Link consent</b>{badge("GRANTED", "ok")}</div>'
                f'<div class="kv"><b>Policy</b>'
                f'{"".join(badge(r, "bad") for r in link.get("policy_flags", [])) or badge("no rule violated", "ok")}'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            if INCIDENT in link["flags"]:
                inc = next((i for i in eng.incidents() if i["incident_id"] == link["incident_id"]), None)
                if inc:
                    st.markdown(
                        f'<div class="card incident"><h4 style="color:#9b1419;font-size:1.35rem">'
                        f'🚨 INCIDENT RECORDED: Out-of-Expected-Behavior</h4>'
                        f'<div class="kv"><b>Incident</b><span class="mono">{html.escape(inc["incident_id"])}</span></div>'
                        f'<div class="kv"><b>Tool</b><span class="mono">{html.escape(inc["tool_name"])} '
                        f'({short(inc["passport_id"])})</span></div>'
                        f'<div class="kv"><b>Attempted</b><span class="mono">{html.escape(inc["tool_call"])} → '
                        f'{html.escape(str(inc["target"]))}</span> {badge(inc["result"].upper(), "bad")}</div>'
                        f'<div class="kv"><b>Expected</b>{badge(inc["expected_behavior"], "ok")}</div>'
                        f'<div class="kv"><b>Approval</b>{badge(inc["approval_status"], "bad" if inc["approval_status"] != APPROVED else "ok")}</div>'
                        f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(inc["timestamp"])}</span></div>'
                        f'<div class="muted" style="margin-top:8px">Written to <span class="mono">data/incidents.json</span>.</div></div>',
                        unsafe_allow_html=True,
                    )

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Linked events** · `data/linked_events.json`")
            st.dataframe([{
                "event": lk["event_id"], "passport": short(lk["passport_id"]), "tool_call": lk["tool_call"],
                "timestamp": lk["timestamp"], "approval": lk["approval_status"], "flags": ", ".join(lk["flags"]) or "—",
            } for lk in eng.linked_events()], width="stretch", hide_index=True)
        with c2:
            st.markdown("**Incident log** · `data/incidents.json`")
            st.dataframe([{
                "incident": i["incident_id"], "tool": i["tool_name"], "tool_call": i["tool_call"],
                "timestamp": i["timestamp"], "result": i["result"],
            } for i in eng.incidents()], width="stretch", hide_index=True)

# ── ⑤ Provenance Map ───────────────────────────────────────────────────────────

with tab5:
    st.subheader("Step 5 · Visual Provenance & Lifecycle Flow")
    demo_hint(5)
    store = eng.data_dir                              # data/ (demo) or data/local/ (real machine), read-only
    st.markdown("**How identity flows**: from client config to a stable passport. Approvals, drift, events, "
                "incidents and policy findings attach by `passport_id` and never mutate the passport.")
    st.graphviz_chart(lifecycle_dot(store), width="stretch")

    st.markdown("**Live provenance map**")
    map_passports = eng.passports()
    focus_options = [None] + sorted(map_passports)
    focus = st.selectbox(
        "Focus", focus_options, key="map_focus",
        format_func=lambda p: "All tools" if p is None else
        f'{map_passports[p]["tool_name"]} @ {map_passports[p]["source_path"]} · {short(p)}',
    )
    dot = live_map_dot(store, candidates=st.session_state.candidates, focus=focus)
    if dot is None:
        st.info("Nothing to map yet. Scan in ① (and mint in ②) and the live map of configs, passports, "
                "events and incidents appears here.")
    else:
        st.graphviz_chart(dot, width="stretch")
        st.caption("Solid box = confirmed passport (integrity re-derived from provenance) · dashed = candidate "
                   "not minted yet · red = MISSING_APPROVAL / incident · orange = drift or same-name tools "
                   "kept apart.")
