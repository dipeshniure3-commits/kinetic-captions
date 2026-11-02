"""Word animations: pure, deterministic functions of time.

Each animation maps ``dt`` (seconds since the word's start) to an
:class:`AnimState`. Offsets ``dx``/``dy`` are in units of the word's font size
so they scale with the text. ``reveal`` is the number of visible characters
(typewriter) or None for all.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

PHRASE_FADE_OUT = 0.08  # seconds of fade at the phrase end


@dataclass
class AnimState:
    scale: float = 1.0
    alpha: float = 1.0
    dx: float = 0.0
    dy: float = 0.0
    reveal: int | None = None
    ghosts: list[tuple[float, float]] = field(default_factory=list)  # (scale, alpha)
    glow: float = 0.0  # 0..1 halo strength


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 3


def ease_out_back(x: float, k: float = 1.70158) -> float:
    x = clamp01(x) - 1.0
    return 1.0 + (k + 1.0) * x ** 3 + k * x ** 2


def _seed(text: str) -> float:
    return (zlib.crc32(text.encode("utf-8")) % 1000) / 1000.0 * 2 * math.pi


def _jitter(dt: float, text: str, amp: float) -> tuple[float, float]:
    """Deterministic pseudo-random jitter (sum of incommensurate sines)."""
    p = _seed(text)
    jx = math.sin(dt * 71.0 + p) * 0.6 + math.sin(dt * 113.0 + 2 * p) * 0.4
    jy = math.sin(dt * 83.0 + 3 * p) * 0.6 + math.sin(dt * 131.0 + p) * 0.4
    return jx * amp, jy * amp


def pop(dt: float, w: dict) -> AnimState:
    # 0.6 -> 1.1 in 0.09 s, settle to 1.0 by 0.15 s
    if dt < 0.09:
        s = 0.6 + 0.5 * ease_out_cubic(dt / 0.09)
    elif dt < 0.15:
        s = 1.1 - 0.1 * ease_out_cubic((dt - 0.09) / 0.06)
    else:
        s = 1.0
    return AnimState(scale=s, alpha=clamp01(dt / 0.05))


def slam(dt: float, w: dict) -> AnimState:
    d = 0.12
    x = clamp01(dt / d)
    s = 2.5 - 1.5 * (x * x)  # accelerate into the frame
    st = AnimState(scale=s, alpha=clamp01(dt / 0.09) ** 1.5)
    if dt < d:
        # motion-blur-ish trail: larger, fainter copies
        st.ghosts = [(s * 1.08, 0.22 * st.alpha), (s * 1.17, 0.10 * st.alpha)]
    else:
        # impact: short squash + decaying shake
        k = dt - d
        st.scale = 1.0 + 0.06 * math.exp(-k * 30) * math.cos(k * 60)
        amp = 0.05 * (0.5 + float(w.get("emphasis", 0.5))) * math.exp(-k * 14)
        st.dx, st.dy = _jitter(dt, w.get("text", ""), amp)
    return st


def slide_up(dt: float, w: dict) -> AnimState:
    x = ease_out_cubic(dt / 0.2)
    return AnimState(dy=0.45 * (1 - x), alpha=clamp01(dt / 0.12))


def fade(dt: float, w: dict) -> AnimState:
    return AnimState(alpha=ease_out_cubic(dt / 0.22))


def typewriter(dt: float, w: dict) -> AnimState:
    text = w.get("text", "")
    n = len(text)
    if n == 0:
        return AnimState()
    dur = float(w.get("end", 0) or 0) - float(w.get("start", 0) or 0)
    per = min(0.045, max(0.015, (dur * 0.8 if dur > 0 else 0.3) / n))
    k = min(n, 1 + int(dt / per))
    return AnimState(reveal=None if k >= n else k)


def shake(dt: float, w: dict) -> AnimState:
    st = pop(dt, w)
    e = float(w.get("emphasis", 0.7))
    amp = 0.035 * (0.4 + e) * (0.35 + 0.65 * math.exp(-dt * 5))
    st.dx, st.dy = _jitter(dt, w.get("text", ""), amp)
    return st


def zoom_out(dt: float, w: dict) -> AnimState:
    x = ease_out_cubic(dt / 0.32)
    drift = 0.03 * clamp01((dt - 0.32) / 1.5)  # slow settle after landing
    return AnimState(scale=1.45 - 0.45 * x - drift, alpha=clamp01(dt / 0.1))


def glow(dt: float, w: dict) -> AnimState:
    st = pop(dt, w)
    st.glow = clamp01(dt / 0.2) * (0.8 + 0.2 * math.sin(dt * 6.0))
    return st


ANIMS = {
    "pop": pop,
    "slam": slam,
    "slide_up": slide_up,
    "fade": fade,
    "typewriter": typewriter,
    "shake": shake,
    "zoom_out": zoom_out,
    "glow": glow,
    "none": lambda dt, w: AnimState(),
}


def word_state(t: float, word: dict, phrase_end: float) -> AnimState | None:
    """State of ``word`` at absolute time ``t`` or None if invisible."""
    start = float(word.get("start", 0.0))
    if t < start or t >= phrase_end:
        return None
    fn = ANIMS.get(word.get("anim", "pop"), pop)
    st = fn(t - start, word)
    st.alpha *= clamp01((phrase_end - t) / PHRASE_FADE_OUT)
    if st.alpha <= 0.003:
        return None
    return st
