"""One call for "normal people": drop in audio OR video, get a captioned video.

    run_job(input_path, out_path, opts, progress) -> dict

- Audio file (or video with no real picture) -> plain canvas (black by
  default) at the chosen aspect, captions on top. No matte needed.
- Video -> person/object matte, captions auto-placed around the subject
  (big words tucked behind the head), optional background removal.

Everything in `opts` is optional; DEFAULTS are tuned so nobody has to touch
anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

DEFAULTS: dict = {
    "preset": "motiversity",     # motiversity | hormozi | clean
    "font": "montserrat",        # any id from kc.fonts.list_fonts()
    "text_color": "#FFFFFF",
    "accent_color": None,        # None = preset decides; else colour for emphasised words
    "size": 1.0,                 # global text size multiplier (0.6 .. 1.6)
    "position": "auto",          # auto | center | lower | top | bottom | top-left
    "behind": True,              # allow big words behind the speaker
    "background": "keep",        # video: keep | black | <hex>; audio: canvas colour
    "canvas": "9:16",            # audio only: 9:16 | 16:9 | 1:1 | 4:5
    "language": None,            # None = auto-detect
    "model": "small",            # whisper model
}

CANVAS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
COLOR_NAMES = {"black": "#000000", "white": "#FFFFFF"}

Progress = Callable[[str, float, str], None]   # (stage, fraction 0..1 overall, message)


class PipelineError(ValueError):
    """ValueError with a stable `code` so the UI can localise the message."""

    def __init__(self, msg: str, code: str):
        super().__init__(msg)
        self.code = code
        self.params: dict = {}


def _noop(stage: str, frac: float, msg: str) -> None:
    pass


def probe_kind(path: str) -> dict:
    """{'kind': 'video'|'audio', 'width', 'height', 'duration', 'has_audio'}"""
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path,
    ], text=True)
    info = json.loads(out)
    streams = info.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
            and s.get("codec_name") not in ("mjpeg", "png", "bmp")]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    dur = float(info.get("format", {}).get("duration") or 0)
    if vids:
        v = vids[0]
        return {"kind": "video", "width": int(v["width"]), "height": int(v["height"]),
                "duration": dur, "has_audio": bool(auds)}
    if auds:
        return {"kind": "audio", "width": 0, "height": 0, "duration": dur, "has_audio": True}
    raise PipelineError("音声も映像も見つかりませんでした", "no_streams")


def _hex(c: str | None, default: str) -> str:
    if not c:
        return default
    return COLOR_NAMES.get(str(c).lower(), c)


def make_canvas(audio_path: str, out_path: str, aspect: str = "9:16",
                color: str = "#000000", fps: int = 30) -> str:
    w, h = CANVAS.get(aspect, CANVAS["9:16"])
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", f"color=c={color.replace('#', '0x')}:s={w}x{h}:r={fps}",
        "-i", audio_path, "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", out_path,
    ], check=True)
    return out_path


def apply_overrides(layout: dict, opts: dict) -> dict:
    """User-facing knobs applied on top of the director's decisions."""
    from kc.fonts import get as get_font
    font = (opts.get("font") or "montserrat").lower()
    font_entry = get_font(font)
    lower_case = bool(font_entry and font_entry.get("case") == "lower")
    size = float(opts.get("size") or 1.0)
    text_color = _hex(opts.get("text_color"), "#FFFFFF")
    accent = opts.get("accent_color")
    position = opts.get("position") or "auto"
    behind = opts.get("behind", True)
    for ph in layout.get("phrases", []):
        if font != "montserrat" and font_entry:
            ph["font"] = font
        if position != "auto":
            ph["anchor"] = position
            ph.pop("pos", None)
        for ln in ph.get("lines", []):
            for w in ln.get("words", []):
                w["scale"] = round(float(w.get("scale", 1.0)) * size, 3)
                if lower_case:  # script/handwriting in ALL CAPS is unreadable
                    t = str(w.get("text", "")).lower()
                    w["text"] = "I" + t[1:] if t in ("i", "i'm", "i'll", "i've", "i'd") else t
                is_plain = str(w.get("color", "#FFFFFF")).upper() == "#FFFFFF"
                if is_plain:
                    w["color"] = text_color
                if accent and float(w.get("emphasis", 0)) >= 0.6:
                    w["color"] = _hex(accent, w["color"])
                if not behind:
                    w["layer"] = "front"
    return layout


