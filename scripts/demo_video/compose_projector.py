"""Builds the projector cut from capture_stills.js output: slow zooms, big captions, clean freezes.

Usage: python compose_projector.py <stills_dir> <out_dir>
Writes clip1..clip5 (one per pause), autodiscover-projector-demo.mp4 (all, with chapters) and a
contact sheet at 1280 px wide for a readability check.
"""

import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
VIEW_H = 890                       # picture area; the caption band sits below it
ASPECT = W / VIEW_H
DPR = 2
BG = (9, 12, 24)
LAV = (190, 175, 255)
YEL = (255, 210, 122)
FONT = "/usr/share/fonts/opentype/inter/"
f_cap = ImageFont.truetype(FONT + "Inter-Bold.otf", 60)
f_tag = ImageFont.truetype(FONT + "Inter-ExtraBold.otf", 30)
f_t1 = ImageFont.truetype(FONT + "InterDisplay-ExtraBold.otf", 110)
f_t2 = ImageFont.truetype(FONT + "Inter-SemiBold.otf", 44)
f_t3 = ImageFont.truetype(FONT + "Inter-Bold.otf", 30)

stills, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
beats = {b["id"]: b for b in json.load(open(os.path.join(stills, "beats.json")))}
imgs = {k: Image.open(b["file"]).convert("RGB") for k, b in beats.items()}
MAIN = [316, 0, 1124, 900]


def fit(rect, pad=28, min_w=720):
    """CSS rect -> source-pixel crop box (x, y, w, h) with the picture-area aspect, clamped to the still."""
    x, y, w, h = rect
    x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
    cx, cy = x + w / 2, y + h / 2
    w = max(w, min_w, h * ASPECT)
    h = w / ASPECT
    sw, sh = 1440, 900
    w, h = min(w, sw), min(h, sh)
    x = min(max(cx - w / 2, 0), sw - w)
    y = min(max(cy - h / 2, 0), sh - h)
    return [v * DPR for v in (x, y, w, h)]


def ease(t):
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return [p + (q - p) * t for p, q in zip(a, b, strict=True)]


def view(img_id, box, hl=(), hl_a=0.0):
    """Render the crop of a still into the picture area, with highlight boxes faded in by hl_a."""
    x, y, w, h = box
    im = imgs[img_id].crop((round(x), round(y), round(x + w), round(y + h))).resize((W, VIEW_H), Image.LANCZOS)
    if hl and hl_a > 0:
        s = W / w
        dim = Image.new("L", (W, VIEW_H), int(110 * hl_a))
        d = ImageDraw.Draw(dim)
        rects = []
        for r in hl:
            rx, ry, rw, rh = [v * DPR for v in r]
            rr = [(rx - x) * s - 10, (ry - y) * s - 10, (rx + rw - x) * s + 10, (ry + rh - y) * s + 10]
            rects.append(rr)
            d.rounded_rectangle(rr, 18, fill=0)
        im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, dim)
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for rr in rects:
            od.rounded_rectangle(rr, 18, outline=YEL + (int(255 * hl_a),), width=6)
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    return im


def band(text, tag=None):
    b = Image.new("RGB", (W, H - VIEW_H), (12, 15, 34))
    d = ImageDraw.Draw(b)
    d.rectangle([0, 0, W, 4], fill=LAV)
    x = 70
    if tag:
        tw = d.textlength(tag, font=f_tag)
        d.rounded_rectangle([x, 60, x + tw + 44, 128], 14, fill=YEL)
        d.text((x + 22, 94), tag, font=f_tag, fill=(26, 20, 0), anchor="lm")
        x += tw + 44 + 40
    d.text((x, 96), text, font=f_cap, fill=(255, 255, 255), anchor="lm")
    return b


def frame(pic, cap, tag=None):
    f = Image.new("RGB", (W, H), BG)
    f.paste(pic, (0, 0))
    f.paste(band(cap, tag), (0, VIEW_H))
    return f


