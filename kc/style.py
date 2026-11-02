"""The "director": words.json -> layout.json.

Decides, deterministically, how every word is presented:
  1. score each word's emphasis (0..1) from lexicon + delivery (loudness,
     pitch, pauses) + position, relative to the clip;
  2. chunk words into 1-4 word phrases (punctuation, pauses, width), pulling
     very strong words out as single-word "beats";
  3. map emphasis -> scale / colour / anim / layer / weight via a preset;
  4. split each phrase into lines and pick a varied, deterministic anchor.

Each word carries an `explain` string listing why it scored what it did.
"""
from __future__ import annotations

import argparse
import json
import re
import zlib
from dataclasses import dataclass, field

from . import lexicon as L

# ---------------------------------------------------------------- presets

PRESETS: dict[str, dict] = {
    "motiversity": {
        "max_words": 4,
        "hero_single": 0.80,        # emphasis >= this -> own phrase
        "scale_min": 0.78, "scale_max": 2.1, "single_boost": 1.3,
        "phrase_cap": 1.75, "second_cap": 1.35,
        "white": "#FFFFFF", "accent": "#FFFFFF", "number": "#FFD34D",
        "negative": "#FFFFFF", "color_threshold": 0.85,  # only number beats get colour
        "allow_behind": True, "behind_scale": 1.45,
        "anchors": ["top-left", "center", "lower", "top", "top-left", "bottom"],
        "behind_anchors": ["top-left", "top", "center"],
        "no_repeat": 1,             # don't reuse the previous anchor
        "anims": {"small": "fade", "normal": "pop", "strong": "slam",
                  "single": ["slam", "zoom_out"], "negative_hero": "shake",
                  "power_glow": True},
        "weights": {"stop": "bold", "content": "black"},
    },
    "hormozi": {
        "max_words": 3,
        "hero_single": 0.88,
        "scale_min": 0.9, "scale_max": 1.45, "single_boost": 1.35,
        "phrase_cap": 1.3, "second_cap": 1.0, "top_floor": 1.15, "lone_floor": 1.3,
        "white": "#FFFFFF", "accent": "#FFD93D", "number": "#39E75F",
        "negative": "#FF3B3B", "color_threshold": 0.0,   # top word / phrase
        "allow_behind": True, "behind_scale": 1.8,       # only single beats
        "anchors": ["center", "lower", "center"],
        "behind_anchors": ["center"],
        "no_repeat": 0,
        "anims": {"small": "pop", "normal": "pop", "strong": "pop",
                  "single": ["slam"], "negative_hero": "shake",
                  "power_glow": False},
        "weights": {"stop": "black", "content": "black"},
    },
    "clean": {
        "max_words": 4,
        "hero_single": 2.0,         # never
        "scale_min": 0.95, "scale_max": 1.15, "single_boost": 1.0,
        "phrase_cap": 1.15, "second_cap": 1.15, "top_floor": 1.0, "lone_floor": 1.0,
        "white": "#FFFFFF", "accent": "#FFFFFF", "number": "#FFFFFF",
        "negative": "#FFFFFF", "color_threshold": 2.0,
        "allow_behind": False, "behind_scale": 99,
        "anchors": ["lower"],
        "behind_anchors": ["lower"],
        "no_repeat": 0,
        "anims": {"small": "fade", "normal": "fade", "strong": "fade",
                  "single": ["fade"], "negative_hero": "fade",
                  "power_glow": False},
        "weights": {"stop": "bold", "content": "bold"},
    },
}

BASE_FONT_FRAC = 0.12      # base font size = height * this (contract)
CHAR_W = 0.62              # avg uppercase glyph width / font size (heavy sans)
WIDTH_FRAC = 0.90          # lines must fit in width * this
PAUSE_BREAK = 0.25         # s; break phrase after a gap this long
MAX_HOLD = 0.45            # s; phrase lingers at most this long after last word
MIN_PHRASE = 0.35          # s; minimum on-screen time
HERO_EVERY = 3.0          # s of speech per single-word beat (budget)
HERO_GAP = 1.2            # s; min distance between beats

_PUNCT_END = re.compile(r"[.!?…]+[\"')\]]*$")
_PUNCT_SOFT = re.compile(r"[,;:\-—–]+[\"')\]]*$")
_NUMERIC = re.compile(r"^[$€£]?\d[\d,.]*(%|k|m|b|x|st|nd|rd|th)?$", re.I)


# ---------------------------------------------------------------- scoring

@dataclass
class W:
    i: int
    raw: str
    core: str            # lowercase, punctuation stripped
    start: float
    end: float
    loud: float          # clip-relative percentile 0..1
    pitch: float         # clip-relative percentile 0..1
    pause: float
    eos: bool            # ends with . ! ?
    soft: bool           # ends with , ; : -
    excl: bool
    quest: bool
    stop: bool
    number: bool
    negative: bool
    power: int           # 0, 1 (tier2), 2 (tier1)
    emph: float = 0.0
    why: list[str] = field(default_factory=list)


