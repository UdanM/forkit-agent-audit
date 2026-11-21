"""
AutoDiscover: live demo UI.

    streamlit run app.py

Journey: Discover → Match → Suggest → Review → Link.

How the v2 toggle works
-----------------------
Discovery scans a *live config workspace* (``data/live_configs/fixtures/``) that
is restored from ``fixtures/`` on reset. "Simulate a settings edit" copies the v2
contents over the live ``claude_desktop_config.json``, which simulates the user
editing that file in place. Because the file path is unchanged, filesystem-mcp
keeps the same passport_id and the engine flags a metadata change on it, exactly
as in ``tests/test_acceptance.py``. The repo's own fixtures are never modified.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from src.ask import ask as ask_question
from src.audit_archive import ArchiveError, git_available
from src.capture.events import read_events
from src.discovery_host import discovered_on_label, machine_name, record_discovery
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
    workspace_config_locations,
)
from src.live_map_html import lifecycle_html, live_map_html
from src.timeline import make_checkpoint, read_log, timeline_from_files
from src.timeline_html import activity_feed_html, findings_html

KIND_LABELS = {"mcp_server": "MCP server", "cli_tool": "CLI", "desktop_app": "Desktop app",
               "editor_extension": "Editor extension"}

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DATA = ROOT / "data"
# Hosted demo (e.g. Streamlit Community Cloud): set AUTODISCOVER_PER_SESSION=1 so every visitor gets
# their own data folder and two people clicking at once never see each other's passports.
if os.environ.get("AUTODISCOVER_PER_SESSION") == "1":
    # Keyed by Streamlit's browser-session id, which survives the in-app "Simulate app restart" (that only
    # clears st.session_state), so a visitor's own registry is still there to be recognized.
    _ctx = get_script_run_ctx()
    DATA = DATA / "sessions" / ((_ctx.session_id if _ctx else uuid.uuid4().hex).replace("-", "")[:16])
LIVE = DATA / "live_configs"                 # base_dir for discovery
LOCAL_DATA = DATA / "local"                  # isolated registry for real-machine scans
LIVE_FIXTURES = LIVE / "fixtures"
ACTIVITY_FIXTURES = FIXTURES / "activity"
ACTIVITY_LIVE = DATA / "activity_live"       # demo copy of the observer log, so tampering never touches fixtures/
CLAUDE = "fixtures/claude_desktop_config.json"
CURSOR = "fixtures/cursor_mcp.json"
CONFIGS = [CLAUDE, CURSOR]
EVENTS_FILE = FIXTURES / "runtime_events.jsonl"
APPROVALS_FILE = FIXTURES / "approvals.json"
# Live agents: what demo/agents/run_scenarios.py captured from real agent runs (local tap + n8n collector).
AGENT_ROOT = ROOT if os.environ.get("AUTODISCOVER_PER_SESSION") != "1" else DATA / "agent_run"
AGENTS = AGENT_ROOT / "data" / "agents"
AGENT_APPROVALS = ROOT / "demo" / "agents" / "approvals.json"
AGENT_CONFIGS = ("claude_desktop_config.json", "cursor_mcp.json", "n8n_mcp.json")
ORIGIN_LABEL = {"local-tap": "local tap", "cloud-n8n": "cloud n8n", "cloud-collector": "cloud collector"}

st.set_page_config(page_title="AutoDiscover · Forkit Passports", page_icon="🛂", layout="wide")

# ── Styling: warm editorial skin on the SaaS layout (alabaster canvas, espresso ink, terracotta accent) ──
#
# One status palette for every tab, the sidebar and the provenance map:
#   PASS / APPROVED / CONFIRMED  green    UNKNOWN        amber
#   CRITICAL / HIGH / MISSING_*  red      MEDIUM / drift orange
#   LOW / CANDIDATE / neutral    slate    [REDACTED]     blue

st.markdown(
    """
<style>
/* Dark navy and lavender look, taken from UdanM/forkit-agent-audit @ 3f5b2aa (index.html) so both
   halves of the demo look the same. One meaning per colour: mint ok, amber unknown, rose bad. */
