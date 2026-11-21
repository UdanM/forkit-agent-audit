# AutoDiscover: 5-minute pitch plan

Follows the organizers' guide: **Hook → Your solution → Live demo → Impact & next steps**, then Q&A
(pre-finals with CTOs: 5 min pitch + about 15 min Q&A; finals with the jury: 5 min + 2 min).
The CTO workshop PDF adds five things the room will check. Each one is covered, marked ✔ below:
one sentence on who it helps, one result with a baseline, one architecture picture, one known limitation with a
failure case, and one concrete next step.

Total spoken time is about 4:45, which leaves 15 seconds of slack.

---

## 0:00 to 0:30 · Hook: make them feel the problem

> "Quick question. How many AI tools can touch your company's files right now?
> Not the ones IT approved. All of them: the Claude Desktop plugin someone added last week, the Cursor
> extension with a database password pasted into its settings, the helper agent that another agent started.
>
> Nobody knows. And when one of them changes a file it shouldn't, nobody can say which tool did it, or who
> set it up. That's the problem we solved."

Delivery: ask the question, then wait a full second before the next line.

## 0:30 to 1:15 · Your solution: what we built and why it matters

> "AutoDiscover finds every AI tool configured on a machine and gives each one a permanent ID card, built with
> Forkit Core. Every action a tool takes is linked back to that ID card, and to the settings file that created
> it.
>
> Three rules, enforced in code: we only read metadata, so passwords are removed before anything is saved. We
> never guess, so missing facts stay UNKNOWN. And we never merge two different tools just because they share a
> name.
>
> Here's why that matters. ✔ *(result with a baseline)* A name-only inventory of our sample machine sees **two**
> tools. There are really **three**, because two different setups are both called postgres-mcp. And when an
> agent writes its own activity log, it reports **one** file change. Our independent observer sees **two**. The
> one the agent left out is the dangerous one: a 'read-only' helper writing a report."

✔ *(who it helps)* One line to keep in your pocket: "It helps the security or platform lead at a company where
people install AI tools faster than anyone can review them."

## 1:15 to 3:45 · Live demo: one user flow, no feature tour

Show `autodiscover-demo-v4.mp4` (1:23, subtitled, silent; full spoken lines per pause in `docs/DEMO_VIDEO_V4_SCRIPT.md`). It's one story: **a tool is found → gets an ID card →
does something it shouldn't → is traced → anyone can ask about it.** Pause when the PAUSE marker appears (0:07, 0:24, 0:40, 0:55, 1:12),
say the lines, and press play. Five pauses at about 15 seconds each, plus 83 seconds of video, comes to about 2:40.

| Pause | Say (one sentence) |
|---|---|
| 1 · Discovery | "Three tools found. The password never reaches our disk, and the owner says UNKNOWN because nobody wrote it down." |
| 2 · ID cards | "After a restart, the same three IDs come back, and the two postgres tools stay two tools." |
| 3 · Incident | "The file tool is only allowed to read. It tried to write, so it became an incident, traced to its ID card and its settings file." |
| 4 · Map | "This is the whole system on one screen. Follow this row from settings file to incident." *(point along it)* |
| 5 · Ask Forkit | "And anyone can ask in plain English. The answer cites the records, so nothing is made up." |

✔ *(architecture picture)* If a CTO asks "how is it built?", the map in pause 4 is the architecture. Settings
file → tool → ID card → action → incident is exactly the data flow.

Plan B (the guide says it always goes wrong): the video *is* plan B for the live app. If the jury asks to see it
running, open forkit-autodiscover-hackathon.streamlit.app, click **Set up the demo in one click**, then open
**Map**. That takes about 20 seconds.

## 3:45 to 4:45 · Impact & next steps

> "What does this unlock? Before you let AI agents near company data, you need an inventory you can trust. This
> gives you one, plus proof of what each tool did.
>
> ✔ *(known limitation)* What it doesn't do yet: the ID depends on where a tool is configured. If someone
> **moves** a settings file, the tool gets a new ID card. We don't silently merge it, because that would be
> guessing. And when an action names only a tool, like 'postgres-mcp', which two setups share, we don't pick
> one. It waits for a person to review it.
>
> ✔ *(next step)* The next step for Forkit: run the scanner as a small agent on every employee laptop and send
> only metadata to the Forkit registry, so the security team sees every machine on one map. The code already
> scans the real config locations for Claude Desktop, Claude Code, Cursor, VS Code, Windsurf and Codex, and we
> ran it against real n8n agent workflows.
>
> Every tool gets an ID card. Every action gets a trail. Nothing is guessed. Thank you."

