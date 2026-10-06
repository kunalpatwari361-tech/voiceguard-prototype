"""Train the VoiceGuard AI-voice detector (pitch ideas #1 + #2).

Frozen self-supervised speech features (WavLM-Base-Plus, the same network the Voice Print uses, so no
extra memory on the server) + a small logistic-regression head, trained on clean audio AND on the
channels real calls and voice notes go through:
  g711         8 kHz phone line (G.711 mu-law, 300-3400 Hz)
  lowbit_loss  AMR-like low bitrate + packet loss
  opus_wa      REAL Opus codec at WhatsApp voice-note bitrates
  phone_opus   phone line + packet loss + 8 kHz Opus (VoIP)
The split is by group (speaker / sentence / vocoded pair), so test sentences are never seen in training.

Features are cached per (clip, condition) so adding clips or conditions only computes what is new.
Usage: python training/train_detector.py K:\\vgtools\\data\\vgset
Writes app/ai/weights/vg_detector.npz + vg_detector_report.json
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
from app.ai.phone_channel import codec_roundtrip, phone_channel  # noqa: E402

CONDS = ["clean", "g711", "lowbit_loss", "opus_wa", "phone_opus"]
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


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data\vgset")
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
    cache.save()

    feats, meta = [], []
    for r in rows:
        for c in CONDS:
            feats.append(cache.data[c][r["path"]])
            meta.append((r["label"], r["source"], r["lang"], r["group"] in test_groups, c))
    feats = np.stack(feats).astype(np.float32)
    y = np.array([m[0] == "fake" for m in meta])
    is_test = np.array([m[3] for m in meta])

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    best = None
    n_layers = feats.shape[1]
    for layers in ([4, 5, 6], [2, 3, 4, 5, 6], list(range(n_layers))):
        X = feats[:, layers, :].reshape(len(feats), -1)
        sc = StandardScaler().fit(X[~is_test])
        for C in (0.003, 0.01, 0.03):
            clf = LogisticRegression(C=C, max_iter=3000).fit(sc.transform(X[~is_test]), y[~is_test])
            p = clf.predict_proba(sc.transform(X[is_test]))[:, 1]
            a = auc(p[y[is_test]], p[~y[is_test]])
            print(f"  layers={layers if len(layers) < 13 else 'all'} C={C} AUC={a:.4f}", flush=True)
            if best is None or a > best[0]:
                best = (a, layers, C, sc, clf)
    a, layers, C, sc, clf = best
    print(f"\nbest layers={layers} C={C} test AUC (all conditions)={a:.4f}")

    X = feats[:, layers, :].reshape(len(feats), -1)
    p = clf.predict_proba(sc.transform(X))[:, 1]
    report = {"layers": layers, "C": C, "auc_all": round(a, 4), "clips": len(rows), "by_condition": {}, "by_source": {}}
    for c in CONDS:
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

    # Refit on ALL data for the shipped model (the test numbers above stay the honest estimate).
    sc_all = StandardScaler().fit(X)
    clf_all = LogisticRegression(C=C, max_iter=3000).fit(sc_all.transform(X), y)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(OUT / "vg_detector.npz", layers=np.array(layers), mean=sc_all.mean_.astype(np.float32),
             scale=sc_all.scale_.astype(np.float32), coef=clf_all.coef_[0].astype(np.float32),
             intercept=np.array(clf_all.intercept_, dtype=np.float32))
    (OUT / "vg_detector_report.json").write_text(json.dumps(report, indent=1))
    print("saved", OUT / "vg_detector.npz")


if __name__ == "__main__":
    torch.set_grad_enabled(False)
    main()
