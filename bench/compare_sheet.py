import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image, ImageDraw
from kc import matte as M
v = "samples/vo_user.mp4"; info = M.probe(v); W, H = info["width"], info["height"]
frames = [f.copy() for i, f in enumerate(M.iter_frames(v, W, H)) if i % 25 == 0][:20]
sets = [("ISNet (現在)", np.load("work/cand_ref.npy")),
        ("Vision accurate", np.load("work/cand_Apple_Vision_accurate.npy")),
        ("Vision balanced", np.load("work/cand_Apple_Vision_balanced.npy"))]
picks = [2, 9, 16]
rows = []
for name, ms in sets:
    tiles = []
    for k in picks:
        f = frames[k].astype(np.float32); m = ms[k][..., None]
        bg = np.array([200, 30, 60], np.float32)
        out = (f * m + (f * 0.25 + bg * 0.75) * (1 - m)).astype(np.uint8)
        tiles.append(Image.fromarray(out))
    row = Image.new("RGB", (W * len(picks), H + 22), (20, 20, 20))
    ImageDraw.Draw(row).text((6, 4), name, fill=(255, 255, 255))
    for i, t in enumerate(tiles): row.paste(t, (i * W, 22))
    rows.append(row)
sheet = Image.new("RGB", (rows[0].width, sum(r.height for r in rows)))
y = 0
for r in rows: sheet.paste(r, (0, y)); y += r.height
sheet.save("work/matte_compare.png")
