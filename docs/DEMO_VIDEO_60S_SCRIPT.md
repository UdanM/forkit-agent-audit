# AutoDiscover demo video (v3): subtitles and pause points

No sound. Subtitles are burned in at the bottom of the video, in a plain dark strip that nothing shows through.
There's no opening title. It starts straight in the app. Each clip ends on a still frame you can pause on: the
picture stops moving and the subtitle explains it.

Files (project folder `autodiscover/demo-video/v3/`):

- `autodiscover-demo-v3.mp4`: everything in one video, 1:06, with chapters
- `clip1-discovery.mp4`, `clip2-id-cards.mp4`, `clip3-incident.mp4`, `clip4-map.mp4`, `clip5-ask.mp4`: one clip
  per slide. Each one ends on its pause frame.
- `readability-check-1280.jpg`: every pause frame at 1280 px wide

The five clips tell one story: a tool is found, gets an ID card, does something it shouldn't, is traced on the
map, and anyone can ask about it afterwards.

| Clip | Pause frame in full video | Subtitles (in order; the last one stays on the pause frame) | Add when paused (optional) |
|---|---|---|---|
| 1 Discovery | 0:07 to 0:12 | "AutoDiscover reads the AI tool settings on this computer." · "It finds three tools. The database password is removed before saving." · **"No owner is written down, so it says UNKNOWN. We never guess."** | "The disk check proves it: zero of two passwords anywhere in our files." |
| 2 ID cards | 0:20 to 0:25 | "Each tool gets an ID card. We restart the app and scan again." · "Same three IDs after the restart. Nothing is registered twice." · **"Same name, different settings file: two separate tools, never merged."** | "A name-only inventory would merge these two." |
| 3 Out-of-policy action | 0:30 to 0:35 | "This file tool may only read. Here it tries to write a report." · "We link the action to the tool, with the operator's consent." · **"No approval on file: it becomes an incident, traced to its ID card and settings file."** | "This is the link people told us can't be made." |
| 4 The map | 0:44 to 0:52 | "Everything ends up on one map." · "Read one row, left to right:" · **"One row, left to right: settings file, tool, ID card, action, incident."** | Point along the highlighted row. |
| 5 Ask Forkit | 0:58 to 1:03 | "Anyone can ask a question in plain English." · "Who changed the Q3 report? Three records, including a helper working for another agent." · **"Answers come only from saved records, with record IDs as proof."** | "It doesn't make anything up. No record, no answer." |
| End | 1:03 | "AutoDiscover. Every tool gets an ID card. Every action gets a trail. Nothing is guessed." | |

## Re-making it

With the app running in demo mode on port 8501:

```bash
node scripts/demo_video/capture_stills.js stills
python scripts/demo_video/compose_v3.py stills out
```

The older versions are still in the folder: the projector cut v2 (`compose_projector.py`) and the full 3:53
walkthrough (`DEMO_VIDEO_SCRIPT.md`).
