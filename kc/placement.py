"""Subject-aware caption placement.

    auto_place(layout, matte_dir, W, H) -> layout

For every phrase we look at the subject mattes over the phrase's time window
and pick where the caption block goes (``pos`` = block centre, normalised)
plus its alignment, the way an editor would:

* front text lives in open space: never over the face / upper body, a little
  breathing room around the subject, preferably in the upper or lower third,
  on a calm (low edge-energy) part of the background, without jumping around
  wildly or sitting on the very same spot three phrases in a row;
* a phrase with a ``behind`` word is placed so that word is partly tucked
  behind the head (about 15-40 % of it covered, >= 60 % visible);
* if the block cannot fit beside the subject we also try re-wrapping it into
  a narrower column and/or shrinking it slightly.

Everything is a deterministic function of the inputs.
"""
from __future__ import annotations

import copy
import json
import math
import os
import subprocess

import numpy as np
from PIL import Image
from scipy import ndimage

from .render import MARGIN_X, MARGIN_Y, FontBook, layout_phrase

GRID_W = 96            # analysis grid width (cells)
MAX_SAMPLES = 12       # matte frames sampled per phrase

# cost weights
K_OVERLAP = 12.0       # weighted overlap of front words with the subject
K_HALO = 2.5           # proximity to the subject / face (breathing room)
K_ZONE = 1.2           # distance from upper / lower third
K_CENTER = 0.6         # pull towards the centre of the free space (balance)
K_CLUTTER = 1.0        # busy background behind the text
K_SMOOTH = 0.5         # distance from previous phrase position
K_REPEAT = 1.5         # same spot as the previous two phrases
K_SHRINK = 3.0         # per unit of size lost
K_REWRAP = 0.12        # per extra line vs. the director's wrap

BEHIND_TARGET = 0.27   # wanted fraction of the behind word covered
BEHIND_MIN, BEHIND_MAX = 0.12, 0.42
BEHIND_BONUS = 1.5    # how much worse (cost) a behind tuck may be vs. all-front


# ---------------------------------------------------------------- inputs

class _Mattes:
    def __init__(self, d: str, gw: int, gh: int):
        self.d, self.gw, self.gh = d, gw, gh
        self.cache: dict[int, np.ndarray] = {}
        meta = {}
        try:
            with open(os.path.join(d, "meta.json")) as f:
                meta = json.load(f)
        except Exception:
            pass
        self.fps = float(meta.get("fps") or 30.0)
        n = int(meta.get("frames") or 0)
        if n <= 0:
            n = len([x for x in os.listdir(d) if x.endswith(".png")]) if os.path.isdir(d) else 0
        self.n = n

    def get(self, i: int) -> np.ndarray | None:
        if i in self.cache:
            return self.cache[i]
        p = os.path.join(self.d, f"{i:06d}.png")
        m = None
        if os.path.isfile(p):
            try:
                im = Image.open(p).convert("L").resize((self.gw, self.gh), Image.BOX)
                m = np.asarray(im, np.float32) / 255.0
            except Exception:
                m = None
        self.cache[i] = m
        return m

    def window(self, t0: float, t1: float) -> list[np.ndarray]:
        if self.n <= 0:
            return []
        a = max(0, int(math.floor(t0 * self.fps)))
        b = min(self.n - 1, int(math.ceil(t1 * self.fps)))
        if b < a:
            a = b = min(max(a, 0), self.n - 1)
        k = min(MAX_SAMPLES, b - a + 1)
        idx = sorted({int(round(x)) for x in np.linspace(a, b, k)})
        return [m for m in (self.get(i) for i in idx) if m is not None]


def _find_video(matte_dir: str) -> str | None:
    """Best effort: the source video for edge-energy analysis."""
    try:
        with open(os.path.join(matte_dir, "meta.json")) as f:
            meta = json.load(f)
        for k in ("video", "source", "input"):
            if meta.get(k) and os.path.isfile(meta[k]):
                return meta[k]
    except Exception:
        pass
    return None


