"""Stage 1: video -> words.json (word timings + loudness / pitch features).

CLI: python -m kc.transcribe in.mp4 out.json [--model small] [--lang en] [--prompt "..."]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np

SR = 16000
MIN_SPAN = 0.06  # seconds; zero/short word spans are widened to this for features


def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """Decode any media file to mono float32 at `sr` Hz via the ffmpeg CLI."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1",
           "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='ignore')}") from e
    return np.frombuffer(raw, dtype=np.float32).copy()


def _span(audio: np.ndarray, start: float, end: float, sr: int = SR) -> np.ndarray:
    if end - start < MIN_SPAN:
        mid = 0.5 * (start + end)
        start, end = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
    a = max(0, int(round(start * sr)))
    b = min(len(audio), int(round(end * sr)))
    return audio[a:b] if b > a else audio[0:0]


def _rms_db(x: np.ndarray) -> float | None:
    if x.size == 0:
        return None
    return float(20.0 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9))


def _f0_autocorr(x: np.ndarray, sr: int = SR, fmin: float = 70.0, fmax: float = 400.0,
                 frame: float = 0.04, hop: float = 0.01) -> float | None:
    """Median f0 (Hz) over voiced frames using normalised autocorrelation."""
    n = int(frame * sr)
    h = int(hop * sr)
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    lag_min, lag_max = int(sr / fmax), min(int(sr / fmin), n - 1)
    win = np.hanning(n)
    # energy gate relative to the span's loudest frame
    frames = [x[i:i + n] for i in range(0, x.size - n + 1, h)]
    if not frames:
        return None
    energies = np.array([np.sqrt(np.mean(f ** 2)) for f in frames])
    gate = max(energies.max() * 0.25, 1e-4)
    f0s = []
    for f, e in zip(frames, energies):
        if e < gate:
            continue
        f = (f - f.mean()) * win
        spec = np.fft.rfft(f, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lag_min:lag_max + 1]
        if seg.size == 0:
            continue
        k = int(np.argmax(seg))
        if seg[k] < 0.3:  # unvoiced / noisy
            continue
        lag = lag_min + k
        # parabolic interpolation for sub-sample lag
        if 0 < k < seg.size - 1:
            y0, y1, y2 = seg[k - 1], seg[k], seg[k + 1]
            d = y0 - 2 * y1 + y2
            if d != 0:
                lag = lag + 0.5 * (y0 - y2) / d
        f0s.append(sr / lag)
    return float(np.median(f0s)) if f0s else None


def _spectral_centroid(x: np.ndarray, sr: int = SR) -> float | None:
    if x.size < 32:
        return None
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    if spec.sum() <= 0:
        return None
    freqs = np.fft.rfftfreq(x.size, 1.0 / sr)
    return float((freqs * spec).sum() / spec.sum())


def _robust_norm(vals: list[float | None], default: float = 0.5,
                 lo_p: float = 5, hi_p: float = 95) -> list[float]:
    """Map values to 0..1 using 5th/95th percentiles; None -> default."""
    known = np.array([v for v in vals if v is not None], dtype=np.float64)
    if known.size == 0:
        return [default] * len(vals)
    lo, hi = np.percentile(known, lo_p), np.percentile(known, hi_p)
    out = []
    for v in vals:
        if v is None:
            out.append(default)
        elif hi - lo < 1e-9:
            out.append(default)
        else:
            out.append(float(np.clip((v - lo) / (hi - lo), 0.0, 1.0)))
    return out


def _trim_to_energy(words: list[dict], audio: np.ndarray, sr: int = SR,
                    hop: float = 0.01, min_dur: float = 0.08, pad: float = 0.03) -> None:
    """Shrink word spans that include leading/trailing silence (in place).

    Only trims, never extends. Threshold sits between the clip's noise floor and
    speech level, so it adapts to recording level.
    """
    h = int(hop * sr)
    n = len(audio) // h
    if n == 0 or not words:
        return
    fr = audio[: n * h].reshape(n, h).astype(np.float64)
    db = 20.0 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    floor, speech = np.percentile(db, 10), np.percentile(db, 90)
    if speech - floor < 10:  # no clear dynamic range; leave timings alone
        return
    thr = floor + 0.35 * (speech - floor)
    for w in words:
        if w["end"] - w["start"] <= min_dur + 2 * pad:
            continue
        a, b = int(w["start"] / hop), min(n, int(np.ceil(w["end"] / hop)))
        voiced = np.nonzero(db[a:b] > thr)[0]
        if voiced.size == 0:
            continue
        s = max(w["start"], (a + voiced[0]) * hop - pad)
        e = min(w["end"], (a + voiced[-1] + 1) * hop + pad)
        if e - s < min_dur:  # keep at least min_dur, centred on the voiced part
            mid = 0.5 * (s + e)
            s = max(w["start"], mid - min_dur / 2)
            e = min(w["end"], s + min_dur)
        w["start"], w["end"] = s, e


