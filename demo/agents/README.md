# Live agents: captured local and cloud runs

Real agents do real file work, and AutoDiscover captures every tool call they make. All files are
synthetic. No LLM is involved: the agent steps are fixed, so every run is reproducible.

```bash
pip install -e ".[langgraph]"                      # optional: without it the same steps run as a plain loop
python -m demo.agents.run_scenarios --consent      # or click "Run agent scenarios" in the app (Source: Live agents)
```

## How actions are captured

| Where | Agent | Capture point |
|---|---|---|
| Local | Scripted LangGraph agent, talking MCP over stdio to the real `@modelcontextprotocol/server-filesystem` | `src/capture/mcp_tap.py`, placed as the `command` in the client config. It passes every message through and writes one metadata-only event per `tools/call`. With `--read-only` it refuses mutating calls itself. |
| Cloud | n8n workflow `n8n/report_flow.json` (read a report, write a summary) | The workflow's last node POSTs its actions to `src/capture/collector.py` with a bearer token. The collector re-builds every event, so a sender can't inject raw fields, claim an approval, or pretend to be the local tap. |

Events are stored in `data/agents/captured_events.jsonl` in the same schema as
`fixtures/runtime_events.jsonl`, plus `argument_keys` and `capture.origin`. Only tool name, redacted target,
result and time are kept: argument values and file contents never are. `approval_id` and `runtime_consent`
stay null, because the capture point can't know them.

## Scenarios

| | Where | What happens | What AutoDiscover concludes |
|---|---|---|---|
| A | local | Agent lists and reads through Claude Desktop's read-only server | APPROVED (`appr-agent-001`) |
| B | local | Agent tries to write through the same server | Tap blocks it (`denied`); MISSING_APPROVAL, incident, POL-005 |
| C | local | Same tool name `files-local` in Cursor writes a draft | Separate passport; MISSING_APPROVAL, POL-005; a link to the Claude passport is refused |
| D | cloud | n8n reads and writes, then reports to the collector | Read APPROVED (`appr-agent-002`); write MISSING_APPROVAL, POL-005 |
| E | local | `--read-only` is removed from the Claude config, then the agent writes | Drift on the same passport (POL-002); the write succeeds and becomes an incident |
| F | local | A report agent drafts a file. Maya Chen, a simulated human reviewer, edits the draft before and after the agent's read-only helper edits the protected summary. The observer hashes the workspace around each step (`demo/agents/pilot.py`, ported from UdanM/forkit-agent-audit). | Activity timeline shows both worker PIDs, delegation, and the two human edits. POL-007, POL-008, POL-009, and POL-015 still flag the helper's activity. Ask Forkit can identify the helper's parent and the human reviewer. |

Run the pilot on its own with `python -m demo.agents.pilot <new folder>`. It only sees changes to its own
workspace around each worker; it does not enforce OS permissions.

## Requirements and fallbacks

- **npx** (Node 18+) for the real filesystem server. Without it the runner uses `mini_fs_server.py`, a
  stdlib stand-in with the same tool names. Tests always use the stand-in.
- **n8n** for scenario D: `npm install n8n@1` (n8n 2.x needs Node 24), then point `AUTODISCOVER_N8N` at
  `node_modules/.bin/n8n` or put `n8n` on PATH. Without it scenario D is reported as skipped. The hosted
  Streamlit app has no n8n, so D is skipped there.
- In this build, "cloud" meant a cloud container running n8n and the collector together. For an n8n
  instance elsewhere, run the collector on a reachable host
  (`python -m src.capture.collector --host 0.0.0.0 --token …`) and set `AUTODISCOVER_COLLECTOR_URL` in n8n.
