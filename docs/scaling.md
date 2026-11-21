# Scaling Forkit’s activity timeline

## Agent identity and review findings

A configured observer can include `deployment`, `agent_id`, and `instance_id`
on each `run_started` row. Together with the source, those fields make a
stable Forkit agent ID. Keep the identifiers stable across sessions and give
helpers or clones their own instance IDs. A name, location, shared MCP
connection, or overlapping file history cannot establish identity. A self
report cannot register an agent identity. Forkit keeps previously observed
identities in SQLite. Archive that database and keep the observer source path
stable to preserve continuity.

| Observer field | What Forkit can flag or explain |
| --- | --- |
| `label`, `location`, `access` | A name or location change under the same identity, or a change from `read_only` to `write` |
| `parent_id` on a run, `initiator_run_id` on a file action | Who delegated the work and which run performed the action, when both references resolve |
| `approval`, `onboarded`, `protected` | Missing or denied approval, unconfirmed onboarding, and changes to files explicitly marked protected |
| `sequence`, `observed_at` | Gaps or repeats in the observer’s numbering, or a timestamp difference greater than five minutes |

The timeline also flags writes recorded under a read-only run, well-known tool
config changes, and conflicts against saved log checkpoints. These findings
call for review. Clock drift, retries, stale permissions, and collector
outages can produce similar evidence. A restored or cloned deployment needs
a separate instance marker if the IDs it copied would otherwise be identical.
An observer must capture run lifecycles and actions where they happen to cover
short-lived helpers, actions through shared MCP servers, and logging gaps.
Forkit keeps unknown actors unassigned until stronger evidence appears.

## Ask Forkit

Ask Forkit can answer questions such as “What needs review?”, “Who edited
the status file?”, and “What tools did you discover?” and link to records.
With an existing local Ollama model, start Forkit with
`--ask-model YOUR_INSTALLED_MODEL` to enable short model-written answers.
The model receives a small set of selected evidence summaries over loopback.
Full diffs are excluded from model requests. If the model is unavailable or
returns invalid citation references, Forkit keeps the record-based answer.

A tool ID answers “Which installed tool?” A run ID answers “Which execution of that tool?” An action record answers “What happened, when, and who observed it?” A helper can use its parent's tool installation while executing under its own run ID. Keep the parent relationship visible so a reviewer can follow delegated work.

## Evidence to collect

| Evidence | What it can establish | What to show a reviewer |
| --- | --- | --- |
| Run and delegation events from a trusted launcher or orchestrator | A run started, and a recorded parent delegated to a child | “The editor run started a helper.” |
| File operations from an independent host or workspace monitor | An operation was observed and, when recorded, tied to a process | “The workspace monitor saw this process update the file.” |
| A tool's own activity log | What the tool reported about its work | “The tool reported this edit.” |
| Folder snapshots | A file changed between two scans | “This file changed between scans. We don't know who changed it.” |
| Approval records from the approval system | A particular decision covered a recorded action, scope, and time | “Approval recorded” or “Approval unknown.” |

Resolve run IDs within the account, workspace, and emitting service that issued them. To associate an observed process with a run, capture the host and process start identity when the run launches. Use process relationships and explicit delegation records to follow helpers. Compare overlapping folders and timelines to surface possible relationships for review. Similar paths, names, and write patterns alone are weak evidence.

## Failure cases to exercise

| Edge case | Detection and handling | Plain-language result |
| --- | --- | --- |
| A helper uses its parent's tool ID | Record each execution at launch and keep the recorded parent ID. When the monitor sees only the shared tool ID, or a helper uses the exact same run reference with no separate start, the available records cannot distinguish the helper. | “Same tool, separate runs” when a separate run was observed. |
| An agent changes or erases its own log | Compare incoming records and source sequence ranges against an independent collector and retained checkpoints. Open a review case for discrepancies. | “The current log differs from the saved record.” |
| Two installations share a name | Include installation and workspace evidence in the identity. Keep the records separate until there is evidence linking them. | “Two installations use the same name.” |
| Two runs edit the same file | Keep both observed actions. Use per-source order and event windows to group them for review. | “More than one run worked on this file.” |
| A human, sync service, build tool, or formatter changes a file | Use an observed process and recorded execution context when available. Snapshot-only changes have an unknown actor. | “This file changed. The source did not identify a run.” |
| A process ID is reused or a machine restarts | Scope the process ID to host, boot, and process start time or a collector-issued instance ID. | “This activity belongs to a later process.” |
| Clocks disagree or a source goes offline | Retain source time, received time, and sequence order. Surface uncertain ordering and outage windows. | “These changes were observed. Their order is uncertain.” |
| Delivery retries produce duplicate records | Deduplicate by source namespace and immutable event ID. Flag different payloads that share an event ID. | “One copy is shown” or “Two versions of this record disagree.” |
| A file is moved, linked, or shown under an alias | Use observed rename pairs and workspace-scoped file identity where available. Preserve the observed paths and mark uncertain matches. | “The monitor saw this file move” or “These paths may refer to one file.” |
| Atomic saves write to a temporary file and then rename it | Group observed operations linked by a rename while retaining the original records. Use grouping as a display aid. | “The editor saved the final file in two steps.” |
| An agent acts through a remote service or leaves the monitored folder | Record monitor coverage and use independently observed records from approved remote sources. | “Activity outside the monitored workspace is unknown.” |
| A collector falls behind or drops events | Detect missing sequence ranges, stale heartbeats, ingestion errors, or reported buffer loss. Mark the affected interval incomplete. | “Some activity may be missing between these times.” |
| A token or session moves across hosts | Correlate through verified orchestration events and preserve per-host process evidence. | “These runs are related across two machines.” |
| A role approves one action and an agent performs a different one | Check the approval’s action, target, allowed time, and issuer against the event. Keep approval and attribution as separate fields. | “The approval on file covers a different action.” |
| A tool performs an unexpected action through an approved workflow | Compare the observed action with an explicit task or allowed scope. Keep the observed event and any explanation together for review. | “The task called for reading. The monitor saw a write.” |

