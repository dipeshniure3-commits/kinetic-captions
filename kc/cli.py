"""End-to-end: video -> words.json -> layout.json (+ mattes) -> captioned video."""
import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def probe_size(video: str) -> tuple[int, int]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", video,
    ], text=True)
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def run(video: str, out: str, preset: str = "motiversity", model: str = "small",
        language: str | None = None, matte: bool = True, work_root: str = "work",
        bg: str | None = None) -> str:
    from kc.render import render
    from kc.style import build_layout
    from kc.transcribe import transcribe

    work = Path(work_root) / Path(video).stem
    work.mkdir(parents=True, exist_ok=True)
    words_path = work / "words.json"
    matte_dir = work / "mattes"

    def do_words():
        if words_path.exists():
            return json.loads(words_path.read_text())
        transcribe(video, str(words_path), model=model, language=language)
        return json.loads(words_path.read_text())

    def do_matte():
        if not matte:
            return None
        from kc.matte import make_mattes
        make_mattes(video, str(matte_dir))
        return str(matte_dir)

    # Transcription and matting are independent; run them side by side.
    with ThreadPoolExecutor(2) as ex:
        f_words, f_matte = ex.submit(do_words), ex.submit(do_matte)
        words, mdir = f_words.result(), f_matte.result()

    w, h = probe_size(video)
    layout = build_layout(words, w, h, preset=preset)
    (work / f"layout_{preset}.json").write_text(json.dumps(layout, ensure_ascii=False, indent=1))
    render(video, layout, out, matte_dir=mdir, bg=bg)
    return out


def main():
    ap = argparse.ArgumentParser(prog="python -m kc")
    ap.add_argument("video")
    ap.add_argument("-o", "--out")
    ap.add_argument("--preset", default="motiversity", choices=["motiversity", "hormozi", "clean"])
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang")
    ap.add_argument("--no-matte", action="store_true")
    ap.add_argument("--bg", help="keep only the speaker; fill background with this colour (black, #000000, ...)")
    a = ap.parse_args()
    out = a.out or str(Path(a.video).with_name(Path(a.video).stem + f"_{a.preset}.mp4"))
    bg = {"black": "#000000", "white": "#FFFFFF"}.get(a.bg, a.bg) if a.bg else None
    print(run(a.video, out, a.preset, a.model, a.lang, not a.no_matte, bg=bg))


if __name__ == "__main__":
    main()
