"""Demo video v4: full page -> zoom -> gently pulsing highlight -> PAUSE, plain subtitles, one MP4.

Usage: python compose_v4.py <stills_dir> <out_dir>   (stills from capture_stills.js)
"""

import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
VIEW_H = 900                       # picture area above the subtitle strip
ASPECT = W / VIEW_H
DPR = 2
BG = (9, 12, 24)
LAV = (190, 175, 255)
YEL = (255, 210, 122)
FONT = "/usr/share/fonts/opentype/inter/"
f_cap = ImageFont.truetype(FONT + "Inter-SemiBold.otf", 50)
f_pause = ImageFont.truetype(FONT + "Inter-Bold.otf", 34)
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


import math  # noqa: E402

BAND = H - VIEW_H
FULL = [0, 0, 1440, 900]          # the whole page, sidebar included


def page_full(focus):
    """Whole page width; vertically placed so the focus is in view."""
    w = 1440
    h = w / ASPECT
    cy = focus[1] + focus[3] / 2
    y = min(max(cy - h / 2, 0), 900 - h)
    return [0, y * DPR, w * DPR, h * DPR]


def render(img_id, box):
    x, y, w, h = box
    return imgs[img_id].crop((round(x), round(y), round(x + w), round(y + h))).resize((W, VIEW_H), Image.LANCZOS)


def wrap(text, font, max_w):
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if d.textlength(text, font=font) <= max_w:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: max(d.textlength(" ".join(words[:i]), font=font),
                                                       d.textlength(" ".join(words[i:]), font=font)))
    return [" ".join(words[:best]), " ".join(words[best:])]


def compose(pic, sub, paused=False):
    f = Image.new("RGB", (W, H), (8, 9, 16))
    f.paste(pic, (0, 0))
    d = ImageDraw.Draw(f)
    d.line([0, VIEW_H, W, VIEW_H], fill=(38, 42, 66), width=2)
    lines = wrap(sub, f_cap, 1380 if paused else 1760)
    lh = 62
    top = VIEW_H + BAND / 2 - lh * len(lines) / 2
    for i, t in enumerate(lines):
        d.text((W / 2 + (90 if paused else 0), top + lh * i + lh / 2), t, font=f_cap, fill=(255, 255, 255), anchor="mm")
    if paused:
        # a plain media-player style marker in the subtitle strip: two bars and the word PAUSE
        x0, y0 = 40, VIEW_H + BAND / 2 - 32
        tw = d.textlength("PAUSE", font=f_pause)
        d.rounded_rectangle([x0, y0, x0 + 96 + tw, y0 + 64], 12, fill=(14, 16, 28), outline=(255, 255, 255), width=2)
        d.rectangle([x0 + 24, y0 + 18, x0 + 33, y0 + 46], fill=(255, 255, 255))
        d.rectangle([x0 + 42, y0 + 18, x0 + 51, y0 + 46], fill=(255, 255, 255))
        d.text((x0 + 70, y0 + 33), "PAUSE", font=f_pause, fill=(255, 255, 255), anchor="lm")
    return f


AMBER = (255, 196, 84)


