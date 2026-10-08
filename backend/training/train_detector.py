"""Train the VoiceGuard AI-voice detector (pitch ideas #1 + #2).

Frozen self-supervised speech features (WavLM-Base-Plus, the same network the Voice Print uses, so no
extra memory on the server) + a small logistic-regression head, trained on clean audio AND on the
channels real calls and voice notes go through:
  g711         8 kHz phone line (G.711 mu-law, 300-3400 Hz)
  lowbit_loss  AMR-like low bitrate + packet loss
  opus_wa      REAL Opus codec at WhatsApp voice-note bitrates
  phone_opus   phone line + packet loss + 8 kHz Opus (VoIP)
  speaker      phone line -> the phone's loudspeaker -> a random room -> its microphone (Live Call Check)
The split is by group (speaker / sentence / vocoded pair), so test sentences are never seen in training.

Features are cached per (clip, condition) so adding clips or conditions only computes what is new.
Usage: python training/train_detector.py K:\\vgtools\\data\\vgset [--max-minutes 100] [--out vg_detector]
Writes app/ai/weights/<out>.npz + <out>_report.json (default out: vg_detector_candidate)
"""
import csv
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402,F401
from app.ai import models  # noqa: E402
from app.ai.audio_io import load_audio  # noqa: E402
from app.ai.phone_channel import codec_roundtrip, phone_channel, speaker_room  # noqa: E402

CONDS = ["clean", "g711", "lowbit_loss", "opus_wa", "phone_opus", "speaker"]
OLD_CONDS = ["clean", "g711", "lowbit_loss"]   # order used by the first version's features.npz
OUT = ROOT / "app" / "ai" / "weights"


def cond_audio(y, cond, seed):
    rng = random.Random(seed)
    if cond == "clean":
        return y
    if cond == "g711":
        return phone_channel(y, codec="g711", packet_loss=0.0, seed=seed)
    if cond == "lowbit_loss":
        return phone_channel(y, codec="lowbitrate", packet_loss=0.04, seed=seed)
    if cond == "opus_wa":
        return codec_roundtrip(y, codec="opus", level=rng.uniform(0.9, 1.0))
    if cond == "phone_opus":
        return codec_roundtrip(phone_channel(y, codec="g711", packet_loss=0.03, seed=seed), codec="opus",
                               level=rng.uniform(0.85, 1.0), codec_sr=8000)
    if cond == "speaker":     # Live Call Check: the call played on the phone's loudspeaker, heard by its mic
        return speaker_room(y, seed)
    raise ValueError(cond)


def auc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    if not len(pos) or not len(neg):
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


class Cache:
    """featcache/<cond>.npz : {paths, feats(float16)}"""

    def __init__(self, root: Path):
        self.dir = root / "featcache"
        self.dir.mkdir(exist_ok=True)
        self.data = {}
        for c in CONDS:
            f = self.dir / f"{c}.npz"
            if f.exists():
                z = np.load(f, allow_pickle=True)
                self.data[c] = dict(zip(z["paths"].tolist(), z["feats"]))
            else:
                self.data[c] = {}

    def migrate_old(self, root: Path, rows):
        old = root / "features.npz"
        if not old.exists() or any(self.data[c] for c in OLD_CONDS):
            return
        z = np.load(old, allow_pickle=True)
        feats = z["feats"]
        n_old = len(feats) // len(OLD_CONDS)
        for i, r in enumerate(rows[:n_old]):
            for j, c in enumerate(OLD_CONDS):
                self.data[c][r["path"]] = feats[i * len(OLD_CONDS) + j].astype(np.float16)
        print(f"migrated {n_old} clips x {len(OLD_CONDS)} conditions from features.npz")
        self.save()

    def save(self):
        for c, d in self.data.items():
            if d:
                np.savez(self.dir / f"{c}.npz", paths=np.array(list(d.keys()), dtype=object),
                         feats=np.stack(list(d.values())).astype(np.float16))


class Scaler:
    """Same as scikit-learn's StandardScaler (population std)."""

    def fit(self, X):
        self.mean_ = X.mean(0)
        self.scale_ = X.std(0)
        self.scale_[self.scale_ == 0] = 1.0
        return self

    def transform(self, X):
        return (X - self.mean_) / self.scale_


