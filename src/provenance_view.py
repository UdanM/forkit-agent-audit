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

# Projector-friendly palette (matches the app's badge colours).
_C = {
    "ink": "#1f2937", "muted": "#6b7280", "line": "#9ca3af",
    "blue_bg": "#e8eefc", "blue": "#1d3f8f",
    "green_bg": "#e3f6e8", "green": "#14632f",
    "amber_bg": "#fff4d6", "amber": "#7a5200",
    "orange_bg": "#ffe9d6", "orange": "#8a3b00",
    "red_bg": "#fde6e6", "red": "#a1161b",
    "grey_bg": "#f3f4f6",
}
_SEV_ORDER = ("critical", "high", "medium", "low")


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

def lifecycle_dot(store_dir: str | Path) -> str:
    """The identity lifecycle, annotated with live counts from ``store_dir``.

    Always a complete diagram, also for an empty store (counts are 0).
    """
    s = load_store(store_dir)
    n_missing = sum(1 for x in s["links"] if x.get("approval_status") == MISSING_APPROVAL)
    c = _C
    # Compact 3-column grid so the diagram stays large on a projector:
    #   row 1: config → redact → provenance hash
    #   row 2: compute_id → passport
    #   rows 3-4: sidecar records joined by passport_id
    out = _header("lifecycle", rankdir="TB", nodesep=0.25, ranksep=0.4)
    out += [
        f'  cfg [label="① Client config\\nClaude · Cursor · VS Code", fillcolor="{c["grey_bg"]}"];',
        f'  red [label="② Redact secrets\\nmetadata-only", fillcolor="{c["blue_bg"]}", fontcolor="{c["blue"]}"];',
        f'  prov [label="③ Provenance hash\\nsource_path + tool + command", fillcolor="{c["blue_bg"]}", '
        f'fontcolor="{c["blue"]}"];',
        f'  cid [label="④ Forkit Core\\ncompute_id()", fillcolor="{c["blue_bg"]}", fontcolor="{c["blue"]}"];',
        f'  pp [label="🔒 Stable passport_id\\n{len(s["passports"])} confirmed · never rewritten", '
        f'fillcolor="{c["green_bg"]}", fontcolor="{c["green"]}", penwidth=3, color="{c["green"]}"];',
        '  { rank=same; cfg; red; prov; }',
        '  { rank=same; cid; pp; }',
        '  cfg -> red -> prov [penwidth=2];',
        '  prov -> cid [penwidth=2];',
        '  cid -> pp [penwidth=2];',
        '  subgraph cluster_side {',
        f'    label="Sidecar records · joined by passport_id · never mutate the passport"; '
        f'style="rounded,dashed"; color="{c["line"]}"; fontcolor="{c["muted"]}"; fontsize=10;',
        f'    s_appr [label="approvals.json\\nexternal", fillcolor="{c["grey_bg"]}"];',
        f'    s_chg [label="metadata_changes.json\\n{len(s["changes"])} drift record(s)", '
        f'fillcolor="{c["orange_bg"]}", fontcolor="{c["orange"]}"];',
        f'    s_lnk [label="linked_events.json\\n{len(s["links"])} consented · {n_missing} MISSING_APPROVAL", '
        f'fillcolor="{c["amber_bg"]}", fontcolor="{c["amber"]}"];',
        f'    s_inc [label="incidents.json\\n{len(s["incidents"])} out-of-expected-behavior", '
        f'fillcolor="{c["red_bg"]}", fontcolor="{c["red"]}"];',
        f'    s_pol [label="policy_findings.json\\n{len(s["findings"])} finding(s)", '
        f'fillcolor="{c["red_bg"]}", fontcolor="{c["red"]}"];',
        '    { rank=same; s_appr; s_chg; s_lnk; }',
        '    { rank=same; s_inc; s_pol; }',
        '    s_chg -> s_inc [style=invis];',
        '    s_lnk -> s_pol [style=invis];',
        '  }',
    ]
    for side in ("s_appr", "s_chg", "s_lnk"):
        out.append(f'  pp -> {side} [style=dashed, arrowhead=none, color="{c["green"]}"];')
    for side in ("s_inc", "s_pol"):
        out.append(f'  pp -> {side} [style=dashed, arrowhead=none, color="{c["green"]}", constraint=false];')
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
    out = _header("provenance_map")
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
        out.append(f'    label="{_lbl(f"{client} · {source}")}"; style="rounded"; color="{c["line"]}"; '
                   f'fontcolor="{c["ink"]}"; fontsize=11; penwidth=1.5;')
        for pid in pids:
            t, nid = tools[pid], node_ids[pid]
            dup = names[str(t.get("tool_name"))] > 1
            access = t.get("access_mode", UNKNOWN)
            tool_label = _lbl(t.get("tool_name"), f"cmd: {t.get('command', UNKNOWN)} · access: {access}",
                              "⚠ same name, different provenance" if dup else "")
            fill = c["orange_bg"] if dup else "white"
            out.append(f'    {nid}_tool [label="{tool_label}", fillcolor="{fill}"];')
        out.append("  }")

    for pid in sorted(tools):
        t, nid = tools[pid], node_ids[pid]
        if t["_minted"]:
            try:
                intact = derive_passport_id(t["source_path"], t["tool_name"], t["command"]) == pid
            except (KeyError, TypeError):
                intact = False
            verdict = "integrity PASS" if intact else "integrity FAIL"
            color = (c["green_bg"], c["green"]) if intact else (c["red_bg"], c["red"])
            out.append(f'  {nid} [label="{_lbl("🔒 passport " + pid[:12] + "…", verdict)}", '
                       f'fillcolor="{color[0]}", fontcolor="{color[1]}", color="{color[1]}", penwidth=2];')
            out.append(f'  {nid}_tool -> {nid} [label="compute_id", penwidth=1.6];')
        else:
            out.append(f'  {nid} [label="{_lbl("candidate " + pid[:12] + "…", "not minted yet")}", '
                       f'style="rounded,dashed", fillcolor="white", fontcolor="{c["muted"]}"];')
            out.append(f'  {nid}_tool -> {nid} [style=dashed, label="compute_id"];')

        # Drift
        drift = [x for x in s["changes"] if x.get("passport_id") == pid]
        if drift:
            out.append(f'  {nid}_chg [label="{_lbl(f"config drift × {len(drift)}", "PENDING_REVIEW")}", '
                       f'fillcolor="{c["orange_bg"]}", fontcolor="{c["orange"]}", shape=note];')
            out.append(f'  {nid} -> {nid}_chg [style=dashed, label="passport_id"];')

        # Policy findings summary
        found = [f for f in s["findings"] if f.get("passport_id") == pid]
        if found:
            rules = sorted({f"{f['rule_id']} {f['severity'].upper()}" for f in found},
                           key=lambda r: (_SEV_ORDER.index(r.split()[-1].lower()) if r.split()[-1].lower()
                                          in _SEV_ORDER else 9, r))
            sev_red = any(f.get("severity") in ("critical", "high") for f in found)
            fill, fg = (c["red_bg"], c["red"]) if sev_red else (c["amber_bg"], c["amber"])
            out.append(f'  {nid}_pol [label="{_lbl("policy", *rules)}", fillcolor="{fill}", fontcolor="{fg}", '
                       f'shape=note];')
            out.append(f'  {nid} -> {nid}_pol [style=dashed, label="passport_id"];')

        # Events and incidents
        links = [x for x in s["links"] if x.get("passport_id") == pid]
        if collapse and links:
            miss = sum(1 for x in links if x.get("approval_status") == MISSING_APPROVAL)
            out.append(f'  {nid}_ev [label="{_lbl(f"{len(links)} linked event(s)", f"{miss} MISSING_APPROVAL")}", '
                       f'fillcolor="{c["amber_bg"] if miss else c["green_bg"]}"];')
            out.append(f'  {nid} -> {nid}_ev [style=dashed, label="consented"];')
            continue
        for li, link in enumerate(links):
            eid = f"{nid}_e{li}"
            missing = link.get("approval_status") == MISSING_APPROVAL
            label = _lbl(link.get("event_id", UNKNOWN), link.get("tool_call", UNKNOWN), link.get("timestamp", UNKNOWN),
                         MISSING_APPROVAL if missing else "APPROVED")
            fill, fg = (c["red_bg"], c["red"]) if missing else (c["green_bg"], c["green"])
            out.append(f'  {eid} [label="{label}", fillcolor="{fill}", fontcolor="{fg}"];')
            out.append(f'  {nid} -> {eid} [style=dashed, label="consented link"];')
            inc = next((i for i in s["incidents"] if i.get("incident_id") == link.get("incident_id")), None)
            if inc:
                out.append(f'  {eid}_inc [label="{_lbl("🚨 INCIDENT", inc.get("type", UNKNOWN), inc.get("incident_id"))}", '
                           f'shape=box, peripheries=2, fillcolor="{c["red_bg"]}", fontcolor="{c["red"]}", '
                           f'color="{c["red"]}", penwidth=2];')
                out.append(f'  {eid} -> {eid}_inc [color="{c["red"]}", penwidth=2];')
    out.append("}")
    return "\n".join(out)