def _percentiles(vals: list[float]) -> list[float]:
    """Rank-based percentile in 0..1 (ties averaged). Robust to outliers."""
    n = len(vals)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda k: vals[k])
    out = [0.0] * n
    k = 0
    while k < n:
        j = k
        while j + 1 < n and vals[order[j + 1]] == vals[order[k]]:
            j += 1
        r = (k + j) / 2 / (n - 1)
        for m in range(k, j + 1):
            out[order[m]] = r
        k = j + 1
    return out


def _parse(words: list[dict]) -> list[W]:
    loud = _percentiles([float(w.get("loudness", 0.5)) for w in words])
    pit = _percentiles([float(w.get("pitch", 0.5)) for w in words])
    out = []
    for i, w in enumerate(words):
        raw = str(w["text"]).strip()
        core = re.sub(r"[^\w'$%€£.]", "", raw.lower()).strip(".'")
        core = core.replace("’", "'")
        number = bool(_NUMERIC.match(core)) or core in L.NUMBER_WORDS or "$" in raw or "%" in raw
        power = 2 if core in L.POWER_TIER1 else 1 if core in L.POWER_TIER2 else 0
        out.append(W(
            i=i, raw=raw, core=core,
            start=float(w["start"]), end=float(w["end"]),
            loud=loud[i], pitch=pit[i],
            pause=float(w.get("pause_after", 0.0)),
            eos=bool(_PUNCT_END.search(raw)), soft=bool(_PUNCT_SOFT.search(raw)),
            excl="!" in raw, quest="?" in raw,
            stop=core in L.STOP_WORDS and not number and power == 0,
            number=number, negative=core in L.NEGATIVE, power=power,
        ))
    # fill pause if missing
    for a, b in zip(out, out[1:]):
        if a.pause <= 0:
            a.pause = max(0.0, b.start - a.end)
    return out


def _score(ws: list[W]) -> None:
    for k, w in enumerate(ws):
        why = w.why
        if w.stop:
            s = 0.05
            why.append("stop")
        else:
            s = 0.22
            if len(w.core) >= 7:
                s += 0.08
                why.append("long+.08")
        if w.power == 2:
            s += 0.45
            why.append(f"power1+.45")
        elif w.power == 1:
            s += 0.25
            why.append("power2+.25")
        if w.number:
            s += 0.40
            why.append("number+.40")
        if w.negative and not w.stop:
            s += 0.12
            why.append("neg+.12")
        if k > 0 and ws[k - 1].core in L.NEGATORS and not w.stop:
            s += 0.18
            why.append("negated+.18")
        if k > 0 and ws[k - 1].core in L.INTENSIFIERS and not w.stop:
            s += 0.10
            why.append("intens+.10")
        # delivery: loud / high pitch relative to the clip
        if w.loud > 0.6:
            b = (w.loud - 0.6) * 0.5
            s += b
            why.append(f"loud{w.loud:.2f}+{b:.2f}")
        elif w.loud < 0.25:
            s -= 0.06
            why.append("quiet-.06")
        if w.pitch > 0.7:
            b = (w.pitch - 0.7) * 0.3
            s += b
            why.append(f"pitch+{b:.2f}")
        # position: sentence-final content word / word before dramatic pause
        if not w.stop and w.eos:
            s += 0.12
            why.append("eos+.12")
        elif not w.stop and w.pause > 0.4:
            s += 0.08
            why.append("pause+.08")
        if w.excl:
            s += 0.15
            why.append("excl+.15")
        if w.stop:
            s = min(s, 0.35)  # loud "the" is still "the"
        w.emph = round(max(0.0, min(1.0, s)), 3)


# ---------------------------------------------------------------- chunking

def _max_chars(width: int, height: int) -> int:
    font = min(width, height) * BASE_FONT_FRAC
    return max(6, int(width * WIDTH_FRAC / (font * CHAR_W)))


def _pick_heroes(ws: list[W], P: dict) -> set[int]:
    """Ration single-word beats: if everything is emphasised nothing is.
    Highest-scoring eligible words win, at most one per HERO_EVERY seconds of
    speech and never closer than HERO_GAP seconds to another beat."""
    cand = [w for w in ws if w.emph >= P["hero_single"] and not w.stop]
    if not cand:
        return set()
    span = ws[-1].end - ws[0].start
    budget = max(1, round(span / HERO_EVERY))
    chosen: list[W] = []
    for w in sorted(cand, key=lambda w: (-w.emph, -w.loud, w.i)):
        if len(chosen) >= budget:
            break
        if all(abs(w.start - c.start) >= HERO_GAP for c in chosen):
            chosen.append(w)
    return {w.i for w in chosen}


