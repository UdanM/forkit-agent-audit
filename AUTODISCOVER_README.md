# AutoDiscover: Zero-Touch AI Onboarding & Passports

Forkit AI Hackathon submission. AutoDiscover finds the AI tools (MCP servers) configured on a machine,
turns them into a **metadata-only inventory** with **stable passport IDs**, flags **config drift**, and links
runtime events to passports **only with explicit consent**, recording missing approvals and
out-of-expected-behavior incidents along the way.

It is built on top of Forkit Core in this repository. Passport IDs come from Forkit Core's own
`forkit.domain.identity.compute_id`, and hashing uses `forkit.domain.hashing.HashEngine`.
The original project README is untouched in [`README.md`](README.md).

| File | Purpose |
|---|---|
| `src/engine.py` | Discovery, redaction, identity, diffing, consented linking, incident detection |
| `app.py` | Streamlit demo: Discover → Match → Suggest → Review → Link |
| `tests/test_acceptance.py` | 6 acceptance tests (the 3 required, the incident bonus, and 2 guardrail tests) |
| `fixtures/` | Mock Claude Desktop and Cursor MCP configs, a v2 config edit, runtime events, approvals |

---

## Core philosophy

### 1. Unknown stays UNKNOWN
MCP configs don't declare an owner or a version, so AutoDiscover stores the literal string `"UNKNOWN"`
and never fills in a plausible-looking guess. The same rule covers `client` (we only recognize known
config file names) and `access_mode`:

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
them `⚠ SAME NAME — DO NOT MERGE: Different Provenance`.

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
- Drift, approvals, event links and incidents live in **separate JSON records** joined by `passport_id`,
  following Forkit Core's identity spec (§2.1): application metadata stays outside the identity material.
- Writes are atomic (temp file plus `os.replace`), so an interrupted demo never corrupts the registry.

---

## Quickstart

Requires Python 3.10+. Run everything from the repository root.

```bash
pip install -r requirements.txt

# Acceptance tests (6 tests)
python -m pytest tests/test_acceptance.py

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

Start from **🧹 1-Click Reset Demo State** in the sidebar. Each tab shows a 🎬 *Next demo step* hint.

| # | Click | Say |
|---|---|---|
| 1 | **① Discover** → *Scan Local MCP Configs* | "Three MCP servers found. Secrets are redacted at discovery, and the disk check proves no raw secret value is stored." |
| 2 | **② Identity & Honesty Review** → mint all three | "Owner and version are UNKNOWN because the config doesn't say. Both postgres-mcp tools share a name but have different provenance, so they get different IDs." |
| 3 | Sidebar → **🔄 Simulate Restart** | "All memory is wiped. The re-scan matched 3 of 3 tools to the same passport IDs, with zero duplicates." |
| 4 | Sidebar → **📝 Load v2 Config Update** → **③** | "Someone edited filesystem-mcp. It's the same passport, but we flag the diff. The read-only evidence is gone, so access mode is now UNKNOWN until a human reviews it." |
| 5 | **④** → pick `evt-0002` → *Link Event* without consent | "Linking is blocked without explicit consent." |
| 6 | Tick consent → *Link Event* | "Source and timestamp are recorded. MISSING_APPROVAL comes from approvals.json, and the incident shows a read-only tool tried to write." |
| 7 | Pick `evt-0003` → choose the filesystem-mcp passport → consent → *Link Event* | "You can't attach an event to the wrong tool." |

### Out-of-order clicks are safe
Each step shows guidance if it's used too early: restarting before anything is minted, loading v2 before
`filesystem-mcp` has a passport (and a warning that minting on v2 makes v2 the baseline), opening ③ or ④
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
