"""Split render time into decode / composite / encode. usage: profile_render.py video layout.json [mattes]"""
import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from kc import render as R
video, layout_p = sys.argv[1], sys.argv[2]
mdir = sys.argv[3] if len(sys.argv) > 3 else None
info = R.probe(video); W, H, fps = info["width"], info["height"], info["fps"]
layout = json.load(open(layout_p))
fonts = R.FontBook(); sprites = R.SpriteCache(fonts); mattes = R.MatteSource(mdir, W, H)
phrases = sorted([R.layout_phrase(p, W, H, fonts) for p in layout["phrases"]], key=lambda p: p.start)
t = time.perf_counter()
dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
frames = []; n = W * H * 3
while True:
    b = dec.stdout.read(n)
    if len(b) < n: break
    frames.append(b)
t_dec = time.perf_counter() - t
t = time.perf_counter(); out = []
for i, b in enumerate(frames):
    f = np.frombuffer(b, np.uint8).reshape(H, W, 3).copy()
    out.append(R.composite_frame(f, i / fps, i, phrases, sprites, fonts, mattes).tobytes())
t_comp = time.perf_counter() - t
def enc(args):
    t = time.perf_counter()
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"] + args + ["/tmp/_enc.mp4"], stdin=subprocess.PIPE)
    for b in out: p.stdin.write(b)
    p.stdin.close(); p.wait(); return time.perf_counter() - t
t_x264 = enc(["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"])
t_fast = enc(["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"])
t_vt = enc(["-c:v", "h264_videotoolbox", "-q:v", "65", "-pix_fmt", "yuv420p"])
print(f"{W}x{H} {len(frames)} frames | decode {t_dec:.2f}s | composite {t_comp:.2f}s | encode x264-medium {t_x264:.2f}s, x264-veryfast {t_fast:.2f}s, videotoolbox {t_vt:.2f}s")
