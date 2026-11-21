# Forkit AI local prototype

Double-click **Start Forkit.cmd** on Windows to start the server and open the browser. Keep the launcher window open while using the demo. You can also start it from this directory with **Python 3.10+**. No packages or credentials are required.

~~~powershell
python app.py
~~~

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Use an unused database filename with `--db` to start with fresh demo state.

## Demo walkthrough

1. Click **Discover tools**. The scan finds a CLI and two MCP servers both named `workspace-search`, each with a different source. Owner and capabilities are unknown.
2. Click **Register tool** on each MCP card. Compare their IDs. Stop the app with Ctrl+C and restart it with the same command. Refresh the page and confirm the IDs are recognized.
3. Click **Scan changed fixture**. The desktop MCP config reports a change from enabled to disabled and retains its ID.
4. In **Event review**, find `evt-104`. Its audit source, UTC timestamp and reported editor config support the editor candidate. Action approval is missing. Check the consent box and click **Link event**. The linked ID should match the editor card.
5. Compare `evt-105`, which lacks source context and has two candidates, and `evt-106`, which reports an unobserved config. Neither can be linked. Click **Record incident** on `evt-106` to save the unexpected write attempt alongside the expected read-only behavior.
6. Click **Export inventory JSON** to download the metadata-only inventory.

## Activity timeline

1. Register both MCP tools, then select **Activity timeline** and **Load activity timeline**.
2. Open **Compare two runs**, then choose **Editor run** and **Editor helper run**. Forkit shows separate recorded runs of the same installed tool and identifies the reported delegation.
3. Compare **Editor helper run** with **Desktop run**, then expand **Compare file and folder activity**. Both touched `reports/shared/summary.md`.
4. Compare **Editor helper run** with **Helper as reported by the agent**. Their sources reused a run reference, so Forkit leaves the relationship unknown.
5. Look under **What the evidence shows** for the changed log and edits without an identified actor. Expand **Why this is shown** on an activity record to check its source and reasoning.

Use [the scaling and evidence guide](docs/scaling.md) for live log imports, folder checks, and the edge cases a production collector needs to handle. The prototype displays up to 250 activity records and reads up to 4 MiB per log or 10,000 entries per watched folder on each refresh. Folder checks read file names, sizes and modification times. The prototype reads existing logs and does not install an operating-system monitor.

When running in **Device scan** mode, use **Try sample activity** to open a separate sandbox with synthetic tools and records. The two views use separate state. To see real file changes, launch with `--live --watch-dir "path/to/project"`, load the timeline once, change a file, then refresh the timeline. A folder change without a run record appears as an unknown-actor finding in **Event review**.

## Scan local tools

### Run an agent workflow pilot

The pilot launches a report worker in a separate process and records its PID
and observed before-and-after file change. The delegated scenario runs a helper
under a declared read-only policy and deliberately lets it write only a
disposable test file, so Forkit can detect the mismatch.

```powershell
python agent_pilot.py .forkit-pilot-data/local --deployment local-windows --scenario approved
python agent_pilot.py .forkit-pilot-data/local --deployment local-windows --scenario delegated
python audit_repo.py .forkit-pilot-data/local/changes.git .forkit-pilot-data/local/observer.jsonl
python app.py --live --audit-log .forkit-pilot-data/local/observer.jsonl --audit-repo .forkit-pilot-data/local/changes.git --port 0 --open
```

On Linux, use `python3` and `--deployment cloud-linux`. To connect the
Windows launcher to an existing Google Cloud IAP deployment, create an
untracked `.forkit-vm.json` beside the launcher with keys `instance`,
`project`, `zone`, `account`, and `host_key`. Set `host_key` to the
VM's independently verified SHA256 SSH fingerprint. The launcher uses an
installed `gcloud`, or a local Python entry point supplied as the optional
`gcloud_python` setting. Run `& '.\Connect Forkit VM.cmd' --pilot` for a
pilot listening on the VM's loopback port 18766.
The pilot only sees changes to its test target around the worker execution.
It does not enforce OS permissions or provide tamper-resistant host auditing.

~~~powershell
python app.py --live --workspace . --config 'C:\path\to\first-mcp.json' --config 'C:\path\to\second-mcp.json'
~~~

