"""Kinetic caption renderer.

ffmpeg decode pipe (rgb24) -> per-frame numpy/Pillow compositing -> ffmpeg
encode pipe (libx264 crf 18 yuv420p), original audio copied.

Per frame: frame -> ``behind`` text -> person cutout (original frame where the
matte says person) -> ``front`` text.

Text is rendered per word into premultiplied RGBA sprites (fill + thin dark
stroke + soft drop shadow) cached by (text, size px, colour, weight); the
animation scale is realised by picking a quantised font size so scaled text
stays crisp.

CLI: python -m kc.render in.mp4 layout.json out.mp4 [--mattes dir]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import queue
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from .anims import AnimState, word_state

BASE_FRAC = 0.12          # base font size = height * BASE_FRAC
FIT_W = 0.90              # lines must fit within width * FIT_W
FIT_H = 0.80              # phrase block must fit within height * FIT_H
MARGIN_X = 0.05
MARGIN_Y = 0.07
WORD_GAP = 0.24           # inter-word gap, fraction of font size
LINE_GAP = 0.22           # inter-line gap, fraction of the line cap height
DEFAULT_GLOW = "#FFD54A"

# ---------------------------------------------------------------- fonts

SYSTEM_FALLBACKS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


class FontBook:
    """Resolves (weight, size) -> FreeTypeFont with caching."""

    WEIGHTS = {"black": 900, "heavy": 900, "extrabold": 800, "bold": 800,
               "semibold": 700, "regular": 500}

    def __init__(self, font_dir: str = "assets/fonts"):
        self.font_dir = font_dir
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._cap: dict[tuple[str, int], int] = {}
        self._adv: dict[tuple[str, str, int], float] = {}

    def _path(self, *names: str) -> str | None:
        for n in names:
            p = os.path.join(self.font_dir, n)
            if os.path.isfile(p):
                return p
        return None

    def _load(self, weight: str, size: int) -> ImageFont.FreeTypeFont:
        w = (weight or "black").lower()
        if w in ("anton", "condensed"):
            p = self._path("Anton-Regular.ttf")
            if p:
                return ImageFont.truetype(p, size)
        if w in ("courgette", "script") or w.startswith("g-"):
            from kc.fonts import font_path
            fp = font_path("courgette" if w == "script" else w)
            if fp and fp.exists():
                return ImageFont.truetype(str(fp), size)
        if w in ("bebas", "bebasneue"):
            p = self._path("BebasNeue-Regular.ttf")
            if p:
                return ImageFont.truetype(p, size)
        wv = self.WEIGHTS.get(w, 900)
        static = {900: "Montserrat-Black.ttf", 800: "Montserrat-ExtraBold.ttf",
                  700: "Montserrat-Bold.ttf"}.get(wv)
        p = self._path(static) if static else None
        if p:
            return ImageFont.truetype(p, size)
        p = self._path("Montserrat[wght].ttf", "Montserrat-VariableFont_wght.ttf")
        if p:
            f = ImageFont.truetype(p, size)
            try:
                f.set_variation_by_axes([wv])
            except Exception:
                pass
            return f
        p = self._path("Anton-Regular.ttf")
        if p:
            return ImageFont.truetype(p, size)
        for sp in SYSTEM_FALLBACKS:
            if os.path.isfile(sp):
                return ImageFont.truetype(sp, size)
        return ImageFont.load_default(size)

    def font(self, weight: str, size: int) -> ImageFont.FreeTypeFont:
        size = max(4, int(size))
        key = (weight, size)
        f = self._cache.get(key)
        if f is None:
            with _FT_LOCK:
                f = self._cache.get(key) or self._load(weight, size)
                self._cache[key] = f
        return f

    def cap(self, weight: str, size: int) -> int:
        key = (weight, max(4, int(size)))
        c = self._cap.get(key)
        if c is None:
            with _FT_LOCK:
                b = self.font(weight, size).getbbox("H", anchor="ls")
                c = self._cap[key] = max(1, -b[1])
        return c

    def adv(self, text: str, weight: str, size: int) -> float:
        key = (text, weight, max(4, int(size)))
        a = self._adv.get(key)
        if a is None:
            with _FT_LOCK:
                a = self._adv[key] = self.font(weight, size).getlength(text)
        return a


# ---------------------------------------------------------------- sprites

@dataclass
class Sprite:
    prem: np.ndarray    # (h, w, 3) float32 premultiplied colour, 0..255
    alpha: np.ndarray   # (h, w, 1) float32 0..1
    px: int             # pen origin x within sprite
    py: int             # baseline y within sprite


def _rgb(c, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(c)[:3]
    except Exception:
        return default


_FT_LOCK = threading.RLock()  # FreeType faces are not thread-safe


class SpriteCache:
    def __init__(self, fonts: FontBook, max_items: int = 6000):
        self.fonts = fonts
        self.max_items = max_items
        self._c: dict[tuple, Sprite] = {}

    def _put(self, key, spr):
        # called with _FT_LOCK held
        if len(self._c) >= self.max_items:
            # drop the oldest half (dicts keep insertion order)
            for k in list(self._c)[: self.max_items // 2]:
                del self._c[k]
        self._c[key] = spr
        return spr

    def text(self, text: str, size: int, color: str, weight: str) -> Sprite:
        s = self._c.get(("t", text, size, color, weight))
        if s is not None:
            return s
        with _FT_LOCK:
            return self._c.get(("t", text, size, color, weight)) or \
                self._make_text(text, size, color, weight)

    def _make_text(self, text: str, size: int, color: str, weight: str) -> Sprite:
        key = ("t", text, size, color, weight)
        font = self.fonts.font(weight, size)
        sw = max(1, round(size * 0.035))          # stroke
        so = max(1, round(size * 0.05))           # shadow offset
        br = max(1.0, size * 0.07)                # shadow blur
        pad = sw + so + int(math.ceil(br * 2.5)) + 2
        l, t, r, b = font.getbbox(text or " ", anchor="ls", stroke_width=sw)
        w, h = (r - l) + 2 * pad, (b - t) + 2 * pad
        px, py = pad - l, pad - t
        full = Image.new("L", (w, h), 0)
        ImageDraw.Draw(full).text((px, py), text, fill=255, font=font, anchor="ls",
                                  stroke_width=sw, stroke_fill=255)
        fill = Image.new("L", (w, h), 0)
        ImageDraw.Draw(fill).text((px, py), text, fill=255, font=font, anchor="ls")
        shadow = Image.new("L", (w, h), 0)
        shadow.paste(full, (max(1, so // 2), so))
        shadow = shadow.filter(ImageFilter.GaussianBlur(br))

        a_t = np.asarray(full, np.float32)[..., None] / 255.0
        a_f = np.asarray(fill, np.float32)[..., None] / 255.0
        a_s = np.asarray(shadow, np.float32)[..., None] / 255.0 * 0.6
        col = np.array(_rgb(color), np.float32)
        stroke_col = np.array((12, 12, 12), np.float32)
        frac = np.divide(a_f, a_t, out=np.zeros_like(a_f), where=a_t > 1e-4)
        rgb = stroke_col + (col - stroke_col) * frac
        alpha = a_t + a_s * (1.0 - a_t)
        prem = rgb * a_t                       # shadow is black: adds no colour
        return self._put(key, Sprite(prem.astype(np.float32), alpha.astype(np.float32), px, py))

    def halo(self, text: str, size: int, color: str, weight: str) -> Sprite:
        s = self._c.get(("h", text, size, color, weight))
        if s is not None:
            return s
        with _FT_LOCK:
            return self._c.get(("h", text, size, color, weight)) or \
                self._make_halo(text, size, color, weight)

    def _make_halo(self, text: str, size: int, color: str, weight: str) -> Sprite:
        key = ("h", text, size, color, weight)
        font = self.fonts.font(weight, size)
        sw = max(1, round(size * 0.08))
        br = max(2.0, size * 0.22)
        pad = sw + int(math.ceil(br * 2.5)) + 2
        l, t, r, b = font.getbbox(text or " ", anchor="ls", stroke_width=sw)
        w, h = (r - l) + 2 * pad, (b - t) + 2 * pad
        px, py = pad - l, pad - t
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).text((px, py), text, fill=255, font=font, anchor="ls",
                               stroke_width=sw, stroke_fill=255)
        m = m.filter(ImageFilter.GaussianBlur(br))
        a = np.asarray(m, np.float32)[..., None] / 255.0
        a = np.clip(a * 1.6, 0, 1) * 0.85
        prem = a * np.array(_rgb(color, (255, 213, 74)), np.float32)
        return self._put(key, Sprite(prem.astype(np.float32), a.astype(np.float32), px, py))


def blit(frame: np.ndarray, spr: Sprite, pen_x: float, base_y: float, a: float):
    """Alpha-composite sprite onto uint8 frame in place. Returns bbox or None."""
    if a <= 0.003:
        return None
    H, W = frame.shape[:2]
    x0 = int(round(pen_x)) - spr.px
    y0 = int(round(base_y)) - spr.py
    h, w = spr.alpha.shape[:2]
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    sx0, sy0 = fx0 - x0, fy0 - y0
    sx1, sy1 = sx0 + (fx1 - fx0), sy0 + (fy1 - fy0)
    al = spr.alpha[sy0:sy1, sx0:sx1]
    pr = spr.prem[sy0:sy1, sx0:sx1]
    reg = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    if a < 0.999:
        out = reg * (1.0 - a * al) + pr * a
    else:
        out = reg * (1.0 - al) + pr
    np.clip(out + 0.5, 0, 255, out=out)
    frame[fy0:fy1, fx0:fx1] = out.astype(np.uint8)
    return (fx0, fy0, fx1, fy1)


# ---------------------------------------------------------------- layout

@dataclass
class PlacedWord:
    w: dict
    text: str
    size: int
    color: str
    weight: str
    layer: str
    pen_x: float
    base_y: float
    adv: float
    cap: int


@dataclass
class PlacedPhrase:
    start: float
    end: float
    words: list[PlacedWord]
    block: tuple[float, float, float, float] | None = None   # left, top, w, h (px)


def _line_metrics(words, sizes, weights, fonts: FontBook):
    advs = [fonts.adv(w.get("text", ""), wt, s) for w, s, wt in zip(words, sizes, weights)]
    caps = [fonts.cap(wt, s) for s, wt in zip(sizes, weights)]
    gaps = [WORD_GAP * (sizes[i] + sizes[i + 1]) / 2 for i in range(len(words) - 1)]
    return advs, caps, gaps, sum(advs) + sum(gaps)


def layout_phrase(ph: dict, W: int, H: int, fonts: FontBook) -> PlacedPhrase:
    base = min(W, H) * BASE_FRAC
    lines = [ln.get("words", []) for ln in ph.get("lines", []) if ln.get("words")]
    anchor = ph.get("anchor", "center")
    pfont = ph.get("font")

    def wt(w):
        return pfont or w.get("weight", "black")

    def measure(f):
        out = []
        for ws in lines:
            sizes = [max(6, int(round(base * float(w.get("scale", 1.0)) * f))) for w in ws]
            weights = [wt(w) for w in ws]
            advs, caps, gaps, lw = _line_metrics(ws, sizes, weights, fonts)
            out.append((ws, sizes, weights, advs, caps, gaps, lw, max(caps)))
        bh = sum(m[7] for m in out) + sum(LINE_GAP * m[7] for m in out[1:])
        bw = max((m[6] for m in out), default=0)
        return out, bw, bh

    f = 1.0
    m, bw, bh = measure(f)
    for _ in range(4):  # shrink to fit (re-measure: rounding makes it non-linear)
        if bw <= W * FIT_W and bh <= H * FIT_H:
            break
        f *= min(W * FIT_W / max(bw, 1), H * FIT_H / max(bh, 1)) * 0.995
        m, bw, bh = measure(f)

    mx, my = W * MARGIN_X, H * MARGIN_Y
    pos = ph.get("pos")
    left = None
    if isinstance(pos, dict) and "cx" in pos and "cy" in pos:
        # explicit block centre (normalised), clamped inside the margins
        def _clamp(c, size, lo, hi):
            if hi - lo <= size:
                return lo + (hi - lo - size) / 2
            return min(max(c - size / 2, lo), hi - size)
        left = _clamp(float(pos["cx"]) * W, bw, mx, W - mx)
        top = _clamp(float(pos["cy"]) * H, bh, my, H - my)
        align = ph.get("align") or "center"
    elif anchor == "top-left":
        top = my
    elif anchor == "top":
        top = my
    elif anchor == "bottom":
        top = H - my - bh
    elif anchor == "lower":
        top = min(H * 0.72 - bh / 2, H - my - bh)
    else:  # center
        top = (H - bh) / 2
    if left is None:
        top = max(my * 0.5, top)

    placed: list[PlacedWord] = []
    y = top
    for i, (ws, sizes, weights, advs, caps, gaps, lw, lcap) in enumerate(m):
        if i:
            y += LINE_GAP * lcap
        base_y = y + lcap
        if left is not None:
            x = left if align == "left" else left + (bw - lw) / 2
        else:
            x = mx if anchor == "top-left" else (W - lw) / 2
        for j, w in enumerate(ws):
            placed.append(PlacedWord(
                w=w, text=w.get("text", ""), size=sizes[j],
                color=w.get("color", "#FFFFFF"), weight=weights[j],
                layer=w.get("layer", "front"), pen_x=x, base_y=base_y,
                adv=advs[j], cap=caps[j]))
            x += advs[j] + (gaps[j] if j < len(gaps) else 0)
        y = base_y
    bl = left if left is not None else (mx if anchor == "top-left" else (W - bw) / 2)
    return PlacedPhrase(float(ph.get("start", 0)), float(ph.get("end", 0)), placed,
                        (bl, top, bw, bh))


# ---------------------------------------------------------------- drawing

def _qsize(size: int, s: float) -> int:
    if abs(s - 1.0) < 0.012:
        return size
    step = max(1, round(size * 0.025))
    return max(4, int(round(size * s / step) * step))


def draw_word(frame, pw: PlacedWord, st: AnimState, sprites: SpriteCache, fonts: FontBook):
    """Draw one animated word. Returns bbox union or None."""
    bbox = None

    def grow(b):
        nonlocal bbox
        if b is None:
            return
        bbox = b if bbox is None else (min(bbox[0], b[0]), min(bbox[1], b[1]),
                                       max(bbox[2], b[2]), max(bbox[3], b[3]))

    cx = pw.pen_x + pw.adv / 2 + st.dx * pw.size
    cy = pw.base_y - pw.cap / 2 + st.dy * pw.size

    def place(scale):
        sz = _qsize(pw.size, scale)
        if sz == pw.size:
            return sz, pw.pen_x + st.dx * pw.size, pw.base_y + st.dy * pw.size
        adv = fonts.adv(pw.text, pw.weight, sz)
        cap = fonts.cap(pw.weight, sz)
        return sz, cx - adv / 2, cy + cap / 2

    for gs, ga in st.ghosts:
        sz, px, py = place(gs)
        grow(blit(frame, sprites.text(pw.text, sz, pw.color, pw.weight), px, py, ga))

    text = pw.text if st.reveal is None else pw.text[: st.reveal]
    if st.reveal is not None:
        sz, px, py = pw.size, pw.pen_x + st.dx * pw.size, pw.base_y + st.dy * pw.size
    else:
        sz, px, py = place(st.scale)
    if st.glow > 0:
        gcol = pw.w.get("glow_color")
        if not gcol:
            gcol = pw.color if _rgb(pw.color) != (255, 255, 255) else DEFAULT_GLOW
        grow(blit(frame, sprites.halo(text, sz, gcol, pw.weight), px, py, st.alpha * st.glow))
    grow(blit(frame, sprites.text(text, sz, pw.color, pw.weight), px, py, st.alpha))
    return bbox


class MatteSource:
    def __init__(self, matte_dir: str | None, W: int, H: int):
        self.dir = matte_dir if matte_dir and os.path.isdir(matte_dir) else None
        self.W, self.H = W, H

    def get(self, idx: int) -> np.ndarray | None:
        if not self.dir:
            return None
        p = os.path.join(self.dir, f"{idx:06d}.png")
        if not os.path.isfile(p):
            return None
        try:
            im = Image.open(p).convert("L")
        except Exception:
            return None
        if im.size != (self.W, self.H):
            im = im.resize((self.W, self.H), Image.BILINEAR)
        return np.asarray(im, np.float32)[..., None] / 255.0


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def any_active(t: float, phrases: list[PlacedPhrase]) -> bool:
    return any(ph.start - 0.5 <= t < ph.end for ph in phrases)


def _auto_workers() -> int:
    env = int(os.environ.get("KC_RENDER_WORKERS", "0") or 0)
    if env:
        return env
    try:
        p = int(subprocess.check_output(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                                        text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        p = 4
    return max(1, min(8, p))


def composite_frame(frame: np.ndarray, t: float, idx: int, phrases: list[PlacedPhrase],
                    sprites: SpriteCache, fonts: FontBook, mattes: MatteSource) -> np.ndarray:
    active = []
    for ph in phrases:
        if ph.start - 0.5 <= t < ph.end:
            for pw in ph.words:
                st = word_state(t, pw.w, ph.end)
                if st is not None:
                    active.append((pw, st))
    if not active:
        return frame
    behind = [a for a in active if a[0].layer == "behind"]
    front = [a for a in active if a[0].layer != "behind"]
    if behind:
        matte = mattes.get(idx)
        if matte is None:
            front = behind + front  # no matte -> everything in front, keep order
        else:
            orig = frame.copy()
            bb = None
            for pw, st in behind:
                bb = _union(bb, draw_word(frame, pw, st, sprites, fonts))
            if bb is not None:
                x0, y0, x1, y1 = bb
                m = matte[y0:y1, x0:x1]
                reg = frame[y0:y1, x0:x1].astype(np.float32)
                o = orig[y0:y1, x0:x1].astype(np.float32)
                frame[y0:y1, x0:x1] = (reg + (o - reg) * m + 0.5).astype(np.uint8)
    for pw, st in front:
        draw_word(frame, pw, st, sprites, fonts)
    return frame


# ---------------------------------------------------------------- video io

def probe(video_path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames",
         "-show_entries", "format=duration", "-of", "json", video_path], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    def rate(r):
        try:
            n, d = r.split("/")
            return float(n) / float(d) if float(d) else 0.0
        except Exception:
            return 0.0
    fps = rate(v.get("avg_frame_rate", "0/0")) or rate(v.get("r_frame_rate", "0/0")) or 30.0
    try:
        nb = int(v.get("nb_frames") or 0)
    except ValueError:
        nb = 0
    try:
        dur = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    if nb <= 0 and dur > 0:
        nb = int(round(dur * fps))
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": fps,
            "frames": nb, "duration": dur,
            "fps_str": v.get("avg_frame_rate") if rate(v.get("avg_frame_rate", "0/0")) else v.get("r_frame_rate", "30/1"),
            "audio": a["codec_name"] if a else None}


def render(video_path: str, layout: dict, out_path: str, matte_dir: str | None = None,
           font_dir: str = "assets/fonts", bg: str | None = None,
           progress: Callable[[float], None] | None = None,
           canvas: dict | None = None, workers: int = 0, preset: str = "veryfast") -> None:
    """bg: replace everything outside the person matte with this colour
    (e.g. "#000000"); needs matte_dir.
    progress: optional callback, called every ~10 frames with 0..1.
    canvas: {"width", "height", "fps", "color", "audio"} -> no source video;
      frames are a solid colour generated in memory, audio taken from "audio"
      (video_path is ignored). Used for audio-only input.
    workers: frames composited in parallel (0 = P-core count).
    preset: x264 preset. "veryfast" at crf 18: SSIM 0.9996 vs "medium" on the
      1080x1920 test, same file size, ~12% quicker."""
    if canvas:
        W, H = int(canvas["width"]), int(canvas["height"])
        fps = float(canvas.get("fps", 30))
        a_src = canvas["audio"]
        ap = probe_audio(a_src)
        info = {"width": W, "height": H, "fps": fps, "fps_str": f"{fps:g}",
                "frames": int(math.ceil(ap["duration"] * fps)), "audio": ap["codec"]}
    else:
        info = probe(video_path)
        W, H, fps = info["width"], info["height"], info["fps"]
        a_src = video_path
    fonts = FontBook(font_dir)
    sprites = SpriteCache(fonts)
    mattes = MatteSource(matte_dir, W, H)
    bg_rgb = np.array(_rgb(bg), np.float32) if bg else None
    if bg_rgb is not None and mattes.dir is None:
        raise ValueError("bg replacement needs person mattes (matte_dir)")
    phrases = [layout_phrase(p, W, H, fonts) for p in layout.get("phrases", [])]
    phrases.sort(key=lambda p: p.start)
    workers = workers or _auto_workers()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    n = W * H * 3
    dec = None
    if canvas:
        base = np.empty((H, W, 3), np.uint8)
        base[:] = _rgb(canvas.get("color", "#000000"), (0, 0, 0))
        base_bytes = base.tobytes()

        def frames_iter():
            for _ in range(info["frames"]):
                yield None
    else:
        dec = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", video_path,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, bufsize=n * 4)

        def frames_iter():
            while True:
                buf = dec.stdout.read(n)
                if not buf or len(buf) < n:
                    return
                yield buf

    ext = os.path.splitext(out_path)[1].lower()
    copy_ok = info["audio"] in {"aac", "mp3", "alac", "ac3", "eac3"} and ext in (".mp4", ".mov", ".m4v")
    acodec = (["-c:a", "copy"] if copy_ok else ["-c:a", "aac", "-b:a", "192k"]) if info["audio"] else []
    enc_cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-framerate", info["fps_str"] or f"{fps}", "-i", "-",
               "-i", a_src, "-map", "0:v:0"]
    if info["audio"]:
        enc_cmd += ["-map", "1:a:0"]
    enc_cmd += ["-c:v", "libx264", "-crf", "18", "-preset", preset, "-pix_fmt", "yuv420p",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"] + acodec + \
               (["-shortest"] if canvas else []) + ["-movflags", "+faststart", out_path]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)

    def work(idx: int, buf: bytes | None) -> bytes:
        t = idx / fps
        if buf is None:                       # canvas frame
            if not any_active(t, phrases):
                return base_bytes             # nothing on screen: reuse
            frame = base.copy()
        else:
            if bg_rgb is None and not any_active(t, phrases):
                return buf                    # untouched source frame
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            if bg_rgb is not None:
                m = mattes.get(idx)
                if m is not None:
                    frame = (bg_rgb + (frame - bg_rgb) * m + 0.5).astype(np.uint8)
        return composite_frame(frame, t, idx, phrases, sprites, fonts, mattes).tobytes()

    # decode -> N compositing threads (numpy/PIL release the GIL) -> in-order
    # writer thread -> x264 (its own process, multi-threaded)
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    q: queue.Queue = queue.Queue(maxsize=workers * 3)

    def writer():
        while True:
            item = q.get()
            if item is None:
                break
            try:
                enc.stdin.write(item)
            except BrokenPipeError:
                break
    th = threading.Thread(target=writer, daemon=True)
    th.start()

    total = info.get("frames") or 0
    idx = 0
    inflight: deque = deque()
    pool = ThreadPoolExecutor(workers)

    def drain(limit: int):
        nonlocal idx
        while len(inflight) > limit:
            q.put(inflight.popleft().result())
            idx += 1
            if progress is not None and idx % 10 == 0:
                try:
                    progress(min(0.999, idx / total) if total > 0 else 0.0)
                except Exception:
                    pass
    try:
        for i, buf in enumerate(frames_iter()):
            inflight.append(pool.submit(work, i, buf))
            drain(workers * 2)
        drain(0)
    finally:
        pool.shutdown(wait=True)
        q.put(None)
        th.join()
        try:
            enc.stdin.close()
        except Exception:
            pass
        if dec is not None:
            dec.stdout.close()
            dec.wait()
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg encode failed (exit {rc})")
    if progress is not None:
        try:
            progress(1.0)
        except Exception:
            pass


def probe_audio(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True)
    j = json.loads(out.stdout)
    st = (j.get("streams") or [{}])[0]
    return {"codec": st.get("codec_name"),
            "duration": float(j.get("format", {}).get("duration") or 0)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render kinetic captions onto a video")
    ap.add_argument("video")
    ap.add_argument("layout")
    ap.add_argument("out")
    ap.add_argument("--mattes", default=None)
    ap.add_argument("--font-dir", default="assets/fonts")
    ap.add_argument("--bg", default=None, help="replace background with colour, e.g. '#000000'")
    a = ap.parse_args(argv)
    with open(a.layout) as f:
        layout = json.load(f)
    t0 = time.time()
    render(a.video, layout, a.out, matte_dir=a.mattes, font_dir=a.font_dir, bg=a.bg)
    print(f"rendered {a.out} in {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