def title(k, t, s, foot=""):
    f = Image.new("RGB", (W, H), BG)
    g = Image.new("RGB", (W, H), (42, 33, 96)).filter(ImageFilter.GaussianBlur(1))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([-500, -700, 1300, 700], fill=150)
    f = Image.composite(g, f, mask.filter(ImageFilter.GaussianBlur(220)))
    d = ImageDraw.Draw(f)
    d.text((150, 330), k, font=f_t3, fill=LAV)
    y = 390
    for line in t.split("\n"):
        d.text((150, y), line, font=f_t1, fill=(255, 255, 255))
        y += 125
    d.text((150, y + 30), s, font=f_t2, fill=(205, 207, 235))
    if foot:
        d.text((150, 980), foot, font=f_t3, fill=(150, 152, 185))
    return f


class Clip:
    def __init__(self):
        self.frames = []          # list of (image, count)

    def hold(self, img, sec):
        self.frames.append((img, max(1, round(sec * FPS))))

    def fade(self, a, b, sec):
        n = round(sec * FPS)
        for i in range(n):
            self.frames.append((Image.blend(a, b, ease((i + 1) / n)), 1))

    def zoom(self, img_id, b0, b1, sec, cap, tag=None):
        n = round(sec * FPS)
        for i in range(n):
            self.frames.append((frame(view(img_id, lerp(b0, b1, ease((i + 1) / n))), cap, tag), 1))

    def hl_in(self, img_id, box, hl, sec, cap, tag=None):
        n = round(sec * FPS)
        for i in range(n):
            self.frames.append((frame(view(img_id, box, hl, (i + 1) / n), cap, tag), 1))

    @property
    def last(self):
        return self.frames[-1][0]

    def seconds(self):
        return sum(c for _, c in self.frames) / FPS