def _gray_frames(video: str, gw: int, gh: int) -> np.ndarray | None:
    """All frames at grid resolution, grayscale (one cheap ffmpeg pass)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", video, "-vf",
             f"scale={gw}:{gh}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, check=True, timeout=600).stdout
    except Exception:
        return None
    n = len(out) // (gw * gh)
    if n == 0:
        return None
    return np.frombuffer(out[: n * gw * gh], np.uint8).reshape(n, gh, gw).astype(np.float32)


def _edge_energy(gray: np.ndarray) -> np.ndarray:
    g = ndimage.gaussian_filter(gray, 0.6)
    e = np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1))
    e = ndimage.gaussian_filter(e, 1.0)
    return np.clip(e / (np.percentile(e, 95) + 1e-3), 0, 1.5)


# ---------------------------------------------------------------- subject

def _head(occ_max: np.ndarray) -> dict | None:
    """Head box of the largest subject blob (grid coords) or None."""
    b = occ_max > 0.5
    if b.sum() < 4:
        return None
    lab, n = ndimage.label(b)
    if n == 0:
        return None
    sizes = ndimage.sum(b, lab, range(1, n + 1))
    blob = lab == (int(np.argmax(sizes)) + 1)
    if blob.sum() < 0.004 * b.size:
        return None
    rows = np.where(blob.any(1))[0]
    top = int(rows[0])
    widths = blob.sum(1).astype(np.float32)
    gh = blob.shape[0]
    # head width: wide percentile of the first rows below the crown
    probe = widths[top: top + max(3, int(0.12 * gh))]
    hw = float(np.percentile(probe, 80)) if probe.size else 1.0
    hw = max(hw, 2.0)
    hh = 1.35 * hw
    # stop at the shoulders (row width jumps well beyond the head width)
    for y in range(top + int(0.6 * hw), min(gh, top + int(hh) + 1)):
        if widths[y] > 1.9 * hw:
            hh = y - top
            break
    hh = max(hh, 2.0)
    y1 = min(gh, top + int(round(hh)))
    cols = np.where(blob[top:y1].any(0))[0]
    xs = np.where(blob[top:y1])[1]
    x0, x1 = (float(np.percentile(xs, 3)), float(np.percentile(xs, 97)) + 1) if xs.size else (cols[0], cols[-1] + 1)
    return {"x0": x0, "x1": x1, "y0": float(top), "y1": float(y1),
            "cx": (x0 + x1) / 2, "touch_top": top <= 1, "blob": blob}


def _box_mask(shape, x0, y0, x1, y1) -> np.ndarray:
    m = np.zeros(shape, np.float32)
    gh, gw = shape
    a, b = max(0, int(math.floor(y0))), min(gh, int(math.ceil(y1)))
    c, d = max(0, int(math.floor(x0))), min(gw, int(math.ceil(x1)))
    if b > a and d > c:
        m[a:b, c:d] = 1.0
    return m


def _sat(a: np.ndarray) -> np.ndarray:
    s = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    s[1:, 1:] = a.cumsum(0).cumsum(1)
    return s


def _rect_sum(S: np.ndarray, x0, y0, x1, y1) -> np.ndarray:
    """Vectorised integral-image sums; coords are int arrays (grid cells)."""
    return S[y1, x1] - S[y0, x1] - S[y1, x0] + S[y0, x0]


# ---------------------------------------------------------------- variants

def _flat(ph: dict) -> list[dict]:
    return [w for ln in ph.get("lines", []) for w in ln.get("words", [])]


def _rewrap(ph: dict, W: int, H: int, fonts: FontBook, col_w: float) -> dict | None:
    """Greedy re-wrap into a column of width col_w (px); big words get their
    own line. Returns a new phrase dict or None when nothing changes."""
    words = _flat(ph)
    if len(words) < 2:
        return None
    base = min(W, H) * 0.12
    pfont = ph.get("font")
    lines, cur, cw = [], [], 0.0
    for w in words:
        sz = max(6, int(round(base * float(w.get("scale", 1.0)))))
        ww = fonts.adv(w.get("text", ""), pfont or w.get("weight", "black"), sz)
        gap = 0.24 * sz
        big = float(w.get("scale", 1.0)) >= 1.5
        if cur and (big or cur[-1].get("_big") or cw + gap + ww > col_w):
            lines.append(cur)
            cur, cw = [], 0.0
        w = dict(w)
        w["_big"] = big
        cur.append(w)
        cw += (gap if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    for ln in lines:
        for w in ln:
            w.pop("_big", None)
    if [len(l) for l in lines] == [len(l.get("words", [])) for l in ph.get("lines", [])]:
        return None
    q = dict(ph)
    q["lines"] = [{"words": l} for l in lines]
    return q


def _shrunk(ph: dict, f: float) -> dict:
    q = copy.deepcopy(ph)
    for w in _flat(q):
        w["scale"] = round(float(w.get("scale", 1.0)) * f, 3)
    return q


def _variants(ph: dict, W: int, H: int, fonts: FontBook) -> list[tuple[dict, float]]:
    """(phrase, penalty) alternatives: director's wrap, narrower columns,
    slight shrink."""
    out: list[tuple[dict, float]] = []
    n0 = len(ph.get("lines", []))
    seen = set()

    def add(q, pen):
        key = tuple(tuple((w.get("text"), w.get("scale")) for w in ln.get("words", []))
                    for ln in q.get("lines", []))
        if key not in seen:
            seen.add(key)
            out.append((q, pen))

    add(ph, 0.0)
    for frac in (0.55, 0.42, 0.32):
        q = _rewrap(ph, W, H, fonts, W * frac)
        if q is not None:
            add(q, K_REWRAP * max(0, len(q["lines"]) - n0))
    for f in (0.85, 0.72):
        for q, pen in list(out):
            if not any(abs(float(w.get("scale", 1)) - float(v.get("scale", 1))) > 1e-6
                       for w, v in zip(_flat(q), _flat(ph))):
                add(_shrunk(q, f), pen + K_SHRINK * (1 - f))
    return out


# ---------------------------------------------------------------- core

def _word_rects(pp, W, H) -> tuple[list[tuple], tuple]:
    """Word ink rects relative to the block's top-left (px) + block size."""
    left, top, bw, bh = pp.block
    rects = []
    for w in pp.words:
        pad = 0.06 * w.size
        rects.append((w.pen_x - left - pad, w.base_y - w.cap - top - pad,
                      w.pen_x + w.adv - left + pad, w.base_y - top + pad,
                      w.layer == "behind"))
    return rects, (bw, bh)


