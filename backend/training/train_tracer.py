"""Train the VoiceGuard Source Tracer (feature 5): WHAT made this voice, not only "AI or not".

Same frozen WavLM features as the AI-voice detector (one network on the server), with a 5-class softmax head:
  human        real people (FLEURS, LibriSpeech)
  ai_speech    neural text-to-speech / voice cloning (Meta MMS-TTS, Microsoft neural TTS)
  classic_tts  old robotic text-to-speech (Windows SAPI)
  neural_vc    a real person's speech re-voiced by a neural vocoder (SpeechT5 HiFi-GAN) - the last stage of
               RVC / so-vits style AI voice changers, used as their stand-in
  dsp_changer  voice-changer apps: pitch / formant / gender / speed effects (training/add_voice_changer.py)
Two heads like the detector: phone + WhatsApp conditions, and the loudspeaker + room condition (Live Call Check).
The split is by group (same seed as the detector), so test sentences/speakers are never seen in training.

Usage: python training/train_tracer.py K:\\vgtools\\data\\vgset [--max-minutes 100]
Writes app/ai/weights/vg_tracer.npz, vg_tracer_room.npz + _report.json
"""
import csv
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_detector import CONDS, OUT, Cache, Scaler, cond_audio  # noqa: E402
from app.ai import models  # noqa: E402
from app.ai.audio_io import load_audio  # noqa: E402

CLASSES = ["human", "ai_speech", "classic_tts", "neural_vc", "dsp_changer"]
SOURCE_CLASS = {"fleurs": "human", "librispeech": "human", "mms_tts": "ai_speech", "neural_tts": "ai_speech",
                "sapi": "classic_tts", "vocoder_hifigan": "neural_vc", "dsp_changer": "dsp_changer"}
HEADS = {"vg_tracer": ["clean", "g711", "lowbit_loss", "opus_wa", "phone_opus"], "vg_tracer_room": ["speaker"]}


class SoftmaxReg:
    """Multinomial L2 logistic regression (PyTorch L-BFGS), class-balanced, no SciPy."""

    def __init__(self, C):
        self.C = C

    def fit(self, X, y, k):
        Xt = torch.tensor(X, dtype=torch.float32)
        yt = torch.tensor(y, dtype=torch.long)
        counts = np.bincount(y, minlength=k).astype(np.float32)
        cw = torch.tensor(len(y) / (k * np.maximum(counts, 1)), dtype=torch.float32)
        W = torch.zeros(Xt.shape[1], k, requires_grad=True)
        b = torch.zeros(k, requires_grad=True)
        with torch.enable_grad():
            opt = torch.optim.LBFGS([W, b], lr=1, max_iter=500, history_size=20, line_search_fn="strong_wolfe")

            def closure():
                opt.zero_grad()
                loss = self.C * torch.nn.functional.cross_entropy(Xt @ W + b, yt, weight=cw, reduction="sum") \
                    + 0.5 * (W * W).sum()
                loss.backward()
                return loss
            opt.step(closure)
        self.W, self.b = W.detach().numpy(), b.detach().numpy()
        return self

    def predict_proba(self, X):
        z = X @ self.W + self.b
        z = np.exp(z - z.max(1, keepdims=True))
        return z / z.sum(1, keepdims=True)