---

## Words to avoid (the coaches' bingo card)

Don't say "It works on my machine", "It's just a prototype", "It should scale", "We'll fix it later",
"As you can see", "Obviously" or "Next slide, please". If something is unfinished, say what is missing and what
you'd do about it.

## How to answer (from the guide)

1. Repeat the question. 2. Give the fact first, in one sentence. 3. If you don't know, say: "I don't know yet.
I'll check and send you the result."

---

## Q&A prep: pre-finals with CTOs (about 15 minutes)

| Likely question | Answer (fact first) |
|---|---|
| How is the ID computed? | "With Forkit Core's `compute_id`: a SHA-256 over the tool type, name, and a hash of the settings file path, tool name and command. Version and owner are UNKNOWN when not stated, and nothing machine-specific goes in, so the same tool gets the same ID on every machine." |
| What happens if the settings change? | "Same ID, and the change is recorded as drift for review. Arguments and environment are fingerprinted separately from the ID. If read-only is no longer proven, access drops to UNKNOWN." |
| Why not merge tools by name? | "Because two tools with the same name can have different credentials and permissions. In our sample, two 'postgres-mcp' tools point at different databases. Merging them would hide which one acted." |
| Do you store secrets? | "No. Values are replaced with [REDACTED] at discovery. Only key names are kept. The app then searches its own files for every raw value it saw: zero found. The audit export refuses to build if one would leak." |
| How do you know which tool did an action? | "From the event's source: the client, the settings file and the server name. Linking needs operator consent. A link to the wrong tool is refused, and an ambiguous one waits for review." |
| Why should we trust the activity log? | "We don't trust the agent's own report. An independent observer records what actually happened, and in our pilot it caught a write the agent left out. A saved checkpoint hash also detects if someone rewrites the log later." |
| What breaks if the input format changes? | "An unreadable settings file is reported as an error for that location, and the scan continues. Unknown fields are ignored, not guessed. A new client format needs one location entry and a parser." |
| What happens with 100 times more tools? | "Discovery is a linear pass over config files and the registry is keyed by ID, so it's not the bottleneck. We haven't load-tested beyond our fixtures and one real laptop. I'd measure it before promising anything." |
| How is it tested? | "539 automated tests, run with ruff in CI on Python 3.11. They include acceptance tests for each challenge rule and an end-to-end UI test of the demo flow." |
| Does it run on a real machine? | "Yes. **Scan this computer** reads the real Claude Desktop, Claude Code, Cursor, VS Code, Windsurf and Codex locations, plus installed AI command-line tools. It's read-only, and usernames are masked as `~`." |
| Is the Ask box an LLM? | "No. By default it matches questions against saved records and cites their IDs, so it can't make up an answer. Optionally, a local Ollama model can rephrase it, still using only those records." |
| What's policy-as-code? | "18 rules in a JSON file, for example 'mutating action without approval'. A security team can review them in a pull request. Nothing in the file is executed." |
| Where does the data live? | "Local JSON files next to the app, with approvals, events and incidents stored separately and joined by ID. There's an optional Git-backed audit archive, and a one-click ZIP with checksums." |
| What doesn't work yet? | "A moved settings file gives a new ID, an event without a settings path waits for a human, and we can't see tools that aren't declared in any config unless the observer captures them." |
| What would you build next? | "A lightweight agent per laptop that sends metadata to the Forkit registry, plus a suggestion when a moved config looks like an existing tool. It would be a suggestion only, never an automatic merge." |

## Q&A prep: finals with the jury (2 minutes)

Keep each answer to about 20 seconds. The three most likely questions:

1. **"What's new here?"** "Linking each action back to the exact tool and the settings file it came from,
   without guessing and without merging look-alikes."
2. **"Is it real or a mock?"** "Both run the same code. Demo mode uses sample settings files. Scan this
   computer reads the real ones, and we ran it with real n8n agent workflows."
3. **"What's the limitation?"** "Moving a settings file gives a new ID. We'd rather ask a person than guess."
