"""
AutoDiscover: live demo UI.

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
from src.timeline import timeline_from_files
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
    # Keyed by Streamlit's browser-session id, which survives the in-app "Simulate Restart" (that only
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
:root {
  --ad-ink:#1C1917; --ad-muted:#57534E; --ad-line:#E6E1D8; --ad-soft:#F2EFE9; --ad-accent:#B0532F;
  --ad-accent-hover:#A84D2D; --ad-canvas:#FAF9F6;
  --ad-serif: Georgia, 'Times New Roman', serif;
  --ad-elev: 0 1px 3px rgba(28,25,23,0.05), 0 1px 2px rgba(28,25,23,0.03);
  --ad-sans: Inter, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --ad-mono: 'JetBrains Mono', Consolas, 'SF Mono', Menlo, ui-monospace, monospace;
}
/* Standalone app chrome */
[data-testid="stDeployButton"], .stAppDeployButton, [data-testid="stMainMenu"] { display:none !important; }
header[data-testid="stHeader"] { height: 2rem; background: transparent; }
.block-container, [data-testid="stMainBlockContainer"] { padding: 0.9rem 2rem 2.5rem 2rem; max-width: 1600px; }
h1, h2, h3, h4 { font-family: var(--ad-sans) !important; color: var(--ad-ink); letter-spacing:-0.015em; }
h3 { font-weight: 650 !important; margin-top: 0.2rem !important; }
/* Editorial contrast: serif only for the brand title and each tab's Step heading (st.subheader = h3) */
[data-testid="stMainBlockContainer"] h3 { font-family: var(--ad-serif) !important; font-weight: 400 !important;
                                          letter-spacing: -0.01em; }
code, .mono { font-family: var(--ad-mono) !important; }

/* Header */
.hero { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px 24px;
        padding:0 0 12px 0; }
.hero-title { font-family:var(--ad-serif); font-size:1.6rem; font-weight:400; color:var(--ad-ink); letter-spacing:-0.015em; line-height:1.15; }
.hero-sub { font-family:var(--ad-sans); font-size:0.95rem; font-weight:500; color:var(--ad-muted); margin-left:12px; letter-spacing:0; }
.pills { display:flex; flex-wrap:wrap; gap:6px; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:4px 11px; border-radius:6px;
        font-weight:550; font-size:0.82rem; color:#44403C; background:#fff; border:1px solid var(--ad-line); box-shadow:var(--ad-elev); }
.pill::before { content:""; width:6px; height:6px; border-radius:50%; background:var(--pill-dot); }

/* Tabs (Streamlit 1.67 renders react-aria tabs) */
.stTabs [role="tablist"] { gap: 28px; border-bottom:1px solid var(--ad-line); }
.stTabs [data-testid="stTab"] { padding: 10px 0; background: transparent; }
.stTabs [data-testid="stTab"] p { font-size:0.98rem; font-weight:500; color:var(--ad-muted); }
.stTabs [data-testid="stTab"]:hover p { color: var(--ad-ink); }
.stTabs [data-testid="stTab"][aria-selected="true"] p { color: var(--ad-ink); font-weight:650; }
.stTabs .react-aria-SelectionIndicator { background-color: var(--ad-accent) !important; height: 2px !important; }

/* Matte status pills: one meaning per colour */
.badge { display:inline-block; padding:1px 8px; border-radius:5px; font-weight:600; font-family:var(--ad-sans);
         font-size:0.78rem; margin:2px 4px 2px 0; letter-spacing:0.01em; line-height:1.55; vertical-align:middle;
         border:1px solid transparent; }
.whatsnew { display:flex; flex-wrap:wrap; gap:6px 14px; align-items:baseline; margin:0 0 10px;
  padding:9px 14px; border:1px solid #E7C9B8; border-left:3px solid #C25E3A; background:#FDF6F1;
  border-radius:8px; font-size:13px; color:#44403C; }
.whatsnew b { color:#9A4527; font-weight:600; margin-right:4px; }
.whatsnew i { font-style:normal; font-weight:600; color:#1C1917; margin-right:4px; }
.b-ok       { background:#ECFDF5; color:#047857; border-color:#A7F3D0; }
.b-unknown  { background:#FFFBEB; color:#B45309; border-color:#FDE68A; }
.b-bad      { background:#FFF1F2; color:#BE123C; border-color:#FECDD3; }
.b-warn     { background:#FFF7ED; color:#C2410C; border-color:#FED7AA; }
.b-slate, .b-neutral { background:#F5F5F4; color:#44403C; border-color:#D6D3D1; }
.b-redacted { background:#EEF2FA; color:#2B4C8C; border-color:#C7D3EA; }

/* Cards */
.card { border:1px solid var(--ad-line); border-radius:10px; padding:16px 18px; margin-bottom:12px; background:#fff;
        box-shadow:var(--ad-elev); }
.card h4, .card-body h4 { margin:0 0 8px 0; font-size:1.05rem; font-weight:650; color:var(--ad-ink); }
.card.drift    { border-color:#FDBA74; }
.card.incident { border-color:#FDA4AF; background:#FFFBFB; }
.card.incident h4 { color:#BE123C; }
.card.audit    { border-color:#C7D3EA; }
.muted { color:var(--ad-muted); }
.mono { font-size:0.84rem; color:#292524; }
.purpose { font-size:1rem; line-height:1.55; color:var(--ad-ink); max-width:62rem; margin:-4px 0 14px 0; }
.ov { display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin:4px 0 14px 0; }
.ov-tile { background:#fff; border:1px solid var(--ad-line); border-radius:8px; padding:12px 16px 11px; }
.ov-label { font-size:0.8rem; color:#57534E; }
.ov-value { font-size:1.75rem; font-weight:600; line-height:1.25; color:var(--ad-ink); font-variant-numeric:tabular-nums; }
.ov-note { font-size:0.8rem; color:#57534E; }
.ov-tile.alert { border-color:#E9B8A3; }
.ov-tile.alert .ov-value { color:#9F1239; }
.ov-tile.warn { border-color:#EBC9A8; }
.ov-tile.warn .ov-value { color:#9A4527; }
.kv { display:flex; flex-wrap:nowrap; align-items:baseline; column-gap:6px; margin:3px 0; }
.kv > b { flex:0 0 140px; color:var(--ad-muted); font-weight:500; font-size:0.9rem; }
.kv > :not(b) { min-width:0; }
.kv .mono { word-break: break-all; }
.diff-del { color:#BE123C; } .diff-add { color:#047857; }

/* Tab 2: equal-height identity cards with the action on one baseline */
[data-testid="stHorizontalBlock"]:has(.card-body) { align-items: stretch; }
[data-testid="stHorizontalBlock"]:has(.card-body) > [data-testid="stColumn"] > div,
[data-testid="stHorizontalBlock"]:has(.card-body) > [data-testid="stColumn"] [data-testid="stVerticalBlock"] { height:100%; }
[class*="st-key-idcard_"] { background:#fff; border-radius:10px !important; border-color:var(--ad-line) !important;
                           box-shadow:var(--ad-elev); }
[class*="st-key-idcard_"] > div:has(.card-body) { flex: 1 1 auto; }
[class*="st-key-idcard_collision_"] { border-color:#FDBA74 !important; }

/* Tab 5: diagrams framed like every other card */
[class*="st-key-graphframe_"] { background:#fff; border-radius:10px !important; border-color:var(--ad-line) !important;
                               box-shadow:var(--ad-elev); }

/* Buttons: flat, matte */
.stButton button, .stDownloadButton button { font-weight:600; border-radius:8px; box-shadow:none; }
.stButton button[kind="primary"], .stDownloadButton button[kind="primary"] { background:var(--ad-accent); border:1px solid var(--ad-accent); color:#fff; }
.stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover { background:var(--ad-accent-hover); border-color:var(--ad-accent-hover); color:#fff; }
.stButton button[kind="secondary"], .stDownloadButton button[kind="secondary"] { background:#fff; border:1px solid #D6D1C7; color:var(--ad-ink); }
.stButton button[kind="secondary"]:hover, .stDownloadButton button[kind="secondary"]:hover { border-color:#A8A29E; background:var(--ad-canvas); color:var(--ad-ink); }
[data-testid="stAlert"] > div { border-radius:8px; }
[data-testid="stExpander"] details { border-radius:8px; }

/* Sidebar */
section[data-testid="stSidebar"] { border-right:1px solid #E5E0D8; }
[data-testid="stSidebarUserContent"] { padding-top: 0.25rem; }
[data-testid="stSidebarHeader"] { height: 2rem; padding-top: 0.6rem; padding-bottom: 0; }
section[data-testid="stSidebar"] hr { margin: 0.8rem 0 !important; border-color: var(--ad-line); }
.sb-brand { display:flex; align-items:center; gap:9px; font-family:var(--ad-serif); font-size:1.3rem; font-weight:400;
            color:var(--ad-ink); letter-spacing:-0.01em; }
.mark { width:22px; height:22px; border-radius:6px; background:var(--ad-accent); color:#fff; font-size:0.72rem;
        font-family:var(--ad-sans); font-weight:700; display:inline-flex; align-items:center; justify-content:center; letter-spacing:0; }
.sb-tag { color:var(--ad-muted); font-size:0.8rem; margin-top:4px; }
.sb-section { font-size:0.72rem; font-weight:650; letter-spacing:0.06em; text-transform:uppercase;
              color:var(--ad-muted); margin:2px 0 8px 0; }
.metrics { background:#fff; border:1px solid var(--ad-line); border-radius:8px; margin-bottom:10px; box-shadow:var(--ad-elev); }
.mrow { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; font-size:0.92rem;
        color:#44403C; }
.mrow + .mrow { border-top:1px solid var(--ad-line); }
.mrow b { font-size:1.15rem; font-weight:700; color:var(--ad-ink); font-variant-numeric: tabular-nums; }
.mrow.alert b { color:#BE123C; }
.mrow .badge { margin:0; }
/* ── 10/10 refinements (style only; no layout rules above are changed) ── */
.hero-title { font-size:1.85rem; }
.hero-sub { color:#5F5954; }
[data-testid="stAlert"] > div { border:1px solid rgba(28,25,23,0.07); box-shadow:var(--ad-elev); }
[data-testid="stAlert"] code { background:rgba(255,255,255,0.65); }
[data-testid="stExpander"] details { background:#fff; border-color:var(--ad-line); box-shadow:var(--ad-elev); }
[data-testid="stExpander"] summary:hover { color:var(--ad-accent); }
[data-baseweb="select"] > div { background:#fff !important; border-color:#D6D1C7 !important; }
[data-baseweb="select"] > div:hover { border-color:#A8A29E !important; }
.stButton button:disabled { background:#F2F7F3 !important; border:1px solid #CFE3D5 !important;
                            color:#2F6B45 !important; opacity:1 !important; cursor:default; }
.stButton button:focus-visible, .stDownloadButton button:focus-visible {
    outline:2px solid rgba(194,94,58,0.45); outline-offset:2px; }
[data-testid="stCaptionContainer"] p { color:#5F5954; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color:#57534E; }
[data-testid="stJson"] * { color:#1C1917 !important; }
[data-testid="stJson"] span.string-value { color:#9A4527 !important; }
[data-testid="stCaptionContainer"] strong { color:#44403C; }
.stTabs [data-testid="stTab"]:hover { box-shadow: inset 0 -2px 0 #E6E1D8; }
.card h4 .mono, .kv .mono { color:#3F3A36; }
.mrow b, .badge { font-variant-numeric: tabular-nums; }
::selection { background: rgba(194,94,58,0.18); }
/* Sober status labels: a small colour square + dark text on a hairline, no tinted pills */
.badge { background:#fff !important; border:1px solid var(--ad-line) !important; border-radius:4px;
         color:var(--ad-ink) !important; font-weight:600; letter-spacing:0.02em; padding:0 7px 0 6px; }
.badge::before { content:""; display:inline-block; width:7px; height:7px; margin:0 6px 1px 0;
                 vertical-align:middle; background:var(--b-dot, #A8A29E); }
.b-ok { --b-dot:#2F7A4F; } .b-unknown { --b-dot:#D97706; } .b-bad { --b-dot:#BE123C; }
.b-warn { --b-dot:#EA580C; } .b-slate, .b-neutral { --b-dot:#A8A29E; } .b-redacted { --b-dot:#3E63A8; }
.card-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:6px; }
.card-head h4 { margin:0 !important; }
.note-warn { border-left:2px solid #EA580C; padding:2px 0 2px 10px; margin:4px 0 8px 0; font-size:0.88rem;
             color:#7C2D12; }
.sb-brand .logo { flex:none; display:block; }
/* ── Pitch refresh: hero, segmented tabs, next-step card (hero and feed ideas from forkit-agent-audit) ── */
[data-testid="stToolbar"] { display:none !important; }
.hero2 { position:relative; overflow:hidden; display:flex; align-items:center; gap:24px; margin:0 0 14px;
  padding:22px 30px; border-radius:16px; color:#FAF9F6;
  background: radial-gradient(ellipse at 85% 50%, rgba(194,94,58,0.38) 0, transparent 55%),
              linear-gradient(135deg, #2A211C 0%, #1C1917 70%); box-shadow:0 10px 30px rgba(28,25,23,0.12); }
.hero2-text { flex:1 1 auto; min-width:0; position:relative; z-index:1; }
.hero2 .eyebrow { font-size:0.78rem; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:#E3A07F; }
.hero2 h1 { font-family:var(--ad-serif) !important; color:#FAF9F6 !important; font-weight:400 !important;
  font-size:1.85rem !important; line-height:1.15 !important; letter-spacing:-0.02em; margin:8px 0 10px !important; padding:0 !important; }
.hero2 p { color:#D6CFC7; font-size:0.98rem; line-height:1.5; max-width:46rem; margin:0 0 12px; }
.flow { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.flow span { display:inline-flex; align-items:center; gap:7px; padding:5px 12px 5px 6px; border-radius:999px;
  background:rgba(250,249,246,0.08); border:1px solid rgba(250,249,246,0.16); font-size:0.88rem; font-weight:550; }
.flow b { display:inline-grid; place-items:center; width:20px; height:20px; border-radius:50%; background:#C25E3A;
  color:#fff; font-size:0.72rem; }
.flow i { width:14px; height:1px; background:rgba(250,249,246,0.35); }
.hero2-art { position:relative; flex:0 0 140px; height:140px; }
.hero2-art .ring { position:absolute; inset:0; border:1px solid rgba(227,160,127,0.45); border-radius:50%; }
.hero2-art .ring.r2 { inset:28px; border-style:dashed; }
.hero2-art .core { position:absolute; inset:40px; display:grid; place-items:center; border-radius:22px;
  background:linear-gradient(135deg,#D7764F,#9A4527); box-shadow:0 0 40px rgba(215,118,79,0.55); }
.hero2-art .dot { position:absolute; width:9px; height:9px; border-radius:50%; background:#F3C9A8; box-shadow:0 0 12px #F3C9A8; }
.hero2-art .d1 { left:6px; top:52px; } .hero2-art .d2 { right:10px; bottom:40px; background:#9ED9B5; box-shadow:0 0 12px #9ED9B5; }
.hero2-art .d3 { right:34px; top:8px; }
@media (max-width: 1100px) { .hero2-art { display:none; } }
.whatsnew { margin:0; flex-direction:column; gap:6px; border:0; background:transparent; padding:0; }
/* Segmented tab bar */
.stTabs [role="tablist"] { gap:4px !important; border-bottom:0 !important; padding:5px; background:#F2EFE9;
  border-radius:12px; width:fit-content; max-width:100%; }
.stTabs [data-testid="stTab"] { padding:7px 14px !important; border-radius:8px; }
.stTabs [data-testid="stTab"]:hover { box-shadow:none !important; background:rgba(255,255,255,0.6); }
.stTabs [data-testid="stTab"][aria-selected="true"] { background:#1C1917; }
.stTabs [data-testid="stTab"][aria-selected="true"] p { color:#FAF9F6 !important; }
.stTabs .react-aria-SelectionIndicator { display:none !important; }
/* Next step card */
.nextstep { display:flex; gap:12px; align-items:baseline; margin:0 0 16px; padding:10px 14px; background:#fff;
  border:1px solid var(--ad-line); border-left:3px solid #C25E3A; border-radius:10px; font-size:0.93rem; color:#292524;
  box-shadow:var(--ad-elev); }
.nextstep > span { flex:none; font-size:0.72rem; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; color:#9A4527; }
.nextstep code { font-size:0.82rem; background:#F2EFE9; padding:0 4px; border-radius:4px; }
.ov-tile { border-radius:12px; box-shadow:var(--ad-elev); }
.ov-value { font-size:2rem; }
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


# Logo: a Bauhaus-style mark. The circle (discovery) meets the square (the passport):
# what is found is anchored to one fixed, stable identity.
LOGO_SVG = (
    # The provenance chain as a mark: config file, provenance hash, then the passport (terracotta square).
    '<svg class="logo" width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">'
    '<rect width="32" height="32" rx="7" fill="#1C1917"/>'
    '<path d="M9.5 22.5 L15.5 16.5 L19 13" stroke="#FAF9F6" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
    '<circle cx="9.5" cy="22.5" r="3.4" fill="#FAF9F6"/>'
    '<circle cx="15.5" cy="16.5" r="3.4" fill="#FAF9F6"/>'
    '<rect x="17.5" y="5.5" width="9" height="9" rx="1.5" fill="#C25E3A"/></svg>'
)


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
    1: "Click **Scan**, point at the [REDACTED] card and the disk check, then open **02 Identity**.",
    2: "Point at the same-name note and the UNKNOWN labels, mint all three, then click **Simulate Restart** in the sidebar.",
    3: "Toggle **Load v2 Config Update** in the sidebar, show the diff and why access mode is UNKNOWN, then open **04 Linking**.",
    4: "Pick evt-0002 and click Link without consent (blocked), then tick consent and link (MISSING_APPROVAL + INCIDENT). "
       "Then evt-0003 on the filesystem-mcp passport (blocked). evt-0004 names only `postgres-mcp`, which two "
       "configs share, so it is ambiguous: record it for review instead. Then open **05 Map**.",
    5: "Walk the top diagram once, then read the evt-0002 row of the live map from left to right.",
    6: "Click **Save Log Checkpoint**, read the helper's delegated write to the protected file, then click "
       "**Simulate Log Tampering**: the rewritten log raises a critical finding.",
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
    st.markdown(f'<div class="sb-brand">{LOGO_SVG}AutoDiscover</div>'
                '<div class="sb-tag">Built on Forkit Core '
                '<span class="mono">compute_id</span></div>', unsafe_allow_html=True)
    st.divider()

    # ── Section 1: Discovery Mode ──
    sidebar_section("Source")
    mode_labels = {"demo": "Demo fixtures (default)", "real": "Scan Real Local Machine",
                   "agents": "Live agents (captured runs)"}
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
            "Click **Run Agent Scenarios** in the sidebar, then follow the tabs. Results go to `data/agents/`."
            if picked == "agents" else "Back to the deterministic demo fixtures.",
        )
        st.rerun()

    # ── Section 2: Demo Controls ──
    st.divider()
    sidebar_section("Demo")
    if agents_mode():
        if st.button("Run Agent Scenarios", width="stretch", type="primary",
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
            st.session_state.flash = ("success", f"Agents ran. {run['events']} tool calls captured and minted "
                                      "passports are ready. Link the events in **04 Linking**.")
            st.rerun()
    elif real_mode():
        if st.button("Clear Local Scan Registry", width="stretch", type="primary"):
            for f in LOCAL_DATA.glob("*.json"):
                f.unlink()
            st.session_state.clear()
            st.session_state.mode = "real"
            st.session_state.flash = ("success", "Local scan registry in `data/local/` cleared.")
            st.rerun()
    elif st.button("1-Click Reset Demo State", width="stretch", type="primary"):
        for f in DATA.glob("*.json"):
            f.unlink()
        shutil.rmtree(LIVE, ignore_errors=True)
        shutil.rmtree(ACTIVITY_LIVE, ignore_errors=True)
        shutil.rmtree(DATA / "audit-archive.git", ignore_errors=True)
        st.session_state.clear()
        restore_live_configs(v2=False)
        st.session_state.flash = ("success", "Demo reset: registry, links, incidents and changes cleared.")
        st.rerun()

    if st.button("Simulate Restart", width="stretch"):
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
        "Load v2 Config Update",
        value=st.session_state.v2_active,
        help="Simulates editing claude_desktop_config.json in place with the v2 contents.",
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
                "v2 config loaded, but filesystem-mcp has no passport yet, so there is nothing to diff against. "
                "For the demo: turn v2 off, mint filesystem-mcp in **02 Identity**, then turn v2 on again.",
            )
        else:
            st.session_state.flash = (
                "warning" if v2_toggle else "info",
                "v2 config loaded: filesystem-mcp args changed on disk. See **03 Drift**."
                if v2_toggle else "Reverted to the original v1 config.",
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
        f'<div class="mrow"><span>Confirmed passports</span><b>{len(passports)}</b></div>'
        f'<div class="mrow"><span>Linked events</span><b>{len(eng.linked_events())}</b></div>'
        f'<div class="mrow{" alert" if n_incidents else ""}"><span>Incidents</span><b>{n_incidents}</b></div>'
        '</div><div class="metrics">'
        f'<div class="mrow"><span>Integrity · {integ["passports_checked"]} passports</span>'
        f'{status_badge(integ["status"])}</div>'
        f'<div class="mrow"><span>Secret scan · {len(scan["files_scanned"])} file{"" if len(scan["files_scanned"]) == 1 else "s"}</span>'
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
        st.download_button("Download Governance Audit Report", bundle, file_name=f"{base}.zip",
                           mime="application/zip", width="stretch", type="primary")
        c_json, c_md = st.columns(2)
        c_json.download_button("JSON", json.dumps(audit, indent=2, sort_keys=True), file_name=f"{base}.json",
                               mime="application/json", width="stretch")
        c_md.download_button("Markdown", render_audit_markdown(audit), file_name=f"{base}.md",
                             mime="text/markdown", width="stretch")

    with st.expander("Append-only Git archive"):
        if not git_available():
            st.caption("Git is not installed here, so the archive is unavailable.")
        else:
            archive_repo = eng.data_dir / "audit-archive.git"
            st.caption("Every link, incident, review item, drift record and activity finding, committed to a "
                       f"separate bare Git repository at `{store_label()}/audit-archive.git`. Earlier records can "
                       "never change without verification failing.")
            if st.button("Archive New Records", width="stretch"):
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
    '<div class="hero2"><div class="hero2-text">'
    '<div class="eyebrow">Forkit AI · Safe onboarding of AI agents and tools</div>'
    '<h1>Every AI tool gets a passport.<br>Every action gets a trail.</h1>'
    '<p>AutoDiscover finds the AI tools on a machine, gives each one a permanent ID, flags changes, and shows '
    'which agent did what, without guessing and without storing a single secret.</p>'
    '<div class="flow"><span><b>1</b>Discover</span><i></i><span><b>2</b>Passport</span><i></i>'
    '<span><b>3</b>Govern</span><i></i><span><b>4</b>Link</span><i></i><span><b>5</b>Map</span><i></i>'
    '<span><b>6</b>Trace</span></div></div>'
    '<div class="hero2-art" aria-hidden="true"><div class="ring"></div><div class="ring r2"></div>'
    '<div class="core"><svg viewBox="0 0 24 24" width="44" height="44"><rect x="5" y="3" width="14" height="18" rx="2.5" '
    'fill="none" stroke="#FAF9F6" stroke-width="1.6"/><circle cx="12" cy="10" r="2.6" fill="none" stroke="#FAF9F6" '
    'stroke-width="1.6"/><path d="M8.5 16.5h7" stroke="#FAF9F6" stroke-width="1.6" stroke-linecap="round"/></svg></div>'
    '<i class="dot d1"></i><i class="dot d2"></i><i class="dot d3"></i></div></div>',
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
    tl = timeline_from_files(obs, rep, rules=eng.policies(), namespace=label) if obs else None
    return {"passports": eng.passports(), "links": eng.linked_events(), "incidents": eng.incidents(),
            "review_queue": eng.review_queue(), "policy_findings": audit["policy_findings"], "timeline": tl}


whats_new_html = (
    '<div class="whatsnew"><b>New</b>'
    '<span><i>06 Timeline</i> delegated runs and the log tampering demo</span>'
    '<span><i>04 Linking</i> ambiguous events evt-0004 and evt-0005 go to a review queue</span>'
    '<span><i>Ask AutoDiscover</i> questions answered with sources</span>'
    '<span><i>Sidebar</i> append-only Git archive</span>'
    '<span><i>Scan Real Local Machine</i> Claude Code, Windsurf, Codex, AI CLIs, apps and extensions</span>'
    '<span><i>Live agents</i> scenario F, a helper that breaks its read-only promise</span></div>'
)

_c_ask, _c_new = st.columns([3, 2])
with _c_new.expander("What's new after the team merge"):
    st.markdown(whats_new_html, unsafe_allow_html=True)
with _c_ask.expander("Ask AutoDiscover"):
    q_col, b_col = st.columns([5, 1])
    question = q_col.text_input("Question", key="ask_q", label_visibility="collapsed",
                                placeholder="What needs review?  ·  Who changed reports/q3/summary.md?  ·  "
                                            "Which tools did you find?")
    if b_col.button("Ask", width="stretch") and question.strip():
        st.session_state.ask_result = ask_question(question, ask_store())
    res = st.session_state.get("ask_result")
    if res:
        st.markdown(f"**{html.escape(res['answer'])}**")
        for c in res["citations"]:
            st.markdown(f'{badge(c["kind"], "neutral")} <span class="mono">{html.escape(c["id"])}</span> '
                        f'{html.escape(c["text"])}', unsafe_allow_html=True)
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
            "Scan in **01 Discover**, mint in **02 Identity**, then click **Simulate Restart** again.")
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
            f"({dupes} duplicates). Mint the rest in **02 Identity** to see them recognized next time."
        )
    with st.expander("Passport IDs before vs. after restart"):
        st.json({"before_restart": proof["before"], "after_restart": proof["after"]})

# Keyed, stateful tabs: clicking a button inside a tab keeps you on that tab.
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["01 Discover", "02 Identity", "03 Drift", "04 Linking",
     "05 Map", "06 Timeline"],
    key="active_tab",
    on_change="rerun",
)

candidates: list[dict[str, Any]] = st.session_state.candidates

# ── 01 Discover ───────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Find MCP servers on this machine")
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
        scan_label = "Scan Real Local Machine"
    elif agents_mode():
        st.caption("Scans the configs the agents ran with: "
                   + (", ".join(f"`configs/{p.name}`" for p in agent_config_paths())
                      or "none yet. Click **Run Agent Scenarios** in the sidebar first."))
        scan_label = "Scan Local MCP Configs"
    else:
        st.caption("Scans local MCP configs: " + ", ".join(f"`{c}`" for c in CONFIGS))
        scan_label = "Scan Local MCP Configs"
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

# ── 02 Identity ──────────────────────────────────────────────────────

with tab2:
    st.subheader("Review each server and issue its passport")
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
        st.info("Scan in **01 Discover** first.")
    else:
        name_sources: dict[str, set[str]] = {}
        for c in candidates:
            name_sources.setdefault(c["tool_name"], set()).add(c["source_path"])
        passports = eng.passports()
        if st.session_state.v2_active and any(
            c["source_path"] == CLAUDE and c["passport_id"] not in passports for c in candidates
        ):
            st.warning("You are on the edited **v2** config. Minting now records v2 as the baseline, so **03 Drift** will "
                       "have no diff to show. For the demo, turn v2 off in the sidebar first and mint on v1.")

        cols = st.columns(len(candidates))
        for col, c in zip(cols, candidates, strict=True):
            with col, st.container(
                border=True, height="stretch",
                key=f"idcard_{'collision_' if len(name_sources[c['tool_name']]) > 1 else ''}{c['passport_id'][:16]}",
            ):
                collision = len(name_sources[c["tool_name"]]) > 1
                minted = c["passport_id"] in passports
                status = badge("CONFIRMED", "ok") if minted else badge("CANDIDATE", "neutral")
                warn = (
                    '<div class="note-warn">Same name as another tool, different provenance. '
                    'Kept as a separate passport.</div>'
                    if collision else ""
                )
                changed = badge("METADATA CHANGED", "bad") if c.get("metadata_changed") else ""
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
                    st.button("Passport minted", key=f"mint_{c['passport_id']}", disabled=True,
                              width="stretch")
                elif st.button("Single-Click Mint Stable ID", key=f"mint_{c['passport_id']}",
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

# ── 03 Drift ─────────────────────────────────────────────────────

with tab3:
    st.subheader("Config changes and policy findings")
    tab_purpose(3)
    demo_hint(3)
    changed = [c for c in candidates if c.get("metadata_changed")]
    if real_mode():
        if not changed:
            st.info("No config drift on confirmed passports. To see it, mint a tool in **02 Identity**, edit that tool's "
                    "args in your MCP config, then re-scan in **01 Discover**.")
    elif agents_mode():
        if not changed:
            st.info("Scenario E removes `--read-only` from the Claude Desktop agent config during the run. Click "
                    "**Run Agent Scenarios** in the sidebar and the change shows up here, on the same passport.")
    elif not st.session_state.scanned:
        st.info("Run discovery in **01 Discover** and mint `filesystem-mcp` in **02 Identity** first.")
    elif not st.session_state.v2_active:
        st.info("Toggle **Load v2 Config Update** in the sidebar to simulate an in-place config edit.")
    elif not changed:
        st.info("v2 is loaded, but no confirmed passport changed. Mint `filesystem-mcp` in **02 Identity** first, "
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
            f'{badge("Same passport_id", "ok")}{badge("PENDING_REVIEW", "warn")}'
            f'<div class="kv"><b>Fingerprint</b><span class="mono">{stored["config_hash"][:12]}… → '
            f'{c["config_hash"][:12]}…</span></div>'
            f'<div style="margin-top:8px">{"".join(lines)}</div>{reason}'
            f'<div class="muted" style="margin-top:10px">The confirmed passport is unchanged. Drift is recorded in '
            f'<span class="mono">{store_label()}/metadata_changes.json</span>.</div></div>',
            unsafe_allow_html=True,
        )
    log = eng.metadata_changes()
    if log:
        with st.expander(f"metadata_changes.json ({len(log)})"):
            st.json(log)

    st.markdown("#### Rule checks")
    st.caption("Company rules are written down in `fixtures/policies.json`, not hidden in code. Each finding shows "
               "the facts it used. Missing facts are never guessed.")
    findings = eng.policy_findings()
    if not findings:
        st.info("No findings yet. Scan in **01 Discover** to evaluate the rules.")
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

# ── 04 Linking ────────────────────────────────────────────────────

with tab4:
    st.subheader("Link runtime events to passports")
    tab_purpose(4)
    demo_hint(4)
    events = load_events()
    passports = eng.passports()
    if real_mode():
        st.info("Event linking runs on the demo's runtime event fixtures, whose sources point at the fixture "
                "configs. Switch to **Demo fixtures** in the sidebar to link events.")
    elif agents_mode() and not events:
        st.info("No captured agent actions yet. Click **Run Agent Scenarios** in the sidebar: a scripted LangGraph "
                "agent works through a real MCP file server behind the AutoDiscover tap, and an n8n workflow "
                "reports its actions to the collector.")
    elif not passports:
        st.info("No passports yet. Scan in **01 Discover** and mint at least `filesystem-mcp` in **02 Identity** before linking events.")
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
                    if st.button("Link All Captured Events", width="stretch"):
                        by_src = {(p["source_path"], p["tool_name"]): pid for pid, p in passports.items()}
                        try:
                            done = [eng.link_event(e, by_src[(e["source"]["config_path"], e["source"]["server_name"])],
                                                   user_consent=all_ok)
                                    for e in unlinked
                                    if (e["source"]["config_path"], e["source"]["server_name"]) in by_src]
                            refresh_policies()
                            st.session_state.last_link = done[-1] if done else None
                            st.session_state.last_error = None
                        except PermissionError as exc:
                            st.session_state.last_error = f"PermissionError: {exc}"
                        st.rerun()
        left, right = st.columns([1, 1])
        with left:
            ev_labels = {
                e["event_id"]: (f'{scen_of.get(e["event_id"], "?")} · {ORIGIN_LABEL.get(e["capture"]["origin"], "")} · '
                                if agents_mode() else f'{e["event_id"]} · ')
                + f'{e["source"].get("server_name", UNKNOWN)} · {e["tool_call"]} · {e["result"]}'
                for e in events
            }
            ev_id = st.selectbox("Runtime event", list(ev_labels), format_func=ev_labels.get, key="ev_select")
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
                         "no_match": "Passport not minted yet"}[att["status"]]
                st.warning(f"**{title}.** {att['reason']} "
                           + ("Mint it in **02 Identity** to link this event. "
                              if att["status"] == "no_match" else "")
                           + "Linking it to a passport will be blocked by the engine.")
                if att["status"] != "no_match":
                    with st.container(border=True):
                        st.markdown("**Keep it for review, unassigned.** The actor stays UNKNOWN until stronger "
                                    "evidence appears.")
                        rv_ok = st.checkbox("I grant explicit consent to record this event for review",
                                            key=f"review_consent_{ev_id}")
                        if st.button("Record for Review", key=f"review_{ev_id}", width="stretch"):
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
            target = st.selectbox("File under passport", pids, index=pids.index(match),
                                  format_func=labels.get, key=f"target_{ev_id}")
            consent = st.checkbox("I grant explicit consent to link this event", key=f"consent_{ev_id}")
            if st.button("Link Event", type="primary", width="stretch"):
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
            st.error(f"Link blocked by engine. {st.session_state.last_error}")

        link = st.session_state.last_link
        if link:
            appr = badge(APPROVED, "ok") if link["approval_status"] == APPROVED else badge(MISSING_APPROVAL, "bad")
            src = link["source"] if isinstance(link["source"], dict) else {}
            st.markdown(
                f'<div class="card audit"><h4>Audit record · {html.escape(link["link_id"])}</h4>'
                f'<div class="kv"><b>Event</b><span class="mono">{html.escape(link["event_id"])} · '
                f'{html.escape(link["tool_call"])} → {html.escape(str(link["target"]))} ({html.escape(link["result"])})</span></div>'
                f'<div class="kv"><b>Source</b><span class="mono">{html.escape(src.get("client", UNKNOWN))} · '
                f'{html.escape(src.get("config_path", UNKNOWN))} · {html.escape(src.get("server_name", UNKNOWN))}</span></div>'
                f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(link["timestamp"])}</span></div>'
                f'<div class="kv"><b>Passport</b><span class="mono">{short(link["passport_id"])}</span></div>'
                f'<div class="kv"><b>Approval</b>{appr} {value_badge(link["approval_id"])} '
                f'<span class="muted">{"Approved in" if link["approval_status"] == APPROVED else "No approval on file in"} '
                f'{html.escape(eng.approvals_path.relative_to(ROOT).as_posix())}</span></div>'
                f'<div class="kv"><b>Runtime consent</b>{value_badge(link["runtime_consent_id"])}'
                f'{" <span class=muted>The event log does not say whether the user agreed.</span>" if link["runtime_consent_id"] == UNKNOWN else ""}</div>'
                f'<div class="kv"><b>Link consent</b>{badge("GRANTED", "ok")} <span class="muted">You ticked the consent box.</span></div>'
                f'<div class="kv"><b>Policy</b>'
                f'{"".join(badge(r, "bad") for r in link.get("policy_flags", [])) or badge("no rule violated", "ok")}'
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
                        f'<div class="kv"><b>Approval</b>{badge(inc["approval_status"], "bad" if inc["approval_status"] != APPROVED else "ok")}</div>'
                        f'<div class="kv"><b>Timestamp</b><span class="mono">{html.escape(inc["timestamp"])}</span></div>'
                        f'<div class="muted" style="margin-top:8px">Written to <span class="mono">{store_label()}/incidents.json</span>.</div></div>',
                        unsafe_allow_html=True,
                    )

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Linked events** · `{store_label()}/linked_events.json`")
            st.dataframe([{
                "event": lk["event_id"], "passport": short(lk["passport_id"]), "tool_call": lk["tool_call"],
                "timestamp": lk["timestamp"], "approval": lk["approval_status"], "flags": ", ".join(lk["flags"]) or "none",
            } for lk in eng.linked_events()], width="stretch", hide_index=True)
        with c2:
            st.markdown(f"**Incident log** · `{store_label()}/incidents.json`")
            st.dataframe([{
                "incident": i["incident_id"], "tool": i["tool_name"], "tool_call": i["tool_call"],
                "timestamp": i["timestamp"], "result": i["result"],
            } for i in eng.incidents()], width="stretch", hide_index=True)

# ── 05 Map ─────────────────────────────────────────────────────────

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
        st.info("Nothing to map yet. Scan in **01 Discover** (and mint in **02 Identity**) and the live map of configs, passports, "
                "events and incidents appears here.")
    else:
        with st.container(border=True, key="graphframe_live"):
            st.html(live)
        st.caption("Read each row left to right: the config file that declares a tool, the passport issued for it, "
                   "runtime events linked to that passport, and any incident they caused. Open checks are drift and "
                   "policy findings still waiting for a reviewer. Red means no approval or an incident. A dashed "
                   "passport has not been issued yet.")


# ── 06 Activity Timeline ──────────────────────────────────────────────────────

_SEV_KIND = {"critical": "bad", "high": "bad", "medium": "warn", "low": "slate"}


with tab6:
    st.subheader("Who did what, and can the log be trusted")
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
            st.info("No pilot log yet. Click **Run Agent Scenarios** in the sidebar: the delegated-helper pilot "
                    "writes an observer log here.")
        else:
            st.info("Point AutoDiscover at an observer log to build the timeline. The format is documented in "
                    "AUTODISCOVER_README.md (Activity timeline).")
    else:
        b1, b2, b3 = st.columns(3)
        if b1.button("Save Log Checkpoint", width="stretch",
                     help="Remembers LENGTH:SHA256 of the log as it is now. Later reads must start with the same bytes."):
            cp = eng.save_log_checkpoint(label, obs_log)
            st.session_state.flash = ("success", f"Checkpoint saved: `{cp[:28]}...`" if cp else "Log is empty.")
            st.rerun()
        if demo_mode():
            if b2.button("Simulate Log Tampering", width="stretch",
                         help="Rewrites one earlier record in the demo copy, as an agent covering its tracks would."):
                text = obs_log.read_text(encoding="utf-8")
                obs_log.write_text(text.replace('"protected": true', '"protected": null', 1), encoding="utf-8")
                st.rerun()
            if b3.button("Restore Original Log", width="stretch"):
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
            "run": r["run_id"], "label": r["label"], "agent": short(r["agent"]) if r["agent"] != UNKNOWN else UNKNOWN,
            "delegated_by": r["delegated_by"] or "", "access": r["access"], "tool": r["tool"],
            "started": r["started_at"], "pid": r["pid"] if r["pid"] is not None else "",
        } for r in tl["runs"]], width="stretch", hide_index=True, column_config={
            "run": col("Run"), "label": col("Label"),
            "agent": col("Agent ID", help="From deployment + agent_id + instance_id on the observer's run record."),
            "delegated_by": col("Delegated by"), "access": col("Access"), "tool": col("Tool"),
            "started": col("Started"), "pid": col("PID"),
        })
        st.markdown("**All actions**")
        with st.expander("All actions as a table"):
            st.dataframe([{
            "record": a["record_id"], "time": a["time"], "actor": a["actor"], "why": a["actor_reason"],
            "initiated_by": a["initiated_by"] or "", "action": a.get("operation") or a.get("tool_call", ""),
            "path_or_target": a.get("path") or a.get("target", ""),
            "approval": {True: "yes", False: "no"}.get(a.get("approval"), UNKNOWN),
            "protected": {True: "yes", False: "no"}.get(a.get("protected"), UNKNOWN),
            } for a in tl["actions"]], width="stretch", hide_index=True, column_config={
                "record": col("Record"), "time": col("Time"), "actor": col("Actor"), "why": col("Actor evidence"),
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