def _place_one(ph, variants, ctx, prev, hist):
    W, H, gw, gh, sx, sy = ctx["W"], ctx["H"], ctx["gw"], ctx["gh"], ctx["sx"], ctx["sy"]
    S_imp, S_halo, S_occ, S_head, S_clut = ctx["S"]
    head = ctx["head"]
    want_behind = ctx["behind"]
    mx, my = W * MARGIN_X, H * MARGIN_Y
    best = None

    for q, vpen in variants:
        pp = layout_phrase({**q, "pos": None}, W, H, ctx["fonts"])
        rects, (bw, bh) = _word_rects(pp, W, H)
        if not want_behind:
            rects = [r[:4] + (False,) for r in rects]
        # candidate block top-left positions
        def axis(lo, hi, size, n):
            if hi - lo <= size:
                return np.array([lo + (hi - lo - size) / 2])
            return np.linspace(lo, hi - size, n)
        Ls = axis(mx, W - mx, bw, 33)
        Ts = axis(my, H - my, bh, 21)
        L, T = np.meshgrid(Ls, Ts)
        L, T = L.ravel(), T.ravel()

        def gsum(S, r):
            x0 = np.clip(np.floor((L + r[0]) * sx).astype(int), 0, gw)
            x1 = np.clip(np.ceil((L + r[2]) * sx).astype(int), 0, gw)
            y0 = np.clip(np.floor((T + r[1]) * sy).astype(int), 0, gh)
            y1 = np.clip(np.ceil((T + r[3]) * sy).astype(int), 0, gh)
            x1 = np.maximum(x1, x0 + 1).clip(0, gw)
            y1 = np.maximum(y1, y0 + 1).clip(0, gh)
            x0 = np.minimum(x0, x1 - 1)
            y0 = np.minimum(y0, y1 - 1)
            area = (x1 - x0) * (y1 - y0)
            return _rect_sum(S, x0, y0, x1, y1), area

        f_imp = np.zeros_like(L); f_halo = np.zeros_like(L); f_clut = np.zeros_like(L)
        f_area = np.zeros_like(L)
        b_occ = np.zeros_like(L); b_head = np.zeros_like(L); b_area = np.zeros_like(L)
        b_cy = np.zeros_like(L); nb = 0
        for r in rects:
            if r[4]:
                s, a = gsum(S_occ, r); b_occ += s; b_area += a
                s, _ = gsum(S_head, r); b_head += s
                b_cy += T + (r[1] + r[3]) / 2; nb += 1
            else:
                s, a = gsum(S_imp, r); f_imp += s; f_area += a
                s, _ = gsum(S_halo, r); f_halo += s
                s, _ = gsum(S_clut, r); f_clut += s
        cx = (L + bw / 2) / W
        cy = (T + bh / 2) / H
        cost = np.full_like(L, vpen)
        ov = np.zeros_like(L)
        if f_area.any():
            fa = np.maximum(f_area, 1)
            ov = f_imp / fa
            cost += K_OVERLAP * ov + K_HALO * (f_halo / fa) + K_CLUTTER * (f_clut / fa)
        if nb:
            ba = np.maximum(b_area, 1)
            cov = b_occ / ba
            covh = b_head / ba
            cost += 6.0 * ((cov - BEHIND_TARGET) / 0.12) ** 2 * 0.25
            cost += 20.0 * (np.maximum(0, cov - BEHIND_MAX) + np.maximum(0, BEHIND_MIN - covh)) * 5
            # vertical centre of the behind word around the head's upper part
            ty = (head["y0"] + 0.35 * (head["y1"] - head["y0"])) / sy
            cost += 4.0 * np.abs(b_cy / nb - ty) / H
        else:
            cost += K_ZONE * np.minimum(np.abs(cy - 0.25), np.abs(cy - 0.75))
            cost += K_CENTER * np.abs(cx - ctx["free_cx"])
        if prev is not None:
            d = np.hypot(cx - prev[0], (cy - prev[1]) * H / W)
            cost += K_SMOOTH * d
        if len(hist) >= 2:
            same = np.ones_like(L, bool)
            for hx, hy in hist[-2:]:
                same &= np.hypot(cx - hx, cy - hy) < 0.06
            cost += K_REPEAT * same
        i = int(np.argmin(cost))
        cand = (float(cost[i]), float(cx[i]), float(cy[i]), q, float(ov[i]) if f_area.any() else 0.0,
                float(L[i]), float(bw))
        if best is None or cand[0] < best[0] - 1e-9:
            best = cand
    return best


