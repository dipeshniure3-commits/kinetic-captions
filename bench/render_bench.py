"""Time render() alone for the audio canvas and the black-bg video, several worker counts."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kc.render import render
A = "work/bench_final_audio/c8c013a4623406d4"; V = "work/bench_final/143326c20f2c1044"
LA = json.load(open(f"{A}/layout_last.json")); LV = json.load(open(f"{V}/layout_last.json"))
cv = {"width": 1080, "height": 1920, "fps": 30, "color": "#000000", "audio": "samples/vo/user.mp3"}
for w in [int(x) for x in sys.argv[1:]] or [1, 2, 4, 6]:
    t = time.time(); render("", LA, f"/tmp/ra_{w}.mp4", canvas=cv, workers=w); ta = time.time() - t
    t = time.time(); render("samples/vo_user.mp4", LV, f"/tmp/rv_{w}.mp4", matte_dir=f"{V}/mattes", bg="#000000", workers=w); tv = time.time() - t
    print(f"workers={w}: 縦1080x1920(音声) {ta:.2f}s | 動画526x278(黒背景) {tv:.2f}s")
