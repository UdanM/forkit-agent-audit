"""
AutoDiscover provenance view: pure-Python Graphviz DOT builders.

No Streamlit import and no writes. Each builder takes ``store_dir`` explicitly
(``data/`` for the demo, ``data/local/`` for real-machine mode) and only reads
the JSON records found there. Every dynamic label goes through ``dot_escape``,
and node IDs are generated (never taken from config text), so unusual tool
names or paths cannot break the DOT syntax.

  lifecycle_dot(store_dir)          the concept: config → hash → ID → sidecars
  live_map_dot(store_dir, ...)      the actual inventory, or None when empty
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import MISSING_APPROVAL, UNKNOWN, derive_passport_id

__all__ = ["dot_escape", "load_store", "lifecycle_dot", "live_map_dot"]

MAX_LABEL = 64

# Warm editorial palette (matches the app's theme and status pills).
_C = {
    "ink": "#1c1917", "muted": "#78716c", "line": "#c9c2b6", "hair": "#e6e1d8",
    "accent": "#c25e3a",
    "blue_bg": "#eef2f8", "blue": "#2b4c8c",
    "green_bg": "#e3f6e8", "green": "#1f6b3a",
    "amber_bg": "#fbf4e4", "amber": "#7a4f0d",
    "orange_bg": "#fbefe9", "orange": "#9a4527",
    "red_bg": "#fde6e6", "red": "#9f1239",
    "grey_bg": "#f5f3ef",
}
_SEV_ORDER = ("critical", "high", "medium", "low")
_INCIDENT_TYPES = {"OUT_OF_EXPECTED_BEHAVIOR": "Out of expected behavior"}


def dot_escape(value: Any, limit: int = MAX_LABEL) -> str:
    """Escape text for a double-quoted DOT string.

    Backslashes and quotes are escaped, real newlines become DOT line breaks,
    other control characters become spaces, and long text is truncated.
    """
    text = "" if value is None else str(value)
    text = "".join(ch if ch == "\n" or ch >= " " else " " for ch in text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _lbl(*lines: Any) -> str:
    """Multi-line label from already-separate lines (each escaped)."""
    return "\\n".join(dot_escape(x) for x in lines if x not in (None, ""))


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError):
        return default


def load_store(store_dir: str | Path) -> dict[str, Any]:
    """Read-only snapshot of one store directory. Missing files are empty."""
    d = Path(store_dir)
    return {
        "passports": _read(d / "passports.json", {}),
        "links": _read(d / "linked_events.json", []),
        "incidents": _read(d / "incidents.json", []),
        "changes": _read(d / "metadata_changes.json", []),
        "findings": _read(d / "policy_findings.json", []),
    }


def _header(name: str, rankdir: str = "LR", font: int = 11, nodesep: float = 0.3, ranksep: float = 0.5) -> list[str]:
    # Streamlit's graphviz renderer forces SVG text to ~11 units, so the DOT
    # layout uses the same size. width="stretch" then scales the whole diagram
    # up to the container, which is what makes it readable on a projector.
    return [
        f'digraph "{name}" {{',
        f'  graph [rankdir={rankdir}, bgcolor="transparent", fontname="Helvetica", fontsize={font + 1}, '
        f'nodesep={nodesep}, ranksep={ranksep}, pad=0.2, splines=true];',
        f'  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize={font}, '
        f'color="{_C["line"]}", fillcolor="white", fontcolor="{_C["ink"]}", margin="0.12,0.06"];',
        f'  edge [fontname="Helvetica", fontsize=9, color="{_C["line"]}", fontcolor="{_C["muted"]}", '
        f'arrowsize=0.8];',
    ]


# ── Concept: lifecycle ─────────────────────────────────────────────────────────

def _card(step: str, title: str, sub: str, strip: str | None = None, border: str | None = None,
          width: int = 1, sub_color: str | None = None) -> str:
    """HTML-like label: a quiet white card with an optional colour strip on the left.

    Only fixed text and integers go in here (never config text), so no escaping is needed.
    All text stays at the header font size: Streamlit renders SVG text at ~11 units
    regardless of POINT-SIZE, so smaller sizes would overflow their cards.
    """
    c = _C
    rows = []
    if step:
        rows.append(f'<TR><TD ALIGN="LEFT"><FONT COLOR="{c["muted"]}">{step}</FONT></TD></TR>')
    rows.append(f'<TR><TD ALIGN="LEFT"><B>{title}</B></TD></TR>')
    rows.append(f'<TR><TD ALIGN="LEFT"><FONT COLOR="{sub_color or c["muted"]}">{sub}</FONT></TD></TR>')
    body = (f'<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">{"".join(rows)}</TABLE>')
    if strip:   # an inset status bar inside the card's padding, so it never pokes past the rounded corners
        body = (f'<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0"><TR>'
                f'<TD BGCOLOR="{strip}" WIDTH="3"></TD><TD WIDTH="8"></TD><TD ALIGN="LEFT">{body}</TD></TR></TABLE>')
    return (f'<<TABLE BORDER="{width}" COLOR="{border or c["line"]}" BGCOLOR="white" STYLE="rounded" '
            f'CELLBORDER="0" CELLSPACING="0" CELLPADDING="6"><TR><TD ALIGN="LEFT">{body}</TD></TR>'
            f'</TABLE>>')


def lifecycle_dot(store_dir: str | Path) -> str:
    """The identity lifecycle, annotated with live counts from ``store_dir``.

    Always a complete diagram, also for an empty store (counts are 0). Styled as
    quiet white cards on hairlines: one accent (the passport) and a thin colour
    strip per sidecar record type, instead of pastel fills.
    """
    s = load_store(store_dir)
    n_missing = sum(1 for x in s["links"] if x.get("approval_status") == MISSING_APPROVAL)
    n_pass, n_chg, n_links = len(s["passports"]), len(s["changes"]), len(s["links"])
    n_inc, n_find = len(s["incidents"]), len(s["findings"])
    c = _C
    #   row 1: config → redact → provenance hash
    #   row 2: compute_id → passport
    #   row 3: sidecar records joined by passport_id
    out = _header("lifecycle", rankdir="TB", nodesep=0.3, ranksep=0.45)
    out += [
        '  graph [splines=ortho];',
        '  node [shape=plain];',
        f'  edge [color="{c["line"]}", penwidth=1.2, arrowsize=0.7];',
        f'  cfg [label={_card("01", "Client config", "Claude · Cursor · VS Code")}];',
        f'  red [label={_card("02", "Redact secrets", "metadata-only")}];',
        f'  prov [label={_card("03", "Provenance hash", "source_path + tool + command")}];',
        f'  cid [label={_card("04", "Forkit Core", "compute_id()")}];',
        f'  pp [label={_card("PASSPORT", "Stable passport_id", f"{n_pass} confirmed · never rewritten", border=c["accent"], width=2, sub_color=c["accent"])}];',
        '  { rank=same; cfg; red; prov; }',
        '  { rank=same; cid; pp; }',
        '  cfg -> red -> prov;',
        '  prov -> cid;',
        f'  cid -> pp [color="{c["accent"]}", penwidth=1.6];',
        '  subgraph cluster_side {',
        f'    label="SIDECAR RECORDS  ·  joined by passport_id  ·  never mutate the passport"; labeljust=l; labelloc=b; '
        f'style="rounded"; color="{c["hair"]}"; bgcolor="#fbfaf7"; fontcolor="{c["muted"]}"; fontsize=9; margin=14;',
        f'    s_appr [label={_card("", "approvals.json", "external", strip="#a8a29e")}];',
        f'    s_chg [label={_card("", "metadata_changes.json", f"{n_chg} drift record(s)", strip="#ea580c", sub_color=c["orange"])}];',
        f'    s_lnk [label={_card("", "linked_events.json", f"{n_links} consented · {n_missing} MISSING_APPROVAL", strip="#d97706", sub_color=c["amber"])}];',
        f'    s_inc [label={_card("", "incidents.json", f"{n_inc} out-of-expected-behavior", strip="#be123c", sub_color=c["red"])}];',
        f'    s_pol [label={_card("", "policy_findings.json", f"{n_find} finding(s)", strip="#be123c", sub_color=c["red"])}];',
        '    { rank=same; s_appr; s_chg; s_lnk; s_inc; s_pol; }',
        '  }',
    ]
    for side in ("s_appr", "s_chg", "s_lnk", "s_inc", "s_pol"):
        out.append(f'  pp -> {side} [arrowhead=none, color="{c["line"]}", penwidth=1];')
    out.append("}")
    return "\n".join(out)


# ── Live provenance map ────────────────────────────────────────────────────────

def live_map_dot(
    store_dir: str | Path,
    candidates: list[dict[str, Any]] | None = None,
    focus: str | None = None,
    max_tools: int = 25,
) -> str | None:
    """Actual inventory as a graph, or None when there is nothing to draw.

    One cluster per config file; tool → passport → linked events → incidents,
    plus drift and policy-finding summaries. Unminted candidates are drawn
    dashed. ``focus`` restricts the map to one passport_id. Beyond
    ``max_tools`` tools, events are collapsed into a per-passport count.
    """
    s = load_store(store_dir)
    passports: dict[str, dict[str, Any]] = s["passports"]
    tools: dict[str, dict[str, Any]] = {pid: {**p, "_minted": True} for pid, p in passports.items()}
    for cand in candidates or []:
        tools.setdefault(cand["passport_id"], {**cand, "_minted": False})
    if focus:
        tools = {k: v for k, v in tools.items() if k == focus}
    if not tools:
        return None

    collapse = len(tools) > max_tools
    c = _C
    out = _header("provenance_map", nodesep=0.3, ranksep=0.9)
    # Every connector leaves a card on the right and enters the next on the left, so the map
    # reads strictly left to right. Edge labels are left off; the app caption names the columns.
    out += ['  graph [splines=spline, newrank=true];', '  edge [tailport=e, headport=w];']
    # Quiet cards: white fills with a coloured hairline; only events keep a tint (red/green status).
    out += ['  node [margin="0.16,0.08", penwidth=1.2];', f'  edge [color="{c["line"]}", penwidth=1.1];']
    by_source: dict[str, list[str]] = {}
    for pid, t in sorted(tools.items(), key=lambda kv: (str(kv[1].get("source_path")), str(kv[1].get("tool_name")))):
        by_source.setdefault(str(t.get("source_path", UNKNOWN)), []).append(pid)
    names: dict[str, int] = {}
    for t in tools.values():
        names[str(t.get("tool_name"))] = names.get(str(t.get("tool_name")), 0) + 1

    node_ids = {pid: f"n{i}" for i, pid in enumerate(sorted(tools))}
    for ci, (source, pids) in enumerate(by_source.items()):
        client = tools[pids[0]].get("client", UNKNOWN)
        out.append(f"  subgraph cluster_{ci} {{")
        out.append(f'    label="{_lbl(f"{client} · {source}")}"; labeljust=l; style="rounded,filled"; '
                   f'color="{c["hair"]}"; fillcolor="#fbfaf7"; fontcolor="{c["muted"]}"; fontsize=11; penwidth=1;')
        for pid in pids:
            t, nid = tools[pid], node_ids[pid]
            dup = names[str(t.get("tool_name"))] > 1
            access = t.get("access_mode", UNKNOWN)
            tool_label = _lbl(t.get("tool_name"), f"{t.get('command', UNKNOWN)}  ·  access {access}",
                              "same name, different provenance" if dup else "")
            edge = f', color="#ea580c", fontcolor="{c["orange"]}"' if dup else ""
            out.append(f'    {nid}_tool [label="{tool_label}", fillcolor="white"{edge}];')
        out.append("  }")

    for pid in sorted(tools):
        t, nid = tools[pid], node_ids[pid]
        if t["_minted"]:
            try:
                intact = derive_passport_id(t["source_path"], t["tool_name"], t["command"]) == pid
            except (KeyError, TypeError):
                intact = False
            verdict = "integrity PASS" if intact else "integrity FAIL"
            color = c["accent"] if intact else c["red"]
            out.append(f'  {nid} [label="{_lbl("Passport " + pid[:12], verdict)}", '
                       f'fillcolor="white", fontcolor="{c["ink"] if intact else c["red"]}", color="{color}", penwidth=2];')
            out.append(f'  {nid}_tool -> {nid} [color="{c["accent"]}", penwidth=1.4];')
        else:
            out.append(f'  {nid} [label="{_lbl("candidate " + pid[:12] + "…", "not minted yet")}", '
                       f'style="rounded,dashed", fillcolor="white", fontcolor="{c["muted"]}"];')
            out.append(f'  {nid}_tool -> {nid} [style=dashed];')

        # Drift
        drift = [x for x in s["changes"] if x.get("passport_id") == pid]
        if drift:
            out.append(f'  {nid}_chg [label="{_lbl(f"config drift × {len(drift)}", "pending review")}", '
                       f'fillcolor="white", color="#ea580c", fontcolor="{c["orange"]}"];')
            out.append(f'  {nid} -> {nid}_chg [color="#ea580c"];')

        # Policy findings summary
        found = [f for f in s["findings"] if f.get("passport_id") == pid]
        if found:
            rules = sorted({f"{f['rule_id']} {f['severity'].upper()}" for f in found},
                           key=lambda r: (_SEV_ORDER.index(r.split()[-1].lower()) if r.split()[-1].lower()
                                          in _SEV_ORDER else 9, r))
            sev_red = any(f.get("severity") in ("critical", "high") for f in found)
            line, fg = ("#be123c", c["red"]) if sev_red else ("#d97706", c["amber"])
            out.append(f'  {nid}_pol [label="{_lbl("policy", *rules)}", fillcolor="white", color="{line}", '
                       f'fontcolor="{fg}"];')
            out.append(f'  {nid} -> {nid}_pol [color="{line}"];')

        # Events and incidents
        links = [x for x in s["links"] if x.get("passport_id") == pid]
        if collapse and links:
            miss = sum(1 for x in links if x.get("approval_status") == MISSING_APPROVAL)
            out.append(f'  {nid}_ev [label="{_lbl(f"{len(links)} linked event(s)", f"{miss} MISSING_APPROVAL")}", '
                       f'fillcolor="{c["amber_bg"] if miss else c["green_bg"]}"];')
            out.append(f'  {nid} -> {nid}_ev [color="{c["line"]}"];')
            continue
        for li, link in enumerate(links):
            eid = f"{nid}_e{li}"
            missing = link.get("approval_status") == MISSING_APPROVAL
            label = _lbl(link.get("event_id", UNKNOWN), link.get("tool_call", UNKNOWN), link.get("timestamp", UNKNOWN),
                         MISSING_APPROVAL if missing else "APPROVED")
            fill, fg, line = ((c["red_bg"], c["red"], "#be123c") if missing
                              else (c["green_bg"], c["green"], "#2f7a4f"))
            out.append(f'  {eid} [label="{label}", fillcolor="{fill}", fontcolor="{fg}", color="{line}"];')
            out.append(f'  {nid} -> {eid} [color="{line}"];')
            inc = next((i for i in s["incidents"] if i.get("incident_id") == link.get("incident_id")), None)
            if inc:
                out.append(f'  {eid}_inc [label="{_lbl("Incident", _INCIDENT_TYPES.get(str(inc.get("type")), inc.get("type", UNKNOWN)), inc.get("incident_id"))}", '
                           f'shape=box, fillcolor="white", fontcolor="{c["red"]}", color="#be123c", penwidth=2.2];')
                out.append(f'  {eid} -> {eid}_inc [color="{c["red"]}", penwidth=2];')
    out.append("}")
    return "\n".join(out)
