"""Per-frame person mattes so captions can sit behind the speaker.

Writes ``out_dir/%06d.png`` (8-bit L, 255 = person, full video resolution,
one per decoded frame, index from 0) and ``out_dir/meta.json``.

Default model is ``isnet-general-use``: on the reference clip (white suit on a
blue stage) ``u2net_human_seg`` drops most of the bright suit, while ISNet
keeps the whole body. ISNet also likes big on-screen text, so masks are
cleaned with a connected-component filter (keep blobs that touch the bottom
edge or are comparable in size to the largest blob).

Speed: on macOS the default backend is "auto" = Apple Vision person
segmentation (Neural Engine, ~16 ms/frame vs ~420 ms for ISNet@1024, IoU 0.96
against ISNet on the reference clip), run on a thread pool. Frames where Vision
finds no person fall back to ISNet so non-human subjects (products, objects)
are still matted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageFilter

DEFAULT_MODEL = "isnet-general-use"


# --------------------------------------------------------------------------- io
def probe(video_path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate",
         "-of", "json", video_path],
        capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "30/1"
    if rate in ("0/0", ""):
        rate = s.get("r_frame_rate", "30/1")
    num, den = (rate.split("/") + ["1"])[:2]
    fps = float(num) / float(den) if float(den) else 30.0
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": fps}


def iter_frames(video_path: str, width: int, height: int):
    """Yield HxWx3 uint8 frames decoded by ffmpeg (rgb24 rawvideo pipe)."""
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", video_path, "-map", "0:v:0",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


# ---------------------------------------------------------------------- model
def _providers() -> list[str]:
    import onnxruntime as ort
    avail = ort.get_available_providers()
    prefs = ["CUDAExecutionProvider", "CoreMLExecutionProvider"]
    return [p for p in prefs if p in avail][:1] + ["CPUExecutionProvider"]


def make_session(model: str):
    import warnings
    from rembg import new_session
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return new_session(model, providers=_providers())
        except Exception:
            return new_session(model, providers=["CPUExecutionProvider"])


def infer_mask(session, frame: np.ndarray, inf_size: tuple[int, int]) -> np.ndarray:
    """Return float32 mask in [0,1] at inf_size (w, h)."""
    img = Image.fromarray(frame)
    if img.size != inf_size:
        img = img.resize(inf_size, Image.BILINEAR)
    m = session.predict(img)[0].convert("L")
    if m.size != inf_size:
        m = m.resize(inf_size, Image.BILINEAR)
    return np.asarray(m, np.float32) / 255.0


# ----------------------------------------------------------- Apple Vision
_VISION_OK: bool | None = None


def vision_available() -> bool:
    global _VISION_OK
    if _VISION_OK is None:
        try:
            import Vision  # noqa: F401  (pyobjc-framework-Vision)
            import Quartz  # noqa: F401
            _VISION_OK = hasattr(Vision, "VNGeneratePersonSegmentationRequest")
        except Exception:
            _VISION_OK = False
    return _VISION_OK


def vision_mask(frame: np.ndarray, inf_size: tuple[int, int], quality: int = 1) -> np.ndarray:
    """Person mask via Apple Vision. quality: 0 accurate, 1 balanced, 2 fast.
    Thread-safe (one request/handler per call)."""
    import io
    import Quartz
    import Vision
    from Foundation import NSData
    import objc
    with objc.autorelease_pool():
        buf = io.BytesIO()
        Image.fromarray(frame).save(buf, "BMP")
        raw = buf.getvalue()
        data = NSData.dataWithBytes_length_(raw, len(raw))
        req = Vision.VNGeneratePersonSegmentationRequest.alloc().initWithCompletionHandler_(None)
        req.setQualityLevel_(quality)
        req.setOutputPixelFormat_(1278226488)  # kCVPixelFormatType_OneComponent8
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        ok, _err = handler.performRequests_error_([req], None)
        res = req.results() if ok else None
        if not res:
            return np.zeros((inf_size[1], inf_size[0]), np.float32)
        pb = res[0].pixelBuffer()
        Quartz.CVPixelBufferLockBaseAddress(pb, 1)
        try:
            w, h = Quartz.CVPixelBufferGetWidth(pb), Quartz.CVPixelBufferGetHeight(pb)
            bpr = Quartz.CVPixelBufferGetBytesPerRow(pb)
            a = np.frombuffer(Quartz.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * h),
                              np.uint8).reshape(h, bpr)[:, :w].copy()
        finally:
            Quartz.CVPixelBufferUnlockBaseAddress(pb, 1)
    m = Image.fromarray(a)
    if m.size != inf_size:
        m = m.resize(inf_size, Image.BILINEAR)
    return np.asarray(m, np.float32) / 255.0


def vision_has_human(frame: np.ndarray, min_conf: float = 0.5) -> bool:
    """Human (upper-body) detector -- far fewer false positives than reading
    the segmentation mask (which lights up on e.g. a parrot's beak)."""
    import io
    import Vision
    from Foundation import NSData
    import objc
    with objc.autorelease_pool():
        buf = io.BytesIO()
        Image.fromarray(frame).save(buf, "BMP")
        raw = buf.getvalue()
        data = NSData.dataWithBytes_length_(raw, len(raw))
        req = Vision.VNDetectHumanRectanglesRequest.alloc().initWithCompletionHandler_(None)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        ok, _ = handler.performRequests_error_([req], None)
        res = req.results() if ok else None
        return bool(res) and any(r.confidence() >= min_conf for r in res)


class Matter:
    """Picks the backend and produces masks for many frames in parallel."""

    def __init__(self, backend: str, model: str, inf_size: tuple[int, int],
                 workers: int, min_person_area: float = 0.004):
        if backend == "auto":
            backend = "vision" if vision_available() else "isnet"
        self.backend, self.model, self.inf_size = backend, model, inf_size
        self.workers = max(1, workers)
        self.min_area = min_person_area
        self._isnet = None
        self._lock = __import__("threading").Lock()
        self.fallbacks = 0
        if backend == "isnet":
            self.workers = 1  # one ONNX session already uses all cores / CoreML

    def _isnet_mask(self, frame):
        with self._lock:
            if self._isnet is None:
                self._isnet = make_session(self.model)
            return infer_mask(self._isnet, frame, self.inf_size)

    def one(self, frame: np.ndarray) -> np.ndarray:
        if self.backend == "vision":
            m = vision_mask(frame, self.inf_size)
            if float(m.mean()) >= self.min_area:
                return m
            self.fallbacks += 1  # no person -> look for any salient object
        return self._isnet_mask(frame)

    def many(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        if self.workers == 1 or len(frames) == 1:
            return [self.one(f) for f in frames]
        from concurrent.futures import ThreadPoolExecutor
        if not hasattr(self, "_pool"):
            self._pool = ThreadPoolExecutor(self.workers)
        return list(self._pool.map(self.one, frames))

    def close(self):
        if hasattr(self, "_pool"):
            self._pool.shutdown()


def _has_person(video_path, W, H, inf_size, n_total: int, samples: int = 8) -> bool:
    """Probe a handful of evenly spaced frames with Vision (~0.15 s total)."""
    if n_total <= 0:
        return True
    want = set(np.linspace(0, n_total - 1, samples).astype(int).tolist())
    for i, fr in enumerate(iter_frames(video_path, W, H)):
        if i in want and vision_has_human(fr):
            return True
        if i >= max(want):
            break
    return False


def clean_mask(m: np.ndarray, min_rel_area: float = 0.25) -> np.ndarray:
    """Drop blobs that are neither touching the bottom edge nor large."""
    try:
        from scipy import ndimage
    except Exception:  # scipy missing -> no cleanup
        return m
    lab, n = ndimage.label(m > 0.5)
    if n <= 1:
        return m
    areas = np.bincount(lab.ravel())[1:]
    bottom = set(np.unique(lab[-2:, :]).tolist()) - {0}
    biggest = areas.max()
    keep = np.zeros(n + 1, bool)
    for i, a in enumerate(areas, start=1):
        if i in bottom or a >= min_rel_area * biggest:
            keep[i] = True
    region = keep[lab]
    # include soft edge pixels around kept blobs, kill everything else
    region = ndimage.binary_dilation(region, iterations=3)
    return m * region


# ----------------------------------------------------------------------- main
def make_mattes(video_path: str, out_dir: str, model: str = DEFAULT_MODEL,
                max_side: int = 512, every_n: int = 2, ema: float = 0.6,
                feather: float | None = None, clean: bool = True,
                force: bool = False, verbose: bool = True,
                backend: str = "auto", workers: int = 0,
                progress=None, motion_iou: float = 0.97) -> dict:
    """Generate per-frame person masks. Returns meta dict.

    ema: weight of the current mask (1.0 = no smoothing).
    feather: Gaussian blur radius in full-res px (default ~0.4% of short side).
    """
    info = probe(video_path)
    W, H, fps = info["width"], info["height"], info["fps"]
    meta_path = os.path.join(out_dir, "meta.json")

    if not force and os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                old = json.load(f)
            n_old = int(old.get("frames", -1))
            want = "vision" if backend == "auto" and vision_available() else \
                ("isnet" if backend == "auto" else backend)
            if (old.get("width"), old.get("height")) == (W, H) and n_old > 0 \
                    and old.get("backend", "isnet") == want \
                    and os.path.exists(os.path.join(out_dir, f"{n_old - 1:06d}.png")) \
                    and n_old == _count_frames(video_path):
                if verbose:
                    print(f"[matte] cache hit: {out_dir} ({n_old} frames)")
                return old
        except Exception:
            pass

    os.makedirs(out_dir, exist_ok=True)
    every_n = max(1, int(every_n))
    scale = min(1.0, max_side / max(W, H))
    inf_size = (max(1, round(W * scale)), max(1, round(H * scale)))
    if feather is None:
        feather = max(0.8, 0.004 * min(W, H))

    workers = workers or int(os.environ.get("KC_MATTE_WORKERS", "0") or 0) or 2
    matter = Matter(backend, model, inf_size, workers)
    if matter.backend == "vision" and not _has_person(video_path, W, H, inf_size, n_total=_count_frames(video_path)):
        # no person anywhere -> salient-object mode with ISNet. Objects move
        # slowly and ISNet is ~30x slower, so infer sparsely and interpolate.
        matter = Matter("isnet", model, inf_size, 1)
        every_n = max(every_n, 6)
        if verbose:
            print("[matte] no person found -> object mode (ISNet, sparse keyframes)")
    total = _count_frames(video_path) if progress else -1
    from concurrent.futures import ThreadPoolExecutor
    writer = ThreadPoolExecutor(max(2, workers))
    futs = []
    state = {"ema": None, "written": 0}

    def save(arr: np.ndarray, i: int):
        img = Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
        if img.size != (W, H):
            img = img.resize((W, H), Image.BILINEAR)
        if feather > 0:
            img = img.filter(ImageFilter.GaussianBlur(feather))
        img.save(os.path.join(out_dir, f"{i:06d}.png"), compress_level=1)

    def emit(m: np.ndarray):
        # EMA is order-dependent -> done here, sequentially; encoding is parallel
        if state["ema"] is None or ema >= 1.0:
            sm = m
        else:
            sm = ema * m + (1.0 - ema) * state["ema"]
        state["ema"] = sm
        futs.append(writer.submit(save, sm, state["written"]))
        state["written"] += 1

    t0 = time.time()
    n_inf = 0
    prev_key = None          # (index, mask) of last inferred frame
    pending: list[int] = []  # indices waiting for the next keyframe
    held: dict[int, np.ndarray] = {}  # skipped frames, kept in case motion is high
    stats = {"motion_refined": 0}
    batch: list[tuple[int, np.ndarray]] = []
    last_frame = None
    idx = -1
    B = max(1, matter.workers * 2)

    def flush():
        nonlocal prev_key, pending, n_inf
        if not batch:
            return
        masks = matter.many([f for _, f in batch])
        for (k, _), m in zip(batch, masks):
            if clean:
                m = clean_mask(m)
            n_inf += 1
            if prev_key is not None:
                k0, m0 = prev_key
                gap = [j for j in pending if j < k]
                A, Bm = m0 > 0.5, m > 0.5
                iou = (A & Bm).sum() / max(1, (A | Bm).sum())
                if gap and iou < motion_iou and matter.backend == "vision" \
                        and all(j in held for j in gap):
                    # fast motion between keyframes: infer the skipped frames too
                    for j, mj in zip(gap, matter.many([held[j] for j in gap])):
                        emit(clean_mask(mj) if clean else mj)
                        n_inf += 1
                    stats["motion_refined"] += len(gap)
                else:
                    for j in gap:
                        a = (j - k0) / (k - k0)
                        emit((1 - a) * m0 + a * m)
                for j in gap:
                    held.pop(j, None)
                pending = [j for j in pending if j > k]
            emit(m)
            prev_key = (k, m)
        batch.clear()
        if progress and total > 0:
            progress(min(0.999, state["written"] / total))

    for idx, frame in enumerate(iter_frames(video_path, W, H)):
        last_frame = frame
        if idx % every_n != 0:
            pending.append(idx)
            held[idx] = frame.copy()
            continue
        batch.append((idx, frame.copy()))
        if len(batch) >= B:
            flush()
            if verbose and n_inf % 100 < B:
                el = time.time() - t0
                print(f"[matte] frame {idx}  {el / max(1, n_inf):.3f}s/frame", file=sys.stderr)
    flush()

    n = idx + 1
    if pending:
        # tail after the last keyframe: infer the final frame and interpolate
        m = matter.one(last_frame)
        if clean:
            m = clean_mask(m)
        n_inf += 1
        k0, m0 = prev_key
        last = pending[-1]
        for j in pending:
            a = (j - k0) / (last - k0)
            emit((1 - a) * m0 + a * m)
    for f in futs:
        f.result()
    writer.shutdown()
    matter.close()

    assert state["written"] == n, (state["written"], n)
    el = time.time() - t0
    meta = {"fps": fps, "frames": n, "width": W, "height": H,
            "model": model, "backend": matter.backend, "workers": matter.workers,
            "isnet_fallback_frames": matter.fallbacks,
            "motion_refined_frames": stats["motion_refined"],
            "every_n": every_n, "max_side": max_side,
            "inferences": n_inf,
            "sec_per_inference": round(el / max(1, n_inf), 4)}
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    if verbose:
        print(f"[matte] motion-refined {stats['motion_refined']} frames")
        print(f"[matte] {n} masks -> {out_dir} in {el:.1f}s via {matter.backend} "
              f"x{matter.workers} ({n_inf} inferences, {el / max(1, n_inf):.3f}s each, "
              f"{matter.fallbacks} ISNet fallbacks)")
    return meta


def _count_frames(video_path: str) -> int:
    """Number of frames ffmpeg decodes (exact; uses -count_frames)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", video_path],
        capture_output=True, text=True).stdout.strip()
    try:
        return int(out.split(",")[0])
    except ValueError:
        return -1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-frame person mattes")
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-side", type=int, default=512)
    ap.add_argument("--every-n", type=int, default=2)
    ap.add_argument("--ema", type=float, default=0.6)
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backend", default="auto", choices=["auto", "vision", "isnet"])
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args(argv)
    make_mattes(a.video, a.out_dir, model=a.model, max_side=a.max_side,
                every_n=a.every_n, ema=a.ema, clean=not a.no_clean, force=a.force,
                backend=a.backend, workers=a.workers)


if __name__ == "__main__":
    main()