def write(clips, path, chapters=None):
    meta = None
    if chapters:
        meta = path + ".chapters.txt"
        with open(meta, "w") as fh:
            fh.write(";FFMETADATA1\ntitle=AutoDiscover projector demo\n")
            for name, s, e in chapters:
                fh.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(s * 1000)}\nEND={int(e * 1000)}\ntitle={name}\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if meta:
        cmd += ["-i", meta, "-map_metadata", "1", "-map_chapters", "1"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for c in clips:
        for img, n in c.frames:
            raw = img.tobytes()
            for _ in range(n):
                p.stdin.write(raw)
    p.stdin.close()
    p.wait()


def wide(img_id):
    """Top of the main column at full column width (no sidebar)."""
    return fit([MAIN[0], 0, MAIN[2], 1], pad=0)


# ── Intro / outro ──
intro = Clip()
intro.hold(title("FORKIT AI HACKATHON", "AutoDiscover", "Every AI tool gets an ID card. Every action gets a trail."), 2.5)
outro = Clip()
outro.hold(title("AUTODISCOVER", "Nothing is guessed.", "Every tool gets an ID card. Every action gets a trail.",
                 "Try it: forkit-autodiscover-hackathon.streamlit.app"), 3.0)

# ── Pause 1 ──
c1 = Clip()
b = beats["p1_after"]
c1.hold(frame(view("p1_before", wide("p1_before")), "We scan the AI tool settings"), 2.2)
c1.fade(c1.last, frame(view("p1_after", wide("p1_after")), "We scan the AI tool settings"), 0.7)
c1.hold(c1.last, 0.6)
c1.zoom("p1_after", wide("p1_after"), fit(b["focus"]), 1.8, "We scan the AI tool settings")
c1.hl_in("p1_after", fit(b["focus"]), b["hl"], 0.5, "Passwords hidden. Unknown stays UNKNOWN.", "PAUSE 1")
c1.hold(c1.last, 4.0)

# ── Pause 2 ──
c2 = Clip()
r, cards = beats["p2_restart"], beats["p2_cards"]
c2.hold(frame(view("p2_restart", wide("p2_restart")), "We restart the app and scan again"), 2.0)
c2.zoom("p2_restart", wide("p2_restart"), fit(r["focus"]), 1.5, "We restart the app and scan again")
c2.hl_in("p2_restart", fit(r["focus"]), r["hl"], 0.4, "Same IDs after restart. No duplicates.")
c2.hold(c2.last, 2.6)
c2.fade(c2.last, frame(view("p2_cards", fit(cards["focus"])), "Same name, different place: two tools"), 0.8)
c2.hl_in("p2_cards", fit(cards["focus"]), cards["hl"], 0.5, "Same name, different place: two tools", "PAUSE 2")
c2.hold(c2.last, 4.0)

# ── Pause 3 ──
c3 = Clip()
pb, pa = beats["p3_before"], beats["p3_after"]
c3.hold(frame(view("p3_before", fit(pb["focus"], pad=60)), "A read-only tool tries to write a file"), 2.6)
c3.fade(c3.last, frame(view("p3_after", wide("p3_after")), "A read-only tool tries to write a file"), 0.7)
c3.zoom("p3_after", wide("p3_after"), fit(pa["focus"]), 1.6, "Caught: no approval on file")
c3.hl_in("p3_after", fit(pa["focus"]), pa["hl"], 0.5, "Caught, and traced back to its source", "PAUSE 3")
c3.hold(c3.last, 4.2)

# ── Pause 4 ──
c4 = Clip()
m = beats["p4_map"]
full = fit(m["focus"], pad=16)
row_box = fit(m["row"], pad=24, min_w=980)
c4.hold(frame(view("p4_map", wide("p4_map")), "Everything on one map"), 1.4)
c4.zoom("p4_map", wide("p4_map"), full, 1.6, "Everything on one map")
c4.hold(c4.last, 3.6)
c4.zoom("p4_map", full, row_box, 1.8, "Follow one action from settings to incident")
c4.hl_in("p4_map", row_box, m["hl"], 0.6, "Follow one action from settings to incident", "PAUSE 4")
c4.hold(c4.last, 8.5)

# ── Pause 5 ──
c5 = Clip()
ru, zp = beats["p5_rules"], beats["p5_zip"]
c5.hold(frame(view("p5_rules", fit(ru["focus"])), "Company rules, checked automatically"), 0.8)
c5.hl_in("p5_rules", fit(ru["focus"]), ru["hl"], 0.5, "Company rules, checked automatically")
c5.hold(c5.last, 2.8)
c5.fade(c5.last, frame(view("p5_zip", fit(zp["focus"], pad=40)), "One-click audit report. Edits are detectable."), 0.8)
c5.hold(frame(view("p5_zip", fit(zp["focus"], pad=40)), "One-click audit report. Edits are detectable.", "PAUSE 5"), 4.2)

names = ["Pause 1 · Passwords hidden, nothing guessed", "Pause 2 · Same IDs, no false merges",
         "Pause 3 · Unapproved action caught and traced", "Pause 4 · One map", "Pause 5 · Rules and audit report"]
clips = [c1, c2, c3, c4, c5]
for i, c in enumerate(clips, 1):
    write([c], os.path.join(out_dir, f"clip{i}-{['secrets', 'ids', 'incident', 'map', 'audit'][i - 1]}.mp4"))
chap, t = [("Intro", 0, intro.seconds())], intro.seconds()
for n, c in zip(names, clips, strict=True):
    chap.append((n, t, t + c.seconds()))
    t += c.seconds()
chap.append(("Summary", t, t + outro.seconds()))
write([intro, *clips, outro], os.path.join(out_dir, "autodiscover-projector-demo.mp4"), chap)
for n, s, _ in chap:
    print(f"{int(s // 60)}:{s % 60:04.1f}  {n}")
print("total", round(t + outro.seconds(), 1))

# readability check: the last frame of each clip at 1280 px
sheet = Image.new("RGB", (1280 * 2, 720 * 3), BG)
for i, c in enumerate([intro, *clips]):
    sheet.paste(c.last.resize((1280, 720), Image.LANCZOS), ((i % 2) * 1280, (i // 2) * 720))
sheet.save(os.path.join(out_dir, "check-1280.jpg"), quality=88)
