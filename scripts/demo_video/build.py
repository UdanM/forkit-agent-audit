"""Turn the recorder's screencast frames into an MP4 with chapters.

Usage: python build.py <frames_dir> <out.mp4>   (reads <frames_dir>/../timeline.json)

Time outside the caption holds and title cards is played at SPEED (default 0.5, so clicks and
scrolling run twice as fast); holds keep real time so they can be read and paused on.
"""

import json
import os
import subprocess
import sys

frames_dir, out = sys.argv[1], sys.argv[2]
with open(os.path.join(frames_dir, "..", "timeline.json")) as fh:
    tl = json.load(fh)
frames = tl["frames"]
t0 = frames[0]["t"]
t_end = tl["tEnd"]
SPEED = float(os.environ.get("SPEED", "0.5"))
windows = sorted((m["t"], m["t"] + m["s"]) for m in tl.get("marks", []))


def warp(t: float) -> float:
    """Seconds of output video between the first frame and recording time t."""
    total, cur = 0.0, t0
    for a, b in windows:
        if a >= t:
            break
        a2, b2 = max(a, cur), min(b, t)
        if a2 > cur:
            total += (a2 - cur) * SPEED
        if b2 > a2:
            total += b2 - a2
        cur = max(cur, b2)
    if t > cur:
        total += (t - cur) * SPEED
    return total


with open("concat.txt", "w") as fh:
    for i, fr in enumerate(frames):
        nxt = frames[i + 1]["t"] if i + 1 < len(frames) else t_end
        fh.write(f"file '{os.path.abspath(fr['file'])}'\nduration {max(warp(nxt) - warp(fr['t']), 0.001):.4f}\n")
    fh.write(f"file '{os.path.abspath(frames[-1]['file'])}'\n")

chapters = tl["chapters"]
with open("chapters.txt", "w") as fh:
    fh.write(";FFMETADATA1\ntitle=AutoDiscover guided demo\n")
    for i, c in enumerate(chapters):
        start = warp(c["t"])
        end = warp(chapters[i + 1]["t"]) if i + 1 < len(chapters) else warp(t_end)
        fh.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(start * 1000)}\nEND={int(end * 1000)}\ntitle={c['title']}\n")
        print(f"{int(start // 60)}:{int(start % 60):02d}  {c['title']}")

subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
     "-i", "chapters.txt", "-map_metadata", "1", "-map_chapters", "1",
     "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
     "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "stillimage",
     "-movflags", "+faststart", out],
    check=True,
)
print("total", round(warp(t_end), 1), "s")
with open(out + ".json", "w") as fh:
    json.dump({"chapters": [(c["title"], round(warp(c["t"]), 1)) for c in chapters],
               "holds": [(round(warp(m["t"]), 1), m["s"]) for m in tl.get("marks", [])]}, fh, indent=1)