def run_job(input_path: str, out_path: str, opts: dict | None = None,
            progress: Progress | None = None, work_root: str = "work") -> dict:
    from kc.render import render
    from kc.style import build_layout
    from kc.transcribe import transcribe

    o = {**DEFAULTS, **{k: v for k, v in (opts or {}).items() if v is not None}}
    cb = progress or _noop
    info = probe_kind(input_path)
    if not info["has_audio"]:
        raise PipelineError("音声トラックがありません（文字おこしできません）", "no_audio")

    # cache dir keyed on file content so re-runs with new settings are instant
    h = hashlib.sha1()
    with open(input_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    work = Path(work_root) / h.hexdigest()[:16]
    work.mkdir(parents=True, exist_ok=True)

    video = input_path
    is_audio = info["kind"] == "audio"
    canvas = None
    if is_audio:
        # solid-colour frames are generated in memory by the renderer; no
        # intermediate canvas video to encode and then decode again
        cb("prepare", 0.02, "準備しています")
        cw, ch = CANVAS.get(o["canvas"], CANVAS["9:16"])
        canvas = {"width": cw, "height": ch, "fps": 30, "audio": input_path,
                  "color": _hex(o["background"] if o["background"] not in (None, "keep")
                                else "black", "#000000")}

    words_path = work / f"words_{o['model']}_{o['language'] or 'auto'}.json"
    matte_dir = None if is_audio else str(work / "mattes")

    # Transcription (CPU / CTranslate2) and person matting (Neural Engine via
    # Apple Vision, or CoreML) use different hardware -> run them side by side.
    from concurrent.futures import ThreadPoolExecutor
    st = {"words_done": words_path.exists(), "matte_frac": 0.0}

    def report():
        if not st["words_done"]:
            msg = "文字おこし中" + ("（人物検出も同時に実行中）" if matte_dir else "")
            cb("transcribe", 0.05 + 0.3 * st["matte_frac"], msg)
        elif matte_dir:
            cb("matte", 0.35 + 0.35 * st["matte_frac"],
               f"人物・物体を検出中（{int(st['matte_frac'] * 100)}%）")

    def do_words():
        if not words_path.exists():
            transcribe(input_path, str(words_path), model=o["model"], language=o["language"])
        st["words_done"] = True
        report()

    def do_matte():
        from kc.matte import make_mattes

        def mp(f):
            st["matte_frac"] = f
            report()
        make_mattes(input_path, matte_dir, verbose=False, progress=mp)
        st["matte_frac"] = 1.0

    report()
    with ThreadPoolExecutor(2) as ex:
        jobs = [ex.submit(do_words)] + ([ex.submit(do_matte)] if matte_dir else [])
        for j in jobs:
            j.result()
    words = json.loads(words_path.read_text())

    w, h = (CANVAS.get(o["canvas"], CANVAS["9:16"]) if is_audio
            else (info["width"], info["height"]))
    cb("layout", 0.7, "演出を決めています")
    layout = build_layout(words, w, h, preset=o["preset"])
    if not o.get("behind", True) or is_audio:
        for ph in layout["phrases"]:
            for ln in ph["lines"]:
                for x in ln["words"]:
                    x["layer"] = "front"
    if is_audio and o["position"] == "auto":
        # nothing to avoid on a plain canvas: keep the eye in the middle
        for ph in layout["phrases"]:
            ph["anchor"] = "center"
    layout = apply_overrides(layout, o)
    if matte_dir and o["position"] == "auto":
        # after overrides, so placement measures the real font/size
        from kc.placement import auto_place
        layout = auto_place(layout, matte_dir, w, h, video_path=input_path)
    (work / "layout_last.json").write_text(json.dumps(layout, ensure_ascii=False, indent=1))

    cb("render", 0.75, "動画を書き出しています")
    bg = None
    if not is_audio and o["background"] not in (None, "keep"):
        bg = _hex(o["background"], "#000000")

    def render_cb(frac: float) -> None:
        cb("render", 0.75 + 0.25 * frac, "動画を書き出しています")

    render(video, layout, out_path, matte_dir=matte_dir, bg=bg, progress=render_cb,
           canvas=canvas)
    cb("done", 1.0, "完成")
    text = " ".join(x["text"] for x in words.get("words", []))
    return {"out": out_path, "kind": info["kind"], "transcript": text,
            "language": words.get("language"), "layout": str(work / "layout_last.json")}