def hl_layer(box, rects, alpha):
    """Highlight boxes (CSS rects) in picture coordinates: thick amber outline plus a faint fill."""
    x, y, w, _ = box
    s = W / w
    ov = Image.new("RGBA", (W, VIEW_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for r in rects:
        rx, ry, rw, rh = [v * DPR for v in r]
        rr = [(rx - x) * s - 10, (ry - y) * s - 10, (rx + rw - x) * s + 10, (ry + rh - y) * s + 10]
        d.rounded_rectangle(rr, 14, fill=AMBER + (int(34 * alpha),), outline=AMBER + (int(255 * alpha),), width=7)
    return ov


class V4:
    def __init__(self):
        self.frames = []
        self.marks = []

    def _add(self, img, n=1):
        self.frames.append((img, n))

    @property
    def last(self):
        return self.frames[-1][0]

    def seconds(self):
        return sum(n for _, n in self.frames) / FPS

    def still(self, img_id, box, sec, sub):
        self._add(compose(render(img_id, box), sub), round(sec * FPS))

    def fade_to(self, img_id, box, sec, sub):
        b = compose(render(img_id, box), sub)
        a = self.last
        n = round(sec * FPS)
        for i in range(n):
            self._add(Image.blend(a, b, ease((i + 1) / n)))

    def zoom(self, img_id, b0, b1, sec, sub):
        n = round(sec * FPS)
        for i in range(n):
            self._add(compose(render(img_id, lerp(b0, b1, ease((i + 1) / n))), sub))

    def pulse(self, img_id, box, rects, sec, sub, paused=False):
        """Highlight fades in, then breathes gently (period 1.6 s, never below 45 % so it stays visible)."""
        if paused:
            self.marks.append(self.seconds())
        base = render(img_id, box).convert("RGBA")
        n = round(sec * FPS)
        period = 1.6 * FPS
        cache = {}
        for i in range(n):
            ramp = min(1.0, (i + 1) / (0.4 * FPS))
            a = ramp * (0.725 + 0.275 * math.cos(2 * math.pi * i / period))
            key = round(a, 2)
            if key not in cache:
                pic = Image.alpha_composite(base, hl_layer(box, rects, key)).convert("RGB")
                cache[key] = compose(pic, sub, paused)
            self._add(cache[key])


v = V4()
chap = []

# 1 · Discovery
chap.append(("1 · Discovery", v.seconds()))
b = beats["p1_after"]
v.still("p1_before", page_full(b["focus"]), 2.6, "AutoDiscover reads the AI tool settings on this computer.")
v.fade_to("p1_after", page_full(b["focus"]), 0.7, "It finds three tools.")
v.still("p1_after", page_full(b["focus"]), 1.4, "It finds three tools.")
v.zoom("p1_after", page_full(b["focus"]), fit(b["focus"]), 2.0, "It finds three tools.")
v.pulse("p1_after", fit(b["focus"]), b["hl"], 6.0, "Passwords are removed before saving. Unknown facts stay UNKNOWN.", paused=True)

# 2 · ID cards
chap.append(("2 · ID cards", v.seconds()))
r, cards = beats["p2_restart"], beats["p2_cards"]
v.fade_to("p2_restart", page_full(r["focus"]), 0.7, "We restart the app and scan again.")
v.still("p2_restart", page_full(r["focus"]), 1.6, "We restart the app and scan again.")
v.zoom("p2_restart", page_full(r["focus"]), fit(r["focus"]), 1.8, "We restart the app and scan again.")
v.pulse("p2_restart", fit(r["focus"]), r["hl"], 3.4, "Same three IDs after the restart. Nothing is registered twice.")
v.fade_to("p2_cards", page_full(cards["focus"]), 0.7, "Each tool has an ID card.")
v.still("p2_cards", page_full(cards["focus"]), 1.2, "Each tool has an ID card.")
v.zoom("p2_cards", page_full(cards["focus"]), fit(cards["focus"]), 1.8, "Each tool has an ID card.")
v.pulse("p2_cards", fit(cards["focus"]), cards["hl"], 6.0, "Same name, different settings file: two separate tools, never merged.", paused=True)

# 3 · Out-of-policy action
chap.append(("3 · Out-of-policy action", v.seconds()))
pb, pa = beats["p3_before"], beats["p3_after"]
v.fade_to("p3_before", page_full(pb["focus"]), 0.7, "This file tool may only read. Here it tries to write a report.")
v.still("p3_before", page_full(pb["focus"]), 1.4, "This file tool may only read. Here it tries to write a report.")
v.zoom("p3_before", page_full(pb["focus"]), fit(pb["focus"], pad=50), 1.8, "This file tool may only read. Here it tries to write a report.")
v.pulse("p3_before", fit(pb["focus"], pad=50), pb["hl"], 2.6, "This file tool may only read. Here it tries to write a report.")
v.fade_to("p3_after", page_full(pa["focus"]), 0.8, "We link the action to the tool.")
v.still("p3_after", page_full(pa["focus"]), 1.2, "We link the action to the tool.")
v.zoom("p3_after", page_full(pa["focus"]), fit(pa["focus"]), 1.8, "We link the action to the tool.")
v.pulse("p3_after", fit(pa["focus"]), pa["hl"], 6.5, "No approval on file: it becomes an incident, traced to its ID card and settings file.", paused=True)

# 4 · The map
chap.append(("4 · The map", v.seconds()))
m = beats["p4_map"]
row = list(m["row"])
row[1] += 26                       # start below the column headers
row[3] -= 26
full_map = fit(m["focus"], pad=16)
row_box = fit(row, pad=24, min_w=980)
v.fade_to("p4_map", page_full(m["focus"]), 0.7, "Everything ends up on one map.")
v.still("p4_map", page_full(m["focus"]), 1.4, "Everything ends up on one map.")
v.zoom("p4_map", page_full(m["focus"]), full_map, 1.8, "Everything ends up on one map.")
v.still("p4_map", full_map, 2.6, "Everything ends up on one map.")
v.zoom("p4_map", full_map, row_box, 1.8, "Follow one row from left to right.")
v.pulse("p4_map", row_box, [row], 10.0, "One row, left to right: settings file, tool, ID card, action, incident.", paused=True)

# 5 · Ask Forkit
chap.append(("5 · Ask Forkit", v.seconds()))
a = beats["p5_answer"]
typing = sorted(k for k in beats if k.startswith("p5_type_") and k != "p5_type_full") + ["p5_type_full"]
abox = fit(a["focus"], pad=30)
v.fade_to(typing[0], page_full(a["focus"]), 0.7, "Anyone can ask a question in plain English.")
v.still(typing[0], page_full(a["focus"]), 1.4, "Anyone can ask a question in plain English.")
v.zoom(typing[0], page_full(a["focus"]), abox, 1.8, "Anyone can ask a question in plain English.")
for k in typing[1:]:
    v.still(k, abox, 0.16, "Anyone can ask a question in plain English.")
v.still("p5_type_full", abox, 1.0, "Who changed the Q3 report?")
v.fade_to("p5_answer", abox, 0.6, "Who changed the Q3 report?")
v.pulse("p5_answer", abox, a["hl"], 6.0, "Three records answer it, each with its ID. Nothing is made up.", paused=True)

# closing: what Forkit unlocks
chap.append(("Forkit AI", v.seconds()))
e = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(e)
d.text((W / 2, 400), "Forkit AI", font=ImageFont.truetype(FONT + "Inter-Bold.otf", 96), fill=(255, 255, 255), anchor="mm")
d.text((W / 2, 520), "A verifiable ID for every AI tool, model and agent,", font=f_cap, fill=(225, 226, 245), anchor="mm")
d.text((W / 2, 590), "so companies can adopt AI and still know who did what.", font=f_cap, fill=(225, 226, 245), anchor="mm")
d.text((W / 2, 980), "AutoDiscover · built on Forkit Core · forkit-autodiscover-hackathon.streamlit.app", font=f_tag, fill=(150, 152, 185), anchor="mm")
last = v.last
n = round(0.8 * FPS)
for i in range(n):
    v._add(Image.blend(last, e, ease((i + 1) / n)))
v._add(e, round(4.0 * FPS))

total = v.seconds()
bounds = [(c[0], c[1], (chap[i + 1][1] if i + 1 < len(chap) else total)) for i, c in enumerate(chap)]
write([v], os.path.join(out_dir, "autodiscover-demo-v4.mp4"), bounds)
for nme, s, _ in bounds:
    print(f"{int(s // 60)}:{s % 60:04.1f}  {nme}")
print("PAUSE at", [f"{int(t // 60)}:{t % 60:04.1f}" for t in v.marks], "total", round(total, 1))
with open(os.path.join(out_dir, "pauses.json"), "w") as fh:
    json.dump({"chapters": bounds, "pauses": v.marks, "total": total}, fh, indent=1)
