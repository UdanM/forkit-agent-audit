# AutoDiscover guided demo video: narration script

The video (`autodiscover-guided-demo.mp4`, 1920x1080, 3:53, no audio) is a recorded run of the app in demo
mode. Instead of a live demo, play it and **pause at the yellow "PAUSE HERE" badge** to talk. Every key screen
already carries a caption bar and a highlight box, so the slide still makes sense if you say nothing.

The MP4 has chapters (Intro, Pause 1 to 5, Summary), so most players can jump straight to a pause point.

**Timing for a 5-minute slot.** The video runs 3:53. Pause only at the five starred moments (★) for about
10 to 15 seconds each and you land near 5:00. Talk over the rest while it plays.

---

## Intro · 0:00

> On screen: title card, then the empty app.

**Say:** "AutoDiscover finds every AI tool configured on a machine, gives each one a permanent passport, and
ties every action it takes back to that passport. Three rules: metadata only, never guess, never merge
two different tools. This is a recording, so no Wi-Fi surprises. I'll pause at the important moments."

---

## Pause 1 · Metadata-only discovery · 0:07

| Time | On screen | Say |
|---|---|---|
| 0:09 | The raw Claude Desktop config, with a (fake) database password in `POSTGRES_URL` | "This is what a real settings file looks like. There's a password sitting in plain text." |
| 0:16 | Scan result: 3 tools. Owner and Access are UNKNOWN | "We read the file and find three tools. Nobody wrote down who owns them, so the owner is UNKNOWN. We don't guess. Access is read-only only where the config proves it, with the `ro` mount." |
| 0:24 | More details: Version and Enabled are UNKNOWN | "Same rule for version and enabled." |
| ★ 0:29 | "Secrets stripped at discovery" with `[REDACTED]`, and "Disk check: 0 of 2" | "The password became `[REDACTED]` before anything was saved. Then the app searches its own files for the raw values it saw: zero found. That's proof, not a promise." |

---

## Pause 2 · Persistent ID and no false merges · 0:35

| Time | On screen | Say |
|---|---|---|
| 0:42 | "How the passport ID is calculated" | "The ID is built with Forkit Core's `compute_id` from where the tool is configured: the settings file, the tool name and the command. No username, machine name or timestamp. So it's the same ID on every computer, every time." |
| ★ 0:54 | Two `postgres-mcp` passports highlighted side by side | "Two tools have the same name, `postgres-mcp`, one in Claude Desktop and one in Cursor. A naive inventory would merge them. We give them two different passports, because they come from two different places." |
| ★ 1:03 | "Restart recognized … 3 of 3 … no duplicates" | "Now we wipe the app's memory and scan again. All three tools come back with the same IDs. Nothing is registered twice." |
| 1:13 | "Settings changed: filesystem-mcp" with SAME PASSPORT ID | "Someone edits a tool's settings. It's still the same tool, so it keeps its passport. The change is recorded and waits for review. Access drops to UNKNOWN because read-only is no longer proven." |

---

## Pause 3 · Out-of-expected behavior and lineage · 1:21

| Time | On screen | Say |
|---|---|---|
| 1:29 | Event evt-0002: `fs.write_file` by filesystem-mcp | "Here's a runtime event: the filesystem tool tried to write a file. It's only approved for reads." |
| 1:34 | "Blocked … requires explicit user_consent" | "Linking an action to a tool needs explicit consent. No consent, nothing is written." |
| 1:41 | Audit record: NO APPROVAL ON FILE, POL-005 | "With consent, the action is linked and flagged: no approval on file, and policy POL-005, mutating action without approval." |
| ★ 1:46 | Incident card: **Parent passport** filesystem-mcp · 9b360ff7 · `fixtures/claude_desktop_config.json` | "And this is the part people told us can't be done. The incident is traced back to its parent passport and the exact settings file that declared the tool: event, then passport, then config. Every action has an owner on record." |
| 2:03 | "Blocked … Event is from 'postgres-mcp', passport is 'filesystem-mcp'" | "It also refuses false links: you can't pin a Postgres action on the filesystem tool, even with consent." |
| ★ 2:09 | Activity timeline: "Report helper … delegated by Report assistant" | "Agents delegate. A helper process wrote to a protected file, and an independent observer ties it back to the agent that delegated the work. If we can't tell who did something, it says UNKNOWN." |

---

## Pause 4 · Visual provenance map · 2:16

| Time | On screen | Say |
|---|---|---|
| 2:22 | "In plain words": filesystem-mcp has 1 INCIDENT | "If you only read one thing, read this: one line per tool, worst news first." |
| ★ 2:26 to 2:48 | The full map (held 22 seconds) | "Read each row left to right: settings file, tool, passport, runtime event, incident, open checks. Follow the red row: Claude Desktop settings, filesystem-mcp, its passport, the write with no approval, the incident. The two `postgres-mcp` rows stay separate. That's the whole system on one screen." |

Tip for the non-technical audience: point with your hand along the red row while it is paused.

---

## Pause 5 · Our extras (beyond the brief) · 2:48

| Time | On screen | Say |
|---|---|---|
| ★ 2:55 | Policy rules on a passport card, and `fixtures/policies.json` | "Extra one: policy-as-code. 18 rules live in a JSON file a security team can review in a pull request. Nothing in it is executed." |
| 3:08 | Activity timeline: "Log REWRITTEN since checkpoint" | "Extra two: tamper evidence. We saved a checkpoint, then someone rewrote an old log line. The app catches it immediately." |
| 3:17 | The downloaded audit ZIP: file list, "SHA256SUMS: 11/11 files verified" | "Extra three: one-click audit export. Report, registry and inputs, with checksums. The export refuses to build if a raw secret would leak." |
| 3:30 | "Scan this computer": well-known config locations | "Extra four: it scans a real machine, read-only: Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Codex, plus installed AI CLIs. Usernames become `~`." |
| 3:36 | Tools found on the recording machine | "Here it found MCP servers in Cursor, VS Code and Codex, and the `claude` CLI. A CLI gets its own passport type, so it never merges with a server of the same name." |

Note: the recording machine was set up with sample Cursor, VS Code and Codex configs (fake token included)
so this screen has something to show. The scan itself is the real code path. To show your own laptop
instead, run the app locally and pick **Scan this computer** in the sidebar.

---

## Summary · 3:43

> On screen: "Every tool gets a passport. Every action gets a trail. Nothing is guessed."

**Say:** "Metadata only, persistent IDs, no false merges, incidents with lineage, and a map anyone can read.
Try it at forkit-autodiscover-hackathon.streamlit.app."

---

## One-line versions (if the jury cuts you short)

1. "Secrets are stripped before saving, and anything not written down stays UNKNOWN."
2. "Same tool, same ID across restarts and machines; same name from different configs, different IDs."
3. "An unapproved write becomes an incident linked to its parent passport and settings file."
4. "One map: config, tool, passport, event, incident, left to right."
5. "Plus policy-as-code, tamper-evident logs, a verified audit ZIP and a real machine scan."

## Re-recording

The recorder lives in `scripts/demo_video/`. See its README. It drives the app with a headless browser,
adds the captions and highlights, and builds the MP4 with ffmpeg.
