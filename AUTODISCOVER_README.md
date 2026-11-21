# AutoDiscover: Zero-Touch AI Onboarding & Passports

[![license](https://img.shields.io/badge/license-Apache%202.0-3f7cc4)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-2b2f5c)](pyproject.toml)
[![streamlit](https://img.shields.io/badge/streamlit-1.67-C25E3A)](requirements.txt)
[![CI](https://github.com/eren-mutlu/forkit-autodiscover-hackathon/actions/workflows/ci.yml/badge.svg)](https://github.com/eren-mutlu/forkit-autodiscover-hackathon/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/AutoDiscover%20tests-70%20passing-2f7a4f)](#quickstart)
[![status](https://img.shields.io/badge/status-hackathon%20prototype-d9884a)](#)

Forkit AI Hackathon submission. AutoDiscover finds the AI tools (MCP servers) configured on a machine,
turns them into a **metadata-only inventory** with **stable passport IDs**, flags **config drift**, and links
runtime events to passports **only with explicit consent**, recording missing approvals and
out-of-expected-behavior incidents along the way.

It is built on top of Forkit Core in this repository. Passport IDs come from Forkit Core's own
`forkit.domain.identity.compute_id`, and hashing uses `forkit.domain.hashing.HashEngine`.
The original project README is untouched in [`README.md`](README.md).

### In plain words

Companies now run many AI tools, and often nobody knows which ones are installed or what they did.
AutoDiscover answers that, one tab at a time:

1. **Discover.** It reads the AI tool settings already on a computer and lists every tool. Passwords and keys are removed first.
2. **Identity.** Each tool gets a permanent ID card (a passport). Facts the settings don't state are marked UNKNOWN, never guessed.
3. **Drift and rules.** If someone edits a tool's settings, the ID stays the same and the change is flagged for review. Company rules are checked automatically.
4. **Linking.** Actions a tool takes are filed under its ID card, only with permission. Actions without approval, or outside what the tool may do, become incidents.
5. **Map.** One page shows the whole chain, from settings file to ID card to action to incident.

### What it looks like

| Discover | Identity and review | Provenance map |
|---|---|---|
| ![01 Discover: tools found in two config files, secrets removed](docs/images/autodiscover/01-discover.png) | ![02 Identity: one passport per tool, UNKNOWN where the config is silent](docs/images/autodiscover/02-identity.png) | ![05 Provenance map: config file, tool, passport, event and incident on one row](docs/images/autodiscover/05-provenance-map.png) |

### Phase 2 at a glance (for the judges)

| Capability | What it proves | Where |
|---|---|---|
| **Real local machine scan** | The same pipeline works on real Claude Desktop, Cursor and VS Code configs on Windows, macOS and Linux, read-only, with usernames masked as `~` and an isolated `data/local/` registry | sidebar mode switch · [details](#real-local-machine-mode) |
| **Policy-as-code** | Declarative, allowlisted rules (`POL-001`…`POL-005`) flag unapproved databases, access-mode downgrades, missing attestation and unapproved writes, each with explicit evidence and no guessing | 02 badges, 03 panel · [details](#6-policy-as-code-guardrails) |
| **Audit bundle** | A one-click ZIP with a tamper-evident `report_sha256`, passport-ID integrity checks, a secret scan that blocks the download on any leak, and `SHA256SUMS` | sidebar · [details](#7-one-click-compliance--audit-bundle) |
| **Provenance map** | Config → provenance hash → passport → events and incidents, drawn from live data as a row-by-row flow chart, with same-name tools visibly kept apart | 05 tab · [details](#8-visual-provenance--lifecycle-flow) |

| File | Purpose |
|---|---|
| `src/engine.py` | Discovery (fixtures and real machine), redaction, identity, diffing, consented linking, incidents, policies, audit export |
| `src/provenance_view.py` | Pure, read-only Graphviz DOT builders for the lifecycle diagram and the live map (kept for export and tests) |
| `src/live_map_html.py` | Read-only HTML flow charts shown in the app: the lifecycle diagram and the live provenance map |
| `src/discovery_host.py` | "Discovered on" machine name, stored as a sidecar record keyed by `passport_id` |
| `app.py` | Streamlit demo: Discover → Match → Suggest → Review → Link → Map |
| `tests/` | `test_acceptance.py` (6) · `test_local_discovery.py` (12) · `test_policies.py` (17) · `test_audit.py` (10) · `test_provenance_view.py` (14) · `test_live_map_html.py` (6) · `test_discovery_host.py` (5) |
| `fixtures/` | Mock MCP configs, a v2 config edit, runtime events, approvals, policies |

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
**Download Governance Audit Report** in the sidebar exports a timestamped ZIP,
`autodiscover-audit-<demo|local>-<UTC timestamp>.zip`. The **JSON** and **Markdown** buttons next to it
download the report alone.

| Entry | Contents |
|---|---|
| `report.json` | Summary counts, confirmed passports, approval coverage, metadata diffs, consented event links, `MISSING_APPROVAL` link IDs, incidents, policy findings, integrity and secret-scan verdicts |
| `report.md` | The same report as readable tables |
| `store/*.json` | The exact store files the report was built from |
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
The **05 Provenance Map** tab shows two diagrams, with no new dependency:

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

# All AutoDiscover tests (70): acceptance, real-machine discovery, policies, audit export, provenance views, discovered-on
python -m pytest tests/test_acceptance.py tests/test_local_discovery.py tests/test_policies.py \
                 tests/test_audit.py tests/test_provenance_view.py tests/test_live_map_html.py \
                 tests/test_discovery_host.py -v

# Live demo
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

## 90-second demo script

Start from **1-Click Reset Demo State** in the sidebar. Each tab shows a *Next demo step* hint.

| # | Click | Say |
|---|---|---|
| 1 | **01 Discover** → *Scan Local MCP Configs* | "Three MCP servers found. Secrets are redacted at discovery, and the disk check proves no raw secret value is stored." |
| 2 | **02 Identity & Review** → mint all three | "Owner and version are UNKNOWN because the config doesn't say. Both postgres-mcp tools share a name but have different provenance, so they get different IDs." |
| 3 | Sidebar → **Simulate Restart** | "All memory is wiped. The re-scan matched 3 of 3 tools to the same passport IDs, with zero duplicates." |
| 4 | Sidebar → **Load v2 Config Update** → **03 Drift & Governance** | "Someone edited filesystem-mcp. It's the same passport, but we flag the diff. The read-only evidence is gone, so access mode is now UNKNOWN until a human reviews it." |
| 5 | **04 Linking & Incidents** → pick `evt-0002` → *Link Event* without consent | "Linking is blocked without explicit consent." |
| 6 | Tick consent → *Link Event* | "Source and timestamp are recorded. MISSING_APPROVAL comes from approvals.json, and the incident shows a read-only tool tried to write." |
| 7 | Pick `evt-0003` → choose the filesystem-mcp passport → consent → *Link Event* | "You can't attach an event to the wrong tool." |
| 8 | **05 Provenance Map** | "Each row reads left to right: config file, tool, passport, event, incident. Everything joins by passport_id, and the passport itself never changes." |
| 9 | Sidebar → **Download Governance Audit Report** | "Integrity and secret scan both PASS. The report is tamper-evident and every file is checksummed." |

### Real local machine mode
Pick **Scan Real Local Machine** in the sidebar to run the same pipeline on your own computer. It's a
read-only scan of these well-known locations:

| OS | Claude Desktop | Cursor | VS Code |
|---|---|---|---|
| Windows | `%APPDATA%/Claude/claude_desktop_config.json` | `%USERPROFILE%/.cursor/mcp.json` | `%APPDATA%/Code/User/mcp.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` | `~/.cursor/mcp.json` | `~/Library/Application Support/Code/User/mcp.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` | `~/.cursor/mcp.json` | `~/.config/Code/User/mcp.json` |

- Paths are stored as the tokens above (`~`, `%APPDATA%`), and the home directory inside args is masked as
  `~`, so OS usernames never reach the inventory and passport IDs stay stable per location.
- VS Code's `"servers"` key is supported as well as `"mcpServers"`.
- Missing, unreadable, corrupt or non-object files are reported per location (`missing`, `unresolved`,
  `unreadable`, `invalid_json`, `invalid_format`) and never crash the scan. A machine with no configs
  returns a clean empty summary.
- Results go to an isolated registry in `data/local/`, which never touches the demo's `data/passports.json`.
- Each scan records which machine it ran on in `data/local/discovered_on.json`. This is context only, not an owner.
- Run Streamlit on your own computer for this mode. In a cloud container it correctly reports 0 configs.

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
| `runtime_events.jsonl` | `evt-0001` approved read, `evt-0002` unauthorized `fs.write_file`, `evt-0003` unapproved Cursor `pg.query` |
| `approvals.json` | `filesystem-mcp` approved for read actions only; Claude `postgres-mcp` pending; Cursor `postgres-mcp` has no record |

All credentials in the fixtures are fake (`FAKE_..._DO_NOT_USE`).

## Known limitations
- Env values are redacted *before* hashing, so rotating only a secret value doesn't register as drift.
  This is deliberate: we never store a derivative of a secret.
- Discovery currently reads the `mcpServers` JSON shape used by Claude Desktop and Cursor.
- Linking is idempotent per (`event_id`, `passport_id`).