def _chunk(ws: list[W], P: dict, max_chars: int, heroes: set[int]) -> list[list[W]]:
    phrases: list[list[W]] = []
    cur: list[W] = []

    def flush():
        nonlocal cur
        if cur:
            phrases.append(cur)
            cur = []

    budget = max_chars * 2  # a phrase may wrap onto 2 lines
    for w in ws:
        if w.i in heroes:
            # pre-roll: a lone leading stop-word ("I | NEVER") or the number
            # part of a compound amount ("$10 | MILLION") rides along
            lead = []
            if cur and cur[-1].pause <= PAUSE_BREAK:
                if cur[-1].number and w.number:
                    lead = cur[-1:]
                elif len(cur) == 1 and cur[0].stop:
                    lead = cur[-1:]
            cur = cur[:len(cur) - len(lead)]
            flush()
            phrases.append(lead + [w])
            continue
        chars = sum(len(x.core) + 1 for x in cur) + len(w.core)
        if (len(cur) >= P["max_words"] or chars > budget
                or (len(cur) >= 2 and w.core in L.CLAUSE_STARTERS)):
            flush()
        cur.append(w)
        if w.eos or w.soft or w.pause > PAUSE_BREAK:
            flush()
    flush()

    # merge orphan single stop-words into a neighbour (never into a hero beat)
    def is_beat(p):
        return any(x.i in heroes for x in p)

    merged: list[list[W]] = []
    k = 0
    while k < len(phrases):
        p = phrases[k]
        if len(p) == 1 and p[0].stop:
            nxt = phrases[k + 1] if k + 1 < len(phrases) else None
            prev = merged[-1] if merged else None
            if (nxt and not is_beat(nxt) and len(nxt) < P["max_words"]
                    and not p[0].eos and p[0].pause <= PAUSE_BREAK):
                phrases[k + 1] = p + nxt
                k += 1
                continue
            if (prev and not is_beat(prev) and len(prev) < P["max_words"]
                    and not prev[-1].eos):
                prev.append(p[0])
                k += 1
                continue
        merged.append(p)
        k += 1
    return merged


# ---------------------------------------------------------------- mapping

def _hash(s: str) -> int:
    return zlib.crc32(s.encode("utf-8"))


def _scale(e: float, P: dict) -> float:
    lo, hi = P["scale_min"], P["scale_max"]
    if e < 0.2:
        return lo
    t = (e - 0.2) / 0.8
    t = t * t * (3 - 2 * t)  # smoothstep: stays modest, then jumps for heroes
    return lo + (hi - lo) * t


def _style_phrase(p: list[W], P: dict, preset: str, width: int, height: int,
                  pidx: int, heroes: set[int]) -> list[dict]:
    font = min(width, height) * BASE_FONT_FRAC
    top = max(p, key=lambda w: (w.emph, w.loud))
    out = []
    for w in p:
        e = w.emph
        beat = w.i in heroes
        sc = _scale(e, P)
        if beat:
            sc *= P["single_boost"]
        elif w is top:
            sc = min(sc, P["phrase_cap"])
            # flat delivery still needs a focal word: the phrase's best
            # content word always stands out from its neighbours
            if not w.stop and e >= 0.25:
                floor = P.get("top_floor", 1.2)
                if len(p) == 1:
                    floor = max(floor, P.get("lone_floor", 1.6))
                sc = max(sc, floor)
        else:
            # restraint: only one word per phrase gets to be really big
            sc = min(sc, P["second_cap"])
        # never exceed the width
        fit = width * WIDTH_FRAC / (max(1, len(w.core)) * font * CHAR_W)
        sc = round(max(0.5, min(sc, fit, 3.0)), 2)

        # colour
        color = P["white"]
        is_key = (w is top and e >= 0.45) if preset == "hormozi" else e >= P["color_threshold"]
        if is_key:
            if w.number:
                color = P["number"]
            elif w.negative:
                color = P["negative"]
            else:
                color = P["accent"]

        # animation
        A = P["anims"]
        if beat:
            choices = A["single"]
            anim = choices[(_hash(w.core) + pidx) % len(choices)]
        elif e >= 0.6:
            anim = A["strong"]
        elif e >= 0.25:
            anim = A["normal"]
        else:
            anim = A["small"]
        if beat and (w.excl or (w.negative and w.loud >= 0.9)):
            anim = A["negative_hero"]
        elif A["power_glow"] and w.power == 2 and not beat and e >= 0.6:
            anim = "glow"
        if w.quest and anim in ("pop", "fade") and preset != "clean":
            anim = "slide_up"

        layer = "behind" if (P["allow_behind"] and sc >= P["behind_scale"]) else "front"
        weight = P["weights"]["stop" if w.stop else "content"]
        text = re.sub(r"[^\w'$%€£.,!?&-]", "", w.raw).upper()
        text = re.sub(r"[.,;:]+$", "", text)  # drop trailing soft punctuation
        out.append({
            "text": text, "start": round(w.start, 3), "end": round(w.end, 3),
            "scale": sc, "color": color, "anim": anim, "layer": layer,
            "weight": weight, "emphasis": e,
            "explain": " ".join(w.why) + f" => {e:.2f}",
        })
    return out


