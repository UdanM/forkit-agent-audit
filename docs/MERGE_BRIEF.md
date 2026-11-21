# AutoDiscover: technical merge brief

*For a developer who built a similar project and wants to combine the best parts of both. Everything here
describes the code at the time of writing; file and function names are exact.*

Repo: https://github.com/eren-mutlu/forkit-autodiscover-hackathon (branch `main`). The project is built on
top of the Forkit Core repository; the original Forkit README is in [`README.md`](../README.md).

## 1. Architecture and module map

```
fixtures/*.json ─┐                         ┌─ data/passports.json          (immutable registry)
real MCP configs ┼─> src/engine.py ────────┼─ data/metadata_changes.json   (drift)
                 │   discovery, redaction, ├─ data/linked_events.json      (consented links)
runtime events ──┘   identity, diff, link, ├─ data/incidents.json
                     policies, audit       ├─ data/policy_findings.json
                                           └─ audit ZIP (report.json, report.md, store/*, inputs/*, SHA256SUMS)
app.py (Streamlit) ── reads/writes only through AutoDiscoverEngine; views in src/live_map_html.py
```

| File | Role | Size |
|---|---|---|
| `src/engine.py` | All logic. Pure functions plus the `AutoDiscoverEngine` class, which owns one `data_dir`. | ~1,190 lines |
| `src/live_map_html.py` | Read-only HTML views: `lifecycle_html` and `live_map_html` (shown in tab 05). | ~270 |
| `src/provenance_view.py` | Read-only Graphviz DOT builders (`lifecycle_dot`, `live_map_dot`), plus `load_store`. Kept for export and tests. | ~280 |
| `src/discovery_host.py` | "Discovered on" machine name, stored as a sidecar keyed by `passport_id`. | ~75 |
| `app.py` | Streamlit UI: five tabs, sidebar metrics, audit download. No business logic. | ~1,000 |
| `src/capture/` | Live capture: `mcp_tap.py` (stdio MCP proxy, enforces `--read-only`), `collector.py` (token-checked HTTP), `events.py` (metadata-only events). | ~370 |
| `demo/agents/` | Live-agent demo: configs, synthetic workspace, approvals, scripted LangGraph agent, n8n workflow, `run_scenarios.py`. | |
| `fixtures/` | Demo configs (`claude_desktop_config.json`, its edited `_v2`, `cursor_mcp.json`), `runtime_events.jsonl`, `approvals.json`, `policies.json`. | |
| `forkit/` | Forkit Core (upstream). We call `forkit.domain.identity.compute_id` and `forkit.domain.hashing.HashEngine`. | |

Main engine entry points: `discover_tools`, `scan_local_machine`, `derive_passport_id`,
`AutoDiscoverEngine.mint_passport`, `.link_event`, `.evaluate_policies`, `.build_audit_report`, and
`build_audit_bundle`.

## 2. Key design decisions and guardrails

**Identity.**
- `derive_passport_id(source_path, tool_name, command)` calls Forkit Core's
  `compute_id(passport_type="mcp_server", name, version="UNKNOWN", creator_name="UNKNOWN", artifact_hash=…)`.
  The `artifact_hash` is a provenance hash over `source_path`, `tool_name` and `command`.
- `source_path` is stored relative to a base dir, or as a token such as `~/.cursor/mcp.json` on real
  machines, so IDs don't depend on the machine or the user.
- Args and env keys go into a separate `config_hash`. Editing them is **drift on the same passport**, not a
  new tool.
- The machine name ("discovered on") is deliberately **not** an ID input.

**Unknown stays UNKNOWN.**
- Owner and version are always `"UNKNOWN"`, because MCP configs don't declare them.
- `access_mode` is `read-only` only with proof: a `ro`/`readonly` mount, `host:container:ro`, or a
  read-only flag (see `_access_mode`). Otherwise it is `UNKNOWN`, never assumed `read-write`.
- A server counts as a database only when its command or package says so (`category_evidence`).

**Redaction.**
- `_redact_mapping` and `_redact_args` replace env values and secret-looking args with `[REDACTED]`
  **before** anything is hashed or written.
- On real machines the home directory is masked as `~` in command, args and URL.

**Immutable passports.**
- `mint_passport` never overwrites an existing passport.
- Approvals (`fixtures/approvals.json`), drift, links, incidents and findings are separate files joined by
  `passport_id`, following Forkit's identity spec §2.1. All writes are atomic (`_write_json`).

**Consented linking.**
- `link_event` raises `PermissionError` without `user_consent=True`, `KeyError` for an unknown passport,
  and `ValueError` when the event's source names another tool or file. No false attribution is possible.
- Approval status is checked against `approvals.json` (`APPROVED` or `MISSING_APPROVAL`).
- A mutating call (`write`, `delete`, …) from a proven read-only tool records an
  `OUT_OF_EXPECTED_BEHAVIOR` incident. Re-linking the same event doesn't create a duplicate.

**Policy as code.**
- `fixtures/policies.json` holds declarative rules `POL-001` to `POL-005`.
- `load_policies` accepts only an allowlist of conditions (`category_in`, `approval`, `field_is_unknown`,
  `access_mode_transition`, `action_mutating`), so nothing in the file is executed.
- Every finding carries its evidence.