def auto_place(layout: dict, matte_dir: str, W: int, H: int,
               font_dir: str = "assets/fonts", video_path: str | None = None) -> dict:
    fonts = FontBook(font_dir)
    gw = int(min(GRID_W, W))
    gh = max(8, int(round(gw * H / W)))
    sx, sy = gw / W, gh / H
    mattes = _Mattes(matte_dir, gw, gh)

    video = video_path or _find_video(matte_dir)
    grays = _gray_frames(video, gw, gh) if video else None

    prev = None
    prev_end = -1e9
    hist: list[tuple[float, float]] = []
    for ph in layout.get("phrases", []):
        t0, t1 = float(ph.get("start", 0)), float(ph.get("end", 0))
        ms = mattes.window(t0 - 0.15, t1)
        if ms:
            stack = np.stack(ms)
            occ_max, occ_mean = stack.max(0), stack.mean(0)
        else:
            occ_max = occ_mean = np.zeros((gh, gw), np.float32)
        occ = 0.6 * occ_max + 0.4 * occ_mean
        head = _head(occ_max)

        # importance: face/head >> upper body > rest of subject
        wmap = np.ones((gh, gw), np.float32)
        headm = np.zeros((gh, gw), np.float32)
        halo = ndimage.gaussian_filter(occ_max, 1.6)
        if head is not None:
            hh, hw = head["y1"] - head["y0"], head["x1"] - head["x0"]
            ub = _box_mask((gh, gw), head["x0"] - hw, head["y1"], head["x1"] + hw, head["y1"] + hh)
            wmap = np.maximum(wmap, 2.0 * ub)
            hb = _box_mask((gh, gw), head["x0"] - 0.15 * hw, head["y0"] - 0.1 * hh,
                           head["x1"] + 0.15 * hw, head["y1"] + 0.1 * hh)
            wmap = np.maximum(wmap, 4.0 * hb)
            # the face area itself is off limits even where the matte is soft
            halo = np.maximum(halo, 0.8 * _box_mask((gh, gw), head["x0"] - 0.35 * hw,
                                                    head["y0"] - 0.2 * hh,
                                                    head["x1"] + 0.35 * hw, head["y1"] + 0.2 * hh))
            headm = occ_mean * hb
        imp = np.maximum(occ * wmap, 4.0 * hb * 0.9) if head is not None else occ * wmap
        clut = np.zeros((gh, gw), np.float32)
        if grays is not None and len(grays):
            fi = min(len(grays) - 1, max(0, int(round((t0 + t1) / 2 * mattes.fps))))
            clut = _edge_energy(grays[fi]) * (1.0 - np.clip(occ_max, 0, 1))
            clut = 0.5 * clut  # clutter is a tie-breaker, not a veto

        has_behind = any(w.get("layer") == "behind" for w in _flat(ph))
        subject_present = head is not None and occ_max.mean() > 0.01
        if has_behind and not subject_present:
            for w in _flat(ph):
                w["layer"] = "front"
            has_behind = False

        # horizontal centre of mass of the free space (columns weighted by
        # how empty they are) -> captions balance the subject
        colfree = (1.0 - np.clip(occ_max, 0, 1)).mean(0) ** 2
        xs = (np.arange(gw) + 0.5) / gw
        free_cx = float((colfree * xs).sum() / max(colfree.sum(), 1e-6))
        free_cx = 0.5 + 0.8 * (free_cx - 0.5)
        ctx = {"free_cx": free_cx, "W": W, "H": H, "gw": gw, "gh": gh, "sx": sx, "sy": sy, "fonts": fonts,
               "head": head, "behind": has_behind,
               "S": (_sat(imp), _sat(halo), _sat(occ_mean), _sat(headm), _sat(clut))}
        p_prev = prev if (prev is not None and t0 - prev_end < 1.0) else None
        variants = _variants(ph, W, H, fonts)
        ctx["behind"] = False
        best = _place_one(ph, variants, ctx, p_prev, hist)
        use_behind = False
        if has_behind:
            # try tucking the big word behind the head; keep it only if that
            # is a real tuck and doesn't push the other words onto the subject
            ctx["behind"] = True
            bb = _place_one(ph, variants, ctx, p_prev, hist)
            if bb is not None:
                pp = layout_phrase({**bb[3], "pos": {"cx": bb[1], "cy": bb[2]}}, W, H, fonts)
                cov = _coverage(pp, occ_mean, sx, sy)
                ok = cov is not None and 0.08 <= cov <= BEHIND_MAX + 0.08 and bb[4] < 0.12
                if ok and (best is None or bb[0] <= best[0] + BEHIND_BONUS):
                    best, use_behind = bb, True
            if not use_behind:
                ctx["behind"] = False
        if best is None:
            continue
        cost, cx, cy, q, ov, left, bw = best
        if q is not ph:
            ph["lines"] = copy.deepcopy(q["lines"])
        if has_behind and not use_behind:
            for w in _flat(ph):
                w["layer"] = "front"
        nlines = len(ph.get("lines", []))
        ph["pos"] = {"cx": round(cx, 4), "cy": round(cy, 4)}
        ph["align"] = "left" if (nlines > 1 and cx < 0.42 and not ctx["behind"]) else "center"
        prev, prev_end = (cx, cy), t1
        hist.append((cx, cy))
    layout["placement"] = "auto"
    return layout


def _coverage(pp, occ_mean, sx, sy) -> float | None:
    gh, gw = occ_mean.shape
    tot = cov = 0.0
    for w in pp.words:
        if w.layer != "behind":
            continue
        x0 = int(max(0, math.floor(w.pen_x * sx))); x1 = int(min(gw, math.ceil((w.pen_x + w.adv) * sx)))
        y0 = int(max(0, math.floor((w.base_y - w.cap) * sy))); y1 = int(min(gh, math.ceil(w.base_y * sy)))
        if x1 <= x0 or y1 <= y0:
            continue
        cov += float(occ_mean[y0:y1, x0:x1].sum())
        tot += (x1 - x0) * (y1 - y0)
    return cov / tot if tot else None
