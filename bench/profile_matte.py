import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, onnxruntime as ort
from PIL import Image, ImageFilter
from kc import matte as M
v = "samples/vo_user.mp4"
info = M.probe(v); W, H = info["width"], info["height"]
frames = [f.copy() for _, f in zip(range(12), M.iter_frames(v, W, H))]
s = M.make_session("isnet-general-use")
inner = s.inner_session
print("providers:", inner.get_providers())
i0 = inner.get_inputs()[0]; print("input:", i0.name, i0.shape)
T = {}
def t(k, f):
    a = time.perf_counter(); r = f(); T[k] = T.get(k, 0) + time.perf_counter() - a; return r
for fr in frames[:2]: M.infer_mask(s, fr, (512, 271))  # warm
for fr in frames[2:]:
    m = t("rembg.predict(total)", lambda: M.infer_mask(s, fr, (512, 271)))
    m = t("clean", lambda: M.clean_mask(m))
    img = t("upsample+blur", lambda: Image.fromarray((m*255).astype(np.uint8)).resize((W, H)).filter(ImageFilter.GaussianBlur(2)))
    t("png save", lambda: img.save("/tmp/_m.png"))
n = len(frames) - 2
for k, val in T.items(): print(f"{k:24s} {val/n*1000:8.1f} ms/frame")
# raw onnx at different input sizes & providers
for prov in (["CoreMLExecutionProvider", "CPUExecutionProvider"], ["CPUExecutionProvider"]):
    so = ort.SessionOptions()
    sess = ort.InferenceSession(str(Path.home()/".u2net/isnet-general-use.onnx") if (Path.home()/".u2net/isnet-general-use.onnx").exists() else inner._model_path, so, providers=prov)
    for sz in (1024, 512, 320):
        x = np.random.rand(1, 3, sz, sz).astype(np.float32)
        try:
            sess.run(None, {i0.name: x})
            a = time.perf_counter()
            for _ in range(3): sess.run(None, {i0.name: x})
            print(prov[0][:6], sz, f"{(time.perf_counter()-a)/3*1000:.0f} ms")
        except Exception as e:
            print(prov[0][:6], sz, "FAIL", str(e)[:80])
