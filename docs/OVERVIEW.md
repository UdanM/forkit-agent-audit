# AutoDiscover in plain words

*A short guide for someone seeing this project for the first time.*

## The problem

AI assistants such as Claude Desktop, Cursor and VS Code can plug in extra tools called **MCP servers**.
One tool might read your files and another might talk to a database. These tools are added by editing a
settings file, so a company often can't answer basic questions about them:

- Which AI tools are installed on this computer?
- Is this the same tool we saw yesterday, or a new one?
- Did someone change its settings?
- What did it actually do, and was that allowed?

## What AutoDiscover does

AutoDiscover reads those settings files, lists every tool it finds, and gives each one a permanent ID
card called a **passport**. From then on, every change and every action is filed under that passport.

It follows four rules throughout:

- **It never guesses.** If the settings don't say who owns a tool or which version it is, the app shows
  `UNKNOWN`.
- **It never keeps secrets.** Passwords and keys are removed before anything is saved.
- **It never mixes up two tools.** Two tools with the same name in different files get two different
  passports.
- **A passport is never changed.** Edits, approvals and incidents are stored next to it, never inside it.

## The app, tab by tab

At the top of every tab there is a row of five numbers: tools found, passports issued, config changes
waiting for review, rule findings, and incidents.

| Tab | What you do | What it shows |
|---|---|---|
| **01 Discover** | Click **Scan Local MCP Configs** | The tools found in two example settings files. The database password shows as `[REDACTED]`, and a disk check confirms that no secret was saved. |
| **02 Identity** | Click **Single-Click Mint Stable ID** on each card | Each tool gets its passport. Owner and version show `UNKNOWN`. The two `postgres-mcp` tools are kept apart. |
| **03 Drift** | Turn on **Load v2 Config Update** in the sidebar | Someone edited a tool's settings. The tool keeps its passport, and the change is flagged for review. The company rules (`POL-001` to `POL-005`) list what needs attention and show their evidence. |
| **04 Linking** | Pick event `evt-0002`, tick the consent box, click **Link Event** | The action is filed under the right passport. A tool that may only read tried to write a file, so an **incident** is recorded. |
| **05 Map** | Just look | One page showing the whole chain: settings file, tool, passport, event and incident. |
| **06 Timeline** | Click **Save Log Checkpoint**, then **Simulate Log Tampering** | Which agent run did what and who handed it the work. A helper that was only allowed to read edited a protected file, and its own report says otherwise. When the log is edited afterwards, a critical finding appears. |

Event `evt-0004` in tab 04 names only `postgres-mcp`, which two settings files share. AutoDiscover will not
guess which one: it refuses the link and keeps the event in a review queue, with the actor marked UNKNOWN.

**Ask AutoDiscover**, under the title, answers questions such as "What needs review?" from the records,
and shows which records the answer comes from.

In the sidebar, **Simulate Restart** wipes the app's memory and scans again: the same tools get the same
passports. **Download Governance Audit Report** exports everything as a checked ZIP file, and
**Append-only Git archive** keeps every record in a Git history that cannot be rewritten unnoticed.

This version combines our project with our teammate's
[forkit-agent-audit](https://github.com/UdanM/forkit-agent-audit); see the README's merge section.

## How to try it

The code is at **https://github.com/eren-mutlu/forkit-autodiscover-hackathon**.

To run it on your own computer, you need Python 3.10 or newer:

```bash
git clone https://github.com/eren-mutlu/forkit-autodiscover-hackathon
cd forkit-autodiscover-hackathon
pip install -r requirements.txt
streamlit run app.py
```

Your browser opens the app. Start with **1-Click Reset Demo State** in the sidebar, then follow the grey
"Next in the demo" hint under each tab title. The whole walk-through takes about 90 seconds.

The demo uses example files that ship with the project. To scan your own computer, choose **Scan Real
Local Machine** in the sidebar. This is read-only, your username is hidden, and the results are kept apart
from the demo data.

To watch real agents at work, choose **Live agents (captured runs)** and click **Run Agent Scenarios**.
A small scripted agent reads and writes example files on this computer, and an n8n workflow does the same
in the cloud. Every action they take is captured and shown in tab 04, where you can file it under the right
passport. One agent tries to write where it may only read: the write is blocked and an incident is
recorded. See [`demo/agents/README.md`](../demo/agents/README.md).

## Glossary

| Word | Meaning |
|---|---|
| **MCP server** | A tool that an AI assistant can use, set up in a settings file. |
| **Config file** | The settings file where those tools are listed, for example `claude_desktop_config.json`. |
| **Passport** | The permanent ID card AutoDiscover gives each tool. |
| **Passport ID** | A long code calculated from where the tool is set up. The same tool always gets the same code. |
| **Provenance** | Where something comes from. Here that means the config file, the tool's name and its command. |
| **Drift** | A change to a tool's settings after its passport was issued. |
| **UNKNOWN** | A fact the settings don't state. AutoDiscover never fills it in by guessing. |
| **Redacted** | Hidden. Secret values are replaced with `[REDACTED]` before anything is saved. |
| **Rule finding** | Something a company rule flagged, such as "database tool without approval". |
| **Incident** | A tool did something outside what it is allowed to do. |
| **Consent** | Your explicit permission. An action is only filed under a passport after you tick the box. |
| **Forkit Core** | The open-source identity library this project is built on. Its `compute_id` function makes the passport ID. |