def _lines(words: list[dict], width: int, height: int) -> list[dict]:
    """Greedy wrap by estimated pixel width. A big word (scale>=1.5) starts a
    new line when 2+ words precede it ("YOU NEED TO / APPRECIATE" stacked
    look) and ends its line when 2+ words follow it."""
    font = min(width, height) * BASE_FONT_FRAC
    limit = width * WIDTH_FRAC
    gap = font * 0.3
    lines: list[list[dict]] = []
    cur: list[dict] = []
    cur_w = 0.0
    n = len(words)
    for k, w in enumerate(words):
        ww = len(w["text"]) * font * CHAR_W * w["scale"]
        big_here = w["scale"] >= 1.5 and len(cur) >= 2
        big_prev = bool(cur) and cur[-1]["scale"] >= 1.5 and n - k >= 2
        if cur and (big_here or big_prev or cur_w + gap + ww > limit):
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append(w)
        cur_w += (gap if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    return [{"words": l} for l in lines]


def _pick_anchor(text: str, idx: int, options: list[str], history: list[str],
                 no_repeat: int) -> str:
    opts = list(dict.fromkeys(options))  # unique, ordered
    seq = options
    a = seq[_hash(f"{text}|{idx}") % len(seq)]
    n = len(opts)
    for step in range(n):
        cand = opts[(opts.index(a) + step) % n]
        three = len(history) >= 2 and history[-1] == history[-2] == cand
        recent = no_repeat and history[-no_repeat:].count(cand) > 0
        if not three and not (recent and n > no_repeat):
            return cand
    return a


# ---------------------------------------------------------------- API

def build_layout(words_json: dict, width: int, height: int,
                 preset: str = "motiversity") -> dict:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {list(PRESETS)}")
    P = PRESETS[preset]
    raw = [w for w in words_json.get("words", []) if str(w.get("text", "")).strip()]
    ws = _parse(raw)
    _score(ws)
    heroes = _pick_heroes(ws, P)
    phrases = _chunk(ws, P, _max_chars(width, height), heroes)

    out = []
    history: list[str] = []
    for k, p in enumerate(phrases):
        words = _style_phrase(p, P, preset, width, height, k, heroes)
        has_behind = any(w["layer"] == "behind" for w in words)
        beat = any(x.i in heroes for x in p)
        text = " ".join(w["text"] for w in words)
        if beat and preset != "motiversity":
            anchor = "center"
        else:
            opts = P["behind_anchors"] if has_behind else P["anchors"]
            anchor = _pick_anchor(text, k, opts, history, P["no_repeat"])
        history.append(anchor)

        start = p[0].start
        nxt = phrases[k + 1][0].start if k + 1 < len(phrases) else None
        # linger through short gaps (max MAX_HOLD), never overlap the next one
        end = max(p[-1].end + MAX_HOLD, start + MIN_PHRASE)
        if nxt is not None:
            end = min(end, max(nxt, p[-1].end))
        out.append({
            "start": round(start, 3), "end": round(end, 3), "anchor": anchor,
            "align": "left" if anchor == "top-left" else "center",
            "lines": _lines(words, width, height),
        })
    return {"width": width, "height": height, "preset": preset, "phrases": out}


def pretty(layout: dict) -> str:
    rows = [f"preset={layout['preset']}  {layout['width']}x{layout['height']}  "
            f"phrases={len(layout['phrases'])}"]
    for p in layout["phrases"]:
        lines = " / ".join(" ".join(w["text"] for w in l["words"]) for l in p["lines"])
        rows.append(f"\n[{p['start']:6.2f}-{p['end']:6.2f}] {p['anchor']:<8} | {lines}")
        for l in p["lines"]:
            for w in l["words"]:
                rows.append(f"    {w['text']:<14} x{w['scale']:<4} {w['anim']:<9} "
                            f"{w['layer']:<6} {w['weight']:<5} {w['color']}  "
                            f"{w['explain']}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="words.json -> layout.json")
    ap.add_argument("words_json")
    ap.add_argument("layout_json")
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--preset", default="motiversity", choices=list(PRESETS))
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    with open(a.words_json) as f:
        wj = json.load(f)
    layout = build_layout(wj, a.width, a.height, a.preset)
    with open(a.layout_json, "w") as f:
        json.dump(layout, f, indent=1)
    if not a.quiet:
        print(pretty(layout))


if __name__ == "__main__":
    main()
