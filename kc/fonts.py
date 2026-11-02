"""Caption font registry: built-in fonts + any Google Font the user adds.

Users can paste whatever they copied from fonts.google.com:
  - a family name                    "Pacifico"
  - a specimen URL                   https://fonts.google.com/specimen/Courgette?preview.script=Latn
  - a css2 URL / <link> embed code   <link href="https://fonts.googleapis.com/css2?family=Pacifico&display=swap" ...>
  - an @import or CSS rule           @import url('...');  /  font-family: "Pacifico", cursive;
add_google_font() resolves the family, downloads the heaviest available
weight as TTF into assets/fonts/user/, and records it in fonts.json.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
USER_DIR = FONT_DIR / "user"
REGISTRY = USER_DIR / "fonts.json"

# case: "upper" = ALL CAPS (punchy display fonts), "lower" = keep it lowercase
# (scripts/handwriting are unreadable in caps)
BUILTIN: list[dict] = [
    {"id": "montserrat", "label": "Montserrat", "file": "Montserrat[wght].ttf",
     "family": "KC Montserrat", "weight": "900", "case": "upper", "category": "SANS_SERIF"},
    {"id": "anton", "label": "Anton", "file": "Anton-Regular.ttf",
     "family": "KC Anton", "weight": "400", "case": "upper", "category": "SANS_SERIF"},
    {"id": "bebas", "label": "Bebas Neue", "file": "BebasNeue-Regular.ttf",
     "family": "KC Bebas Neue", "weight": "400", "case": "upper", "category": "DISPLAY"},
    {"id": "courgette", "label": "Courgette（筆記体）", "file": "Courgette-Regular.ttf",
     "family": "KC Courgette", "weight": "400", "case": "lower", "category": "HANDWRITING",
     "sample": "Never give up"},
]

UA = "Wget/1.21"   # makes the css2 API answer with .ttf URLs instead of woff2
_ALLOWED_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "raw.githubusercontent.com"}
_lock = threading.Lock()


class FontError(ValueError):
    """`code` (+ `params`) is a stable id so the UI can show the message in any language."""

    def __init__(self, msg: str, code: str = "font_error", **params):
        super().__init__(msg)
        self.code = code
        self.params = params


def _load_user() -> list[dict]:
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return []


def list_fonts() -> list[dict]:
    out = [f for f in BUILTIN if (FONT_DIR / f["file"]).exists()]
    out += [f for f in _load_user() if (FONT_DIR / f["file"]).exists()]
    return out


def get(font_id: str | None) -> dict | None:
    if not font_id:
        return None
    fid = font_id.lower()
    return next((f for f in list_fonts() if f["id"] == fid), None)


def font_path(font_id: str) -> Path | None:
    f = get(font_id)
    return FONT_DIR / f["file"] if f else None


def parse_family(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise FontError("フォント名かURLを入れてください", "font_empty")
    m = re.search(r"fonts\.google\.com/specimen/([^?#\s\"'/]+)", q)
    if m:
        return urllib.parse.unquote_plus(m.group(1)).strip()
    m = re.search(r"[?&]family=([^&:\"'\s)]+)", q)
    if m:
        return urllib.parse.unquote_plus(m.group(1)).strip()
    m = re.search(r"font-family\s*:\s*['\"]?([^,'\";]+)", q)
    if m:
        return m.group(1).strip()
    return q.strip(" '\"")


def _valid_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,60}", name):
        raise FontError(f"「{name}」はフォント名として読み取れませんでした", "font_bad_name", name=name)
    return re.sub(r"\s+", " ", name)


def _get(url: str, timeout: int = 30) -> bytes:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in _ALLOWED_HOSTS:
        raise FontError("Google Fonts 以外からはダウンロードしません", "font_host")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _category(name: str) -> str:
    slug = name.lower().replace(" ", "").replace("-", "")
    for lic in ("ofl", "apache", "ufl"):
        try:
            meta = _get(f"https://raw.githubusercontent.com/google/fonts/main/{lic}/{slug}/METADATA.pb", 15)
        except Exception:
            continue
        m = re.search(rb'category:\s*"([A-Z_]+)"', meta)
        if m:
            return m.group(1).decode()
    return "UNKNOWN"


def _ttf_url(name: str) -> tuple[str, str]:
    """Heaviest weight first: captions want bold."""
    fam = urllib.parse.quote_plus(name)
    for w in ("900", "800", "700", None):
        q = f"{fam}:wght@{w}" if w else fam
        try:
            css = _get(f"https://fonts.googleapis.com/css2?family={q}&display=swap").decode()
        except Exception:
            continue
        m = re.search(r"src:\s*url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
        if m:
            wm = re.search(r"font-weight:\s*(\d+)", css)
            return m.group(1), (wm.group(1) if wm else (w or "400"))
    raise FontError(f"Google Fonts に「{name}」が見つかりませんでした（スペルを確認してください）",
                    "font_not_found", name=name)


def add_google_font(query: str) -> dict:
    name = _valid_name(parse_family(query))
    fid = "g-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    existing = get(fid)
    if existing:
        return existing
    for b in BUILTIN:
        if b["label"].split("（")[0].lower() == name.lower():
            return b
    url, weight = _ttf_url(name)
    data = _get(url, 60)
    if data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        raise FontError("フォントファイルを取得できませんでした", "font_fetch_failed")
    USER_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{fid}-{weight}.ttf"
    (USER_DIR / fname).write_bytes(data)
    cat = _category(name)
    entry = {"id": fid, "label": name, "file": f"user/{fname}", "family": f"KC {name}",
             "weight": "400", "case": "lower" if cat == "HANDWRITING" else "upper",
             "category": cat, "source": "google", "font_weight": weight}
    if entry["case"] == "lower":
        entry["sample"] = "Never give up"
    with _lock:
        reg = [f for f in _load_user() if f["id"] != fid] + [entry]
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    return entry


def remove_font(font_id: str) -> bool:
    with _lock:
        reg = _load_user()
        keep = [f for f in reg if f["id"] != font_id]
        if len(keep) == len(reg):
            return False
        for f in reg:
            if f["id"] == font_id:
                (FONT_DIR / f["file"]).unlink(missing_ok=True)
        REGISTRY.write_text(json.dumps(keep, ensure_ascii=False, indent=1))
    return True


if __name__ == "__main__":
    import sys
    print(json.dumps(add_google_font(" ".join(sys.argv[1:])), ensure_ascii=False, indent=1))
