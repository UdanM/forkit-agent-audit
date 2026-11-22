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

## Prompt injection comparison

Open **Testing** to type a report prompt, preview the staged VM attachment, and send both runs with
**Send to both agents**. To demonstrate a manual upload, download the supplied JSON and choose it in **Replace
the VM attachment** before sending. The Local PC receives the prompt only, and the Cloud VM receives both the
prompt and the file. **Review a previous run** opens the replay before the first send.

Both scripted agents use the request to write `reports/status.txt`. The VM receives the file at
`inputs/extra-instructions.json`. The script deliberately
follows one fixed directive in that attachment and creates the harmless marker `scratch/off-task.txt`.
The observer compares the workspace before and after each run. Forkit flags a change outside the declared task
as **Possible indirect prompt injection** (`POL-019`) when an attachment was present. The finding cites the file
record and its timestamp. An attachment by itself produces no finding.

Run both roles on one machine for a quick check with `python -m demo.agents.prompt_pair <new-folder>`.
For a two-host run, run the module on each machine with the same unique `--trial` and future `--start-at`
(such as `2026-11-22T12:00:00+00:00`), choosing `--deployment local-pc` on the PC and `--deployment cloud-vm`
on the VM. Use a fresh output folder on each host. Combine both `observer.jsonl` files without changing their rows,
set `AUTODISCOVER_PROMPT_PAIR_LOG` to the combined file, and restart Streamlit to show the capture.

The records contain relative paths, times and fingerprints. This checks how Forkit flags observed behavior from
a scripted workflow. Workspace snapshots measure the net file changes, and the finding calls for human review.

For an interactive two-host demo, start the VM runner from the checkout on the VM with
`python -m demo.agents.prompt_pair_live --port 18768 --token-file <private-token-file>`. Place the same randomly
generated token in a private file on each host and keep both files outside Git. Forward a PC loopback port to
`127.0.0.1:18768` on the VM over your authorized SSH connection. Set `AUTODISCOVER_PROMPT_PAIR_VM_PORT` to the
forwarded PC port and `AUTODISCOVER_PROMPT_PAIR_VM_TOKEN_FILE` to the PC token file before starting Streamlit.

## Requirements and fallbacks

- **npx** (Node 18+) for the real filesystem server. Without it the runner uses `mini_fs_server.py`, a
  stdlib stand-in with the same tool names. Tests always use the stand-in.
- **n8n** for scenario D: `npm install n8n@1` (n8n 2.x needs Node 24), then point `AUTODISCOVER_N8N` at
  `node_modules/.bin/n8n` or put `n8n` on PATH. Without it scenario D is reported as skipped. The hosted
  Streamlit app has no n8n, so D is skipped there.
- In this build, "cloud" meant a cloud container running n8n and the collector together. For an n8n
  instance elsewhere, run the collector on a reachable host
  (`python -m src.capture.collector --host 0.0.0.0 --token …`) and set `AUTODISCOVER_COLLECTOR_URL` in n8n.
