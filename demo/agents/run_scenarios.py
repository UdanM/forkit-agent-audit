"""Run real agents through AutoDiscover and capture what they do, locally and in the cloud.

    python -m demo.agents.run_scenarios --consent

What happens, with synthetic files only:

1. A fresh store in ``data/agents``: the three demo configs are copied to ``data/agents/live/configs``,
   discovered and minted as passports (approvals from ``demo/agents/approvals.json``).
2. An HTTP collector starts on a free local port with a random token.
3. Six scenarios run. The local ones drive a scripted LangGraph agent (no LLM) through a real MCP
   filesystem server behind the AutoDiscover tap. The cloud one runs a real n8n workflow that reads
   and writes files and then POSTs its own actions to the collector.
4. With ``--consent`` (the operator consents for this scripted run) every captured event is linked to
   the passport its source names, and a deliberate wrong link is tried and refused.
5. Policies are evaluated and ``data/agents/scenarios.json`` explains which event belongs to which
   scenario. Open the app, choose Source "Live agents", to see it all.

The scenario steps are fixed; nothing here guesses. If n8n or npx is missing, that part is reported as
skipped (or the stdlib stand-in server is used) instead of failing.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from demo.agents.local_agent import run as run_agent
from demo.agents.pilot import run_pilot
from src.capture.collector import serve
from src.capture.events import read_events
from src.engine import AutoDiscoverEngine

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "demo" / "agents"
CONFIGS = ("claude_desktop_config.json", "cursor_mcp.json", "n8n_mcp.json")
N8N_WORKFLOW_ID = "adReportFlow0001"
MINI_SERVER = ["python", "-m", "demo.agents.mini_fs_server"]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _swap_fs_server(cfg: Path, use_mini: bool) -> None:
    """Point the tapped filesystem server at the stdlib stand-in when npx is not available."""
    if not use_mini:
        return
    data = _load(cfg)
    for spec in data["mcpServers"].values():
        args = spec.get("args", [])
        if "--" in args:
            i = args.index("--")
            spec["args"] = [*args[: i + 1], *MINI_SERVER, args[-1]]
    _save(cfg, data)


def _drop_read_only(cfg: Path) -> None:
    data = _load(cfg)
    spec = data["mcpServers"]["files-local"]
    spec["args"] = [a for a in spec["args"] if a != "--read-only"]
    _save(cfg, data)


def find_n8n(explicit: str | None = None) -> str | None:
    for cand in (explicit, os.environ.get("AUTODISCOVER_N8N"), shutil.which("n8n")):
        if cand and Path(cand).exists():
            return str(cand)
    return None


def run_n8n(n8n: str, root: Path, out: Path, url: str, token: str) -> tuple[bool, str]:
    env = {**os.environ,
           "N8N_USER_FOLDER": str(out / "n8n-home"),
           "AUTODISCOVER_ROOT": str(root),
           "AUTODISCOVER_WORKSPACE": str(out / "workspace"),
           "AUTODISCOVER_COLLECTOR_URL": url,
           "N8N_COLLECTOR_TOKEN": token,
           "N8N_RESTRICT_FILE_ACCESS_TO": str(out / "workspace"),
           "N8N_DIAGNOSTICS_ENABLED": "false",
           "N8N_RUNNERS_ENABLED": "false",
           "N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS": "false"}
    flow = DEMO / "n8n" / "report_flow.json"
    for cmd in ([n8n, "import:workflow", f"--input={flow}"], [n8n, "execute", f"--id={N8N_WORKFLOW_ID}"]):
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
            return False, f"{Path(cmd[0]).name} {cmd[1]} failed: {tail[0][:200]}"
    return True, "n8n workflow finished"


def run_all(root: Path = REPO, fs_server: str = "auto", n8n: str | None = None, consent: bool = False,
            say: Callable[[str], None] = print) -> dict[str, Any]:
    out = root / "data" / "agents"
    shutil.rmtree(out, ignore_errors=True)
    (out / "live").mkdir(parents=True)
    shutil.copytree(DEMO / "configs", out / "live" / "configs")
    shutil.copytree(DEMO / "workspace", out / "workspace")
    use_mini = fs_server == "mini" or (fs_server == "auto" and not shutil.which("npx"))
    for name in CONFIGS[:2]:
        _swap_fs_server(out / "live" / "configs" / name, use_mini)

    log = out / "captured_events.jsonl"
    os.environ["AUTODISCOVER_CAPTURE_LOG"] = str(log)
    paths = [x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep) if x]
    os.environ["PYTHONPATH"] = os.pathsep.join([str(REPO), *[x for x in paths if x != str(REPO)]])

    engine = AutoDiscoverEngine(data_dir=out, base_dir=out / "live",
                                approvals_path=DEMO / "approvals.json",
                                policies_path=REPO / "fixtures" / "policies.json")
    cfg_paths = [out / "live" / "configs" / n for n in CONFIGS]
    for cand in engine.discover_tools(cfg_paths):
        engine.mint_passport(cand)
    say(f"Minted {len(engine.passports())} passports from {len(CONFIGS)} configs.")
    by_source = {(p["source_path"], p["tool_name"]): pid for pid, p in engine.passports().items()}

    token = secrets.token_urlsafe(24)
    srv = serve(0, log, token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/events"

    scenarios: list[dict[str, Any]] = []

    def scenario(sid: str, title: str, where: str, expect: str, action: Callable[[], str | None]) -> None:
        before = {e["event_id"] for e in read_events(log)}
        say(f"\n[{sid}] {title}")
        try:
            note = action()
            status = "ran"
        except Exception as exc:  # noqa: BLE001  a scenario failure is reported, not fatal
            note, status = f"{type(exc).__name__}: {exc}"[:200], "failed"
        new = [e for e in read_events(log) if e["event_id"] not in before]
        if status == "ran" and note and note.startswith("skipped"):
            status = "skipped"
        for e in new:
            say(f"    captured  {e['capture']['origin']:<10} {e['tool_call']:<30} {e['result']:<8} {e['target']}")
        if note:
            say(f"    {note}")
        scenarios.append({"id": sid, "title": title, "where": where, "expect": expect, "status": status,
                          "note": note or "", "event_ids": [e["event_id"] for e in new]})

    claude_cfg = out / "live" / "configs" / "claude_desktop_config.json"
    cursor_cfg = out / "live" / "configs" / "cursor_mcp.json"
    reports, drafts = "data/agents/workspace/reports", "data/agents/workspace/drafts"

    scenario("A", "Approved read through Claude Desktop's read-only file server", "local",
             "Captured and APPROVED: list and read are in appr-agent-001.",
             lambda: (run_agent(claude_cfg, "files-local", root, read_path=f"{reports}/q3_summary.md"), None)[1])
    scenario("B", "Agent tries to write through the same read-only server", "local",
             "The tap blocks the write (result denied). Linked: MISSING_APPROVAL, an incident and POL-005.",
             lambda: (run_agent(claude_cfg, "files-local", root, write_path=f"{reports}/summary.md"), None)[1])
    scenario("C", "Same tool name in Cursor, a different config", "local",
             "A separate passport. The write succeeds but no approval covers it: MISSING_APPROVAL and POL-005. "
             "Linking it to the Claude Desktop passport is refused.",
             lambda: (run_agent(cursor_cfg, "files-local", root, read_path=f"{drafts}/notes.md",
                                write_path=f"{drafts}/summary.md"), None)[1])

    n8n_bin = find_n8n(n8n)

    def cloud() -> str:
        if not n8n_bin:
            return "skipped: n8n is not installed (see demo/agents/README.md)"
        ok, msg = run_n8n(n8n_bin, root, out, url, token)
        if not ok:
            raise RuntimeError(msg)
        return msg

    scenario("D", "n8n workflow reads a report, writes a summary and reports both actions", "cloud",
             "Posted to the collector, re-validated there. Read is APPROVED (appr-agent-002); "
             "the write has no approval: MISSING_APPROVAL and POL-005.", cloud)

    def drift_then_write() -> str:
        _drop_read_only(claude_cfg)
        changed = [c for c in engine.discover_tools(cfg_paths) if c["metadata_changed"]]
        run_agent(claude_cfg, "files-local", root, write_path=f"{reports}/summary.md")
        return f"config edited: --read-only removed; drift on {len(changed)} existing passport(s), no new tool"

    scenario("E", "Someone quietly removes --read-only, then the agent writes", "local",
             "Drift on the same passport (POL-002). The write now succeeds, and because the passport was "
             "confirmed read-only it becomes an incident.", drift_then_write)

    pilot_info: dict[str, Any] = {}

    def delegated_pilot() -> str:
        res = run_pilot(out / "pilot")
        engine.save_log_checkpoint("agents-pilot", res["observer_log"])
        tl = engine.activity_timeline("agents-pilot", res["observer_log"], res["reported_log"])
        pilot_info.update(runs=len(tl["runs"]), findings=[f["rule_id"] for f in tl["findings"]])
        return (f"{len(tl['runs'])} worker processes observed; findings "
                + (", ".join(pilot_info["findings"]) or "none"))

    scenario("F", "Agent delegates to a helper that is declared read-only, and the helper writes", "local",
             "Two real worker processes with their PIDs. The observer sees the helper change the protected "
             "summary: POL-007 (read-only write), POL-008 (protected file) and POL-009 (no approval). The "
             "helper's own report calls it read-only, which disagrees with the observer (POL-015). See tab 06.",
             delegated_pilot)

    srv.shutdown()
    srv.server_close()

    events = read_events(log)
    cross: dict[str, Any] = {"tried": False}
    if consent:
        for ev in events:
            pid = by_source.get((ev["source"]["config_path"], ev["source"]["server_name"]))
            if pid:
                engine.link_event(ev, pid, user_consent=True)
        cursor_write = next((e for e in events if e["source"]["config_path"] == "configs/cursor_mcp.json"
                             and e["tool_call"] == "mcp.write_file"), None)
        if cursor_write:
            try:
                engine.link_event(cursor_write, by_source[("configs/claude_desktop_config.json", "files-local")],
                                  user_consent=True)
                cross = {"tried": True, "refused": False}
            except ValueError as exc:
                cross = {"tried": True, "refused": True, "reason": str(exc), "event_id": cursor_write["event_id"]}
        say(f"\nLinked {len(engine.linked_events())} events with operator consent; "
            f"{len(engine.incidents())} incident(s). Wrong link refused: {cross.get('refused')}.")
    findings = engine.evaluate_policies(engine.discover_tools(cfg_paths))
    say(f"Policy findings: {', '.join(sorted({f['rule_id'] for f in findings})) or 'none'}.")

    summary = {"fs_server": "mini (stdlib stand-in)" if use_mini else "@modelcontextprotocol/server-filesystem",
               "n8n": bool(n8n_bin), "consent": consent, "scenarios": scenarios, "cross_link": cross,
               "events": len(events), "pilot": pilot_info}
    _save(out / "scenarios.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_scenarios", description=__doc__.splitlines()[0])
    p.add_argument("--consent", action="store_true", help="link every captured event (operator consent)")
    p.add_argument("--fs-server", choices=("auto", "npx", "mini"), default="auto")
    p.add_argument("--n8n", default=None, help="path to the n8n binary (default: $AUTODISCOVER_N8N or PATH)")
    a = p.parse_args(argv)
    summary = run_all(REPO, a.fs_server, a.n8n, a.consent)
    failed = [s["id"] for s in summary["scenarios"] if s["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
