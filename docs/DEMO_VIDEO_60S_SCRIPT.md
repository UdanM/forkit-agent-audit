# AutoDiscover demo for the projector: what to say

Made for a PowerPoint shown on a projector. The video has no sound. Each pause ends on a still frame with a
yellow **PAUSE** tag and one big caption. Press pause there, say the line, then press play.

Files (project folder `autodiscover/demo-video/v2/`):

- `autodiscover-projector-demo.mp4`: everything in one video, 1:03, with chapters
- `clip1-secrets.mp4`, `clip2-ids.mp4`, `clip3-incident.mp4`, `clip4-map.mp4`, `clip5-audit.mp4`: one clip per
  slide. Each clip ends on its pause frame, so if you set the clip to play automatically, it stops there on its own.

Each line is short enough to say in about 10 seconds.

| Clip | Time in full video | Caption on screen | Say |
|---|---|---|---|
| Intro | 0:00 | AutoDiscover | "AutoDiscover finds every AI tool on a computer and gives it an ID card." |
| 1 | 0:08 (still until 0:12) | Passwords hidden. Unknown stays UNKNOWN. | "We scanned the settings. The password is hidden before anything is saved. Nobody wrote down an owner, so it says UNKNOWN. We never guess." |
| 2 | 0:20 (still until 0:24) | Same name, different place: two tools | "After a restart, every tool keeps the same ID. These two share a name but live in different places, so they stay two separate tools." |
| 3 | 0:29 (still until 0:33) | Caught, and traced back to its source | "This tool may only read, but it tried to write. We catch it and trace it back to its ID card and the exact settings file." |
| 4 | 0:42 (still until 0:51) | Follow one action from settings to incident | "One picture of everything. Read left to right: where the tool is set up, its ID, what it did, and the problem it caused." |
| 5 | 0:56 (still until 1:00) | One-click audit report. Edits are detectable. | "Company rules are checked automatically. One click gives an audit report, and if anyone changes a single byte, the check fails." |
| End | 1:00 | Nothing is guessed. | "Every tool gets an ID card, every action gets a trail, and nothing is guessed." |

The full detailed recording (3:53) is still available as `autodiscover-guided-demo.mp4`
(`DEMO_VIDEO_SCRIPT.md`).

## Re-making it

With the app running in demo mode on port 8501:

```bash
node scripts/demo_video/capture_stills.js stills          # high-resolution screenshots of each step
python scripts/demo_video/compose_projector.py stills out  # zooms, captions, the 5 clips and the combined MP4
```

`out/check-1280.jpg` shows every pause frame at 1280 px wide, so you can check that it's readable.
