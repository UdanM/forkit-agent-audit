"""Activity feed and per-actor chart for tab 06 (read-only HTML).

Layout idea (activity lines with a coloured edge, bar chart of changes per
actor) from UdanM/forkit-agent-audit @ 3f5b2aa (index.html), in the same
dark palette. Every value is HTML-escaped.
"""
from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

UNKNOWN = "UNKNOWN"

_CSS = """
<style>
.af{font-family:"Segoe UI",system-ui,sans-serif;color:#EEF1FF;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:18px}
@media (max-width:1000px){.af{grid-template-columns:1fr}}
.af ol{list-style:none;margin:0;padding:0}
.af li{display:grid;grid-template-columns:92px minmax(0,1fr);gap:16px;padding:14px 16px;margin:0 0 8px;background:#131A30;
  border:1px solid #2D3654;border-left:3px solid #8D78EF;border-radius:12px}
.af li.bad{border-left-color:#FF8FA3}.af li.warn{border-left-color:#FFC988}
.af time{font-variant-numeric:tabular-nums;font-size:13px;color:#B1BBDA;padding-top:2px}
.af .who{font-weight:650;font-size:14.5px}.af .who.unk{color:#FFC988}
.af .what{font-size:14px;margin-top:2px}.af .what code{font-family:"JetBrains Mono",Consolas,monospace;font-size:12.5px;
  color:#B1BBDA;background:transparent;padding:0;word-break:break-all}
.af .why{font-size:12.5px;color:#B1BBDA;margin-top:4px}
.af .flags{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}
.af .flags span{font-size:11.5px;font-weight:600;border-radius:999px;padding:2px 9px;color:#FFC2CD;background:#3A1A26}
.af .flags span.w{color:#FFDB9D;background:#392C25}
.af aside{background:linear-gradient(145deg,#161D34,#111629);border:1px solid #2F395A;border-radius:16px;padding:16px 18px;align-self:start}
.af aside h5{margin:0 0 2px;font-size:15px;color:#EEF1FF}.af aside p{margin:0 0 12px;font-size:12.5px;color:#B1BBDA}
.af .bar{display:grid;grid-template-columns:minmax(0,1fr) 26px;gap:3px 8px;margin:0 0 10px;font-size:13px}
.af .bar .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.af .bar .lbl.unk{color:#FFC988;font-weight:600}
.af .bar .n{text-align:right;font-variant-numeric:tabular-nums;color:#B1BBDA}
.af .track{grid-column:1/3;height:12px;border-radius:99px;background:#2A3250;overflow:hidden}
.af .fill{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#A78CFF,#8DE8EE)}
.af .fill.unk{background:#FFC988}
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
.fl{font-family:"Segoe UI",system-ui,sans-serif;color:#EEF1FF;background:#131A30;border:1px solid #2D3654;
  border-radius:12px;overflow:hidden}
.fl div.r{display:grid;grid-template-columns:96px 74px minmax(0,1fr) auto;gap:12px;align-items:baseline;
  padding:10px 16px;border-top:1px solid #232B47;font-size:14px}
.fl div.r:first-child{border-top:0}
.fl .sev{font-size:11px;font-weight:700;letter-spacing:.04em;border-radius:999px;padding:2px 9px;color:var(--d);background:var(--g)}
.fl .id{font-family:"JetBrains Mono",Consolas,monospace;font-size:12.5px;color:#B1BBDA}
.fl .t b{font-weight:600}.fl .t span{display:block;font-size:12.5px;color:#B1BBDA;margin-top:2px}
.fl .rec{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;color:#B1BBDA;text-align:right}
</style>
"""
_DOT = {"critical": ("#FFC2CD", "#3A1A26"), "high": ("#FFC2CD", "#3A1A26"), "medium": ("#FFDB9D", "#392C25"),
        "low": ("#C5CBED", "#1C2340")}


def findings_html(findings: list[dict[str, Any]]) -> str:
    """One compact list: severity, rule, title, why, records."""
    rows = "".join(
        f'<div class="r"><span class="sev" style="--d:{_DOT.get(f["severity"], _DOT["low"])[0]};'
        f'--g:{_DOT.get(f["severity"], _DOT["low"])[1]}">'
        f'{escape(f["severity"].upper())}</span><span class="id">{escape(f["rule_id"])}</span>'
        f'<span class="t"><b>{escape(f["title"])}</b><span>{escape(f["explanation"])}</span></span>'
        f'<span class="rec">{escape(", ".join(f["record_ids"]))}</span></div>'
        for f in findings)
    return _FCSS + f'<div class="fl">{rows}</div>'