Live discovery checks supported command names on PATH, known Windows desktop install paths, and supported editor extension directories. It also checks known user MCP configs. Repeat `--workspace` for projects and `--config` for additional files. Codex TOML parsing requires Python 3.11 or newer. Event review uses synthetic fixtures in demo mode. All state stays in local SQLite, with separate default databases for demo and live runs.

An MCP identity describes a named slot in a specific config file. A CLI identity describes a named command at its resolved location. A move yields a new candidate. A replacement in the same slot retains the ID, and changes in observed fields are flagged. MCP transport and explicit disabled state are observed metadata. URLs, commands, arguments, environment values, raw paths and event records are excluded from the inventory export. Config files are read as data, and their servers and detected commands are never launched.

## Acceptance tests

### Record collector events in a separate Git repository

Use an append-only JSON Lines feed from your collector. Include the actual
change in each event, such as a unified diff in a `patch` field, alongside
the event timestamp, path, operation, source, and run and parent IDs when known.
See [the synthetic example](fixtures/audit-repo.jsonl). The recorder preserves
each complete collector row as supplied. A metadata-only row stays metadata-only.

```powershell
python audit_repo.py '.\forkit-audit-demo.git' '.\fixtures\audit-repo.jsonl'
git -C '.\forkit-audit-demo.git' log --oneline
git -C '.\forkit-audit-demo.git' show main:events.jsonl
```

For real records, substitute your collector log and a new output directory.
Run the recorder after each batch to commit new rows. It leaves incomplete
last lines for the next run and rejects changes to a saved source prefix.

Use a new output directory to start with fresh Git history. Omit `--from-now`
to replay the log from the beginning, or include it to begin with an empty
archive and capture only subsequent complete records. The old repository is
separate and can be retained or deleted locally. Published copies may persist.
Only publish records you intend to make public, including the contents of diffs.

To search the archive in the dashboard, start Forkit with the repository path:

```powershell
python app.py --live --audit-repo '.\forkit-audit-demo.git' --port 0 --open
```

Ask Forkit can look up a file path, run, event ID, recent changes, unknown
actors, or missing approval. Answers link to the audit entry, timeline,
or event review. The assistant uses local record matching and searches the
latest 250 committed archive rows and the currently loaded dashboard records.
The fixture demo and offline preview include sample audit evidence.
Optional [local model setup](docs/scaling.md#ask-forkit) uses a local Ollama
instance without sending full diffs in model requests.

### Verification scope

The tests cover scoped identity across sessions, delegated runs, ambiguous
evidence, log checkpoints, review findings, audit history and metadata-only
recording, plus a Windows/Linux pilot with real subprocess file writes.
Discovery checks supported tools and readable configurations. Complete agent
coverage, every intermediate write, and tamper-resistant collection still
require an independent observer appropriate to the deployment. Git imports
record committed changes and leave agent attribution unknown.

Generate the offline sample with `python make_preview.py`. Generated HTML
can embed every record in its input. Keep live state, audit logs, VM settings,
and generated exports outside the tracked source files.

### Python tests

~~~powershell
python -m unittest -v
~~~

The tests exercise the local HTTP workflow across independent server processes. The discovery design references [Forkit Footprints](https://github.com/Forkit-Dev-Core/footprints).

### Headless browser test

With Python 3.10+, Node.js 24, and Chrome or Chromium installed, run from the project folder:

```powershell
python browser_smoke.py 'C:\Program Files\Google\Chrome\Application\chrome.exe'
```

On Linux, pass the installed Chrome or Chromium executable, for example
`python3 browser_smoke.py /usr/bin/chromium`. The test uses a temporary browser
profile and database and runs the fixture workflow through a real browser.
For restart and live-import checks, also run `python -m unittest -v`.

## If the web preview is unreachable

From a PowerShell terminal in this folder, run `python app.py --port 0 --open`.
Forkit picks a free port and opens your system browser. Keep the terminal running.
If the tab opens in Codex, paste the full address printed in the terminal into
desktop Chrome or Edge. In a second terminal, use
`curl.exe --noproxy "*" http://127.0.0.1:PORT/api/state` with the printed port to
check that the local server responds.

Run `python demo.py` for the fixture walkthrough directly in the terminal using
a temporary database. Run `python -m unittest -v` for automated verification.
