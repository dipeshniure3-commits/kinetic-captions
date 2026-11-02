"""Speed + agreement of person-matte backends vs the current ISNet@1024."""
import io, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, onnx, onnxruntime as ort
from PIL import Image
from kc import matte as M

v = sys.argv[1] if len(sys.argv) > 1 else "samples/vo_user.mp4"
info = M.probe(v); W, H = info["width"], info["height"]
frames = [f.copy() for i, f in enumerate(M.iter_frames(v, W, H)) if i % 25 == 0][:20]
MODEL = Path.home() / ".rembg/models/isnet-general-use/isnet-general-use.onnx"

def isnet_session(size, opts=None):
    m = onnx.load(str(MODEL))
    if size != 1024:
        d = m.graph.input[0].type.tensor_type.shape.dim
        d[2].dim_value = size; d[3].dim_value = size
        for o in m.graph.output:
            for k in (2, 3): o.type.tensor_type.shape.dim[k].dim_value = size
        del m.graph.value_info[:]
    prov = [("CoreMLExecutionProvider", opts), "CPUExecutionProvider"] if opts else ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ort.InferenceSession(m.SerializeToString(), providers=prov)

MEAN, STD = np.array([0.5, 0.5, 0.5]), np.array([1.0, 1.0, 1.0])
def isnet_run(sess, fr, size):
    im = np.asarray(Image.fromarray(fr).resize((size, size), Image.BILINEAR), np.float32) / 255.0
    x = ((im - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    y = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    y = (y - y.min()) / max(1e-6, y.max() - y.min())
    return np.asarray(Image.fromarray((y * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.float32) / 255

def vision_run(fr, level):
    import Vision, Quartz
    from Foundation import NSData
    buf = io.BytesIO(); Image.fromarray(fr).save(buf, "BMP")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    req = Vision.VNGeneratePersonSegmentationRequest.alloc().initWithCompletionHandler_(None)
    req.setQualityLevel_(level)
    req.setOutputPixelFormat_(1278226488)  # kCVPixelFormatType_OneComponent8 'L008'
    h = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    ok, err = h.performRequests_error_([req], None)
    pb = req.results()[0].pixelBuffer()
    Quartz.CVPixelBufferLockBaseAddress(pb, 1)
    w, hh = Quartz.CVPixelBufferGetWidth(pb), Quartz.CVPixelBufferGetHeight(pb)
    bpr = Quartz.CVPixelBufferGetBytesPerRow(pb)
    raw = Quartz.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * hh)
    a = np.frombuffer(raw, np.uint8).reshape(hh, bpr)[:, :w].copy()
    Quartz.CVPixelBufferUnlockBaseAddress(pb, 1)
    return np.asarray(Image.fromarray(a).resize((W, H), Image.BILINEAR), np.float32) / 255

def iou(a, b):
    A, B = a > 0.5, b > 0.5
    return (A & B).sum() / max(1, (A | B).sum())

def bench(name, fn):
    fn(frames[0]); fn(frames[1])               # warm-up
    t = time.perf_counter(); out = [fn(f) for f in frames]; dt = (time.perf_counter() - t) / len(frames)
    return name, dt, out

res = []
s1024 = isnet_session(1024); res.append(bench("ISNet 1024 (現在)", lambda f: isnet_run(s1024, f, 1024)))
ref = res[0][2]
for sz in ():
    s = isnet_session(sz); res.append(bench(f"ISNet {sz}", lambda f, s=s, sz=sz: isnet_run(s, f, sz)))
for nm, op in (("MLProgram/ALL", {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL"}),
               ("MLProgram/GPU", {"ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndGPU"}),
               ("MLProgram/ANE", {"ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndNeuralEngine"})):
    try:
        sx = isnet_session(1024, op); res.append(bench(f"ISNet 1024 {nm}", lambda f, sx=sx: isnet_run(sx, f, 1024)))
    except Exception as e:
        print(nm, "failed:", str(e)[:120])
for lvl, nm in ((0, "accurate"), (1, "balanced")):
    try:
        res.append(bench(f"Apple Vision {nm}", lambda f, l=lvl: vision_run(f, l)))
    except Exception as e:
        print("vision", nm, "failed:", e)
for name, dt, out in res:
    print(f"{name:22s} {dt*1000:7.1f} ms/frame   一致度(IoU vs 現在) {np.mean([iou(a, b) for a, b in zip(out, ref)]):.3f}")
np.save("work/cand_ref.npy", np.stack(ref)); 
for name, dt, out in res[1:]:
    np.save("work/cand_" + "".join(c if c.isalnum() else "_" for c in name) + ".npy", np.stack(out))
