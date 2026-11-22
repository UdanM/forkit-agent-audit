"""Projector cut v3: plain subtitles (no sound), no intro card, Ask Forkit as the last pause.

Usage: python compose_v3.py <stills_dir> <out_dir>   (stills from capture_stills.js)
Writes clip1..clip5, autodiscover-demo-v3.mp4 (with chapters) and check-1280.jpg.
"""

import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
VIEW_H = 900                       # where the focus sits; subtitles go below it, over the picture
ASPECT = W / VIEW_H
DPR = 2
BG = (9, 12, 24)
LAV = (190, 175, 255)
YEL = (255, 210, 122)
FONT = "/usr/share/fonts/opentype/inter/"
f_cap = ImageFont.truetype(FONT + "Inter-SemiBold.otf", 50)
f_lbl = ImageFont.truetype(FONT + "Inter-Medium.otf", 26)
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


# ── v3 style: full-frame picture, subtitles over it, thin outlines, no dimming ──
PIC_H = H


def view3(img_id, box, hl=(), hl_a=0.0):
    """Like view(), but the crop is extended downward so the picture fills the whole frame."""
    x, y, w, h = box
    h2 = w * H / W
    y2 = min(y, 1800 - h2) if y + h2 > 1800 else y
    im = imgs[img_id].crop((round(x), round(y2), round(x + w), round(y2 + h2))).resize((W, H), Image.LANCZOS)
    if hl and hl_a > 0:
        s = W / w
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for r in hl:
            rx, ry, rw, rh = [v * DPR for v in r]
            rr = [(rx - x) * s - 8, (ry - y2) * s - 8, (rx + rw - x) * s + 8, (ry + rh - y2) * s + 8]
            od.rounded_rectangle(rr, 12, outline=(242, 201, 120, int(235 * hl_a)), width=4)
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    return im


def wrap(text, font, max_w):
    """One line if it fits, else two lines split where they are most even."""
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if d.textlength(text, font=font) <= max_w:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: max(d.textlength(" ".join(words[:i]), font=font),
                                                       d.textlength(" ".join(words[i:]), font=font)))
    return [" ".join(words[:best]), " ".join(words[best:])]


BAND = 180


def subbed(pic, sub, label=None):
    """Plain subtitles on a solid dark strip at the bottom, like a film; nothing shows through."""
    f = pic.copy()
    d = ImageDraw.Draw(f)
    d.rectangle([0, H - BAND, W, H], fill=(8, 9, 16))
    d.line([0, H - BAND, W, H - BAND], fill=(38, 42, 66), width=2)
    if sub:
        lines = wrap(sub, f_cap, 1760)
        lh = 62
        top = H - BAND / 2 - lh * len(lines) / 2
        for i, t in enumerate(lines):
            d.text((W / 2, top + lh * i + lh / 2), t, font=f_cap, fill=(255, 255, 255), anchor="mm")
    return f


class Clip3(Clip):
    def zoom(self, img_id, b0, b1, sec, sub, label=None):
        n = round(sec * FPS)
        for i in range(n):
            self.frames.append((subbed(view3(img_id, lerp(b0, b1, ease((i + 1) / n))), sub, label), 1))

    def hl_in(self, img_id, box, hl, sec, sub, label=None):
        n = round(sec * FPS)
        for i in range(n):
            self.frames.append((subbed(view3(img_id, box, hl, (i + 1) / n), sub, label), 1))

    def still(self, img_id, box, sec, sub, label=None, hl=()):
        self.hold(subbed(view3(img_id, box, hl, 1.0 if hl else 0.0), sub, label), sec)


def wide3(_):
    return fit([MAIN[0], 0, MAIN[2], 1], pad=0)


L = ["1/5  Discovery", "2/5  ID cards", "3/5  Out-of-policy action", "4/5  The map", "5/5  Ask Forkit"]

# 1 · Discovery
c1 = Clip3()
b = beats["p1_after"]
c1.still("p1_before", wide3(0), 2.6, "AutoDiscover reads the AI tool settings on this computer.", L[0])
c1.fade(c1.last, subbed(view3("p1_after", wide3(0)), "It finds three tools. The database password is removed before saving.", L[0]), 0.7)
c1.hold(c1.last, 1.6)
c1.zoom("p1_after", wide3(0), fit(b["focus"]), 1.8, "It finds three tools. The database password is removed before saving.", L[0])
c1.hl_in("p1_after", fit(b["focus"]), b["hl"], 0.5, "No owner is written down, so it says UNKNOWN. We never guess.", L[0])
c1.hold(c1.last, 4.5)

