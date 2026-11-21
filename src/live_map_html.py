"""Live provenance map as a plain flow-chart table (HTML), for the Streamlit UI.

Reads the same local JSON store as ``provenance_view.live_map_dot`` and lays it
out like an office diagram: fixed columns, one row per runtime event, straight
left-to-right arrows. Read-only: nothing here writes to the store. Every value
from the store is HTML-escaped before it is rendered.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .engine import MISSING_APPROVAL, UNKNOWN, derive_passport_id
from .provenance_view import load_store

__all__ = ["lifecycle_html", "live_map_html"]

_SEV = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
_SEV_ORDER = ("critical", "high", "medium", "low")
_INCIDENT_TYPES = {"OUT_OF_EXPECTED_BEHAVIOR": "Out of expected behavior"}

_CSS = """
<style>
.lm-wrap{overflow-x:auto;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#1C1917}
.lm{border-collapse:separate;border-spacing:0 6px;min-width:1180px;width:100%;table-layout:fixed}
.lm th{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#57534E;
  text-align:left;padding:0 0 4px 2px;border-bottom:1.5px solid #1C1917}
.lm th.lm-a{border-bottom:1.5px solid #1C1917}
.lm td{vertical-align:middle;padding:0;position:relative}
.lm col.c-box{width:auto}.lm col.c-arr{width:34px}.lm col.c-gap{width:22px}
.lm .box{border:1px solid #A8A29E;background:#fff;padding:8px 10px;font-size:13px;line-height:1.4;
  height:100%;box-sizing:border-box}
.lm td.span{height:1px}.lm td.span .box{display:flex;flex-direction:column;justify-content:center}
.lm .t{font-weight:600}
.lm .m{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;color:#44403C;word-break:break-all}
.lm .s{font-size:12px;color:#57534E}
.lm .ok{color:#1F6B3A}.lm .bad{color:#9F1239}.lm .warn{color:#9A4527}
.lm .box.pp{border:1.5px solid #1C1917}
.lm .box.cand{border:1.5px dashed #A8A29E;color:#57534E}
.lm .box.red{border-color:#9F1239;background:#FDF3F3}
.lm .box.green{border-color:#1F6B3A;background:#F2F8F3}
.lm .box.none{border:1px dashed #D6D3CE;background:transparent;color:#6B6560;font-size:12px;text-align:center}
.lm .box.chk{background:#FAF9F6}
.lm .chk div+div{margin-top:3px}
.lm .arr{position:absolute;inset:0}
.lm .arr i{position:absolute;left:3px;right:7px;top:50%;height:0;border-top:1.5px solid #57534E}
.lm .arr b{position:absolute;right:2px;top:50%;margin-top:-4.5px;width:0;height:0;
  border-top:5px solid transparent;border-bottom:5px solid transparent;border-left:7px solid #57534E}
.lm tr.grp td{padding-top:10px}
.lc{display:grid;grid-template-columns:1fr 34px 1fr 34px 1fr 34px 1fr 34px 1fr;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#1C1917}
.lc .box{border:1px solid #A8A29E;background:#fff;padding:9px 11px;font-size:13px;line-height:1.4}
.lc .n{font-size:11px;color:#6B6560;font-variant-numeric:tabular-nums}
.lc .t{font-weight:600}.lc .s{font-size:12px;color:#57534E}
.lc .box.pp{border:1.5px solid #C25E3A}.lc .box.pp .s{color:#9A4527}
.lc .ar{position:relative}
.lc .ar i{position:absolute;left:3px;right:7px;top:50%;border-top:1.5px solid #57534E}
.lc .ar b{position:absolute;right:2px;top:50%;margin-top:-4.5px;border-top:5px solid transparent;
  border-bottom:5px solid transparent;border-left:7px solid #57534E}
.lc .stem{grid-column:9;height:22px;position:relative}
.lc .stem i{position:absolute;left:50%;top:0;bottom:0;border-left:1.5px solid #57534E}
.lc .grp{grid-column:1 / -1;border:1px solid #D6D3CE;background:#FAF9F6;padding:10px 12px 12px}
.lc .grp-h{font-size:12px;color:#57534E;margin-bottom:8px}
.lc .recs{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
.lc .recs .m{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;font-weight:600}
.lc .red{color:#9F1239}.lc .warn{color:#9A4527}
</style>
"""


def _e(v: Any) -> str:
    return escape(str(v if v not in (None, "") else UNKNOWN))


def _arrow() -> str:
    return '<td><div class="arr"><i></i><b></b></div></td>'


def _ts(v: Any) -> str:
    s = str(v or UNKNOWN)
    return s.replace("T", " ").replace("Z", " UTC")


def live_map_html(
    store_dir: str | Path,
    candidates: list[dict[str, Any]] | None = None,
    focus: str | None = None,
    max_tools: int = 25,
    found_on: dict[str, str] | None = None,
) -> str | None:
    """Return the map as an HTML string, or None when there is nothing to show.

    ``found_on`` maps passport_id to a display label for the machine it was found
    on (context only; it is never part of the passport).
    """
    s = load_store(store_dir)
    tools: dict[str, dict[str, Any]] = {pid: {**p, "_minted": True} for pid, p in s["passports"].items()}
    for cand in candidates or []:
        tools.setdefault(cand["passport_id"], {**cand, "_minted": False})
    if focus:
        tools = {k: v for k, v in tools.items() if k == focus}
    if not tools:
        return None
    collapse = len(tools) > max_tools

    names: dict[str, int] = {}
    for t in tools.values():
        names[str(t.get("tool_name"))] = names.get(str(t.get("tool_name")), 0) + 1

    by_source: dict[str, list[str]] = {}
    for pid, t in sorted(tools.items(), key=lambda kv: (str(kv[1].get("source_path")), str(kv[1].get("tool_name")))):
        by_source.setdefault(str(t.get("source_path", UNKNOWN)), []).append(pid)

    def rows_for(pid: str) -> list[dict[str, Any] | None]:
        links = [x for x in s["links"] if x.get("passport_id") == pid]
        if collapse and links:
            return [{"_summary": links}]
        return links or [None]

    body: list[str] = []
    for source, pids in by_source.items():
        tool_rows = {pid: rows_for(pid) for pid in pids}
        total = sum(len(r) for r in tool_rows.values())
        client = tools[pids[0]].get("client", UNKNOWN)
        first_in_file = True
        for pid in pids:
            t = tools[pid]
            events = tool_rows[pid]
            n = len(events)
            for ri, link in enumerate(events):
                cells: list[str] = []
                if first_in_file:
                    cells.append(f'<td class="span" rowspan="{total}"><div class="box">'
                                 f'<div class="t">{_e(client)}</div><div class="m">{_e(source)}</div>'
                                 f'<div class="s">{len(pids)} tool(s) in this file</div></div></td>')
                if ri == 0:
                    cells.append(f'<td rowspan="{n}">{_arrow_div()}</td>')
                    cells.append(f'<td class="span" rowspan="{n}">{_tool_box(t, names, (found_on or {}).get(pid))}</td>')
                    cells.append(f'<td rowspan="{n}">{_arrow_div()}</td>')
                    cells.append(f'<td class="span" rowspan="{n}">{_passport_box(pid, t)}</td>')
                cells.append(_arrow())
                cells.extend(_event_cells(link, s["incidents"]))
                if ri == 0:
                    cells.append('<td></td>')
                    cells.append(f'<td class="span" rowspan="{n}">{_checks_box(pid, s)}</td>')
                cls = ' class="grp"' if first_in_file and body else ""
                body.append(f"<tr{cls}>{''.join(cells)}</tr>")
                first_in_file = False

    cols = ('<colgroup><col class="c-box" style="width:15%"><col class="c-arr"><col class="c-box" style="width:15%">'
            '<col class="c-arr"><col class="c-box" style="width:13%"><col class="c-arr">'
            '<col class="c-box" style="width:16%"><col class="c-arr"><col class="c-box" style="width:14%">'
            '<col class="c-gap"><col class="c-box" style="width:19%"></colgroup>')
    head = ('<tr><th>Config file</th><th class="lm-a"></th><th>MCP tool</th><th class="lm-a"></th><th>Passport</th>'
            '<th class="lm-a"></th><th>Runtime event</th><th class="lm-a"></th><th>Incident</th><th></th>'
            '<th>Open checks</th></tr>')
    return f'{_CSS}<div class="lm-wrap"><table class="lm">{cols}<thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'


def _arrow_div() -> str:
    return '<div class="arr"><i></i><b></b></div>'


def _tool_box(t: dict[str, Any], names: dict[str, int], found: str | None = None) -> str:
    dup = names[str(t.get("tool_name"))] > 1
    note = ('<div class="s warn">Another tool has the same name. Kept as a separate passport.</div>' if dup else "")
    return (f'<div class="box"><div class="t">{_e(t.get("tool_name"))}</div>'
            f'<div class="s">Command: <span class="m">{_e(t.get("command"))}</span></div>'
            f'<div class="s">Access: {_e(t.get("access_mode"))}</div>'
            + (f'<div class="s">Found on: {_e(found)}</div>' if found else "") + f'{note}</div>')


def _passport_box(pid: str, t: dict[str, Any]) -> str:
    if not t["_minted"]:
        return (f'<div class="box cand"><div class="t">Not issued yet</div>'
                f'<div class="m">{_e(pid[:12])}</div><div class="s">Issue it in 02 Identity &amp; Review</div></div>')
    try:
        intact = derive_passport_id(t["source_path"], t["tool_name"], t["command"]) == pid
    except (KeyError, TypeError):
        intact = False
    check = ('<div class="s ok">Integrity check passed</div>' if intact
             else '<div class="s bad">Integrity check failed</div>')
    return f'<div class="box pp"><div class="t">Passport</div><div class="m">{_e(pid[:12])}</div>{check}</div>'


def _event_cells(link: dict[str, Any] | None, incidents: list[dict[str, Any]]) -> list[str]:
    if link is None:
        return ['<td><div class="box none">No events yet</div></td>', "<td></td>", "<td></td>"]
    if "_summary" in link:
        links = link["_summary"]
        miss = sum(1 for x in links if x.get("approval_status") == MISSING_APPROVAL)
        cls = "red" if miss else "green"
        return [f'<td><div class="box {cls}"><div class="t">{len(links)} linked events</div>'
                f'<div class="s">{miss} without approval</div></div></td>', "<td></td>", "<td></td>"]
    missing = link.get("approval_status") == MISSING_APPROVAL
    status = ('<div class="s bad">No approval on file</div>' if missing else '<div class="s ok">Approved</div>')
    ev = (f'<td><div class="box {"red" if missing else "green"}"><div class="t">{_e(link.get("event_id"))}</div>'
          f'<div class="m">{_e(link.get("tool_call"))}</div><div class="s">{_e(_ts(link.get("timestamp")))}</div>'
          f'{status}</div></td>')
    inc = next((i for i in incidents if i.get("incident_id") == link.get("incident_id")), None)
    if not inc:
        return [ev, "<td></td>", "<td></td>"]
    kind = _INCIDENT_TYPES.get(str(inc.get("type")), str(inc.get("type") or UNKNOWN))
    return [ev, _arrow(), f'<td><div class="box red"><div class="t bad">Incident</div><div class="m">'
            f'{_e(inc.get("incident_id"))}</div><div class="s">{escape(kind)}</div></div></td>']


def _checks_box(pid: str, s: dict[str, Any]) -> str:
    lines: list[str] = []
    drift = [x for x in s["changes"] if x.get("passport_id") == pid]
    if drift:
        lines.append(f'<div class="warn">Config changed since issued ({len(drift)}). Needs review.</div>')
    found = sorted((f for f in s["findings"] if f.get("passport_id") == pid),
                   key=lambda f: (_SEV_ORDER.index(f.get("severity")) if f.get("severity") in _SEV_ORDER else 9,
                                  str(f.get("rule_id"))))
    seen: set[str] = set()
    for f in found:
        key = f"{f.get('rule_id')}|{f.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        sev = str(f.get("severity", "")).lower()
        cls = "bad" if sev in ("critical", "high") else ("warn" if sev == "medium" else "s")
        lines.append(f'<div class="{cls}"><b>{_e(f.get("rule_id"))}</b> {_SEV.get(sev, _e(sev))} · {_e(f.get("title"))}</div>')
    if not lines:
        return '<div class="box none">Nothing open</div>'
    return f'<div class="box chk">{"".join(lines)}</div>'


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def lifecycle_html(store_dir: str | Path) -> str:
    """How a passport is made, with live counts. Always complete, also for an empty store."""
    s = load_store(store_dir)
    missing = sum(1 for x in s["links"] if x.get("approval_status") == MISSING_APPROVAL)
    steps = [
        ("1", "Client config", "Claude Desktop, Cursor, VS Code", ""),
        ("2", "Secrets removed", "Only metadata is kept", ""),
        ("3", "Provenance hash", "File path + tool name + command", ""),
        ("4", "Forkit Core", "compute_id()", ""),
        ("", "Passport ID", f"{_n(len(s['passports']), 'issued', 'issued')}. Never rewritten.", " pp"),
    ]
    cells: list[str] = []
    for i, (num, title, sub, cls) in enumerate(steps):
        if i:
            cells.append('<div class="ar"><i></i><b></b></div>')
        num_html = f'<div class="n">Step {num}</div>' if num else '<div class="n">Result</div>'
        cells.append(f'<div class="box{cls}">{num_html}<div class="t">{escape(title)}</div>'
                     f'<div class="s">{escape(sub)}</div></div>')
    link_line = _n(len(s["links"]), "event linked", "events linked")
    if missing:
        link_line += f", {missing} without approval"
    records = [
        ("approvals.json", "Approvals, kept outside the app", ""),
        ("metadata_changes.json", _n(len(s["changes"]), "config change", "config changes"), "warn" if s["changes"] else ""),
        ("linked_events.json", link_line, "red" if missing else ""),
        ("incidents.json", _n(len(s["incidents"]), "incident", "incidents"), "red" if s["incidents"] else ""),
        ("policy_findings.json", _n(len(s["findings"]), "finding", "findings"), "warn" if s["findings"] else ""),
    ]
    recs = "".join(f'<div class="box"><div class="m">{escape(f)}</div><div class="s {c}">{escape(d)}</div></div>'
                   for f, d, c in records)
    return (f'{_CSS}<div class="lc">{"".join(cells)}<div class="stem"><i></i></div>'
            f'<div class="grp"><div class="grp-h">Records stored separately and joined by passport_id. '
            f'They never change the passport.</div><div class="recs">{recs}</div></div></div>')