class LogReg:
    """L2 logistic regression fitted with PyTorch L-BFGS – the same objective as scikit-learn's
    LogisticRegression(C): 0.5*||w||^2 + C * sum(log-loss). No SciPy needed, so Windows Smart App Control
    (which blocks SciPy's DLLs on this laptop) cannot stop training."""

    def __init__(self, C):
        self.C = C

    def fit(self, X, y):
        Xt = torch.tensor(X, dtype=torch.float32)
        yt = torch.tensor(y, dtype=torch.float32)
        w = torch.zeros(Xt.shape[1], requires_grad=True)
        b = torch.zeros(1, requires_grad=True)
        with torch.enable_grad():
            opt = torch.optim.LBFGS([w, b], lr=1, max_iter=500, history_size=20, line_search_fn="strong_wolfe")

            def closure():
                opt.zero_grad()
                loss = self.C * torch.nn.functional.binary_cross_entropy_with_logits(Xt @ w + b, yt, reduction="sum") \
                    + 0.5 * (w @ w)
                loss.backward()
                return loss
            opt.step(closure)
        self.coef_ = w.detach().numpy()[None, :]
        self.intercept_ = b.detach().numpy()
        return self

    def predict_proba(self, X):
        z = X @ self.coef_[0] + self.intercept_[0]
        p = 1 / (1 + np.exp(-z))
        return np.stack([1 - p, p], axis=1)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=r"K:\vgtools\data\vgset")
    ap.add_argument("--max-minutes", type=float, default=0, help="pause feature extraction after this long (resume later)")
    ap.add_argument("--out", default="vg_detector_candidate", help="weights file name (vg_detector = deploy directly)")
    ap.add_argument("--conds", default=",".join(CONDS), help="conditions to fit on, e.g. 'speaker' for the room detector")
    a = ap.parse_args()
    root = Path(a.root)
    rows = list(csv.DictReader(open(root / "manifest.csv", encoding="utf-8")))
    groups = sorted({r["group"] for r in rows})
    random.Random(1).shuffle(groups)
    test_groups = set(groups[: len(groups) // 5])
    print(f"clips={len(rows)} groups={len(groups)} test_groups={len(test_groups)}", flush=True)

    cache = Cache(root)
    cache.migrate_old(root, rows)
    spk = models.speaker()  # reuse the WavLM backbone
    todo = [(i, r, c) for i, r in enumerate(rows) for c in CONDS if r["path"] not in cache.data[c]]
    print(f"features to compute: {len(todo)}", flush=True)
    t = time.time()
    last_y = (None, None)
    for k, (i, r, c) in enumerate(todo):
        if last_y[0] != r["path"]:
            last_y = (r["path"], load_audio(Path(r["path"]).read_bytes()))
        cache.data[c][r["path"]] = models.layer_stats(spk, cond_audio(last_y[1], c, i)).astype(np.float16)
        if k % 200 == 0:
            print(f"  {k}/{len(todo)} {time.time() - t:.0f}s", flush=True)
            cache.save()
        if a.max_minutes and time.time() - t > a.max_minutes * 60:
            cache.save()
            print(f"PAUSED after {k + 1}/{len(todo)} features – run again to continue.", flush=True)
            return
    cache.save()
    print("features complete", flush=True)

    fit_conds = [c for c in a.conds.split(",") if c in CONDS]
    feats, meta = [], []
    for r in rows:
        for c in fit_conds:
            feats.append(cache.data[c][r["path"]])
            meta.append((r["label"], r["source"], r["lang"], r["group"] in test_groups, c))
    feats = np.stack(feats).astype(np.float32)
    y = np.array([m[0] == "fake" for m in meta])
    is_test = np.array([m[3] for m in meta])

    best = None
    for layers in ([4, 5, 6], [2, 3, 4, 5, 6]):
        X = feats[:, layers, :].reshape(len(feats), -1)
        sc = Scaler().fit(X[~is_test])
        for C in (0.01, 0.03, 0.1):
            clf = LogReg(C).fit(sc.transform(X[~is_test]), y[~is_test].astype(np.float32))
            p = clf.predict_proba(sc.transform(X[is_test]))[:, 1]
            auc_v = auc(p[y[is_test]], p[~y[is_test]])
            print(f"  layers={layers} C={C} AUC={auc_v:.4f}", flush=True)
            if best is None or auc_v > best[0]:
                best = (auc_v, layers, C, sc, clf)
    auc_v, layers, C, sc, clf = best
    print(f"\nbest layers={layers} C={C} test AUC (all conditions)={auc_v:.4f}")

    X = feats[:, layers, :].reshape(len(feats), -1)
    p = clf.predict_proba(sc.transform(X))[:, 1]
    report = {"layers": layers, "C": C, "auc_all": round(auc_v, 4), "clips": len(rows),
              "by_condition": {}, "by_source": {}, "by_language": {}}
    for c in fit_conds:
        m = is_test & np.array([mm[4] == c for mm in meta])
        acc = float(((p[m] >= 0.5) == y[m]).mean())
        report["by_condition"][c] = {"auc": round(auc(p[m & y], p[m & ~y]), 4), "acc": round(acc, 4)}
        print(f"  {c:12s} AUC={report['by_condition'][c]['auc']:.4f} acc={acc:.4f}")
    for src in sorted({mm[1] for mm in meta}):
        m = is_test & np.array([mm[1] == src for mm in meta])
        if m.any():
            acc = float(((p[m] >= 0.5) == y[m]).mean())
            report["by_source"][src] = {"n": int(m.sum()), "acc": round(acc, 4)}
            print(f"  source {src:16s} n={int(m.sum()):4d} acc={acc:.4f}")
    for lang in sorted({mm[2] for mm in meta}):
        m = is_test & np.array([mm[2] == lang for mm in meta])
        if m.any():
            real = m & ~y
            report["by_language"][lang] = {"n": int(m.sum()), "acc": round(float(((p[m] >= 0.5) == y[m]).mean()), 4),
                                           "real_kept_as_real": round(float((p[real] < 0.5).mean()), 4) if real.any() else None}
            print(f"  lang {lang:4s} n={int(m.sum()):4d} acc={report['by_language'][lang]['acc']:.4f} "
                  f"real-ok={report['by_language'][lang]['real_kept_as_real']}")

    # Refit on ALL data for the shipped model (the test numbers above stay the honest estimate).
    sc_all = Scaler().fit(X)
    clf_all = LogReg(C).fit(sc_all.transform(X), y.astype(np.float32))
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(OUT / f"{a.out}.npz", layers=np.array(layers), mean=sc_all.mean_.astype(np.float32),
             scale=sc_all.scale_.astype(np.float32), coef=clf_all.coef_[0].astype(np.float32),
             intercept=np.array(clf_all.intercept_, dtype=np.float32))
    (OUT / f"{a.out}_report.json").write_text(json.dumps(report, indent=1))
    print("saved", OUT / f"{a.out}.npz")


if __name__ == "__main__":
    torch.set_grad_enabled(False)
    main()
