"""Time each pipeline stage on a cold cache: python bench/bench.py <video> <label>"""
import json, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kc.pipeline import run_job

video, label = sys.argv[1], sys.argv[2]
root = Path("work/bench_" + label)
shutil.rmtree(root, ignore_errors=True)
marks, t0 = [], time.time()
def cb(stage, frac, msg):
    marks.append((stage, round(time.time() - t0, 2)))
r = run_job(video, str(root / "out.mp4"), {"background": "black"}, progress=cb, work_root=str(root))
total = round(time.time() - t0, 2)
# first time each stage was reported
first = {}
for s, t in marks:
    first.setdefault(s, t)
print(json.dumps({"label": label, "total_s": total, "stage_start_s": first}, ensure_ascii=False))
