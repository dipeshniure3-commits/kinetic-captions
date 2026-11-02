"""Local web UI for Kinetic Captions (stdlib only).

    python -m app            # or: .venv/bin/python app/server.py
    python -m app --port 7861

Endpoints
    GET  /                    UI (app/static/index.html)
    GET  /static/<file>       UI assets
    POST /api/upload          raw body, header X-Filename (URL-encoded) -> file info
    POST /api/jobs            {"file_id": ..., "opts": {...}} -> {"id": job_id}
    GET  /api/job/<id>        status: state, stage, step, total, frac, msg, error, result
    GET  /api/fonts           caption fonts available for preview
    GET  /media/<file>        rendered outputs (HTTP Range supported)
    GET  /fonts/<file>        assets/fonts (for @font-face previews)

Errors are JSON {"error", "code", "error_i18n": {"ja", "en"}}; send `X-Lang: ja|en`
to choose the language of "error" (default ja).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
STATIC = APP_DIR / "static"
UPLOADS = APP_DIR / "uploads"
OUTPUTS = APP_DIR / "outputs"
FONTS = ROOT / "assets" / "fonts"

# kc.pipeline uses relative paths (work/, assets/fonts) -> run from project root
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kc import fonts as kcfonts  # noqa: E402
from kc.pipeline import probe_kind, run_job  # noqa: E402

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}
MAX_UPLOAD = 4 * 1024 ** 3          # 4 GB
CHUNK = 1 << 20


STAGES = {
    "audio": ["prepare", "transcribe", "layout", "render"],
    "video": ["prepare", "transcribe", "matte", "layout", "render"],
}

# whitelist of user options -> allowed values (None = free-form, validated below)
OPT_CHOICES = {
    "preset": {"motiversity", "hormozi", "clean"},
    "position": {"auto", "top", "center", "bottom", "lower"},
    "canvas": {"9:16", "16:9", "1:1", "4:5"},
    "language": {None, "en", "ja"},
    "model": {"base", "small", "large-v3"},
}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# ------------------------------------------------------------------ i18n
# Client sends `X-Lang: ja|en`; every user-facing message is looked up here.
LANGS = ("ja", "en")
DEFAULT_LANG = "ja"
MESSAGES: dict[str, dict[str, str]] = {
    "not_found": {"ja": "見つかりません", "en": "Not found."},
    "file_missing": {"ja": "ファイルが見つかりません", "en": "File not found."},
    "video_missing": {"ja": "動画が見つかりません", "en": "Video not found."},
    "font_missing": {"ja": "フォントが見つかりません", "en": "Font not found."},
    "job_missing": {"ja": "ジョブが見つかりません", "en": "Job not found."},
    "unsupported_type": {
        "ja": "このファイル形式には対応していません。音声（mp3 / wav / m4a / aac）か動画（mp4 / mov / webm）を選んでください。",
        "en": "This file type isn't supported. Choose audio (mp3 / wav / m4a / aac) or video (mp4 / mov / webm)."},
    "empty_file": {"ja": "ファイルが空です。", "en": "The file is empty."},
    "too_large": {"ja": "ファイルが大きすぎます（4GBまで）。", "en": "The file is too large (4 GB max)."},
    "upload_interrupted": {"ja": "アップロードが途中で止まりました。もう一度お試しください。",
                           "en": "The upload stopped partway. Please try again."},
    "unreadable": {"ja": "ファイルを読み込めませんでした。壊れているか、対応していない形式です。",
                   "en": "Couldn't read the file. It may be damaged or in an unsupported format."},
    "no_audio_upload": {"ja": "音声が入っていないため、文字おこしができません。音声つきのファイルを選んでください。",
                        "en": "This file has no sound, so it can't be transcribed. Choose a file with audio."},
    "input_too_long": {"ja": "入力が長すぎます。", "en": "The input is too long."},
    "bad_request": {"ja": "リクエストが正しくありません。", "en": "Invalid request."},
    "fonts_offline": {"ja": "Google Fonts に接続できませんでした。ネット接続を確認してください。",
                      "en": "Couldn't reach Google Fonts. Check your internet connection."},
    "upload_again": {"ja": "ファイルが見つかりません。もう一度アップロードしてください。",
                     "en": "File not found. Please upload it again."},
    # job failures
    "ffmpeg_failed": {"ja": "動画の変換（ffmpeg）に失敗しました。別のファイルでお試しください。",
                      "en": "Video conversion (ffmpeg) failed. Please try a different file."},
    "missing_tool": {"ja": "必要なファイルやツールが見つかりませんでした。",
                     "en": "A required file or tool could not be found."},
    "out_of_memory": {"ja": "メモリが足りません。精度を「速い」にして、もう一度お試しください。",
                      "en": "Not enough memory. Set Accuracy to \"Fast\" and try again."},
    "job_failed": {"ja": "処理中にエラーが発生しました。設定を変えるか、もう一度お試しください。",
                   "en": "Something went wrong while processing. Change the settings or try again."},
    # codes raised by kc.pipeline (PipelineError) and kc.fonts (FontError)
    "no_streams": {"ja": "音声も映像も見つかりませんでした", "en": "No audio or video was found in this file."},
    "no_audio": {"ja": "音声トラックがありません（文字おこしできません）",
                 "en": "There's no audio track, so it can't be transcribed."},
    "font_empty": {"ja": "フォント名かURLを入れてください", "en": "Enter a font name or URL."},
    "font_bad_name": {"ja": "「{name}」はフォント名として読み取れませんでした",
                      "en": "Couldn't read \"{name}\" as a font name."},
    "font_host": {"ja": "Google Fonts 以外からはダウンロードしません",
                  "en": "Fonts can only be downloaded from Google Fonts."},
    "font_not_found": {"ja": "Google Fonts に「{name}」が見つかりませんでした（スペルを確認してください）",
                       "en": "\"{name}\" wasn't found on Google Fonts (check the spelling)."},
    "font_fetch_failed": {"ja": "フォントファイルを取得できませんでした", "en": "Couldn't download the font file."},
}


def tr(code: str, lang: str, **params) -> str:
    entry = MESSAGES.get(code) or MESSAGES["job_failed"]
    text = entry.get(lang) or entry[DEFAULT_LANG]
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def tr_all(code: str, **params) -> dict[str, str]:
    """Every language at once, so the UI can switch language without refetching."""
    return {lg: tr(code, lg, **params) for lg in LANGS}


# ------------------------------------------------------------------ jobs
class Job:
    def __init__(self, file_info: dict, opts: dict):
        self.id = uuid.uuid4().hex[:12]
        self.file = file_info
        self.opts = opts
        self.kind = file_info["kind"]
        self.stages = STAGES[self.kind]
        self.state = "queued"          # queued | running | done | error
        self.stage = "queued"
        self.frac = 0.0
        self.msg = "順番待ちです"     # informational only; the UI renders progress text from `stage`
        self.error = None              # (code, params) -> localised in to_dict()
        self.detail = None
        self.result = None
        self.created = time.time()
        self.lock = threading.Lock()

    def update(self, stage: str, frac: float, msg: str) -> None:
        with self.lock:
            self.stage = stage
            self.frac = max(self.frac, min(1.0, float(frac)))
            self.msg = msg

    def to_dict(self, lang: str = DEFAULT_LANG) -> dict:
        with self.lock:
            st = self.stage
            step = self.stages.index(st) + 1 if st in self.stages else (
                len(self.stages) if st == "done" else 0)
            ahead = 0
            if self.state == "queued":
                ahead = sum(1 for j in list(JOB_ORDER) if j.state in ("queued", "running")
                            and j.created < self.created)
            return {
                "id": self.id, "state": self.state, "kind": self.kind,
                "stage": st, "stages": self.stages, "step": step,
                "total": len(self.stages), "frac": round(self.frac, 4),
                "msg": self.msg,
                "error": tr(self.error[0], lang, **self.error[1]) if self.error else None,
                "error_code": self.error[0] if self.error else None,
                "error_i18n": tr_all(self.error[0], **self.error[1]) if self.error else None,
                "detail": self.detail,
                "result": self.result, "queue_ahead": ahead,
            }


JOBS: dict[str, Job] = {}
JOB_ORDER: list[Job] = []
JOB_QUEUE: "queue.Queue[Job]" = queue.Queue()
FILES: dict[str, dict] = {}        # file_id -> info (also persisted next to upload)


def _friendly_error(e: BaseException) -> tuple[tuple[str, dict], str]:
    """-> ((message code, params), technical detail)."""
    detail = f"{type(e).__name__}: {e}"
    code = getattr(e, "code", None)
    if isinstance(code, str) and code in MESSAGES:   # PipelineError / FontError from kc
        return (code, dict(getattr(e, "params", None) or {})), detail
    if isinstance(e, subprocess.CalledProcessError):
        return ("ffmpeg_failed", {}), detail
    if isinstance(e, FileNotFoundError):
        return ("missing_tool", {}), detail
    if isinstance(e, MemoryError):
        return ("out_of_memory", {}), detail
    return ("job_failed", {}), detail


def _worker() -> None:
    while True:
        job = JOB_QUEUE.get()
        with job.lock:
            job.state = "running"
        job.update("prepare", 0.01, "準備しています")
        stem = re.sub(r"[^\w\-]+", "_", Path(job.file["name"]).stem)[:40] or "video"
        out_name = f"{stem}_{job.opts.get('preset', 'motiversity')}_{job.id[:6]}.mp4"
        out_path = OUTPUTS / out_name
        try:
            res = run_job(job.file["path"], str(out_path), job.opts, job.update)
            with job.lock:
                job.result = {
                    "url": f"/media/{out_name}", "file": out_name,
                    "kind": res.get("kind"), "transcript": res.get("transcript", ""),
                    "language": res.get("language"),
                }
                job.state = "done"
                job.stage = "done"
                job.frac = 1.0
                job.msg = "完成しました"
        except BaseException as e:  # noqa: BLE001 - surface every failure to the UI
            traceback.print_exc()
            err, detail = _friendly_error(e)
            with job.lock:
                job.state = "error"
                job.error = err
                job.detail = detail
                job.msg = tr(err[0], DEFAULT_LANG, **err[1])
            try:
                if out_path.exists():
                    out_path.unlink()
            except OSError:
                pass
        finally:
            JOB_QUEUE.task_done()


def _clean_opts(raw: dict, kind: str) -> dict:
    o: dict = {}
    for k, allowed in OPT_CHOICES.items():
        v = raw.get(k)
        if v in ("", "auto") and k == "language":
            v = None
        if v in allowed:
            o[k] = v
    if isinstance(raw.get("font"), str) and kcfonts.get(raw["font"]):
        o["font"] = raw["font"].lower()
    for k in ("text_color",):
        if isinstance(raw.get(k), str) and HEX.match(raw[k]):
            o[k] = raw[k]
    acc = raw.get("accent_color")
    o["accent_color"] = acc if isinstance(acc, str) and HEX.match(acc) else None
    try:
        o["size"] = min(1.6, max(0.6, float(raw.get("size", 1.0))))
    except (TypeError, ValueError):
        o["size"] = 1.0
    o["behind"] = bool(raw.get("behind", True))
    bg = raw.get("background")
    if kind == "video":
        o["background"] = "black" if bg == "black" else "keep"
    else:
        o["background"] = bg if isinstance(bg, str) and (bg in ("black", "white") or HEX.match(bg)) \
            else "black"
    if "language" not in o:
        o["language"] = None
    return o


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "KineticCaptions/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter: skip polling noise
        if "/api/job/" in (args[0] if args else ""):
            return
        sys.stderr.write("[app] " + (fmt % args) + "\n")

    # -- helpers
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @property
    def lang(self) -> str:
        lg = (self.headers.get("X-Lang") or "").strip().lower()[:2]
        return lg if lg in LANGS else DEFAULT_LANG

    def _err(self, status: int, code: str, **params) -> None:
        self._json({"error": tr(code, self.lang, **params), "code": code,
                    "error_i18n": tr_all(code, **params)}, status)

    def _file(self, path: Path, cache: str = "no-cache") -> None:
        """Serve a file with single-range support (needed for <video> seeking)."""
        try:
            size = path.stat().st_size
        except OSError:
            return self._err(404, "file_missing")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".ttf":
            ctype = "font/ttf"
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if m.group(1) == "":                     # suffix range: last N bytes
                start = max(0, size - int(m.group(2)))
            else:
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if self.command == "GET" and "download" in (urlparse(self.path).query or ""):
            ascii_name = path.name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _safe(base: Path, name: str) -> Path | None:
        p = (base / unquote(name)).resolve()
        try:
            p.relative_to(base.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None

    # -- routes
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if path.startswith("/static/"):
            p = self._safe(STATIC, path[len("/static/"):])
            return self._file(p) if p else self._err(404, "not_found")
        if path.startswith("/media/"):
            p = self._safe(OUTPUTS, path[len("/media/"):])
            return self._file(p) if p else self._err(404, "video_missing")
        if path.startswith("/fonts/"):
            p = self._safe(FONTS, path[len("/fonts/"):])
            return self._file(p, "public, max-age=86400") if p else self._err(404, "font_missing")
        if path == "/api/fonts":
            fonts = [{**f, "url": "/fonts/" + f["file"].replace("[", "%5B").replace("]", "%5D")}
                     for f in kcfonts.list_fonts()]
            return self._json({"fonts": fonts})
        m = re.match(r"^/api/job/([0-9a-f]+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            return self._json(job.to_dict(self.lang)) if job else self._err(404, "job_missing")
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._err(404, "not_found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/upload":
            return self._upload()
        if path == "/api/jobs":
            return self._create_job()
        if path == "/api/fonts/add":
            return self._add_font()
        self._err(404, "not_found")

    def _upload(self) -> None:
        name = unquote(self.headers.get("X-Filename") or "").strip()
        name = Path(name).name or "upload"
        ext = Path(name).suffix.lower()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if ext not in AUDIO_EXT | VIDEO_EXT:
            self._drain(length)
            return self._err(415, "unsupported_type")
        if length <= 0:
            return self._err(411, "empty_file")
        if length > MAX_UPLOAD:
            self._drain(length)
            return self._err(413, "too_large")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        fid = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w.\-]+", "_", name)[-80:]
        dest = UPLOADS / f"{fid}_{safe}"
        left = length
        try:
            with open(dest, "wb") as f:
                while left > 0:
                    buf = self.rfile.read(min(CHUNK, left))
                    if not buf:
                        raise ConnectionError("upload truncated")
                    f.write(buf)
                    left -= len(buf)
        except (ConnectionError, OSError):
            dest.unlink(missing_ok=True)
            return self._err(400, "upload_interrupted")
        try:
            info = probe_kind(str(dest))
        except Exception as e:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            code = getattr(e, "code", None)
            return self._err(422, code if code in MESSAGES else "unreadable")
        if not info.get("has_audio"):
            dest.unlink(missing_ok=True)
            return self._err(422, "no_audio_upload")
        rec = {"file_id": fid, "name": name, "path": str(dest), "size": length, **info}
        FILES[fid] = rec
        self._json({k: v for k, v in rec.items() if k != "path"})

    def _drain(self, length: int) -> None:
        left = min(length, MAX_UPLOAD)
        while left > 0:
            buf = self.rfile.read(min(CHUNK, left))
            if not buf:
                break
            left -= len(buf)

    def _add_font(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 20000:
                return self._err(413, "input_too_long")
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._err(400, "bad_request")
        try:
            f = kcfonts.add_google_font(str(body.get("query", "")))
        except kcfonts.FontError as e:
            code = getattr(e, "code", "")
            if code not in MESSAGES:
                return self._json({"error": str(e)}, 400)
            return self._err(400, code, **getattr(e, "params", {}))
        except Exception:
            return self._err(502, "fonts_offline")
        url = "/fonts/" + f["file"].replace("[", "%5B").replace("]", "%5D")
        return self._json({"font": {**f, "url": url}})

    def _create_job(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._err(400, "bad_request")
        f = FILES.get(str(body.get("file_id", "")))
        if not f or not os.path.exists(f["path"]):
            return self._err(404, "upload_again")
        job = Job(f, _clean_opts(body.get("opts") or {}, f["kind"]))
        JOBS[job.id] = job
        JOB_ORDER.append(job)
        JOB_QUEUE.put(job)
        self._json(job.to_dict(self.lang), 201)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Kinetic Captions local web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("KC_PORT", 7860)))
    a = ap.parse_args(argv)
    for d in (UPLOADS, OUTPUTS):
        d.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("警告: ffmpeg / ffprobe が見つかりません。インストールしてください。", file=sys.stderr)
    threading.Thread(target=_worker, daemon=True, name="kc-worker").start()
    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    httpd.daemon_threads = True
    url = f"http://{a.host}:{a.port}/"
    print(f"Kinetic Captions UI: {url}  (Ctrl+C で終了)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
