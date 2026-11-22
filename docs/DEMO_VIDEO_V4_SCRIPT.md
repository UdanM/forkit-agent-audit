# AutoDiscover demo video (v4): what's on screen and what to say

`autodiscover-demo-v4.mp4`, 1:23, silent, with chapters. Each moment starts on the full page, zooms in, and then
a gently pulsing amber box marks the thing to look at. When the **PAUSE** marker appears in the subtitle strip,
pause the video, say the lines below, and press play again.

Five pauses at about 15 seconds each, plus the 1:23 video, takes about 2:40.

---

## 1 · Discovery · PAUSE at 0:07

**Subtitles:** "AutoDiscover reads the AI tool settings on this computer." · "It finds three tools." ·
**"Passwords are removed before saving. Unknown facts stay UNKNOWN."**

**Say:**
> "We just read the AI tool settings on this laptop and found three tools. One of them had a database password
> in plain text, and we removed it before saving anything. Nobody wrote down who owns these tools, so we say
> UNKNOWN instead of guessing."

## 2 · ID cards · PAUSE at 0:24

**Subtitles:** "We restart the app and scan again." · "Same three IDs after the restart. Nothing is registered
twice." · **"Same name, different settings file: two separate tools, never merged."**

**Say:**
> "Every tool gets a permanent ID card, like a passport. We restarted and scanned again, and the same three IDs
> came back. These two are both called postgres-mcp, but they come from different apps and reach different
> databases, so they stay two separate tools."

## 3 · Out-of-policy action · PAUSE at 0:40

**Subtitles:** "This file tool may only read. Here it tries to write a report." · "We link the action to the
tool." · **"No approval on file: it becomes an incident, traced to its ID card and settings file."**

**Say:**
> "This tool was only allowed to read files, but it tried to write one. Nobody approved that, so it became an
> incident. And we can point to exactly which tool did it, and the settings file that set it up. People told us
> this link can't be made. Here it is."

## 4 · The map · PAUSE at 0:55 (highlight keeps pulsing for 10 s)

**Subtitles:** "Everything ends up on one map." · "Follow one row from left to right." · **"One row, left to
right: settings file, tool, ID card, action, incident."**

**Say** *(point along the highlighted row with your hand)*:
> "This is the whole system on one screen. Read this row from left to right: the settings file, the tool it
> created, its ID card, what it did, and the incident. If something goes wrong, you can follow it back to where
> it started."

## 5 · Ask Forkit · PAUSE at 1:12

**Subtitles:** "Anyone can ask a question in plain English." · "Who changed the Q3 report?" · **"Three records
answer it, each with its ID. Nothing is made up."**

**Say:**
> "You don't need to read the map. You can just ask: who changed the Q3 report? It answers from the saved
> records and shows their IDs as proof. One of them is a helper that another agent started. If there's no
> record, there's no answer."

## Closing · 1:18 to 1:23

**On screen:** "Forkit AI. A verifiable ID for every AI tool, model and agent, so companies can adopt AI and
still know who did what."

**Say (optional, or go straight to Impact):**
> "That's what Forkit is for: companies can let AI tools in and still know who did what."

---

## Re-making it

With the app running in demo mode on port 8501:

```bash
node scripts/demo_video/capture_stills.js stills
python scripts/demo_video/compose_v4.py stills out
```

`out/pauses.json` lists the pause times and chapters.