def macro_recall(pred, y, k):
    return float(np.mean([(pred[y == c] == c).mean() for c in range(k) if (y == c).any()]))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=r"K:\vgtools\data\vgset")
    ap.add_argument("--max-minutes", type=float, default=0, help="pause feature extraction after this long (resume later)")
    a = ap.parse_args()
    root = Path(a.root)
    base = list(csv.DictReader(open(root / "manifest.csv", encoding="utf-8")))
    groups = sorted({r["group"] for r in base})
    random.Random(1).shuffle(groups)
    test_groups = set(groups[: len(groups) // 5])            # identical split to train_detector.py
    rows = base + list(csv.DictReader(open(root / "manifest_vc.csv", encoding="utf-8")))
    rows = [r for r in rows if r["source"] in SOURCE_CLASS]
    print(f"clips={len(rows)} " + " ".join(f"{c}={sum(SOURCE_CLASS[r['source']] == c for r in rows)}" for c in CLASSES),
          flush=True)

    cache = Cache(root)
    spk = models.speaker()
    todo = [(i, r, c) for i, r in enumerate(rows) for c in CONDS if r["path"] not in cache.data[c]]
    print(f"features to compute: {len(todo)}", flush=True)
    t, last_y = time.time(), (None, None)
    for k, (i, r, c) in enumerate(todo):
        if last_y[0] != r["path"]:
            last_y = (r["path"], load_audio(Path(r["path"]).read_bytes()))
        cache.data[c][r["path"]] = models.layer_stats(spk, cond_audio(last_y[1], c, i)).astype(np.float16)
        if k % 200 == 0:
            print(f"  {k}/{len(todo)} {time.time() - t:.0f}s", flush=True)
            if k:
                cache.save()
        if a.max_minutes and time.time() - t > a.max_minutes * 60:
            cache.save()
            print(f"PAUSED after {k + 1}/{len(todo)} features – run again to continue.", flush=True)
            return
    if todo:
        cache.save()
    print("features complete", flush=True)

    K = len(CLASSES)
    for out, conds in HEADS.items():
        feats, y, test, cond_of, src_of = [], [], [], [], []
        for r in rows:
            for c in conds:
                feats.append(cache.data[c][r["path"]])
                y.append(CLASSES.index(SOURCE_CLASS[r["source"]]))
                test.append(r["group"] in test_groups)
                cond_of.append(c)
                src_of.append(r.get("vc_type", "").split(":")[0] or r["source"])
        feats = np.stack(feats).astype(np.float32)
        y, test = np.array(y), np.array(test)
        cond_of, src_of = np.array(cond_of), np.array(src_of)
        print(f"\n== {out}: conditions {conds}, train={int((~test).sum())} test={int(test.sum())}", flush=True)
        best = None
        for layers in ([4, 5, 6], [2, 3, 4, 5, 6]):
            X = feats[:, layers, :].reshape(len(feats), -1)
            sc = Scaler().fit(X[~test])
            for C in (0.01, 0.03, 0.1):
                clf = SoftmaxReg(C).fit(sc.transform(X[~test]), y[~test], K)
                pred = clf.predict_proba(sc.transform(X[test])).argmax(1)
                mr = macro_recall(pred, y[test], K)
                print(f"  layers={layers} C={C} balanced accuracy={mr:.4f}", flush=True)
                if best is None or mr > best[0]:
                    best = (mr, layers, C, sc, clf)
        mr, layers, C, sc, clf = best
        X = feats[:, layers, :].reshape(len(feats), -1)
        P = clf.predict_proba(sc.transform(X))
        pred = P.argmax(1)
        report = {"classes": CLASSES, "layers": layers, "C": C, "balanced_accuracy": round(mr, 4),
                  "clips": len(rows), "conditions": conds, "recall": {}, "by_condition": {}, "confusion": {},
                  "dsp_changer_by_effect": {}, "human_flagged_as_changer": None}
        tm = test
        for ci, cname in enumerate(CLASSES):
            m = tm & (y == ci)
            report["recall"][cname] = round(float((pred[m] == ci).mean()), 4) if m.any() else None
            report["confusion"][cname] = {CLASSES[j]: int(((pred == j) & m).sum()) for j in range(K)}
        for c in conds:
            m = tm & (cond_of == c)
            report["by_condition"][c] = round(macro_recall(pred[m], y[m], K), 4)
        for eff in ("gender", "pitch", "formant", "speed"):
            m = tm & (src_of == eff)
            if m.any():
                report["dsp_changer_by_effect"][eff] = round(float((pred[m] == CLASSES.index("dsp_changer")).mean()), 4)
        hm = tm & (y == 0)
        report["human_flagged_as_changer"] = round(float((pred[hm] == CLASSES.index("dsp_changer")).mean()), 4)
        print(f"best layers={layers} C={C} balanced accuracy={mr:.4f}")
        print("  recall:", report["recall"])
        print("  by condition:", report["by_condition"])
        print("  voice-changer effects caught:", report["dsp_changer_by_effect"],
              " real voices wrongly called 'voice changer':", report["human_flagged_as_changer"])
        for cname, row in report["confusion"].items():
            print(f"  {cname:12s} -> " + " ".join(f"{k2}:{v}" for k2, v in row.items()))

        # shipped head: refit on ALL data (the test numbers above stay the honest estimate)
        sc_all = Scaler().fit(X)
        clf_all = SoftmaxReg(C).fit(sc_all.transform(X), y, K)
        OUT.mkdir(parents=True, exist_ok=True)
        np.savez(OUT / f"{out}.npz", classes=np.array(CLASSES), layers=np.array(layers),
                 mean=sc_all.mean_.astype(np.float32), scale=sc_all.scale_.astype(np.float32),
                 W=clf_all.W.astype(np.float32), b=clf_all.b.astype(np.float32))
        (OUT / f"{out}_report.json").write_text(json.dumps(report, indent=1))
        print("saved", OUT / f"{out}.npz", flush=True)


if __name__ == "__main__":
    torch.set_grad_enabled(False)
    main()
