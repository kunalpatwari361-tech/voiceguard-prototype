"""Make voice-changer training clips for the Source Tracer (pitch idea: catch callers who disguise their voice).

Takes real human clips from the detector dataset and runs them through the effects that voice-changer apps use,
done with Praat (PSOLA + formant scaling, the same maths as the apps):
  gender   pitch AND formants moved together - "male to female / female to male" presets
  pitch    pitch moved 3-7 semitones, formants kept - "deeper / higher voice"
  formant  formants moved, pitch kept - "different person / bigger or smaller head"
  speed    cheap apps: play faster/slower (pitch + formants together), then put the tempo back
Each changed clip keeps the group (sentence/speaker) of its source, so the train/test split never mixes them.

Usage: python training/add_voice_changer.py K:\\vgtools\\data\\vgset [--scale 1.0]
Writes <root>/vc/*.wav and <root>/manifest_vc.csv (the detector's manifest.csv is not touched).
"""
import csv
import random
import sys
from pathlib import Path

import numpy as np
import parselmouth
import soundfile as sf
import soxr
from parselmouth.praat import call

SR = 16000
KINDS = [("gender", 0.4), ("pitch", 0.25), ("formant", 0.15), ("speed", 0.2)]
PER_LANG = {"hi": 100, "en": 100, "mr": 50, "pa": 50, "ta": 50, "te": 50}


def median_f0(snd) -> float:
    f0 = call(snd.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=500), "Get quantile", 0, 0, 0.5, "Hertz")
    return f0 if f0 and np.isfinite(f0) else 150.0


def change_voice(y: np.ndarray, kind: str, rng: random.Random) -> tuple[np.ndarray, str]:
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    f0 = median_f0(snd)
    if kind == "gender":
        if f0 < 165:   # sounds male -> female preset
            fr, st = rng.uniform(1.12, 1.25), rng.uniform(7, 11)
        else:          # sounds female -> male preset
            fr, st = rng.uniform(0.80, 0.90), -rng.uniform(7, 11)
        out = call(snd, "Change gender", 75, 500, fr, f0 * 2 ** (st / 12), 1.0, 1.0)
        desc = f"gender formants x{fr:.2f} pitch {st:+.1f} st"
    elif kind == "pitch":
        st = rng.choice([-1, 1]) * rng.uniform(3, 7)
        out = call(snd, "Change gender", 75, 500, 1.0, f0 * 2 ** (st / 12), 1.0, 1.0)
        desc = f"pitch {st:+.1f} st"
    elif kind == "formant":
        fr = rng.uniform(0.82, 0.9) if rng.random() < 0.5 else rng.uniform(1.1, 1.2)
        out = call(snd, "Change gender", 75, 500, fr, 0, 1.0, 1.0)
        desc = f"formants x{fr:.2f}"
    elif kind == "speed":
        st = rng.choice([-1, 1]) * rng.uniform(3, 6)
        factor = 2 ** (st / 12)
        faster = soxr.resample(y.astype(np.float32), SR, SR / factor)     # played at SR: pitch + formants x factor
        snd2 = parselmouth.Sound(faster.astype(np.float64), sampling_frequency=SR)
        out = call(snd2, "Lengthen (overlap-add)", 75, 600, factor)     # tempo back to normal
        desc = f"speed {st:+.1f} st, tempo restored"
    else:
        raise ValueError(kind)
    z = out.values[0].astype(np.float32)
    if int(round(out.sampling_frequency)) != SR:
        z = soxr.resample(z, out.sampling_frequency, SR).astype(np.float32)
    peak = float(np.abs(z).max()) or 1.0
    z = z * min(1.0, 0.9 * float(np.abs(y).max() or 0.9) / peak)
    return z, desc


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=r"K:\vgtools\data\vgset")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply the clips per language")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [r for r in csv.DictReader(open(root / "manifest.csv", encoding="utf-8")) if r["label"] == "real"]
    rng = random.Random(7)
    rng.shuffle(rows)
    out_dir = root / "vc"
    out_dir.mkdir(exist_ok=True)
    picked, per = [], {k: 0 for k in PER_LANG}
    for r in rows:
        want = int(PER_LANG.get(r["lang"], 0) * a.scale)
        if per.get(r["lang"], 0) < want:
            picked.append(r)
            per[r["lang"]] += 1
    kinds, weights = zip(*KINDS)
    out_rows = []
    for i, r in enumerate(picked):
        kind = rng.choices(kinds, weights)[0]
        y, sr = sf.read(r["path"], dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(1)
        if sr != SR:
            y = soxr.resample(y, sr, SR).astype(np.float32)
        z, desc = change_voice(y, kind, rng)
        p = out_dir / f"{Path(r['path']).stem}__vc_{kind}.wav"
        sf.write(p, z, SR, subtype="PCM_16")
        out_rows.append({"path": str(p), "label": "changer", "source": "dsp_changer", "lang": r["lang"],
                         "group": r["group"], "vc_type": f"{kind}: {desc}"})
        if i % 50 == 0:
            print(f"  {i}/{len(picked)} {p.name} ({desc})", flush=True)
    with open(root / "manifest_vc.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group", "vc_type"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {len(out_rows)} voice-changer clips -> {root / 'manifest_vc.csv'}  per language: {per}")


if __name__ == "__main__":
    sys.exit(main())
