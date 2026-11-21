# AutoDiscover: Zero-Touch AI Onboarding & Passports

[![license](https://img.shields.io/badge/license-Apache%202.0-3f7cc4)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-2b2f5c)](pyproject.toml)
[![streamlit](https://img.shields.io/badge/streamlit-1.67-C25E3A)](requirements.txt)
[![CI](https://github.com/eren-mutlu/forkit-autodiscover-hackathon/actions/workflows/ci.yml/badge.svg)](https://github.com/eren-mutlu/forkit-autodiscover-hackathon/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/AutoDiscover%20tests-158%20passing-2f7a4f)](#quickstart)
[![status](https://img.shields.io/badge/status-hackathon%20prototype-d9884a)](#)

Forkit AI Hackathon submission. AutoDiscover finds the AI tools configured or installed on a machine (MCP servers, AI CLIs, desktop apps and editor extensions),
turns them into a **metadata-only inventory** with **stable passport IDs**, flags **config drift**, and links
runtime events to passports **only with explicit consent**, recording missing approvals and
out-of-expected-behavior incidents along the way. An **activity timeline** shows which agent run did what,
who delegated it, and whether the activity log itself was rewritten.

> **Team merge.** This build combines our AutoDiscover with the best parts of our teammate's prototype,
> [UdanM/forkit-agent-audit](https://github.com/UdanM/forkit-agent-audit): wider discovery, ambiguity-safe
> linking, the activity timeline with observer evidence, the Git audit archive, the delegated-helper pilot,
> Ask and the double-click launcher. See [Merged from forkit-agent-audit](#merged-from-forkit-agent-audit).

It is built on top of Forkit Core in this repository. Passport IDs come from Forkit Core's own
`forkit.domain.identity.compute_id`, and hashing uses `forkit.domain.hashing.HashEngine`.
The original project README is untouched in [`README.md`](README.md).

> **New here?** Read [docs/OVERVIEW.md](docs/OVERVIEW.md). **Building something similar?** See [docs/MERGE_BRIEF.md](docs/MERGE_BRIEF.md).

### In plain words

Companies now run many AI tools, and often nobody knows which ones are installed or what they did.
AutoDiscover answers that, one tab at a time:

1. **Discover.** It reads the AI tool settings already on a computer and lists every tool. Passwords and keys are removed first.
2. **Identity.** Each tool gets a permanent ID card (a passport). Facts the settings don't state are marked UNKNOWN, never guessed.
3. **Drift and rules.** If someone edits a tool's settings, the ID stays the same and the change is flagged for review. Company rules are checked automatically.
4. **Linking.** Actions a tool takes are filed under its ID card, only with permission. Actions without approval, or outside what the tool may do, become incidents.
5. **Map.** One page shows the whole chain, from settings file to ID card to action to incident.
6. **Activity timeline.** Which agent run did what, which helper it delegated to, and whether the activity log is still the one that was saved. An action nobody can be tied to stays UNKNOWN.

Start it with a double-click on **`Start AutoDiscover.cmd`** (Windows) or `./start-autodiscover.sh` (macOS, Linux).

### What it looks like

| Discover | Identity and review | Provenance map |
|---|---|---|
| ![Tools: tools found in two config files, secrets removed](docs/images/autodiscover/01-discover.png) | ![Passports: one passport per tool, UNKNOWN where the config is silent](docs/images/autodiscover/02-identity.png) | ![05 Provenance map: config file, tool, passport, event and incident on one row](docs/images/autodiscover/05-provenance-map.png) |

### Phase 2 at a glance (for the judges)

| Capability | What it proves | Where |
|---|---|---|
| **Real local machine scan** | The same pipeline works on real Claude Desktop, Cursor and VS Code configs on Windows, macOS and Linux, read-only, with usernames masked as `~` and an isolated `data/local/` registry | sidebar mode switch · [details](#real-local-machine-mode) |
| **Policy-as-code** | Declarative, allowlisted rules (`POL-001`…`POL-005`) flag unapproved databases, access-mode downgrades, missing attestation and unapproved writes, each with explicit evidence and no guessing | 02 badges, 03 panel · [details](#6-policy-as-code-guardrails) |
| **Audit bundle** | A one-click ZIP with a tamper-evident `report_sha256`, passport-ID integrity checks, a secret scan that blocks the download on any leak, and `SHA256SUMS` | sidebar · [details](#7-one-click-compliance--audit-bundle) |
| **Live agent capture** | Real agents (a scripted LangGraph agent on a real MCP file server, and an n8n workflow) run five scenarios, and every tool call is captured: locally by an MCP tap that also enforces read-only, in the cloud by a token-checked collector | sidebar "Live agents" · [details](#live-agents-captured-local-and-cloud-runs) |
| **Provenance map** | Config → provenance hash → passport → events and incidents, drawn from live data as a row-by-row flow chart, with same-name tools visibly kept apart | 05 tab · [details](#8-visual-provenance--lifecycle-flow) |

### Phase 3: merged with our teammate's prototype

| Capability | What it proves | Where |
|---|---|---|
| **Wider discovery** | Claude Code (incl. per-project servers), Windsurf, Codex `config.toml`, VS Code Insiders and profiles, per-workspace configs; transport and explicit enabled state; disabling a server is drift | 01 · [details](#real-local-machine-mode) |
| **Installed AI clients** | CLIs on PATH, desktop apps, editor extensions as their own passport types, so a CLI named `claude` never merges with an MCP server named `claude`; an extension upgrade is drift | 01 checkbox |
| **Ambiguity-safe linking** | An event links only if its own source matches exactly one passport; ambiguous or source-less events go to a review queue, actor UNKNOWN | 04 · `evt-0004`, `evt-0005` |
| **Activity timeline** | Agent identity only from observer evidence, delegation chains with PIDs, shared-file overlap, self-reports checked against the observer, `LENGTH:SHA256` log checkpoints; 13 checks as rules `POL-006`…`POL-018` | 06 tab · [details](#activity-timeline) |
| **Git audit archive** | Every link, incident, review item, drift record and activity finding in an append-only bare Git repo; rewrites and replaced histories fail verification | sidebar · [details](#append-only-git-archive) |
| **Delegated-helper pilot** | Real worker processes; a helper declared read-only edits a protected file and its own report says otherwise | Live agents scenario F |
| **Ask Forkit** | Short questions answered from records with citations; optional local model, loopback only | expander under the header |

| File | Purpose |
|---|---|
| `src/engine.py` | Discovery (fixtures and real machine), redaction, identity, diffing, consented linking, incidents, policies, audit export |
| `src/provenance_view.py` | Pure, read-only Graphviz DOT builders for the lifecycle diagram and the live map (kept for export and tests) |
| `src/live_map_html.py` | Read-only HTML flow charts shown in the app: the lifecycle diagram and the live provenance map |
| `src/discovery_host.py` | "Discovered on" machine name, stored as a sidecar record keyed by `passport_id` |
| `src/installed.py` | Installed AI clients: CLIs on PATH, desktop apps, editor extensions |
| `src/timeline.py` | Activity timeline: strict observer-log parser, runs, agents, delegation, checks, checkpoints |
| `src/audit_archive.py` | Audit records (append-only Git archive) and its verifier |
| `src/ask.py` | Ask Forkit: record matching with citations, optional local model |
| `src/capture/` | Live capture: `mcp_tap.py` (stdio proxy), `collector.py` (HTTP), `events.py` (metadata-only event schema) |
| `demo/agents/` | Agent configs, synthetic workspace, approvals, the scripted agent, the n8n workflow, `pilot.py` and `run_scenarios.py` |
| `app.py` | Streamlit demo: Discover → Match → Suggest → Review → Link → Map |
| `tests/` | `test_acceptance.py` (6) · `test_local_discovery.py` (12) · `test_policies.py` (17) · `test_audit.py` (10) · `test_provenance_view.py` (14) · `test_live_map_html.py` (6) · `test_discovery_host.py` (5) · `test_capture.py` (11) · `test_agent_scenarios.py` (2) · `test_wider_discovery.py` (14) · `test_installed_clients.py` (12) · `test_attribution.py` (8) · `test_timeline.py` (23) · `test_audit_archive.py` (7) · `test_pilot.py` (3) · `test_ask.py` (6) · `test_launchers.py` (2) |
| `fixtures/` | Mock MCP configs, a v2 config edit, runtime events, approvals, policies, `activity/` observer log and agent self-report |

---

## Core philosophy

### 1. Unknown stays UNKNOWN
MCP configs don't declare an owner or a version, so AutoDiscover stores the literal string `"UNKNOWN"`
and never fills in a plausible-looking guess. The same rule covers `client` (we only recognize known
config file names) and `access_mode`:

- **Discovered on is not an owner.** In real-machine mode the app records the machine's short host name
  in `data/local/discovered_on.json` (keyed by `passport_id`, with first and last seen times) and shows it
  as "discovered on". Owner stays `UNKNOWN`. The host name is never a `compute_id` input, so the same
  config gives the same passport ID on every machine, and `passports.json` never contains it. Demo mode
  shows "Demo fixtures (not a real machine)" instead of the host running the demo. If nothing was
  recorded, it shows `UNKNOWN`.
- `access_mode = "read-only"` only when the config **proves** it, for example a Docker bind mount marked
  `ro`, `:ro`, or a `--read-only` flag.
- Without that evidence it is `UNKNOWN`. It is **never** assumed to be `read-write`. When the v2 config
  drops `,ro`, access mode drops to `UNKNOWN` and the change waits for human review.
- In event links and incidents, a missing timestamp, consent ID or approval ID is also `UNKNOWN`.

### 2. Metadata-only, with secrets redacted at discovery
Secrets are stripped **before** anything is hashed, stored or shown:

- Every `env` and `headers` **value** becomes `"[REDACTED]"`. Only the key names are kept.
- Passwords embedded in URLs (`scheme://user:[REDACTED]@host`), secret query parameters, secret-named
  flags (`--token x`, `--api-key=x`, `API_KEY=x`) and well-known token prefixes (`sk-`, `ghp_`, `xox*-`,
  `AKIA`, …) in `args` are redacted.
- Runtime events carry only a content hash and a size, never the payload.
- A test (and a live "disk check" in the UI) proves that none of the raw secret values from the
  fixtures appear anywhere in `data/*.json`.

### 3. Deterministic passport IDs built on Forkit Core
```python
provenance_hash = HashEngine.hash_dict({"source_path": ..., "tool_name": ..., "command": ...})
passport_id = compute_id(
    passport_type="mcp_server", name=tool_name,
    version="UNKNOWN", creator_name="UNKNOWN", creator_org=None,
    artifact_hash=provenance_hash,
)
```
- We call `compute_id` directly and don't use `AgentPassport`. The schema's `validate_version` rejects
  `"UNKNOWN"`, and `AgentPassport` would force us to invent `model_id`, `task_type` and `architecture`,
  which breaks principle 1.
- `source_path` is stored **relative to the scan root**, so the same config gives the same `passport_id`
  on every machine and after every restart.
- `args` and env key names are **not** part of the identity. They feed `config_hash`, so editing a
  server's arguments shows up as a *metadata change on the same passport*, not as a brand-new tool.

### 4. No false merges
Two servers both called `postgres-mcp`, one in Claude Desktop and one in Cursor pointing at a different
database, get **different** passport IDs because their provenance (config path) differs. The UI marks
them with the note "Same name as another tool, different provenance", and the live map says "Another tool has
the same name. Kept as a separate passport."

### 5. Consent-gated event linking
`link_event(event, passport_id, user_consent)`:
- raises `PermissionError` unless `user_consent is True`;
- raises `ValueError` if the event's `source` (server name or config path) doesn't match the chosen passport,
  so an event can't be attached to the wrong tool;
- records the event's `source` and `timestamp` verbatim;
- checks `fixtures/approvals.json`, **not** the event's own `approval_id` claim, and flags
  `MISSING_APPROVAL` when there is no approved record covering that action;
- records an `INCIDENT` (`OUT_OF_EXPECTED_BEHAVIOR`) when a tool with proven `read-only` access attempts
  a mutating action (`write`, `delete`, `move`, …). This is recorded even if the action was denied,
  because the attempt is the signal.

### 6. Policy-as-code guardrails
Rules live in [`fixtures/policies.json`](fixtures/policies.json) as declarative JSON. Nothing in that file
is ever executed. Each rule's `when` block may only use an allowlisted condition, and all conditions must hold:

| Rule kind | Allowed conditions |
|---|---|
| `passport` | `category_in`, `approval`, `field_is_unknown` |
| `change` | `access_mode_transition`, `approval` |
| `event` | `action_mutating`, `approval` |

The loader raises an error for any unknown condition, bad severity or misplaced condition, instead of
skipping the rule. Shipped rules:

| Rule | Severity | Fires when |
|---|---|---|
| `POL-001` | high | A database MCP server (by explicit package, image or command evidence) has no approved record |
| `POL-002` | high | A config edit drops access mode from `read-only` to `UNKNOWN` |
| `POL-003` | medium | Owner is `UNKNOWN` (missing attestation) |
| `POL-004` | low | Version is `UNKNOWN` (missing attestation) |
| `POL-005` | critical | A consented event shows a write/delete-type action with no approval |

A tool is only treated as a database when its command or package args say so (for example
`@modelcontextprotocol/server-postgres` or `mcp/postgres`). Filesystem paths are never used as evidence,
and without evidence the category is `UNKNOWN`, so `POL-001` cannot fire on a guess. Every finding records
the exact facts it used. Findings are saved to `policy_findings.json` in the active store (`data/` for the
demo, `data/local/` for real-machine mode) and never written into `passports.json`.

### 7. One-click compliance & audit bundle
**Download audit report (ZIP)** in the sidebar exports a timestamped ZIP,
`autodiscover-audit-<demo|local>-<UTC timestamp>.zip`. The **JSON** and **Markdown** buttons next to it
download the report alone.

| Entry | Contents |
|---|---|
| `report.json` | Summary counts, confirmed passports, approval coverage, metadata diffs, consented event links, `MISSING_APPROVAL` link IDs, incidents, policy findings, integrity and secret-scan verdicts |
| `report.md` | The same report as readable tables |
| `store/*.json` | Metadata projections of the store records, with fingerprints and changed field names in place of raw config details |
| `inputs/approvals.json`, `inputs/policies.json` | The approvals and rules that were applied |
| `SHA256SUMS` | `sha256sum -c`-compatible checksums, with POSIX `/` paths |

- **Read-only:** building the report never writes to the store, since policies are evaluated in memory.
  An empty or freshly reset store exports cleanly with all counts at 0.
- **Integrity:** every passport ID is re-derived from its stored `source_path`, `tool_name` and `command`.
  Any mismatch, for example a hand-edited command, sets `integrity.status = FAIL`.
- **Secret scan:** every store file and the report itself are checked against the raw secret values from
  the scanned configs (held in memory only, so the report contains only a count) and against URL-password
  and known-token patterns. On a leak, the report content is scrubbed, `secret_scan.status = FAIL`, and the
  ZIP download is **blocked**. The error names the file, never the value.
- **Tamper evidence:** `report_sha256` is the SHA-256 of
  `json.dumps(report_without_report_sha256, sort_keys=True)` in UTF-8. To verify it:

```python
import hashlib, json
r = json.load(open("report.json")); claimed = r.pop("report_sha256")
assert hashlib.sha256(json.dumps(r, sort_keys=True).encode("utf-8")).hexdigest() == claimed
```

### 8. Visual provenance & lifecycle flow
The **Map** tab shows two diagrams, with no new dependency:

- **Lifecycle:** client config → redaction → provenance hash → Forkit Core `compute_id` → stable
  `passport_id`. The side records (approvals, drift, links, incidents, policy findings) hang off the
  passport by `passport_id`, with live counts. Drawn as a straight left-to-right chart.
- **Live provenance map:** a flow chart with fixed columns, read left to right: Config file → MCP tool →
  Passport → Runtime event → Incident, plus an Open checks column for drift and policy findings. There is
  one row per linked event and every arrow is straight. Labels are plain English: "Integrity check passed"
  (re-derived from provenance), "No approval on file" (red, `MISSING_APPROVAL`), "Approved" (green),
  "Incident · Out of expected behavior", and "Not issued yet" (dashed) for candidates. The two
  `postgres-mcp` tools sit on separate rows under their own config files. You can focus on one passport,
  and for large inventories events collapse into counts.

`src/provenance_view.py` and `src/live_map_html.py` only read the `store_dir` they are given (`data/` or
`data/local/`). In the DOT diagrams node IDs are generated rather than taken from config text and every
label escapes quotes, backslashes and newlines. In the HTML map every stored value is HTML-escaped. Unusual
real-machine names can't break either view. An empty store shows the lifecycle diagram with zero
counts and a friendly placeholder for the live map.

---

## Architecture & data boundary

```
 fixtures/*.json (MCP configs)
          │  discover_tools()  ── redact → extract metadata → derive passport_id + config_hash
          ▼
   candidates (in memory)
          │  mint_passport()   ── single-click confirm, idempotent
          ▼
 ┌──────────────────────────┐
 │ data/passports.json      │  SOURCE OF TRUTH: confirmed passports.
 │   {passport_id: record}  │  Written once per passport, never rewritten.
 └────────────┬─────────────┘
              │ joined by passport_id (separate files, the passport is never mutated)
   ┌──────────┼───────────────────┬──────────────────────────┬─────────────────────────┐
   ▼          ▼                   ▼                          ▼                         │
 fixtures/approvals.json  data/metadata_changes.json  data/linked_events.json  data/incidents.json
 (external approvals)     (config drift, PENDING_     (consented event links,  (out-of-expected-
                          REVIEW, human-readable      source, timestamp,       behavior records)
                          diff)                       approval status, flags)
```

- **`data/passports.json` is effectively append-only.** `mint_passport` returns the existing ID if the
  passport already exists and never overwrites it. Tests assert the file is byte-identical after a
  restart re-scan, a config change, and event linking.
- Drift, approvals, event links, incidents and policy findings (`policy_findings.json`) live in **separate JSON records** joined by `passport_id`,
  following Forkit Core's identity spec (§2.1): application metadata stays outside the identity material.
- Writes are atomic (temp file plus `os.replace`), so an interrupted demo never corrupts the registry.

---

## Quickstart

Requires Python 3.10+. Run everything from the repository root.

```bash
pip install -r requirements.txt

# All AutoDiscover tests (158)
python -m pytest tests/test_acceptance.py tests/test_local_discovery.py tests/test_policies.py \
                 tests/test_audit.py tests/test_provenance_view.py tests/test_live_map_html.py \
                 tests/test_discovery_host.py tests/test_capture.py tests/test_agent_scenarios.py \
                 tests/test_wider_discovery.py tests/test_installed_clients.py tests/test_attribution.py \
                 tests/test_timeline.py tests/test_audit_archive.py tests/test_pilot.py tests/test_ask.py \
                 tests/test_launchers.py -v

# Live demo (or double-click "Start AutoDiscover.cmd" on Windows, ./start-autodiscover.sh elsewhere)
streamlit run app.py
```

> Run the acceptance file directly. The repo's wider test suite needs Forkit Core's optional extras
> (`pip install -e ".[dev]"`).

### Acceptance tests

| Test | Proves |
|---|---|
| `test_restart_recognition_yields_same_passport_ids` | **Restart recognition**: a fresh engine re-scans to the same IDs, with 0 duplicates and the registry unchanged |
| `test_same_name_tools_from_different_configs_stay_distinct` | **No false merges**: both `postgres-mcp` tools stay separate |
| `test_consented_linking_preserves_source_and_flags_missing_approval` | **Correctly linked event**: blocked without consent; with consent, source and timestamp are kept, `MISSING_APPROVAL` is checked against `approvals.json`, the passport is untouched, and wrong-tool links are blocked |
| `test_write_from_read_only_tool_records_incident` | **Bonus**: `fs.write_file` from read-only `filesystem-mcp` records one incident (no duplicates on re-link) |
| `test_secrets_never_reach_disk_and_unknowns_stay_unknown` | Redaction holds on disk; owner and version stay `UNKNOWN` |
| `test_config_change_flags_metadata_diff_without_mutating_passport` | v2 edit keeps the same ID, sets `metadata_changed`, produces a readable diff, and drops access mode to `UNKNOWN` |

---


### Share it online (Streamlit Community Cloud)

1. Sign in at [share.streamlit.io](https://share.streamlit.io) with the GitHub account that owns this repository.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Repository `eren-mutlu/forkit-autodiscover-hackathon`, branch `main`, main file path `app.py`.
4. Open **Advanced settings**: choose Python 3.12, and paste this into **Secrets** so every visitor gets their own demo data:
   ```toml
   AUTODISCOVER_PER_SESSION = "1"
   ```
5. Click **Deploy**. Dependencies come from `requirements.txt`; the first start takes about a minute.

On the hosted app, demo mode works as on a laptop. **Scan this computer** scans the cloud server, so it
correctly reports 0 configs; run the app locally to scan your own computer.

The pitch cover slide is in [`docs/cover/`](docs/cover/autodiscover-cover.pdf) (PDF and PNG).

## 90-second demo script

Start from **Start the demo over** in the sidebar. Each tab shows a *Next step* hint. In a hurry? **Set up the demo in one click** scans and registers all three sample tools, so you can jump straight to step 3.

| # | Click | Say |
|---|---|---|
| 1 | **Tools** → *Scan for AI tools* | "Three MCP servers found. Secrets are redacted at discovery, and the disk check proves no raw secret value is stored." |
| 2 | **Passports** → *Register tool* on all three | "Owner and version are UNKNOWN because the config doesn't say. Both postgres-mcp tools share a name but have different provenance, so they get different IDs." |
| 3 | Sidebar → **Simulate app restart** | "All memory is wiped. The re-scan matched 3 of 3 tools to the same passport IDs, with zero duplicates." |
| 4 | Sidebar → **Simulate a settings edit** → **Changes** | "Someone edited filesystem-mcp. It's the same passport, but we flag the diff. The read-only evidence is gone, so access mode is now UNKNOWN until a human reviews it." |
| 5 | **Event review** → pick `evt-0002` → *Link action to tool* without consent | "Linking is blocked without explicit consent." |
| 6 | Tick consent → *Link action to tool* | "Source and timestamp are recorded. *No approval on file* comes from approvals.json, and the incident shows a read-only tool tried to write." |
| 7 | Pick `evt-0003` → choose the filesystem-mcp passport → consent → *Link action to tool* | "You can't attach an event to the wrong tool." |
| 7b | Pick `evt-0004` | "It only says postgres-mcp, and two configs have one. That's ambiguous, so the engine refuses to link it. We record it for review with the actor UNKNOWN." |
| 8 | **Map** | "Each row reads left to right: config file, tool, passport, event, incident. Everything joins by passport_id, and the passport itself never changes." |
| 8b | **Activity timeline** → *Save log checkpoint* → *Simulate log tampering* | "The helper was declared read-only and still edited a protected file. Its own report disagrees with the observer. And when someone edits the log afterwards, the checkpoint catches it: POL-006, critical." |
| 9 | Sidebar → **Download audit report (ZIP)** | "Integrity and secret scan both PASS. The report is tamper-evident and every file is checksummed." |

### Real local machine mode
Pick **Scan this computer** in the sidebar to run the same pipeline on your own computer. It's a
read-only scan of these well-known locations:

| OS | Claude Desktop | Cursor | VS Code |
|---|---|---|---|
| Windows | `%APPDATA%/Claude/claude_desktop_config.json` | `%USERPROFILE%/.cursor/mcp.json` | `%APPDATA%/Code/User/mcp.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` | `~/.cursor/mcp.json` | `~/Library/Application Support/Code/User/mcp.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` | `~/.cursor/mcp.json` | `~/.config/Code/User/mcp.json` |

Also on every OS: Claude Code `~/.claude.json` (user and per-project servers, each project its own source),
Windsurf `~/.codeium/windsurf/mcp_config.json`, Codex `~/.codex/config.toml` (or `%CODEX_HOME%`), VS Code
Insiders, and every VS Code profile under `User/profiles/<id>/mcp.json`. Optional **project folders** add
`.vscode/mcp.json`, `.cursor/mcp.json`, `.mcp.json` and `.codex/config.toml`, labelled by folder name only.
Each config read is capped at 1 MiB.

With **Also find installed AI clients** ticked, the scan adds CLIs on PATH (`claude`, `codex`, `gemini`,
`ollama`, `aider` and more), desktop apps in their install folders (Windows, macOS), and AI editor extensions
(Copilot, Claude Code, Cline, Roo, Continue and more). Each is its own passport type, its location is stored
with the home folder masked, and an extension's declared version and publisher come from its own
`package.json`. Owner stays UNKNOWN. Nothing is launched.

- Paths are stored as the tokens above (`~`, `%APPDATA%`), and the home directory inside args is masked as
  `~`, so OS usernames never reach the inventory and passport IDs stay stable per location.
- VS Code's `"servers"` key is supported as well as `"mcpServers"`.
- Missing, unreadable, corrupt or non-object files are reported per location (`missing`, `unresolved`,
  `unreadable`, `invalid_json`, `invalid_format`) and never crash the scan. A machine with no configs
  returns a clean empty summary.
- Results go to an isolated registry in `data/local/`, which never touches the demo's `data/passports.json`.
- Each scan records which machine it ran on in `data/local/discovered_on.json`. This is context only, not an owner.
- Run Streamlit on your own computer for this mode. In a cloud container it correctly reports 0 configs.

### Live agents: captured local and cloud runs
Pick **Live agents (captured runs)** in the sidebar and click **Run agent scenarios**. Real agents then
work on synthetic files while AutoDiscover captures each tool call:

- **Local:** a scripted LangGraph agent (no LLM) talks to the real `@modelcontextprotocol/server-filesystem`
  through the AutoDiscover tap, which sits in the client config as the server's command. The tap records
  metadata only and blocks writes on a server marked `--read-only`.
- **Cloud:** an n8n workflow reads a report, writes a summary and POSTs its actions to the collector,
  which checks a token and re-builds every event.

The six scenarios cover an approved read, a blocked write, the same tool name in another client, the n8n
run, a config edit that removes `--read-only` before a write, and a delegated helper (two real processes,
watched by an independent observer) that writes although it was declared read-only; see tab 06. Linking still needs your consent tick in
**Event review**. Same from the command line: `python -m demo.agents.run_scenarios --consent`.
Details and requirements: [`demo/agents/README.md`](demo/agents/README.md).

![Live agents: captured scenarios](docs/images/autodiscover/06-live-agents.png)

### Activity timeline
Tab **06** reads an observer's activity log (JSON Lines), never writes to it, and builds runs, agents,
delegation chains and file actions.

- **Identity needs observer evidence.** An agent ID exists only when the observer's `run_started` row
  carries `deployment`, `agent_id` and `instance_id`. A name, a path or the agent's own report never makes one.
- **Unknown stays UNKNOWN.** A change with no observed run has actor UNKNOWN.
- **Self-reports are checked, not trusted.** Rows an agent reports about itself are compared with the observer.
- **Tamper evidence.** *Save log checkpoint* stores `LENGTH:SHA256` of the log; any later rewrite or truncation
  raises `POL-006` (critical).
- **Rules are data.** The 13 checks (read-only write, protected file, missing approval, unknown actor, access
  escalation, config file change, sequence gap, clock drift, self-report mismatch, rename or move, missing
  onboarding, run reuse, rewritten log) are rules `POL-006`…`POL-018` in `fixtures/policies.json`. The policy
  loader only accepts these named checks.
- **Metadata-only.** Paths must be relative to the workspace; a diff is reduced to its hash and size.

Row shape: `{"id", "time", "kind": "run_started"|"file_write"|"tool_call", "run_id", "deployment", "sequence",
"observed_at", ...}`. Run rows add `agent_id`, `instance_id`, `parent_id`, `label`, `location`, `access`
(`read_only`|`write`), `pid`, `onboarded`; file rows add `path`, `operation`, `initiator_run_id`, `approval`,
`protected`, `patch`. In real-machine mode, point the tab at any observer log; `python -m demo.agents.pilot
<folder>` writes one.

### Audit records (append-only Git archive)
The sidebar's **Audit records (append-only Git archive)** commits every new link, incident, review item, drift record and
activity finding to a bare Git repository next to the store (`audit-archive.git`), one commit per batch,
with plumbing commands only (no hooks, no user config, fixed identity). Each commit may only append to
`records.jsonl`; verification walks the history and also checks that the last head AutoDiscover wrote is still
an ancestor, so a replaced history fails too. A batch with a raw secret or token pattern is refused.

```bash
git --git-dir data/audit-archive.git log --oneline
git --git-dir data/audit-archive.git show main:records.jsonl
```

### Ask Forkit
The **Ask Forkit** expander answers questions like "What needs review?", "Who changed
reports/q3/summary.md?" or "Which tools did you find?" from the local records, with a citation per claim.
Set `AUTODISCOVER_ASK_MODEL` to an installed Ollama model to have the answer rephrased locally: the model
gets short evidence lines only, over loopback, and its answer is dropped unless every citation is valid.

### Out-of-order clicks are safe
Each step shows guidance if it's used too early: restarting before anything is minted, loading v2 before
`filesystem-mcp` has a passport (and a warning that minting on v2 makes v2 the baseline), opening 03 or 04
with nothing minted, or picking an event whose tool isn't minted yet.

### How the v2 toggle works
Discovery scans a working copy at `data/live_configs/fixtures/`. The v2 toggle copies
`claude_desktop_config_v2.json` **over** the working copy of `claude_desktop_config.json`, which simulates
a user editing the file in place. The path is unchanged, so the passport ID is too, and the change shows
up as drift. `fixtures/` itself is never modified.

---

## Fixtures

| File | Contents |
|---|---|
| `claude_desktop_config.json` | `postgres-mcp` (fake `POSTGRES_URL` credential) and `filesystem-mcp` (Docker bind mount, `ro`) |
| `cursor_mcp.json` | A second `postgres-mcp` pointing at a different database |
| `claude_desktop_config_v2.json` | `filesystem-mcp` with `ro` removed and a `finance` mount added |
| `runtime_events.jsonl` | `evt-0001` approved read, `evt-0002` unauthorized `fs.write_file`, `evt-0003` unapproved Cursor `pg.query`, `evt-0004` names only the shared `postgres-mcp` (ambiguous), `evt-0005` has no source (delete attempt) |
| `activity/observer.jsonl` | Observer log: a report agent, its read-only helper writing a protected file, a Cursor run editing `.cursor/mcp.json`, a sequence gap, clock drift, two unknown actors, an access escalation, missing onboarding |
| `activity/agent_reported.jsonl` | The helper's own report, which disagrees with the observer |
| `approvals.json` | `filesystem-mcp` approved for read actions only; Claude `postgres-mcp` pending; Cursor `postgres-mcp` has no record |

All credentials in the fixtures are fake (`FAKE_..._DO_NOT_USE`).

## Known limitations
- Env values are redacted *before* hashing, so rotating only a secret value doesn't register as drift.
  This is deliberate: we never store a derivative of a secret.
- Discovery reads the `mcpServers` shape (Claude Desktop, Cursor, Claude Code, Windsurf), the `servers` shape (VS Code) and Codex's `[mcp_servers]` TOML.
- The demo tab uses fixture events. Live capture exists (MCP tap and HTTP collector, see Live agents), but it
  covers stdio MCP servers and agents that report to the collector; there is no hook inside closed clients.
- The activity timeline is only as complete as its observer. The pilot sees its own workspace around each
  worker; it does not enforce OS permissions and is not a tamper-proof host monitor.
- Approvals come from JSON files; there is no in-app approval flow yet.
- Linking is idempotent per (`event_id`, `passport_id`).

## Merged from forkit-agent-audit

Our teammate built the same challenge as [UdanM/forkit-agent-audit](https://github.com/UdanM/forkit-agent-audit)
(compared at commit `3f5b2aa`). We kept AutoDiscover's base (Forkit Core identity, redaction and leak scan,
policy-as-code, audit ZIP, Streamlit UI, CI) and ported his strongest ideas into it, one tested commit each:

| Ours now | From his | Commit |
|---|---|---|
| Wider MCP discovery, transport and enabled state | `discovery.py`, `scan()` | `414f340` |
| Installed AI clients as passport types | `discovery.py` | `6811e62` |
| Ambiguity-safe linking and review queue | `Inventory.candidates`, `fixtures/events.json` | `ca726e5` |
| Activity timeline, observer identity, log checkpoints | `timeline.py`, `docs/scaling.md` | `3922bb3` |
| Audit records (append-only Git archive) | `audit_repo.py` | `afbc7d7` |
| Delegated-helper pilot | `agent_pilot.py` | `4a7f446` |
| Ask Forkit | `local_ask.py` | `b65f1b5` |
| Double-click launchers | `Start Forkit.cmd` | `ed9da68` |

What we deliberately kept from AutoDiscover instead: Forkit Core `compute_id` (his IDs use absolute paths, so
they differ per machine), redaction before hashing (his export drops fields instead), rules as data (his
checks are code). Every port was re-implemented against our guardrails and tests, not copied.