## Preserving evidence

Give agents no credentials to rewrite the evidence store. Run collection under a separate identity with narrowly scoped ingestion access. Use an append-only ingestion path and read-only access for reviewers. Retain independently stored checkpoints, such as signed digests over accepted batches and sequence ranges, so later comparisons can uncover changes to records after collection. A digest comparison establishes a difference from the checkpoint. It cannot identify who caused it, recover events never collected, or prove that the underlying activity was observed accurately. Monitor coverage, collector health, and the integrity of checkpoint storage need their own evidence.

Preserve raw evidence according to access and retention controls. The review interface can show time, source, operation, relative path, and the reason for an attribution. Redact home directory names and sensitive paths where possible. Avoid collecting file contents, full command lines, secrets, and prompts for routine attribution. Apply workspace separation to both ingestion and review. [NIST Guide to Computer Security Log Management](https://csrc.nist.gov/pubs/sp/800/92/final)

## Reviewing at scale

Start with one sentence per event, then let a reviewer expand its evidence and compare runs side by side. Group related file changes by folder and run while retaining per-file records for drill-down. Distinguish “Observed by the monitor,” “Reported by the tool,” and “Who acted is unknown.” A reviewer decision should preserve the chosen outcome, rationale, and evidence available at the time. Corrections can be added as new decisions so later reviewers can see how an attribution changed.

## Try the local evidence inputs

Start a live session from the project folder. Each log is an existing JSON Lines file, with one event object per line. The prototype reads the inputs. A separate collector must supply the records.

~~~powershell
python app.py --live --audit-log .\workspace-audit.jsonl --agent-log .\tool-reported.jsonl --watch-dir .\reports --port 0 --open
~~~

Both logs and watched folders are optional. Repeat `--watch-dir` to compare snapshots for multiple folders. A first snapshot sets the baseline. A later snapshot compares file paths, sizes, and modification times to find net changes. An edit followed by a reversal between scans can be missed. Snapshot changes leave the actor unknown. Supply only folders you intend to include in the review.

Each JSON Lines record requires an event `id`, an ISO timestamp in `time`, and a `kind` of `run_started` or `file_write`. A run declaration includes `run_id` and a registered tool ID in `tool`. It can include a display `label` and a `parent_id`. A file record includes the relative `path`, a `run_id` when known, and an optional `operation` such as `write`, `create`, `delete`, or `rename`. Run references match declarations in the same stream. See [the sample timeline](../fixtures/timeline.jsonl) for concrete records. The sample uses demo installation labels where a live record would use a registered tool ID.

Keep the audit checkpoint in a trusted location separate from the agent. To generate the argument from a trusted copy of the audit log, use the original byte length and SHA-256 digest.

~~~powershell
$base = Get-Item .\trusted-audit-copy.jsonl
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $base.FullName
$checkpoint = "$($base.Length):$($hash.Hash)"
python app.py --live --audit-log .\workspace-audit.jsonl --audit-checkpoint $checkpoint --port 0 --open
~~~

The check covers the original byte prefix. Later appended bytes remain unverified. The audit input and checkpoint are local evidence for the review. Their trust depends on how they were collected and who can change them.

The same checkpoint can be generated with `python timeline.py .\trusted-audit-copy.jsonl`. Keep watched folders separate from Forkit's database so writes from Forkit itself stay outside the folder comparison.

For a Windows pilot, Microsoft Sysmon provides process creation identifiers and selected file events. Measure coverage before linking a monitor event to an AI run, especially when several runs share one process. [Microsoft Sysmon event reference](https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon)
