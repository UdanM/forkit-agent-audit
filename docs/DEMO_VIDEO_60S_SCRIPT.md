# AutoDiscover in 60 seconds: voiceover script

Video: `autodiscover-60s-demo.mp4` (1920x1080, 1:02, chapters, no audio). Five pauses, each with a one-line
caption and a yellow **PAUSE n/5** card. Play it, stop at each pause card, say the line (25 words or fewer), and
press play again. Spoken at a normal pace, the whole thing takes about 1.5 minutes with the stops.

The full, detailed version is still `autodiscover-guided-demo.mp4` (3:53, see `DEMO_VIDEO_SCRIPT.md`).

| Time | Pause | On screen | Say (max 25 words) |
|---|---|---|---|
| 0:00 | Intro | "AutoDiscover in 60 seconds" | "AutoDiscover finds every AI tool on a machine and gives it a passport. Watch." |
| 0:04 | **1/5** Secrets redacted, UNKNOWN stays UNKNOWN | Tools table (Owner UNKNOWN), `[REDACTED]` card, "Disk check: 0 of 2" | "The database password is redacted before anything is saved. Nobody wrote down the owner, so it says UNKNOWN. We never guess." (21) |
| 0:15 | **2/5** Same ID after restart, no false merges | "Restart recognized: 3 of 3", two `postgres-mcp` cards | "We wiped memory and rescanned: same three IDs. Two tools share a name but come from different configs, so they get two passports." (23) |
| 0:26 | **3/5** Unapproved action caught and linked | Incident card with "Parent passport · settings file" | "This read-only tool tried to write without approval. It becomes an incident, traced to its parent passport and the exact config file." (22) |
| 0:35 | **4/5** Provenance map (15 s) | The full map | "The whole system on one screen. Left to right: config file, tool, passport, action, incident. Follow the red row." (19) |
| 0:51 | **5/5** Policy-as-code and audit ZIP | Policy rule JSON and the audit ZIP ("checksums verified") | "Rules live in a reviewable JSON file. One click exports a checksummed audit ZIP, and it refuses to build if a secret would leak." (23) |
| 0:59 | Summary | "Every tool gets a passport…" | "Every tool gets a passport, every action gets a trail, and nothing is guessed." |

Re-record with `scripts/demo_video/record_short.js` (same steps as the README, then
`SPEED=0.22 python build.py frames autodiscover-60s-demo.mp4`).
