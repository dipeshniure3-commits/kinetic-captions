# kinetic_captions — module contract

Goal: input talking-head video → output video with motivational-short style
kinetic captions (bold white caps, 1–4 words at a time, per-word size/colour/
animation decided by the word itself and how it is spoken, text optionally
placed BEHIND the speaker using a person matte).

Run everything with `.venv/bin/python` (Python 3.11). Do NOT use OpenCV
(dylib clash with faster-whisper's PyAV). Use numpy + Pillow + ffmpeg CLI.
Reference clip: `samples/ref.mp4` (526x278, 18.5 s, Kanye speech).

Pipeline (each stage writes JSON/files into a work dir, e.g. `work/<name>/`):

```
video ──transcribe.py──> words.json ──style.py──> layout.json ─┐
  └────matte.py──────> mattes/ (per-frame masks) ──────────────┴─ render.py ─> out.mp4
```

## 1. `kc/transcribe.py`
`transcribe(video_path: str, out_json: str, model: str = "small", language: str | None = None) -> list[dict]`

words.json:
```json
{"language": "en", "duration": 18.53,
 "words": [{"text": "Nothing", "start": 0.42, "end": 0.80, "prob": 0.97,
            "loudness": 0.81, "pitch": 0.55, "pause_after": 0.12}]}
```
- faster-whisper (cached HF models `Systran/faster-whisper-{tiny,base,small,large-v3}`), `word_timestamps=True`, vad filter on.
- `text` stripped of leading space; keep trailing punctuation (style.py uses it).
- `loudness`: RMS of that word's audio span normalised 0..1 across the clip (robust: use percentiles).
- `pitch`: optional 0..1 relative pitch / energy-of-emphasis proxy (spectral centroid is fine); 0.5 if unknown.
- `pause_after`: gap (s) to next word's start (0 for last word).
- CLI: `python -m kc.transcribe in.mp4 out.json [--model small] [--lang en]`.

## 2. `kc/style.py`  (the "director")
`build_layout(words_json: dict, width: int, height: int, preset: str = "motiversity") -> dict`

layout.json:
```json
{"width": 526, "height": 278, "preset": "motiversity",
 "phrases": [
   {"start": 0.42, "end": 1.60, "anchor": "top-left|top|center|lower|bottom",
    "lines": [
      {"words": [
        {"text": "NOTHING", "start": 0.42, "end": 0.80,
         "scale": 1.6,             // relative to base font size (base = height*0.12)
         "color": "#FFFFFF",
         "anim": "pop|slam|slide_up|fade|typewriter|shake|zoom_out|glow",
         "layer": "behind|front",  // behind = drawn under the speaker cutout
         "weight": "black|bold",
         "emphasis": 0.0}          // 0..1 how strongly emphasised (for render fx)
      ]}
    ]}
 ]}
```
Rules to implement (research + extend): phrase chunking (1–4 words, break on
punctuation / pauses > ~0.25 s / max chars per line), UPPERCASE, power-word
lexicon (big + accent colour + slam), stop-words small, numbers & money
highlighted, negative words red-ish, loud words bigger, question/exclamation
handling, single-word phrases for dramatic beats, layer=behind for large
emphasised words so the head can occlude them. Presets: `motiversity`
(white, black-weight, minimal colour), `hormozi` (yellow/green accents,
centered), `clean` (no fx). Deterministic (no randomness unless seeded).
CLI: `python -m kc.style words.json layout.json --width W --height H [--preset P]`.

## 3. `kc/matte.py`
`make_mattes(video_path: str, out_dir: str, model: str = "isnet-general-use", max_side: int = 512) -> dict`
- Writes `out_dir/%06d.png` 8-bit grayscale masks (255 = person) at FULL video
  resolution, one per decoded frame (frame index from 0, same count ffmpeg
  decodes), plus `out_dir/meta.json` `{"fps": .., "frames": N, "width": .., "height": ..}`.
- Use rembg (models in `~/.rembg`, `isnet-general-use` already cached; others may download from rembg's official GitHub release URLs). Run inference downscaled to `max_side`, upsample mask.
- Temporal smoothing (EMA over a few frames) to stop flicker; slight feather.
- Option `every_n` to infer every N frames and interpolate for speed.
- CLI: `python -m kc.matte in.mp4 out_dir [--model ...] [--every-n 1]`.

## 4. `kc/render.py`
`render(video_path: str, layout: dict, out_path: str, matte_dir: str | None = None, font_dir: str = "assets/fonts") -> None`
- Decode frames with ffmpeg → rawvideo rgb24 pipe; composite with Pillow/numpy; encode with ffmpeg pipe (libx264, crf 18, yuv420p) and copy original audio.
- Compositing order per frame: frame → `behind` text → person cutout (frame * mask) → `front` text.
  If no matte, everything is front.
- Animations driven by word start/end time (word appears at its `start`, stays until phrase `end`), all deterministic functions of t: pop (scale overshoot 0.6→1.1→1.0, ~0.15 s), slam (from 2.5x + motion blur-ish fade in), slide_up, fade, typewriter (letters reveal), shake (small jitter for high emphasis), zoom_out, glow (soft blurred halo in accent colour).
- Subtle drop shadow / stroke for legibility. Lines auto-fit within width*0.9.
- Fonts: put OFL fonts in `assets/fonts/` (e.g. Montserrat Black/ExtraBold, Anton, Bebas Neue from github.com/google/fonts). Fallback to a system bold font.
- CLI: `python -m kc.render in.mp4 layout.json out.mp4 [--mattes dir]`.

## 5. `kc/cli.py` (integration, done by lead)
`python -m kc in.mp4 -o out.mp4 [--preset motiversity] [--model small] [--no-matte]`