**Audit bundle.**
- `build_audit_report` re-derives every passport ID (integrity PASS/FAIL) and scans every store file for
  raw secret values, URL passwords and token prefixes.
- `build_audit_bundle` refuses with `PermissionError` on any leak, and the UI then hides the download.
- The report carries `report_sha256` (sha256 over the sort_keys JSON without that field). The ZIP also
  has `SHA256SUMS`.

**Real machine scan.**
- `known_config_locations` covers Claude Desktop, Cursor and VS Code on Windows, macOS and Linux.
- The scan reads both the `mcpServers` and the `servers` (VS Code) shapes.
- A bad file is reported per location (`missing`, `unreadable`, `invalid_json`, …) and never crashes the
  scan.
- Results go to an isolated `data/local/`.

## 3. Tests and CI

- **AutoDiscover tests (83):** `test_acceptance.py` (6), `test_local_discovery.py` (12),
  `test_policies.py` (17), `test_audit.py` (10), `test_provenance_view.py` (14),
  `test_live_map_html.py` (6), `test_discovery_host.py` (5), `test_capture.py` (11) and
  `test_agent_scenarios.py` (2). Every test runs in `tmp_path`.
- **Full repository suite:** `pytest` runs 459 tests, including Forkit Core's own.
- **CI (`.github/workflows/ci.yml`, Python 3.11):** `ruff check .`, then `pytest`, then a wheel and sdist
  build, plus a web lint and build job. CI is green. Run `ruff check .` before pushing, and avoid
  f-string syntax that only works on Python 3.12.

## 4. Strongest parts, worth keeping in a merge

1. **Deterministic, machine-independent passport IDs** through Forkit Core's `compute_id`, with a clean
   split between identity (where the tool is configured) and drift (how it is configured).
2. **Redaction before hashing**, plus a fail-closed leak scan on export.
3. **Consent-gated linking** with source checks, so events can't be attributed to the wrong tool.
4. **Allowlisted policy-as-code**, with evidence on every finding.
5. **A tamper-evident audit ZIP** that a reviewer can verify with three lines of Python (see the README).
6. **Real-machine scan on three OSes**, with injectable `platform`, `home` and `env`, so it is fully
   testable on any OS.

## 5. Known weak spots and limits

- **Live capture is proxy-based.** The MCP tap captures stdio servers it is placed in front of, and the
  collector captures agents that report to it (the n8n demo). There is no hook inside closed clients, and
  remote (URL) MCP servers are not tapped. The default demo tab still uses fixture events.
- **Approvals are a fixture file.** There is no in-app approve or reject workflow, and no user or role
  model.
- **Storage is local JSON.** Writes are atomic, but there is no locking or database. The hosted demo can
  give each visitor their own folder with `AUTODISCOVER_PER_SESSION=1`.
- **Rotating only a secret value is not drift**, because env values are redacted before hashing. This is
  deliberate.
- **`access_mode` only recognises Docker-style read-only evidence and flags.** Other permission models
  read as `UNKNOWN`.
- **`app.py` is one large Streamlit file.** The UI isn't unit-tested; it is checked with a Playwright
  click-through.
- **Only the stdio-style `command` field feeds identity.** URL-only (remote) servers are discovered, but
  their URL isn't part of the passport ID.

## 6. Clean extension and integration points

| To add… | Plug in at |
|---|---|
| A new MCP client or config location | `_LOCATIONS` in `src/engine.py`; `_candidates_from_config` for a new JSON shape. |
| Real runtime events | Already captured by `src/capture/` (see `demo/agents/README.md`). Other sources: POST to the collector, or feed dicts shaped like `runtime_events.jsonl` lines into `AutoDiscoverEngine.link_event(event, passport_id, user_consent=True)`. |
| A new rule | Add a JSON rule in `fixtures/policies.json`. A new *condition type* needs an evaluator next to `_eval_passport`, `_eval_change` or `_eval_event`, plus an allowlist entry in `load_policies`. |
| A different store (SQLite, API) | `AutoDiscoverEngine` reads and writes only through `_read_json` and `_write_json` on its `data_dir`, so swap those. |
| Another UI or a CLI | Use `AutoDiscoverEngine` directly; `app.py` holds no logic. |
| Extra context per tool | A sidecar keyed by `passport_id`, like `src/discovery_host.py`. Never add it to the ID inputs. |

## 7. Questions to compare against your project

- [ ] How do you derive a tool's identity? Does it survive a restart and a reinstall, and is it the same on
      every machine?
- [ ] What happens when two tools share a name? Do you ever merge them?
- [ ] Do you store or hash any secret value, even indirectly?
- [ ] How do you treat facts the config doesn't state: guessed, defaulted, or UNKNOWN?
- [ ] Is a config edit a new tool or drift on the same one?
- [ ] Do you capture real runtime events? From which client, and how?
- [ ] Is linking an event to a tool consent-gated and source-checked?
- [ ] Are your policies data or code? Can a policy file execute anything?
- [ ] Can a reviewer verify your export independently (hashes, integrity re-derivation)?
- [ ] Which OSes and clients does your scanner cover, and how is it tested?
- [ ] What's your storage, and is it safe for several users at once?
- [ ] What do you have that we don't? That list is the merge plan.