:root {
  --ad-ink:#EEF1FF; --ad-muted:#B1BBDA; --ad-line:#2D3654; --ad-soft:#171E36; --ad-card:#131A30;
  --ad-canvas:#090C18; --ad-accent:#BEAFFF; --ad-accent-ink:#1A1239; --ad-link:#C6B9FF;
  --ad-sans:'Segoe UI', system-ui, -apple-system, Roboto, 'Helvetica Neue', Arial, sans-serif;
  --ad-mono:'JetBrains Mono', Consolas, 'SF Mono', Menlo, ui-monospace, monospace;
}
[data-testid="stDeployButton"], .stAppDeployButton, [data-testid="stMainMenu"],
[data-testid="stToolbar"] { display:none !important; }
header[data-testid="stHeader"] { height:2rem; background:transparent; }
.block-container, [data-testid="stMainBlockContainer"] { padding:0.9rem 2rem 2.5rem 2rem; max-width:1600px; }
h1, h2, h3, h4 { font-family:var(--ad-sans) !important; color:var(--ad-ink); letter-spacing:-0.01em; }
h3 { font-weight:650 !important; margin-top:0.2rem !important; }
code, .mono { font-family:var(--ad-mono) !important; }
::selection { background:rgba(190,175,255,0.3); }
:focus-visible { outline:2px solid #9EF4F1 !important; outline-offset:3px; }

/* Hero, as in the teammate's dashboard */
.hero { position:relative; overflow:hidden; isolation:isolate; margin:0 0 16px; border:1px solid #323967;
  border-radius:24px; padding:34px 40px;
  background:radial-gradient(ellipse at 82% 55%, #302761 0, transparent 57%), linear-gradient(135deg, #1B2145, #101529 65%); }
.hero h1 { max-width:14ch; font:700 2.9rem/1.02 var(--ad-sans) !important; letter-spacing:-0.055em; margin:0 0 10px !important;
  padding:0 !important; color:var(--ad-ink) !important; position:relative; z-index:1; }
.hero p { color:var(--ad-muted); margin:0; font-size:1rem; position:relative; z-index:1; max-width:44rem; }
.hero-art { display:grid; place-items:center; width:210px; aspect-ratio:1; position:absolute; z-index:-1; right:6%;
  top:50%; transform:translateY(-50%); border:1px solid #998cfa58; border-radius:50%;
  background:radial-gradient(circle, #a088ff30, transparent 72%); box-shadow:0 0 90px #8e78ff30; }
.hero-art::before, .hero-art::after { content:""; position:absolute; inset:15%; border:1px solid #9d8aff80; border-radius:50%; }
.hero-art::after { inset:32%; border-style:dashed; }
.hero-art b { display:grid; place-items:center; width:28%; aspect-ratio:1; border-radius:24%;
  background:linear-gradient(135deg, #b8a7ff, #7254ea); color:#160f36; font-size:2.2rem; box-shadow:0 0 55px #ae94ff8a; }
.hero-art i { position:absolute; width:10px; aspect-ratio:1; border-radius:50%; background:#9ef4f1; box-shadow:0 0 16px #9ef4f1; }
.hero-art i:nth-of-type(1) { left:13%; top:28%; }
.hero-art i:nth-of-type(2) { right:11%; bottom:29%; background:#ffb8ff; box-shadow:0 0 16px #ffb8ff; }
.hero-art i:nth-of-type(3) { right:20%; top:20%; }
@media (max-width:900px) { .hero-art { opacity:.37; right:-16px; } }

/* View switcher: pill row like the teammate's .views */
.stTabs [role="tablist"] { gap:7px !important; border-bottom:0 !important; padding:6px; width:fit-content; max-width:100%;
  border:1px solid #272F4E; border-radius:14px; background:#0F1427; }
.stTabs [data-testid="stTab"] { padding:7px 13px !important; border-radius:10px; border:1px solid transparent; background:transparent; }
.stTabs [data-testid="stTab"] p { font-size:0.92rem; font-weight:650; color:#E4E8FF; }
.stTabs [data-testid="stTab"]:hover { outline:2px solid #9787F2; }
.stTabs [data-testid="stTab"][aria-selected="true"] { background:#322956; border-color:#514278; }
.stTabs [data-testid="stTab"][aria-selected="true"] p { color:#EFEBFF !important; }
.stTabs .react-aria-SelectionIndicator { display:none !important; }

/* Tags: mint by default, amber for warnings, as in the teammate's .tag */
.badge { display:inline-block; padding:2px 9px; border-radius:999px; font-weight:600; font-family:var(--ad-sans);
  font-size:0.76rem; margin:2px 4px 2px 0; line-height:1.5; vertical-align:middle; font-variant-numeric:tabular-nums; }
.b-ok { color:#AFF3DC; background:#153632; }
.b-unknown, .b-warn { color:#FFDB9D; background:#392C25; }
.b-bad { color:#FFC2CD; background:#3A1A26; }
.b-slate, .b-neutral { color:#C5CBED; background:#1C2340; }
.b-redacted { color:#C9DBFF; background:#1B2445; }

/* Cards: the teammate's article */
.card { border:1px solid #2F395A; border-radius:16px; padding:18px 20px; margin-bottom:12px;
  background:linear-gradient(145deg, #161D34, #111629); }
.card h4, .card-body h4 { margin:0 0 8px 0; font-size:1.08rem; font-weight:650; color:var(--ad-ink); overflow-wrap:anywhere; }
.card.drift { border-color:#6B5A3A; } .card.incident { border-color:#7A3348; } .card.incident h4 { color:#FFC2CD; }
.card.audit { border-color:#3B456A; }
.card-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:6px; }
.card-head h4 { margin:0 !important; }
.muted { color:var(--ad-muted); }
.mono { font-size:0.84rem; color:#D7DCF5; }
.purpose { font-size:0.98rem; line-height:1.55; color:var(--ad-muted); max-width:62rem; margin:-4px 0 14px 0; }
.kv { display:flex; flex-wrap:nowrap; align-items:baseline; column-gap:6px; margin:3px 0; }
.kv > b { flex:0 0 140px; color:var(--ad-muted); font-weight:500; font-size:0.9rem; }
.kv > :not(b) { min-width:0; } .kv .mono { word-break:break-all; }
.diff-del { color:#FF8FA3; } .diff-add { color:#7FE0BE; }
.note-warn { border-left:2px solid #FFC988; padding:2px 0 2px 10px; margin:4px 0 8px 0; font-size:0.88rem; color:#FFDB9D; }

/* Summary row */
.ov { display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin:4px 0 16px 0; }
.ov-tile { background:linear-gradient(145deg, #161D34, #111629); border:1px solid #2F395A; border-radius:16px; padding:14px 18px 12px; }
.ov-label { font-size:0.8rem; color:var(--ad-muted); }
.ov-value { font-size:2rem; font-weight:650; line-height:1.25; color:var(--ad-ink); font-variant-numeric:tabular-nums; }
.ov-note { font-size:0.8rem; color:var(--ad-muted); }
.ov-tile.alert { border-color:#7A3348; } .ov-tile.alert .ov-value { color:#FFC2CD; }
.ov-tile.warn { border-color:#6B5A3A; } .ov-tile.warn .ov-value { color:#FFDB9D; }

/* Next step note: the teammate's comparison-result strip */
.nextstep { display:flex; gap:12px; align-items:baseline; margin:0 0 16px; padding:11px 14px; background:#221D3D;
  border-left:3px solid #AC96FF; border-radius:5px; font-size:0.93rem; color:var(--ad-ink); }
.nextstep > span { flex:none; font-size:0.72rem; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; color:#C6B9FF; }
.nextstep code { font-size:0.82rem; background:#171E36; padding:0 4px; border-radius:4px; }

/* Framed containers (identity cards, diagrams) */
[data-testid="stHorizontalBlock"]:has(.card-body) { align-items:stretch; }
[data-testid="stHorizontalBlock"]:has(.card-body) > [data-testid="stColumn"] > div,
[data-testid="stHorizontalBlock"]:has(.card-body) > [data-testid="stColumn"] [data-testid="stVerticalBlock"] { height:100%; }
[class*="st-key-idcard_"], [class*="st-key-graphframe_"] { background:linear-gradient(145deg, #161D34, #111629);
  border-radius:16px !important; border-color:#2F395A !important; }
[class*="st-key-idcard_"] > div:has(.card-body) { flex:1 1 auto; }
[class*="st-key-idcard_collision_"] { border-color:#6B5A3A !important; }

/* Buttons: the teammate's flat buttons, lavender primary */
.stButton button, .stDownloadButton button { font-weight:650; border-radius:10px; box-shadow:none;
  background:#171E36; border:1px solid #3B456A; color:#E4E8FF; }
.stButton button:hover, .stDownloadButton button:hover { outline:2px solid #9787F2; border-color:#3B456A; color:#E4E8FF; background:#171E36; }
.stButton button[kind="primary"], .stDownloadButton button[kind="primary"] { background:var(--ad-accent);
  border:1px solid var(--ad-accent); color:var(--ad-accent-ink); }
.stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover { background:#CEC2FF; color:var(--ad-accent-ink); }
.stButton button:disabled { background:#153632 !important; border:1px solid #255248 !important; color:#AFF3DC !important;
  opacity:1 !important; cursor:default; outline:0 !important; }
[data-testid="stExpander"] details { border-radius:12px; background:var(--ad-card); border-color:var(--ad-line); }
[data-testid="stExpander"] summary { color:var(--ad-link); }
[data-testid="stAlert"] > div { border-radius:8px; }
[data-testid="stCaptionContainer"] p { color:var(--ad-muted); }

/* Sidebar */
section[data-testid="stSidebar"] { border-right:1px solid #232943; }
[data-testid="stSidebarUserContent"] { padding-top:0.25rem; }
[data-testid="stSidebarHeader"] { height:2rem; padding-top:0.6rem; padding-bottom:0; }
section[data-testid="stSidebar"] hr { margin:0.8rem 0 !important; border-color:var(--ad-line); }
.sb-brand { display:flex; align-items:center; gap:10px; font-size:1.1rem; font-weight:700; color:var(--ad-ink); }
.brand-mark { display:grid; place-items:center; width:30px; height:30px; border-radius:9px; background:#B3A3FF; color:#161033; font-size:1rem; }
.sb-tag { color:var(--ad-muted); font-size:0.8rem; margin-top:4px; }
.sb-section { font-size:0.72rem; font-weight:650; letter-spacing:0.06em; text-transform:uppercase; color:var(--ad-muted); margin:2px 0 8px 0; }
.metrics { background:var(--ad-card); border:1px solid var(--ad-line); border-radius:12px; margin-bottom:10px; }
.mrow { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; font-size:0.92rem; color:#D7DCF5; }
.mrow + .mrow { border-top:1px solid var(--ad-line); }
.mrow b { font-size:1.15rem; font-weight:700; color:var(--ad-ink); font-variant-numeric:tabular-nums; }
.mrow.alert b { color:#FFC2CD; } .mrow .badge { margin:0; }
.mode-pill { display:inline-block; color:#C5CBED; border:1px solid #333E63; border-radius:999px; padding:2px 11px; font-size:0.78rem; margin-top:6px; }
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


TAB_PURPOSE = {
    1: "AutoDiscover reads the AI tool settings already on a computer and lists every tool it finds. "
       "Passwords and keys are removed before anything is saved.",
    2: "Each tool gets a permanent ID card, called a passport. Anything the settings do not state is marked "
       "UNKNOWN instead of guessed, and two tools with the same name are never mixed up.",
    3: "When someone edits a tool's settings, its ID stays the same but the change is flagged for a person to "
       "review. Company rules are checked automatically, and each finding shows its evidence.",
    4: "When a tool does something, the action is filed under that tool's ID card, but only with explicit "
       "permission. Actions without approval, or outside what the tool may do, become incidents.",
    5: "The whole chain on one page: where each tool was configured, its ID card, what it did, and what still "
       "needs attention.",
    6: "Which agent run did what, who delegated it, and whether the activity log itself can be trusted. Agents are "
       "identified only by an independent observer, never by their own word, and an unknown actor stays UNKNOWN.",
}


def tab_purpose(step: int) -> None:
    st.markdown(f'<p class="purpose">{html.escape(TAB_PURPOSE[step])}</p>', unsafe_allow_html=True)


def sidebar_section(title: str) -> None:
    st.markdown(f'<div class="sb-section">{html.escape(title)}</div>', unsafe_allow_html=True)


DEMO_HINTS = {
    1: "Click **Scan for AI tools**, point at the [REDACTED] card and the disk check, then open **Passports**.",
    2: "Point at the same-name note and the UNKNOWN labels, click **Register tool** on all three, then click "
       "**Simulate app restart** in the sidebar.",
    3: "Turn on **Simulate a settings edit** in the sidebar, show the diff and why access mode is UNKNOWN, then open **Event review**.",
    4: "Pick evt-0002 and click **Link action to tool** without consent (blocked), then tick consent and link it "
       "(no approval on file, so an incident is recorded). "
       "Then evt-0003 on the filesystem-mcp passport (blocked). evt-0004 names only `postgres-mcp`, which two "
       "configs share, so it is ambiguous: keep it for human review instead. Then open **Map**.",
    5: "Walk the top diagram once, then read the evt-0002 row of the live map from left to right.",
    6: "Click **Save log checkpoint**, read the helper's delegated write to the protected file, then click "
       "**Simulate log tampering**: the rewritten log raises a critical finding.",
}


def real_mode() -> bool:
    return st.session_state.get("mode") == "real"


def agents_mode() -> bool:
    return st.session_state.get("mode") == "agents"


def demo_mode() -> bool:
    return not real_mode() and not agents_mode()


def store_label() -> str:
    return "data/local" if real_mode() else "data/agents" if agents_mode() else "data"


def demo_hint(step: int) -> None:
    if not demo_mode():
        return
    st.markdown(f'<div class="nextstep"><span>Next step</span><div>{_md_inline(DEMO_HINTS[step])}</div></div>',
                unsafe_allow_html=True)


def _md_inline(text: str) -> str:
    """Escape, then render **bold** and `code` from the hint strings."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


SEVERITY_KIND = {"critical": "bad", "high": "bad", "medium": "warn", "low": "slate"}


def rule_badges(findings: list[dict[str, Any]]) -> str:
    return "".join(
        badge(f'{f["rule_id"]} · {f["severity"].upper()}', SEVERITY_KIND.get(f["severity"], "neutral"))
        for f in sorted(findings, key=lambda f: f["rule_id"])
    )


def refresh_policies() -> None:
    """Re-evaluate policy-as-code rules into the ACTIVE store (data/ or data/local/)."""
    engine().evaluate_policies(st.session_state.get("candidates") or [])


def found_on_html(pid: str) -> str:
    """Discovered-on cell: demo label, recorded machine name(s), or the UNKNOWN badge."""
    label = discovered_on_label(engine().data_dir, pid, demo=demo_mode())
    if label == UNKNOWN:
        return value_badge(label)
    return f'<span class="mono">{"demo fixtures" if demo_mode() else html.escape(label)}</span>'


def short(pid: str) -> str:
    return pid[:12] + "…"


# ── Workspace / state helpers ──────────────────────────────────────────────────

def restore_live_configs(v2: bool) -> None:
    """Rebuild the live config workspace from fixtures/ (v1 or v2 Claude config)."""
    LIVE_FIXTURES.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "cursor_mcp.json", LIVE_FIXTURES / "cursor_mcp.json")
    src = "claude_desktop_config_v2.json" if v2 else "claude_desktop_config.json"
    shutil.copy(FIXTURES / src, LIVE_FIXTURES / "claude_desktop_config.json")


def agent_config_paths() -> list[Path]:
    live = AGENTS / "live" / "configs"
    return [live / n for n in AGENT_CONFIGS if (live / n).exists()]


def engine() -> AutoDiscoverEngine:
    if agents_mode():
        return AutoDiscoverEngine(data_dir=AGENTS, base_dir=AGENTS / "live", approvals_path=AGENT_APPROVALS,
                                  policies_path=FIXTURES / "policies.json")
    if real_mode():
        return AutoDiscoverEngine(data_dir=LOCAL_DATA, base_dir=ROOT, approvals_path=APPROVALS_FILE)
    return AutoDiscoverEngine(data_dir=DATA, base_dir=LIVE, approvals_path=APPROVALS_FILE)


def run_discovery() -> None:
    if real_mode():
        # read-only; never raises for a bad file
        summary = engine().scan_local_machine(workspaces=workspace_dirs(),
                                              include_clients=st.session_state.get("include_clients", True))
        # Sidecar only: the machine name never enters passport_id inputs, and owner stays UNKNOWN.
        record_discovery(engine().data_dir, [c["passport_id"] for c in summary["candidates"]], machine_name())
        st.session_state.scan_summary = summary
        st.session_state.candidates = summary["candidates"]
    elif agents_mode():
        cands = engine().discover_tools(agent_config_paths())
        # The agents really ran on this machine, so its name is recorded (sidecar only, never an ID input).
        record_discovery(engine().data_dir, [c["passport_id"] for c in cands], machine_name())
        st.session_state.candidates = cands
    else:
        st.session_state.candidates = engine().discover_tools(CONFIGS)
    st.session_state.scanned = True
    refresh_policies()


def demo_setup() -> None:
    """One click to a ready demo: original settings, a fresh scan, and every sample tool registered."""
    if st.session_state.get("v2_active"):
        st.session_state.v2_active = False
        restore_live_configs(v2=False)
    run_discovery()
    eng_ = engine()
    for c in st.session_state.candidates:
        if c["passport_id"] not in eng_.passports():
            eng_.mint_passport(c)
    run_discovery()
    st.session_state.restart_proof = None
    st.session_state.flash = ("success", f"Demo ready: {len(st.session_state.candidates)} tools found and registered. "
                              "Open any tab, or link an action in **Event review**.")


def empty_state(message: str, key: str) -> None:
    """An empty tab explains itself and, in the demo, offers the one-click setup."""
    st.info(message)
    if demo_mode():
        if st.button("Set up the demo in one click", key=f"setup_{key}", type="primary"):
            demo_setup()
            st.rerun()


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
    if agents_mode():
        return read_events(AGENTS / "captured_events.jsonl")
    return [json.loads(line) for line in EVENTS_FILE.read_text().splitlines() if line.strip()]


def workspace_dirs() -> list[Path]:
    """Project folders the user typed in (one per line); only existing folders are scanned."""
    raw = st.session_state.get("workspace_input", "")
    return [Path(line.strip()).expanduser() for line in raw.splitlines()
            if line.strip() and Path(line.strip()).expanduser().is_dir()]


def scanned_config_paths() -> list[Path]:
    if real_mode():
        locs = known_config_locations() + workspace_config_locations(workspace_dirs())
        return [loc["path"] for loc in locs if loc["path"] is not None]
    if agents_mode():
        return agent_config_paths()
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
    st.markdown('<div class="sb-brand"><span class="brand-mark" aria-hidden="true">✦</span>Forkit AI</div>'
                '<div class="sb-tag">Built on Forkit Core '
                '<span class="mono">compute_id</span></div>', unsafe_allow_html=True)
    st.divider()

    # ── Section 1: Discovery Mode ──
    sidebar_section("Source")
    mode_labels = {"demo": "Demo with sample data", "real": "Scan this computer",
                   "agents": "Recorded agent runs"}
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
            if picked == "real" else
            "Live agents: real agent runs captured by the AutoDiscover tap (local) and collector (cloud n8n). "
            "Click **Run agent scenarios** in the sidebar, then follow the tabs. Results go to `data/agents/`."
            if picked == "agents" else "Back to the demo with sample data.",
        )
        st.rerun()

    # ── Section 2: Demo Controls ──
    st.divider()
    sidebar_section("Demo")
    if agents_mode():
        if st.button("Run agent scenarios", width="stretch", type="primary",
                     help="Runs five scripted agent scenarios (no LLM) and captures every tool call."):
            from demo.agents.run_scenarios import run_all
            with st.spinner("Running agents through the tap and the collector..."):
                st.session_state.agent_run = run_all(AGENT_ROOT, say=lambda _msg: None)
            mode, tab = st.session_state.mode, st.session_state.get("active_tab")
            run = st.session_state.agent_run
            st.session_state.clear()
            st.session_state.mode, st.session_state.agent_run = mode, run
            if tab:
                st.session_state.active_tab = tab
            run_discovery()
            st.session_state.flash = ("success", f"Agents ran. {run['events']} tool calls captured and the "
                                      "tools are registered. Link the events in **Event review**.")
            st.rerun()
    elif real_mode():
        if st.button("Clear this computer's results", width="stretch", type="primary"):
            for f in LOCAL_DATA.glob("*.json"):
                f.unlink()
            st.session_state.clear()
            st.session_state.mode = "real"
            st.session_state.flash = ("success", "Local scan registry in `data/local/` cleared.")
            st.rerun()
    elif st.button("Set up the demo in one click", width="stretch", type="primary",
                   help="Scans the sample settings files and registers all three tools, so every tab has data."):
        demo_setup()
        st.rerun()
    if demo_mode() and st.button("Start the demo over", width="stretch"):
        for f in DATA.glob("*.json"):
            f.unlink()
        shutil.rmtree(LIVE, ignore_errors=True)
        shutil.rmtree(ACTIVITY_LIVE, ignore_errors=True)
        shutil.rmtree(DATA / "audit-archive.git", ignore_errors=True)
        st.session_state.clear()
        restore_live_configs(v2=False)
        st.session_state.flash = ("success", "Demo cleared. Click **Set up the demo in one click**, or start with **Scan for AI tools**.")
        st.rerun()

    if st.button("Simulate app restart", width="stretch",
                 help="Forgets everything in memory and scans again. Registered tools must come back with the same IDs."):
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

    v2_toggle = False if not demo_mode() else st.toggle(
        "Simulate a settings edit",
        value=st.session_state.v2_active,
        help="Swaps in an edited copy of the sample Claude Desktop settings (v2), as if someone changed a tool."
    )
    if demo_mode() and v2_toggle != st.session_state.v2_active:
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
                "Settings edit loaded, but filesystem-mcp is not registered yet, so there is nothing to diff against. "
                "For the demo: turn the edit off, register filesystem-mcp in **Passports**, then turn it on again.",
            )
        else:
            st.session_state.flash = (
                "warning" if v2_toggle else "info",
                "Settings edit loaded: filesystem-mcp's arguments changed on disk. See **Changes**."
                if v2_toggle else "Back to the original settings.",
            )
        st.rerun()

    if agents_mode():
        st.caption("Settings source: **live agent configs** in `data/agents/live/`")
    elif real_mode():
        st.caption("Settings source: **this computer**")
    else:
        st.caption(f"Settings file: **{'v2, edited' if st.session_state.v2_active else 'v1, original'}**")

    # ── Section 3: Live Governance Metrics & Audit Export (read-only snapshot) ──
    st.divider()
    sidebar_section("Status and audit")
    passports = eng.passports()
    n_incidents = len(eng.incidents())
    audit = eng.build_audit_report(
        candidates=st.session_state.candidates,
        raw_secret_values=raw_secret_values(),
        mode="real-local-machine" if real_mode() else "live-agents" if agents_mode() else "demo",
    )
    integ, scan = audit["integrity"], audit["secret_scan"]
    st.markdown(
        '<div class="metrics">'
        f'<div class="mrow"><span>Registered tools</span><b>{len(passports)}</b></div>'
        f'<div class="mrow"><span>Linked actions</span><b>{len(eng.linked_events())}</b></div>'
        f'<div class="mrow{" alert" if n_incidents else ""}"><span>Incidents</span><b>{n_incidents}</b></div>'
        '</div><div class="metrics">'
        f'<div class="mrow"><span>IDs re-checked · {integ["passports_checked"]}</span>'
        f'{status_badge(integ["status"])}</div>'
        f'<div class="mrow"><span>No secrets stored · {len(scan["files_scanned"])}</span>'
        f'{status_badge(scan["status"])}</div></div>',
        unsafe_allow_html=True,
    )
    st.caption(f"Stored in `{store_label()}/passports.json`")
    stamp = audit["generated_at"][:19].replace("-", "").replace(":", "") + "Z"
    base = f"autodiscover-audit-{'local' if real_mode() else 'agents' if agents_mode() else 'demo'}-{stamp}"
    try:
        bundle = build_audit_bundle(audit, eng.data_dir, inputs=[APPROVALS_FILE, eng.policies_path])
    except PermissionError as exc:
        st.error(f"{exc}. Fix the leak, then the download returns.")
    else:
        st.download_button("Download audit report (ZIP)", bundle, file_name=f"{base}.zip",
                           mime="application/zip", width="stretch", type="primary")
        c_json, c_md = st.columns(2)
        c_json.download_button("JSON", json.dumps(audit, indent=2, sort_keys=True), file_name=f"{base}.json",
                               mime="application/json", width="stretch")
        c_md.download_button("Markdown", render_audit_markdown(audit), file_name=f"{base}.md",
                             mime="text/markdown", width="stretch")

    with st.expander("Audit records"):
        if not git_available():
            st.caption("Git is not installed here, so the archive is unavailable.")
        else:
            archive_repo = eng.data_dir / "audit-archive.git"
            st.caption("Every link, incident, review item, drift record and activity finding, committed to a "
                       f"separate bare Git repository at `{store_label()}/audit-archive.git`. Earlier records can "
                       "never change without verification failing.")
            if st.button("Save new records to the archive", width="stretch"):
                try:
                    res = eng.archive_to_git(archive_repo, raw_secret_values=raw_secret_values())
                    st.session_state.flash = ("success", f"Archive: +{res['added']} record(s), "
                                              f"{res['total']} in total, verification {res['verify']['status']}.")
                except (ArchiveError, PermissionError) as exc:
                    st.session_state.flash = ("error", f"Archive refused: {exc}")
                st.rerun()
            arch = eng.archive_status(archive_repo)
            if arch["status"] != "MISSING":
                st.markdown(f'{status_badge(arch["status"])} <span class="mono">{arch["commits"]} commits · '
                            f'{arch["records"]} records · head {html.escape((arch["head"] or "none")[:12])}</span>',
                            unsafe_allow_html=True)
                for prob in arch["problems"]:
                    st.error(prob)

# ── Header ─────────────────────────────────────────────────────────────────────

st.markdown(
    '<div class="hero"><div class="hero-art" aria-hidden="true"><b>✦</b><i></i><i></i><i></i></div>'
    '<h1>Local AI inventory</h1>'
    '<p>See what your AI tools are doing. Every tool gets a passport, every action gets a trail, '
    'and nothing is guessed.</p></div>',
    unsafe_allow_html=True,
)


def activity_sources() -> tuple[str, Path | None, Path | None]:
    """(label, observer log, self-reported log) for the current mode. Logs are only read."""
    if demo_mode():
        ACTIVITY_LIVE.mkdir(parents=True, exist_ok=True)
        for name in ("observer.jsonl", "agent_reported.jsonl"):
            if not (ACTIVITY_LIVE / name).exists():
                shutil.copy(ACTIVITY_FIXTURES / name, ACTIVITY_LIVE / name)
        return "demo", ACTIVITY_LIVE / "observer.jsonl", ACTIVITY_LIVE / "agent_reported.jsonl"
    if agents_mode():
        log, rep = AGENTS / "pilot" / "observer.jsonl", AGENTS / "pilot" / "agent_reported.jsonl"
        return "agents-pilot", (log if log.is_file() else None), (rep if rep.is_file() else None)
    obs = st.session_state.get("activity_observer", "").strip()
    rep = st.session_state.get("activity_reported", "").strip()
    obs_p = Path(obs).expanduser() if obs else None
    rep_p = Path(rep).expanduser() if rep else None
    return ("local", obs_p if obs_p and obs_p.is_file() else None, rep_p if rep_p and rep_p.is_file() else None)


def ask_store() -> dict[str, Any]:
    """Everything Ask AutoDiscover may cite, read-only."""
    label, obs, rep = activity_sources()
    checkpoint = eng.log_checkpoints().get(label, {}).get("checkpoint")
    tl = timeline_from_files(obs, rep, rules=eng.policies(), namespace=label, checkpoint=checkpoint) if obs else None
    return {"passports": eng.passports(), "links": eng.linked_events(), "incidents": eng.incidents(),
            "review_queue": eng.review_queue(), "policy_findings": audit["policy_findings"], "timeline": tl}


with st.container(border=True, key="askpanel"):
    st.markdown("#### Ask Forkit")
    st.caption("Try “Who is the parent of the Report helper?”, “What did it change?”, or "
               "“Who was the human in the workflow?”")
    ask_label, ask_observer, ask_report = activity_sources()
    q_col, b_col = st.columns([6, 1])
    question = q_col.text_input("Question about Forkit records", key="ask_q", label_visibility="collapsed",
                                max_chars=240, placeholder="Ask about a file, run, or event")
    submitted = b_col.button("Ask", width="stretch", type="primary") and question.strip()
    ask_scope = None
    if submitted or st.session_state.get("ask_result"):
        saved = eng.log_checkpoints().get(ask_label, {}).get("checkpoint")
        ask_scope = (ask_label, saved, tuple((str(path), make_checkpoint(read_log(path)))
                                            for path in (ask_observer, ask_report) if path))
    previous = st.session_state.get("ask_result") if st.session_state.get("ask_scope") == ask_scope else None
    if submitted:
        st.session_state.ask_result = ask_question(question, ask_store(), subject=(previous or {}).get("subject"))
        st.session_state.ask_scope = ask_scope
    res = st.session_state.get("ask_result") if st.session_state.get("ask_scope") == ask_scope else None
    if res:
        st.markdown(f"**{html.escape(res['answer'])}**")
        for c in res["citations"]:
            st.markdown(f'{badge(c["kind"], "neutral")} <span class="mono">{html.escape(c["id"])}</span> '
                        f'{html.escape(c["text"])}', unsafe_allow_html=True)
        if any(c["kind"] in ("run", "action") for c in res["citations"]):
            if st.button("Open Activity timeline", key="ask_open_timeline"):
                st.session_state.active_tab = "Activity timeline"
        st.caption(f"Answered from {res.get('source', 'records')}. Matching runs on local records only"
                   + ("" if res.get("source", "records") != "records" else
                      "; set AUTODISCOVER_ASK_MODEL to an installed Ollama model to have it rephrased locally") + ".")


def _read_scenarios() -> dict[str, Any]:
    try:
        return json.loads((AGENTS / "scenarios.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def agent_run_html(run: dict[str, Any], events: list[dict[str, Any]], linked: set[str]) -> str:
    """One card per scenario: where it ran, what was captured, and what AutoDiscover expects to conclude."""
    by_id = {e["event_id"]: e for e in events}
    cards = []
    for sc in run.get("scenarios", []):
        rows = "".join(
            f'<div class="mono" style="font-size:12.5px">{badge(ORIGIN_LABEL.get(e["capture"]["origin"], "?"), "slate")} '
            f'{html.escape(e["tool_call"])} → {html.escape(e["target"])} '
            f'{badge(e["result"].upper(), "bad" if e["result"] == "denied" else "neutral")}'
            f'{badge("LINKED", "ok") if e["event_id"] in linked else ""}</div>'
            for e in (by_id[i] for i in sc["event_ids"] if i in by_id)
        ) or f'<div class="muted">{html.escape(sc.get("note") or "No actions captured.")}</div>'
        state = {"skipped": badge("SKIPPED", "warn"), "failed": badge("FAILED", "bad")}.get(sc["status"], "")
        cards.append(
            f'<div class="card"><h4>Scenario {html.escape(sc["id"])} · {html.escape(sc["title"])} '
            f'{badge(sc["where"].upper(), "slate")}{state}</h4>{rows}'
            f'<div class="muted" style="margin-top:6px">Expected: {html.escape(sc["expect"])}</div></div>'
        )
    server = html.escape(str(run.get("fs_server", "UNKNOWN")))
    return (f'<div class="muted" style="margin-bottom:6px">Captured from real runs. File server: {server}. '
            f'n8n: {"ran" if run.get("n8n") else "not installed here, so the cloud scenario was skipped"}.</div>'
            + "".join(cards))


def overview_html() -> str:
    """Dashboard row: the five numbers a reviewer asks for first, from the same stores the tabs use."""
    cands = st.session_state.candidates
    files = len({c["source_path"] for c in cands})
    issued = eng.passports()
    waiting = sum(1 for c in cands if c["passport_id"] not in issued)
    drift = len(eng.metadata_changes())
    findings = eng.policy_findings()
    severe = sum(1 for f in findings if f["severity"] in ("critical", "high"))
    links = eng.linked_events()
    missing = sum(1 for lk in links if lk["approval_status"] == MISSING_APPROVAL)
    incidents = len(eng.incidents())

    def tile(label: str, value: int, note: str, alert: str = "") -> str:
        cls = f"ov-tile {alert}" if alert and value else "ov-tile"
        return (f'<div class="{cls}"><div class="ov-label">{html.escape(label)}</div>'
                f'<div class="ov-value">{value}</div><div class="ov-note">{html.escape(note)}</div></div>')

    return '<div class="ov">' + "".join([
        tile("Tools found", len(cands), f"in {files} config file{'' if files == 1 else 's'}" if cands else "scan to start"),
        tile("Passports issued", len(issued), f"{waiting} waiting for review" if waiting else "all reviewed" if issued else "none yet"),
        tile("Config changes", drift, "waiting for review" if drift else "no edits since issue", alert="warn"),
        tile("Rule findings", len(findings), f"{severe} high or critical" if findings else "none", alert="alert" if severe else ""),
        tile("Incidents", incidents, f"{len(links)} event{'' if len(links) == 1 else 's'} linked, {missing} without approval" if links else "no events linked", alert="alert"),
    ]) + "</div>"


st.markdown(overview_html(), unsafe_allow_html=True)

if st.session_state.flash:
    kind, msg = st.session_state.flash
    getattr(st, kind)(msg)
    st.session_state.flash = None

proof = st.session_state.restart_proof
if proof and proof["registry_count"] == 0:
    st.info("Restart simulated, but the registry is empty so there is nothing to recognize yet. "
            "Scan in **Tools**, register in **Passports**, then click **Simulate app restart** again.")
elif proof:
    same = proof["after"] == proof["recognized"] and (not proof["before"] or proof["before"] == proof["after"])
    keys = [(p["source_path"], p["tool_name"]) for p in eng.passports().values()]
    dupes = len(keys) - len(set(keys))
    if same and dupes == 0:
        st.success(
            f"Restart recognized. Memory was cleared and the re-scan matched "
            f"{len(proof['recognized'])} of {len(proof['after'])} tools to their existing passports. "
            f"Same passport IDs, {proof['registry_count']} passports in the registry, no duplicates."
        )
    else:
        st.warning(
            f"Restart re-scan: {len(proof['recognized'])}/{len(proof['after'])} tools already have passports "
            f"({dupes} duplicates). Register the rest in **Passports** to see them recognized next time."
        )
    with st.expander("Passport IDs before vs. after restart"):
        st.json({"before_restart": proof["before"], "after_restart": proof["after"]})

# Keyed, stateful tabs: clicking a button inside a tab keeps you on that tab.
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["Tools", "Passports", "Changes", "Event review",
     "Map", "Activity timeline"],
    key="active_tab",
    on_change="rerun",
)

candidates: list[dict[str, Any]] = st.session_state.candidates

# ── Tools ───────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Discovered tools")
    tab_purpose(1)
    demo_hint(1)
    if real_mode():
        st.caption("Read-only scan of the well-known MCP config locations on this computer: Claude Desktop, "
                   "Claude Code, Cursor, VS Code (and Insiders, with profiles), Windsurf and Codex. "
                   "Nothing is written to those files.")
        st.text_area("Project folders to scan too (optional, one per line)", key="workspace_input",
                     placeholder="~/code/my-project",
                     help="Looks for .vscode/mcp.json, .cursor/mcp.json, .mcp.json and .codex/config.toml. "
                          "Only the folder name is stored, never the full path.")
        st.checkbox("Also find installed AI clients (CLIs on PATH, desktop apps, editor extensions)",
                    value=True, key="include_clients",
                    help="Each gets its own passport type, so a CLI named claude never merges with an MCP "
                         "server named claude. Nothing is launched.")
        scan_label = "Scan this computer"
    elif agents_mode():
        st.caption("Scans the configs the agents ran with: "
                   + (", ".join(f"`configs/{p.name}`" for p in agent_config_paths())
                      or "none yet. Click **Run agent scenarios** in the sidebar first."))
        scan_label = "Scan for AI tools"
    else:
        st.caption("Reads two sample settings files: " + ", ".join(f"`{c}`" for c in CONFIGS))
        scan_label = "Scan for AI tools"
    if st.button(scan_label, type="primary"):
        run_discovery()
        st.session_state.restart_proof = None
        st.rerun()

    summary = st.session_state.scan_summary
    if real_mode() and st.session_state.scanned and summary:
        counts = summary.get("installed_counts")
        extra = (f" · {counts['cli_tool']} CLI(s), {counts['desktop_app']} app(s), "
                 f"{counts['editor_extension']} extension(s)") if counts else ""
        st.markdown(f"**Locations checked** · platform `{summary['platform']}` · "
                    f"{summary['configs_found']} config file(s) found{extra}")
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
        col = st.column_config.TextColumn
        st.dataframe([{
            "tool_name": c["tool_name"],
            "kind": KIND_LABELS.get(c.get("passport_type", "mcp_server"), UNKNOWN),
            "client": c["client"],
            "source_path": c["source_path"],
            "command": c["command"],
            "owner": c["owner"],
            "discovered_on": "Demo fixtures" if demo_mode() else discovered_on_label(eng.data_dir, c["passport_id"], demo=False),
            "version": c["version"],
            "access_mode": c["access_mode"],
            "transport": c.get("transport", UNKNOWN),
            "enabled": {True: "yes", False: "no"}.get(c.get("enabled"), UNKNOWN),
        } for c in candidates], width="stretch", hide_index=True, column_config={
            "tool_name": col("Tool"), "kind": col("Kind"), "client": col("Client"), "source_path": col("Config file or location", width="large"),
            "command": col("Command"), "owner": col("Owner"),
            "discovered_on": col("Discovered on",
                                 help="Machine the scan ran on. Context only: not the owner, never part of the passport ID."),
            "version": col("Version"), "access_mode": col("Access"),
            "transport": col("Transport"), "enabled": col("Enabled", help="UNKNOWN unless the config says so."),
        })
        with st.expander("Arguments, environment and settings fingerprint"):
            st.dataframe([{
                "tool_name": c["tool_name"],
                "source_path": c["source_path"],
                "args": " ".join(c["args"]),
                "env": ", ".join(f'{k}="{v}"' for k, v in c["env"].items()) or "none",
                "config_hash": c["config_hash"][:12] + "…",
            } for c in candidates], width="stretch", hide_index=True, column_config={
                "tool_name": col("Tool", width=130), "source_path": col("Config file", width=260),
                "args": col("Arguments", width=560, help="Secrets redacted. Hover a cell to read it all."),
                "env": col("Environment", width=230), "config_hash": col("Fingerprint", width=120),
            })

        redacted = [(c, k) for c in candidates for k, v in c["env"].items() if v == REDACTED]
        if redacted:
            items = "".join(
                f'<div class="kv"><b>{html.escape(c["tool_name"])}</b><span>'
                f'<span class="mono">{html.escape(k)}: </span>{badge("[REDACTED]", "redacted")}'
                f' <span class="mono">({html.escape(c["client"])})</span></span></div>'
                for c, k in redacted
            )
            st.markdown(
                f'<div class="card"><h4>Secrets stripped at discovery</h4>{items}'
                f'<div class="muted" style="margin-top:8px">Raw values are never hashed, stored, or displayed. '
                f'Only the env <i>key names</i> are kept.</div></div>',
                unsafe_allow_html=True,
            )
        leaks = leaked_secrets()
        if leaks:
            st.error(f"Secret value found in: {', '.join(sorted(set(leaks)))}")
        else:
            store = f"{store_label()}/*.json"
            st.success(f"Disk check: 0 of {len(raw_secret_values())} raw secret values appear anywhere in `{store}`.")

# ── Passports ──────────────────────────────────────────────────────

with tab2:
    st.subheader("Review each tool and register it")
    tab_purpose(2)
    demo_hint(2)
    with st.expander("How the passport ID is calculated"):
        st.markdown("The ID depends only on **where** a tool is configured: the config file, the tool's name and its "
                    "command. The same tool in the same file always gets the same ID, on any machine. Arguments and "
                    "environment are tracked separately, so editing them flags a change instead of creating a new tool.")
        st.code("passport_id = compute_id(passport_type='mcp_server' | 'cli_tool' | 'desktop_app'\n"
                "                                       | 'editor_extension',\n"
                "                         name, version='UNKNOWN',\n"
                "                         creator_name='UNKNOWN',\n"
                "                         artifact_hash=sha256(source_path + tool_name + command))", language=None)
    if not candidates:
        empty_state("Nothing to register yet. Scan in **Tools** first.", key="passports")
    else:
        name_sources: dict[str, set[str]] = {}
        for c in candidates:
            name_sources.setdefault(c["tool_name"], set()).add(c["source_path"])
        passports = eng.passports()
        if st.session_state.v2_active and any(
            c["source_path"] == CLAUDE and c["passport_id"] not in passports for c in candidates
        ):
            st.warning("The **settings edit** is on. Registering now records the edited settings as the baseline, so **Changes** will "
                       "have no diff to show. For the demo, turn the settings edit off in the sidebar first.")

        cols = st.columns(len(candidates))
        for col, c in zip(cols, candidates, strict=True):
            with col, st.container(
                border=True, height="stretch",
                key=f"idcard_{'collision_' if len(name_sources[c['tool_name']]) > 1 else ''}{c['passport_id'][:16]}",
            ):
                collision = len(name_sources[c["tool_name"]]) > 1
                minted = c["passport_id"] in passports
                status = badge("REGISTERED", "ok") if minted else badge("NOT REGISTERED YET", "neutral")
                warn = (
                    '<div class="note-warn">Same name as another tool, different provenance. '
                    'Kept as a separate passport.</div>'
                    if collision else ""
                )
                changed = badge("SETTINGS CHANGED", "bad") if c.get("metadata_changed") else ""
                st.markdown(
                    f'<div class="card-body">'
                    f'<div class="card-head"><h4>{html.escape(c["tool_name"])}</h4><span>{changed}{status}</span></div>'
                    f'{warn}'
                    f'<div class="kv"><b>Policy</b><span>{rule_badges(c.get("policy_findings", [])) or "none"}</span></div>'
                    f'<div class="kv"><b>Client</b>{value_badge(c["client"])}</div>'
                    f'<div class="kv"><b>Config file</b>{value_badge(c["source_path"])}</div>'
                    f'<div class="kv"><b>Command</b>{value_badge(c["command"])}</div>'
                    f'<div class="kv"><b>Owner</b>{value_badge(c["owner"])}</div>'
                    f'<div class="kv"><b>Discovered on</b>{found_on_html(c["passport_id"])}</div>'
                    f'<div class="kv"><b>Version</b>{value_badge(c["version"])}</div>'
                    f'<div class="kv"><b>Access</b>{value_badge(c["access_mode"])}</div>'
                    f'<div class="kv"><b>Passport ID</b><span class="mono">{short(c["passport_id"])}</span></div>'
                    f'<div class="kv"><b>Provenance</b><span class="mono">{c["provenance_hash"][:12]}…</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.space("stretch")                     # pushes the action to one shared baseline
                if minted:
                    st.button("Registered ✓", key=f"mint_{c['passport_id']}", disabled=True,
                              width="stretch")
                elif st.button("Register tool", key=f"mint_{c['passport_id']}",
                               type="primary", width="stretch"):
                    pid = eng.mint_passport(c)
                    refresh_policies()
                    st.session_state.restart_proof = None  # stale once the registry changes
                    st.session_state.flash = ("success", f"Registered {c['tool_name']}. Passport ID {pid[:12]}…")
                    st.rerun()

        if any(len(s) > 1 for s in name_sources.values()):
            pg = [c for c in candidates if len(name_sources[c["tool_name"]]) > 1]
            st.warning(
                "**No false merge.** "
                + " vs. ".join(f"`{c['tool_name']}` @ `{c['source_path']}` → `{short(c['passport_id'])}`" for c in pg)
                + ". Same name, different provenance, so they get different passport IDs."
            )

# ── Changes ─────────────────────────────────────────────────────

with tab3:
    st.subheader("Settings changes and rule checks")
    tab_purpose(3)
    demo_hint(3)
    changed = [c for c in candidates if c.get("metadata_changed")]
    if real_mode():
        if not changed:
            st.info("No settings changes on registered tools. To see one, register a tool in **Passports**, edit that tool's "
                    "args in your MCP config, then re-scan in **Tools**.")
    elif agents_mode():
        if not changed:
            st.info("Scenario E removes `--read-only` from the Claude Desktop agent config during the run. Click "
                    "**Run agent scenarios** in the sidebar and the change shows up here, on the same passport.")
    elif not st.session_state.scanned:
        empty_state("Nothing has changed yet. Scan in **Tools** and register `filesystem-mcp` in **Passports** first.", key="changes")
    elif not st.session_state.v2_active:
        st.info("Turn on **Simulate a settings edit** in the sidebar to see what happens when someone edits a tool.")
    elif not changed:
        st.info("The settings edit is on, but no registered tool changed. Register `filesystem-mcp` in **Passports** "
                "first, then turn the edit off and on again.")
    for c in changed:
        stored = eng.passports()[c["passport_id"]]
        lines = []
        for d in c["diff"]:
            cls = "diff-add" if " added" in d else "diff-del" if (" removed" in d or "changed" in d) else ""
            lines.append(f'<div class="mono {cls}">• {html.escape(d)}</div>')
        reason = ""
        if stored.get("access_mode") != c["access_mode"] and c["access_mode"] == UNKNOWN:
            reason = (
                f'<div style="margin-top:10px"><b>Why access is now {badge("UNKNOWN", "unknown")}:</b> '
                + ("the <span class='mono'>--read-only</span> flag is gone from the config. " if agents_mode() else
                   "the read-only evidence (<span class='mono'>,ro</span> on the bind mount) is gone from the config. ")
                + "We will not assume <i>read-write</i> either, so it stays UNKNOWN until a human reviews it.</div>"
            )
        st.markdown(
            f'<div class="card drift">'
            f'<h4>Settings changed: {html.escape(c["tool_name"])} '
            f'<span class="mono">({short(c["passport_id"])})</span></h4>'
            f'<p style="margin:2px 0 8px 0">Someone edited this tool\'s settings. The tool keeps its passport, '
            f'and the change waits for a person to review it.</p>'
            f'{badge("SAME PASSPORT ID", "ok")}{badge("WAITING FOR REVIEW", "warn")}'
            f'<div class="kv"><b>Fingerprint</b><span class="mono">{stored["config_hash"][:12]}… → '
            f'{c["config_hash"][:12]}…</span></div>'
            f'<div style="margin-top:8px">{"".join(lines)}</div>{reason}'
            f'<div class="muted" style="margin-top:10px">The registered passport is unchanged. The change is recorded in '
            f'<span class="mono">{store_label()}/metadata_changes.json</span>.</div></div>',
            unsafe_allow_html=True,
        )
    log = eng.metadata_changes()
    if log:
        with st.expander(f"Raw change log: metadata_changes.json ({len(log)})"):
            st.json(log)

    st.markdown("#### Rule checks")
    st.caption("Company rules are written down in `fixtures/policies.json`, not hidden in code. Each finding shows "
               "the facts it used. Missing facts are never guessed.")
    findings = eng.policy_findings()
    if not findings:
        st.info("No findings yet. Scan in **Tools** to check the rules.")
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
                f'<div class="kv"><b>Facts used</b><span class="mono">{html.escape(ev)}</span></div>'
                f'<div class="muted" style="margin-top:4px">{html.escape(f["explanation"])}</div></div>',
                unsafe_allow_html=True,
            )

# ── Event review ────────────────────────────────────────────────────

with tab4:
    st.subheader("Event review")
    tab_purpose(4)
    demo_hint(4)
    events = load_events()
    passports = eng.passports()
    if real_mode():
        st.info("Event linking runs on the demo's runtime event fixtures, whose sources point at the fixture "
                "configs. Switch to **Demo with sample data** in the sidebar to link events.")
    elif agents_mode() and not events:
        st.info("No captured agent actions yet. Click **Run agent scenarios** in the sidebar: a scripted LangGraph "
                "agent works through a real MCP file server behind the AutoDiscover tap, and an n8n workflow "
                "reports its actions to the collector.")
    elif not passports:
        empty_state("No registered tools yet. Scan in **Tools** and register at least `filesystem-mcp` in **Passports** "
                    "before linking actions to tools.", key="events")
    else:
        scen_of: dict[str, str] = {}
        if agents_mode():
            run = _read_scenarios()
            scen_of = {eid: sc["id"] for sc in run.get("scenarios", []) for eid in sc["event_ids"]}
            st.markdown(agent_run_html(run, events, {lk["event_id"] for lk in eng.linked_events()}),
                        unsafe_allow_html=True)
            unlinked = [e for e in events if e["event_id"] not in {lk["event_id"] for lk in eng.linked_events()}]
            if unlinked:
                with st.container(border=True):
                    st.markdown(f"**Link every captured action** · {len(unlinked)} not linked yet. Each one is "
                                "filed under the passport its own source names; nothing is guessed.")
                    all_ok = st.checkbox(f"I grant explicit consent to link all {len(unlinked)} captured events",
                                         key="consent_all_agents")
                    if st.button("Link all captured actions", width="stretch"):
                        try:
                            matches = [(event, eng.attribution(event)) for event in unlinked]
                            done = [eng.link_event(event, match["candidates"][0], user_consent=all_ok)
                                    for event, match in matches if match["status"] == "unique"]
                            refresh_policies()
                            st.session_state.last_link = done[-1] if done else None
                            st.session_state.last_error = None
                        except (PermissionError, ValueError, KeyError) as exc:
                            st.session_state.last_error = f"{type(exc).__name__}: {exc}"
                        st.rerun()
        left, right = st.columns([1, 1])
        with left:
            ev_labels = {
                e["event_id"]: (f'{scen_of.get(e["event_id"], "?")} · {ORIGIN_LABEL.get(e["capture"]["origin"], "")} · '
                                if agents_mode() else f'{e["event_id"]} · ')
                + f'{e["source"].get("server_name", UNKNOWN)} · {e["tool_call"]} · {e["result"]}'
                for e in events
            }
            ev_id = st.selectbox("Action a tool took (runtime event)", list(ev_labels), format_func=ev_labels.get, key="ev_select")
            event = next(e for e in events if e["event_id"] == ev_id)
            with st.expander("Raw event record"):
                st.json(event)
        with right:
            pids = list(passports)
            labels = {p: f'{passports[p]["tool_name"]} @ {passports[p]["source_path"]} · {short(p)}' for p in pids}
            att = eng.attribution(event)
            if att["status"] == "unique":
                match = att["candidates"][0]
            else:
                match = pids[0]
                title = {"ambiguous": "Ambiguous source", "no_source_context": "No source context",
                         "insufficient_source": "More source details needed",
                         "no_match": "Tool not registered yet"}[att["status"]]
                st.warning(f"**{title}.** {att['reason']} "
                           + ("Register it in **Passports** to link this action. "
                              if att["status"] == "no_match" else "")
                           + "Linking it to a passport will be blocked by the engine.")
                if att["status"] != "no_match":
                    with st.container(border=True):
                        st.markdown("**Keep it for review, unassigned.** The actor stays UNKNOWN until stronger "
                                    "evidence appears.")
                        rv_ok = st.checkbox("I give consent to keep this action for review",
                                            key=f"review_consent_{ev_id}")
                        if st.button("Keep for human review", key=f"review_{ev_id}", width="stretch"):
                            try:
                                eng.record_for_review(event, user_consent=rv_ok)
                                st.session_state.last_error = None
                            except (PermissionError, ValueError) as exc:
                                st.session_state.last_error = f"{type(exc).__name__}: {exc}"
                            st.rerun()
                queued = [r for r in eng.review_queue() if r["event_id"] == event["event_id"]]
                if queued:
                    st.info(f"In the review queue as `{queued[0]['review_id']}` · actor UNKNOWN · "
                            f"{len(queued[0]['candidate_passport_ids'])} candidate passport(s).")
            target = st.selectbox("Tool it belongs to", pids, index=pids.index(match),
                                  format_func=labels.get, key=f"target_{ev_id}")
            consent = st.checkbox("I give consent to link this action to the tool", key=f"consent_{ev_id}")
            if st.button("Link action to tool", type="primary", width="stretch",
                         disabled=att["status"] != "unique"):
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
            st.error(f"Blocked, nothing was linked. {st.session_state.last_error}")

        link = st.session_state.last_link
        if link:
            appr = badge("APPROVED", "ok") if link["approval_status"] == APPROVED else badge("NO APPROVAL ON FILE", "bad")
            src = link["source"] if isinstance(link["source"], dict) else {}
            rule_titles = {r["rule_id"]: r["title"] for r in eng.policies()}
            policy_html = "".join(f'{badge(r, "bad")}<span class="muted">{html.escape(rule_titles.get(r, ""))}</span> '
                                  for r in link.get("policy_flags", [])) or badge("no rule broken", "ok")
            st.markdown(
                f'<div class="card audit"><h4>Audit record · {html.escape(link["link_id"])}</h4>'
                f'<div class="kv"><b>Event</b><span class="mono">{html.escape(link["event_id"])} · '
                f'{html.escape(link["tool_call"])} → {html.escape(str(link["target"]))} ({html.escape(link["result"])})</span></div>'
                f'<div class="kv"><b>Source</b><span class="mono">{html.escape(src.get("client", UNKNOWN))} · '
                f'{html.escape(src.get("config_path", UNKNOWN))} · {html.escape(src.get("server_name", UNKNOWN))}</span></div>'
                f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(link["timestamp"])}</span></div>'
                f'<div class="kv"><b>Passport</b><span class="mono">{short(link["passport_id"])}</span></div>'
                f'<div class="kv"><b>Approval</b>{appr} '
                f'{"" if link["approval_id"] == UNKNOWN else value_badge(link["approval_id"])} '
                f'<span class="muted">{"Approved in" if link["approval_status"] == APPROVED else "No approval on file in"} '
                f'{html.escape(eng.approvals_path.relative_to(ROOT).as_posix())}</span></div>'
                f'<div class="kv"><b>Runtime consent</b>{value_badge(link["runtime_consent_id"])}'
                f'{" <span class=muted>The event log does not say whether the user agreed.</span>" if link["runtime_consent_id"] == UNKNOWN else ""}</div>'
                f'<div class="kv"><b>Link consent</b>{badge("GRANTED", "ok")} <span class="muted">You ticked the consent box.</span></div>'
                f'<div class="kv"><b>Policy</b>'
                f'{policy_html}'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            if INCIDENT in link["flags"]:
                inc = next((i for i in eng.incidents() if i["incident_id"] == link["incident_id"]), None)
                if inc:
                    st.markdown(
                        f'<div class="card incident"><h4>'
                        f'Incident recorded: out of expected behavior</h4>'
                        f'<p style="margin:2px 0 8px 0">This tool was confirmed as read-only, but it tried to change something '
                        f'({html.escape(inc["tool_call"])}). The result was {html.escape(str(inc["result"]))}, and the attempt is on record.</p>'
                        f'<div class="kv"><b>Incident</b><span class="mono">{html.escape(inc["incident_id"])}</span></div>'
                        f'<div class="kv"><b>Tool</b><span class="mono">{html.escape(inc["tool_name"])} '
                        f'({short(inc["passport_id"])})</span></div>'
                        f'<div class="kv"><b>Attempted</b><span class="mono">{html.escape(inc["tool_call"])} → '
                        f'{html.escape(str(inc["target"]))}</span> {badge(inc["result"].upper(), "bad")}</div>'
                        f'<div class="kv"><b>Expected</b>{badge(inc["expected_behavior"], "ok")}</div>'
                        f'<div class="kv"><b>Approval</b>{badge("APPROVED" if inc["approval_status"] == APPROVED else "NO APPROVAL ON FILE", "bad" if inc["approval_status"] != APPROVED else "ok")}</div>'
                        f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(inc["timestamp"])}</span></div>'
                        f'<div class="muted" style="margin-top:8px">Written to <span class="mono">{store_label()}/incidents.json</span>.</div></div>',
                        unsafe_allow_html=True,
                    )

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Linked actions** · `{store_label()}/linked_events.json`")
            st.dataframe([{
                "Event": lk["event_id"], "Passport": short(lk["passport_id"]), "Action": lk["tool_call"],
                "Time": lk["timestamp"],
                "Approval": "approved" if lk["approval_status"] == APPROVED else "no approval on file",
                "Flags": ", ".join(f.lower().replace("_", " ") for f in lk["flags"]) or "none",
            } for lk in eng.linked_events()] or [{"Event": "None linked yet"}], width="stretch", hide_index=True)
        with c2:
            st.markdown(f"**Incidents** · `{store_label()}/incidents.json`")
            st.dataframe([{
                "Incident": i["incident_id"], "Tool": i["tool_name"], "Action": i["tool_call"],
                "Time": i["timestamp"], "Result": i["result"],
            } for i in eng.incidents()] or [{"Incident": "None yet"}], width="stretch", hide_index=True)

# ── Map ─────────────────────────────────────────────────────────

with tab5:
    st.subheader("Where every record comes from")
    tab_purpose(5)
    demo_hint(5)
    store = eng.data_dir                              # data/ (demo) or data/local/ (real machine), read-only
    st.markdown("**How a passport is made.** The ID comes only from where the tool is configured. "
                "Everything learned later is stored next to it, never inside it.")
    with st.container(border=True, key="graphframe_lifecycle"):
        st.html(lifecycle_html(store))

    st.markdown("**Live provenance map**")
    map_passports = eng.passports()
    focus_options = [None] + sorted(map_passports)
    focus = st.selectbox(
        "Focus", focus_options, key="map_focus",
        format_func=lambda p: "All tools" if p is None else
        f'{map_passports[p]["tool_name"]} @ {map_passports[p]["source_path"]} · {short(p)}',
    )
    found_on = {p: discovered_on_label(store, p, demo=demo_mode())
                for p in set(map_passports) | {c["passport_id"] for c in st.session_state.candidates}}
    live = live_map_html(store, candidates=st.session_state.candidates, focus=focus, found_on=found_on)
    if live is None:
        empty_state("Nothing to map yet. Scan in **Tools** and register tools in **Passports**, and the live map "
                    "of settings files, passports, actions and incidents appears here.", key="map")
    else:
        with st.container(border=True, key="graphframe_live"):
            st.html(live)
        st.caption("Read each row left to right: the config file that declares a tool, the passport issued for it, "
                   "runtime events linked to that passport, and any incident they caused. Open checks are drift and "
                   "policy findings still waiting for a reviewer. Red means no approval or an incident. A dashed "
                   "box means the tool is not registered yet.")


# ── 06 Activity Timeline ──────────────────────────────────────────────────────

_SEV_KIND = {"critical": "bad", "high": "bad", "medium": "warn", "low": "slate"}


with tab6:
    st.subheader("Activity timeline")
    tab_purpose(6)
    demo_hint(6)
    if real_mode():
        c1, c2 = st.columns(2)
        c1.text_input("Observer activity log (JSON Lines)", key="activity_observer",
                      placeholder="~/forkit-pilot/observer.jsonl",
                      help="Written by an independent observer: a launcher, workspace monitor or the AutoDiscover pilot.")
        c2.text_input("Agent's own report (optional)", key="activity_reported",
                      help="Rows an agent reports about itself. Compared with the observer, never trusted on their own.")
    label, obs_log, rep_log = activity_sources()
    if obs_log is None:
        if agents_mode():
            st.info("No pilot log yet. Click **Run agent scenarios** in the sidebar: the delegated-helper pilot "
                    "writes an observer log here.")
        else:
            st.info("Point AutoDiscover at an observer log to build the timeline. The format is documented in "
                    "AUTODISCOVER_README.md (Activity timeline).")
    else:
        b1, b2, b3 = st.columns(3)
        if b1.button("Save log checkpoint", width="stretch",
                     help="Remembers LENGTH:SHA256 of the log as it is now. Later reads must start with the same bytes."):
            try:
                cp = eng.save_log_checkpoint(label, obs_log)
                st.session_state.flash = ("success", f"Checkpoint saved: `{cp[:28]}...`" if cp else "Log is empty.")
            except ValueError as exc:
                st.session_state.flash = ("error", str(exc))
            st.rerun()
        if demo_mode():
            if b2.button("Simulate log tampering", width="stretch",
                         help="Rewrites one earlier record in the demo copy, as an agent covering its tracks would."):
                text = obs_log.read_text(encoding="utf-8")
                obs_log.write_text(text.replace('"protected": true', '"protected": null', 1), encoding="utf-8")
                st.rerun()
            if b3.button("Restore original log", width="stretch"):
                shutil.copy(ACTIVITY_FIXTURES / "observer.jsonl", obs_log)
                st.rerun()
        tl = eng.activity_timeline(label, obs_log, rep_log)
        cp_state = {"none": ("No checkpoint saved yet", "unknown"), "intact": ("Log matches its checkpoint", "ok"),
                    "rewritten": ("Log REWRITTEN since checkpoint", "bad"),
                    "truncated": ("Log TRUNCATED since checkpoint", "bad"),
                    "invalid": ("Saved checkpoint is invalid", "bad")}[tl["checkpoint"]]
        st.markdown(
            f'<div class="card"><h4>Evidence</h4>'
            f'<div class="kv"><b>Log integrity</b>{badge(cp_state[0], cp_state[1])}</div>'
            f'<div class="kv"><b>Records</b><span class="mono">{len(tl["runs"])} runs · {len(tl["agents"])} agents · '
            f'{len(tl["actions"])} actions · {len(tl["self_reported"])} self-reported</span></div>'
            f'<div class="kv"><b>Rejected rows</b><span class="mono">{len(tl["rejected"]["observer"])} observer · '
            f'{len(tl["rejected"]["self_reported"])} self-reported</span></div></div>',
            unsafe_allow_html=True)

        st.markdown("**What needs review**")
        if not tl["findings"]:
            st.success("No activity rule fired.")
        else:
            st.html(findings_html(tl["findings"]))

        st.markdown("**What happened, in order**")
        st.html(activity_feed_html(tl))

        col = st.column_config.TextColumn
        st.markdown("**Runs and delegation** · one agent identity can have many runs; a helper keeps its own run")
        st.dataframe([{
            "record": r["record_id"], "run": r["run_id"], "label": r["label"],
            "agent": short(r["agent"]) if r["agent"] != UNKNOWN else UNKNOWN,
            "delegated_by": r["delegated_by"] or "", "access": r["access"], "tool": r["tool"],
            "started": r["started_at"], "pid": r["pid"] if r["pid"] is not None else "",
        } for r in tl["runs"]], width="stretch", hide_index=True, column_config={
            "record": col("Record"), "run": col("Run"), "label": col("Label"),
            "agent": col("Agent ID", help="From deployment + agent_id + instance_id on the observer's run record."),
            "delegated_by": col("Delegated by"), "access": col("Access"), "tool": col("Tool"),
            "started": col("Started"), "pid": col("PID"),
        })
        st.markdown("**All actions**")
        with st.expander("All actions as a table"):
            st.dataframe([{
            "record": a["record_id"], "time": a["time"], "actor": a["actor"], "actor_type": a.get("actor_type", ""),
            "why": a["actor_reason"],
            "initiated_by": a["initiated_by"] or "", "action": a.get("operation") or a.get("tool_call", ""),
            "path_or_target": a.get("path") or a.get("target", ""),
            "approval": {True: "yes", False: "no"}.get(a.get("approval"), UNKNOWN),
            "protected": {True: "yes", False: "no"}.get(a.get("protected"), UNKNOWN),
            } for a in tl["actions"]], width="stretch", hide_index=True, column_config={
                "record": col("Record"), "time": col("Time"), "actor": col("Actor"),
                "actor_type": col("Actor type"), "why": col("Actor evidence"),
                "initiated_by": col("Initiated by"), "action": col("Action"),
                "path_or_target": col("Path / target", width="large"), "approval": col("Approval"),
                "protected": col("Protected"),
            })
        if tl["shared_files"]:
            st.markdown("**Files touched by more than one actor** · overlap is a lead for review, not proof")
            st.dataframe([{"path": p, "actors": ", ".join(who)} for p, who in tl["shared_files"].items()],
                         width="stretch", hide_index=True)
        if tl["self_reported"]:
            st.markdown("**The agent's own report, checked against the observer**")
            st.dataframe([{
                "record": r["record_id"], "run": r["run_id"], "claim": r.get("path") or r.get("label") or r["kind"],
                "verdict": r["verdict"],
            } for r in tl["self_reported"]], width="stretch", hide_index=True)
        st.caption("Evidence model ported from UdanM/forkit-agent-audit. Rules POL-006 to POL-018 live in "
                   "fixtures/policies.json; diffs are reduced to a hash and size, never stored.")