def _default_threads() -> int:
    """Performance cores + 2. Measured on M5 (4P+6E), 19 s clip: 4 thr 12.6 s,
    6 thr 10.6 s, 10 thr 19.9 s -- efficiency cores slow the whole decode."""
    env = int(os.environ.get("KC_WHISPER_THREADS", "0") or 0)
    if env:
        return env
    try:
        import subprocess
        p = int(subprocess.check_output(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                                        text=True, stderr=subprocess.DEVNULL).strip())
        return max(2, p)  # P-cores only: whisper shares the CPU with matting
    except Exception:
        return max(2, min(8, (os.cpu_count() or 4)))


_MODELS: dict = {}


def _load_model(model: str):
    """Loaded once per process (the UI server reuses it across jobs)."""
    if model in _MODELS:
        return _MODELS[model]
    from faster_whisper import WhisperModel
    kw = dict(device="cpu", compute_type="int8", cpu_threads=_default_threads())
    try:  # cached copy first: no network round-trip
        m = WhisperModel(model, local_files_only=True, **kw)
    except Exception:
        try:  # first run on this machine: download from Hugging Face
            m = WhisperModel(model, **kw)
        except Exception as e:
            raise RuntimeError(
                f"faster-whisper model '{model}' is not cached and could not be "
                "downloaded (check the internet connection)") from e
    _MODELS[model] = m
    return m


def transcribe(video_path: str, out_json: str, model: str = "small",
               language: str | None = None, initial_prompt: str | None = None) -> list[dict]:
    audio = load_audio(video_path)
    duration = round(len(audio) / SR, 3)

    wm = _load_model(model)
    # Run Silero VAD ourselves and decode each speech chunk on the real timeline
    # (clip_timestamps). faster-whisper's built-in vad_filter concatenates chunks,
    # which makes words at chunk joins swallow the removed silence.
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    chunks = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=200))
    clips: list[float] = []
    for c in chunks:
        clips += [c["start"] / SR, c["end"] / SR]

    raw = []
    lang = language
    if clips:
        segments, info = wm.transcribe(
            audio, language=language, word_timestamps=True, vad_filter=False,
            clip_timestamps=clips, initial_prompt=initial_prompt, beam_size=5,
            condition_on_previous_text=False,
        )
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if not text:
                    continue
                s, e = float(w.start), float(w.end)
                if e < s:
                    s, e = e, s
                raw.append({"text": text, "start": s, "end": e, "prob": float(w.probability)})
        lang = info.language
    lang = lang or "en"

    raw.sort(key=lambda w: w["start"])
    # enforce monotonic, non-overlapping spans
    for i in range(1, len(raw)):
        if raw[i]["start"] < raw[i - 1]["end"]:
            raw[i]["start"] = raw[i - 1]["end"]
            raw[i]["end"] = max(raw[i]["end"], raw[i]["start"])

    _trim_to_energy(raw, audio)

    loud_db, f0s, cents = [], [], []
    for w in raw:
        x = _span(audio, w["start"], w["end"])
        loud_db.append(_rms_db(x))
        # pitch needs a bit more context than tiny spans give
        f0s.append(_f0_autocorr(_span(audio, w["start"], max(w["end"], w["start"] + 0.1))))
        cents.append(_spectral_centroid(x))

    loud = _robust_norm(loud_db)
    # prefer f0; fall back to spectral centroid if f0 mostly missing
    voiced = sum(v is not None for v in f0s)
    if raw and voiced >= max(3, 0.3 * len(raw)):
        f0_log = [np.log2(v) if v is not None else None for v in f0s]
        pitch = _robust_norm(f0_log)
    else:
        pitch = _robust_norm(cents)

    words = []
    for i, w in enumerate(raw):
        nxt = raw[i + 1]["start"] if i + 1 < len(raw) else None
        words.append({
            "text": w["text"],
            "start": round(w["start"], 3),
            "end": round(w["end"], 3),
            "prob": round(w["prob"], 3),
            "loudness": round(loud[i], 3),
            "pitch": round(pitch[i], 3),
            "pause_after": round(max(0.0, nxt - w["end"]), 3) if nxt is not None else 0.0,
        })

    data = {"language": lang, "duration": duration, "words": words}
    d = os.path.dirname(os.path.abspath(out_json))
    os.makedirs(d, exist_ok=True)
    with open(out_json, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    return words


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Transcribe video to words.json")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--prompt", default=None, help="initial_prompt for whisper")
    a = ap.parse_args(argv)
    words = transcribe(a.input, a.output, model=a.model, language=a.lang, initial_prompt=a.prompt)
    for w in words:
        print(f"{w['start']:6.2f}-{w['end']:6.2f}  {w['text']:<16} p={w['prob']:.2f} "
              f"L={w['loudness']:.2f} P={w['pitch']:.2f} gap={w['pause_after']:.2f}")
    print(" ".join(w["text"] for w in words), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
