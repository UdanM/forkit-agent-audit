"""Activity feed and per-actor chart for tab 06 (read-only HTML).

Layout idea (activity lines with a coloured edge, bar chart of changes per
actor) from UdanM/forkit-agent-audit @ 3f5b2aa (index.html), restyled in
AutoDiscover's palette. Every value is HTML-escaped.
"""
from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

UNKNOWN = "UNKNOWN"

_CSS = """
<style>
.af{font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#1C1917;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:18px}
@media (max-width:1000px){.af{grid-template-columns:1fr}}
.af ol{list-style:none;margin:0;padding:0}
.af li{display:grid;grid-template-columns:92px minmax(0,1fr);gap:14px;padding:11px 14px;margin:0 0 8px;background:#fff;
  border:1px solid #E6E1D8;border-left:3px solid #2F7A4F;border-radius:10px}
.af li.bad{border-left-color:#BE123C}.af li.warn{border-left-color:#D97706}
.af time{font-variant-numeric:tabular-nums;font-size:12.5px;color:#57534E;padding-top:2px}
.af .who{font-weight:650;font-size:14px}.af .who.unk{color:#B45309}
.af .what{font-size:14px;margin-top:2px}.af .what code{font-family:"JetBrains Mono",Consolas,monospace;font-size:12.5px;
  background:#F2EFE9;padding:1px 5px;border-radius:4px;word-break:break-all}
.af .why{font-size:12.5px;color:#57534E;margin-top:4px}
.af .flags{margin-top:6px;display:flex;flex-wrap:wrap;gap:5px}
.af .flags span{font-size:11.5px;font-weight:600;border:1px solid #E6E1D8;border-radius:4px;padding:0 6px;background:#fff}
.af .flags span::before{content:"";display:inline-block;width:6px;height:6px;margin:0 5px 1px 0;background:#BE123C}
.af .flags span.w::before{background:#D97706}
.af aside{background:#fff;border:1px solid #E6E1D8;border-radius:10px;padding:14px 16px;align-self:start}
.af aside h5{margin:0 0 2px;font-size:14px}.af aside p{margin:0 0 10px;font-size:12.5px;color:#57534E}
.af .bar{display:grid;grid-template-columns:minmax(0,1fr) 26px;gap:2px 8px;margin:0 0 9px;font-size:13px}
.af .bar .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.af .bar .lbl.unk{color:#B45309;font-weight:600}
.af .bar .n{text-align:right;font-variant-numeric:tabular-nums;color:#57534E}
.af .track{grid-column:1/3;height:8px;border-radius:99px;background:#F2EFE9;overflow:hidden}
.af .fill{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#C25E3A,#E3A07F)}
.af .fill.unk{background:#E8B04B}
</style>
"""

_FLAGS = {
    "read_only_write": ("read-only run wrote", "b"), "protected_write": ("protected file", "b"),
    "approval_missing": ("no approval", "b"), "unknown_actor": ("actor unknown", "w"),
    "config_file_write": ("tool config changed", "w"), "clock_drift": ("clock drift", "w"),
    "sequence_gap": ("log gap before this", "w"),
}


def activity_feed_html(timeline: dict[str, Any]) -> str:
    flags_by_record: dict[str, list[tuple[str, str]]] = {}
    for check, ids in timeline.get("signals", {}).items():
        if check in _FLAGS:
            for rid in ids:
                flags_by_record.setdefault(rid, []).append(_FLAGS[check])
    runs = {r["run_id"]: r for r in timeline.get("runs", [])}
    items = []
    for a in timeline.get("actions", []):
        flags = flags_by_record.get(a["record_id"], [])
        tone = "bad" if any(k == "b" for _, k in flags) else "warn" if flags else ""
        who = a["actor"]
        what = a.get("operation") or a.get("tool_call") or a["kind"]
        target = a.get("path") or a.get("target") or ""
        why = "No observed run, so the actor stays UNKNOWN" if who == UNKNOWN else f"Observed run {a['run_id']}"
        if a.get("initiated_by"):
            parent = runs.get(a["initiated_by"], {}).get("label", a["initiated_by"])
            why += f", delegated by {parent}"
        items.append(
            f'<li class="{tone}"><time>{escape(a["time"][11:19])}</time><div>'
            f'<div class="who{" unk" if who == UNKNOWN else ""}">{escape(who)}</div>'
            f'<div class="what">{escape(what)} <code>{escape(target)}</code></div>'
            f'<div class="why">{escape(why)}. Record {escape(a["record_id"])}.</div>'
            + ('<div class="flags">' + "".join(f'<span class="{k if k == "w" else ""}">{escape(t)}</span>'
                                              for t, k in flags) + "</div>" if flags else "")
            + "</div></li>")
    counts = Counter(a["actor"] for a in timeline.get("actions", []) if a.get("path"))
    top = max(counts.values(), default=1)
    bars = "".join(
        f'<div class="bar"><span class="lbl{" unk" if who == UNKNOWN else ""}">{escape(who)}</span>'
        f'<span class="n">{n}</span><span class="track"><span class="fill{" unk" if who == UNKNOWN else ""}" '
        f'style="width:{max(8, round(100 * n / top))}%"></span></span></div>'
        for who, n in counts.most_common())
    aside = ('<aside><h5>File changes per actor</h5><p>From the observer only. UNKNOWN means nobody can be tied '
             'to the change yet.</p>' + (bars or '<p>No file changes.</p>') + "</aside>")
    return _CSS + '<div class="af"><ol>' + ("".join(items) or "<li>No actions recorded.</li>") + "</ol>" + aside + "</div>"


_FCSS = """
<style>
.fl{font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#1C1917;background:#fff;border:1px solid #E6E1D8;
  border-radius:12px;overflow:hidden}
.fl div.r{display:grid;grid-template-columns:96px 74px minmax(0,1fr) auto;gap:12px;align-items:baseline;
  padding:9px 14px;border-top:1px solid #F0ECE5;font-size:14px}
.fl div.r:first-child{border-top:0}
.fl .sev{font-size:11.5px;font-weight:700;letter-spacing:.04em}
.fl .sev::before{content:"";display:inline-block;width:7px;height:7px;margin:0 6px 1px 0;background:var(--d)}
.fl .id{font-family:"JetBrains Mono",Consolas,monospace;font-size:12.5px;color:#57534E}
.fl .t b{font-weight:600}.fl .t span{display:block;font-size:12.5px;color:#57534E;margin-top:2px}
.fl .rec{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;color:#57534E;text-align:right}
</style>
"""
_DOT = {"critical": "#BE123C", "high": "#BE123C", "medium": "#EA580C", "low": "#A8A29E"}


def findings_html(findings: list[dict[str, Any]]) -> str:
    """One compact list: severity, rule, title, why, records."""
    rows = "".join(
        f'<div class="r"><span class="sev" style="--d:{_DOT.get(f["severity"], "#A8A29E")}">'
        f'{escape(f["severity"].upper())}</span><span class="id">{escape(f["rule_id"])}</span>'
        f'<span class="t"><b>{escape(f["title"])}</b><span>{escape(f["explanation"])}</span></span>'
        f'<span class="rec">{escape(", ".join(f["record_ids"]))}</span></div>'
        for f in findings)
    return _FCSS + f'<div class="fl">{rows}</div>'