# 2 · ID cards
c2 = Clip3()
r, cards = beats["p2_restart"], beats["p2_cards"]
c2.still("p2_restart", wide3(0), 2.4, "Each tool gets an ID card. We restart the app and scan again.", L[1])
c2.zoom("p2_restart", wide3(0), fit(r["focus"]), 1.5, "Each tool gets an ID card. We restart the app and scan again.", L[1])
c2.hl_in("p2_restart", fit(r["focus"]), r["hl"], 0.4, "Same three IDs after the restart. Nothing is registered twice.", L[1])
c2.hold(c2.last, 2.8)
c2.fade(c2.last, subbed(view3("p2_cards", fit(cards["focus"])), "Same name, different settings file: two separate tools, never merged.", L[1]), 0.8)
c2.hl_in("p2_cards", fit(cards["focus"]), cards["hl"], 0.5, "Same name, different settings file: two separate tools, never merged.", L[1])
c2.hold(c2.last, 4.5)

# 3 · Out-of-policy action
c3 = Clip3()
pb, pa = beats["p3_before"], beats["p3_after"]
c3.still("p3_before", fit(pb["focus"], pad=60), 3.0, "This file tool may only read. Here it tries to write a report.", L[2])
c3.fade(c3.last, subbed(view3("p3_after", wide3(0)), "We link the action to the tool, with the operator's consent.", L[2]), 0.7)
c3.zoom("p3_after", wide3(0), fit(pa["focus"]), 1.6, "We link the action to the tool, with the operator's consent.", L[2])
c3.hl_in("p3_after", fit(pa["focus"]), pa["hl"], 0.5, "No approval on file: it becomes an incident, traced to its ID card and settings file.", L[2])
c3.hold(c3.last, 5.0)

# 4 · The map
c4 = Clip3()
m = beats["p4_map"]
full = fit(m["focus"], pad=16)
row_box = fit(m["row"], pad=24, min_w=980)
c4.still("p4_map", wide3(0), 1.4, "Everything ends up on one map.", L[3])
c4.zoom("p4_map", wide3(0), full, 1.6, "Everything ends up on one map.", L[3])
c4.hold(c4.last, 3.4)
c4.zoom("p4_map", full, row_box, 1.8, "Read one row, left to right:", L[3])
c4.hl_in("p4_map", row_box, m["hl"], 0.6, "One row, left to right: settings file, tool, ID card, action, incident.", L[3])
c4.hold(c4.last, 8.5)

# 5 · Ask Forkit
c5 = Clip3()
a = beats["p5_answer"]
typing = sorted(k for k in beats if k.startswith("p5_type_") and k != "p5_type_full") + ["p5_type_full"]
abox = fit(a["focus"], pad=30)
c5.still(typing[0], abox, 1.4, "Anyone can ask a question in plain English.", L[4])
for k in typing[1:]:
    c5.still(k, abox, 0.16, "Anyone can ask a question in plain English.", L[4])
c5.hold(c5.last, 1.2)
c5.fade(c5.last, subbed(view3("p5_answer", abox), "Who changed the Q3 report? Three records, including a helper working for another agent.", L[4]), 0.6)
c5.hl_in("p5_answer", abox, a["hl"], 0.5, "Answers come only from saved records, with record IDs as proof.", L[4])
c5.hold(c5.last, 5.0)

# end card: plain, product-like
end = Clip3()
e = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(e)
d.text((W / 2, 470), "AutoDiscover", font=ImageFont.truetype(FONT + "Inter-Bold.otf", 96), fill=(255, 255, 255), anchor="mm")
d.text((W / 2, 580), "Every tool gets an ID card. Every action gets a trail. Nothing is guessed.", font=f_cap, fill=(205, 207, 235), anchor="mm")
d.text((W / 2, 980), "forkit-autodiscover-hackathon.streamlit.app", font=f_lbl, fill=(150, 152, 185), anchor="mm")
end.hold(e, 3.0)

names = ["1 · Discovery", "2 · ID cards", "3 · Out-of-policy action", "4 · The map", "5 · Ask Forkit"]
clips = [c1, c2, c3, c4, c5]
slugs = ["discovery", "id-cards", "incident", "map", "ask"]
for i, c in enumerate(clips, 1):
    write([c], os.path.join(out_dir, f"clip{i}-{slugs[i - 1]}.mp4"))
chap, t = [], 0.0
for n, c in zip(names, clips, strict=True):
    chap.append((n, t, t + c.seconds()))
    t += c.seconds()
chap.append(("End", t, t + end.seconds()))
write([*clips, end], os.path.join(out_dir, "autodiscover-demo-v3.mp4"), chap)
for n, s, _ in chap:
    print(f"{int(s // 60)}:{s % 60:04.1f}  {n}")
print("total", round(t + end.seconds(), 1))
sheet = Image.new("RGB", (1280 * 2, 720 * 3), BG)
for i, c in enumerate([*clips, end]):
    sheet.paste(c.last.resize((1280, 720), Image.LANCZOS), ((i % 2) * 1280, (i // 2) * 720))
sheet.save(os.path.join(out_dir, "check-1280.jpg"), quality=88)
