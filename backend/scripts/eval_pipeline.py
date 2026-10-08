"""End-to-end check of the voice AI on voices it has NEVER seen, in the conditions a phone really gives it.

Runs the same analysis as /api/analyze (AI-voice detector + voice fingerprints + risk fusion, audio only)
on unseen real speech and unseen AI speech, each in four conditions:
  clean     the file as it is
  phone     8 kHz phone line, G.711, 2 % packet loss
  whatsapp  real Opus codec at voice-note bitrate
  speaker   what the Live Call Check hears: phone line -> the phone's loudspeaker -> the room
            (reverb + noise) -> the phone's microphone
Prints catch rates (AI voices) and false alarms (real voices) per condition, plus every mistake.

Usage: .venv\\Scripts\\python scripts\\eval_pipeline.py [--neural <folder of extra AI clips>]
"""
import argparse
import csv
import glob
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402,F401
from app.ai.audio_io import load_audio  # noqa: E402
from app.ai.phone_channel import codec_roundtrip, phone_channel  # noqa: E402
from app.ai.pipeline import analyze_voice  # noqa: E402

SR = 16000
DATA = Path(r"K:\vgtools\data")


def speaker_capture(y: np.ndarray, seed: int) -> np.ndarray:
    """Phone line -> small loudspeaker -> room -> microphone (the Live Call Check's real input)."""
    rng = np.random.default_rng(seed)
    x = phone_channel(y, codec="g711", packet_loss=0.02, seed=seed)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X = np.fft.rfft(x) / np.sqrt(1 + (400 / np.maximum(f, 1)) ** 4)          # tiny speaker: little bass
    x = np.fft.irfft(X, len(x))
    x = np.tanh(1.5 * x / (np.max(np.abs(x)) + 1e-9) * 0.7) / np.tanh(1.5)    # mild speaker distortion
    rt60 = rng.uniform(0.25, 0.5)
    n = int(rt60 * SR)
    ir = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / SR / rt60) * rng.uniform(0.03, 0.08)
    ir[0] = 1.0                                                                  # direct sound + room echo
    L = len(x) + n
    x = np.fft.irfft(np.fft.rfft(x, L) * np.fft.rfft(ir, L), L)[: len(x)]
    w = np.fft.rfft(rng.normal(0, 1, len(x))) / np.sqrt(np.maximum(np.fft.rfftfreq(len(x), 1 / SR), 20))
    noise = np.fft.irfft(w, len(x))                                              # pink-ish room + mic noise
    frames = x[: len(x) // 400 * 400].reshape(-1, 400)
    p = np.mean(frames ** 2, axis=1)
    speech_p = np.mean(p[p > 0.01 * p.max()])
    snr = rng.uniform(15, 25)
    x = x + noise * np.sqrt(speech_p / (np.mean(noise ** 2) * 10 ** (snr / 10)))
    return (x / (np.max(np.abs(x)) + 1e-9) * 0.5).astype(np.float32)


CONDS = {
    "clean": lambda y, i: y,
    "phone": lambda y, i: phone_channel(y, codec="g711", packet_loss=0.02, seed=i),
    "whatsapp": lambda y, i: codec_roundtrip(y, codec="opus", level=0.95),
    "speaker": speaker_capture,
}


def unseen_real(per_lang: int = 4) -> list[tuple[str, Path]]:
    used = {Path(r["path"]).stem.split("_", 2)[-1] for r in csv.DictReader(open(DATA / "vgset" / "manifest.csv", encoding="utf-8"))}
    out = [(f"en-ext {Path(p).stem}", Path(p)) for p in sorted(glob.glob(r"K:\vgtools\eval\real\*.*")) if not p.endswith(".parquet")]
    rnd = random.Random(5)
    test_hi = sorted((DATA / "fleurs" / "hi_in_test").rglob("*.wav"))
    out += [(f"hi-test {p.stem[:10]}", p) for p in rnd.sample(test_hi, min(2 * per_lang, len(test_hi)))]
    for lang in ("mr_in", "ta_in", "te_in", "pa_in"):
        free = [p for p in sorted((DATA / "fleurs" / lang).rglob("*.wav")) if p.stem not in used]
        out += [(f"{lang[:2]}-dev {p.stem[:10]}", p) for p in rnd.sample(free, min(per_lang, len(free)))]
    return out


def unseen_fake(neural: Path | None) -> list[tuple[str, Path]]:
    out = [(f"demo {Path(p).stem[:26]}", Path(p)) for p in sorted(glob.glob(str(ROOT / "demo_audio" / "*_hd.wav")))[::3]]
    out += [(f"sapi {Path(p).stem}", Path(p)) for p in sorted(glob.glob(r"K:\vgtools\eval\fake\sapi_*.wav"))[:4]]
    if neural:
        out += [(f"neural {p.stem}", p) for p in sorted(neural.glob("*.*"))]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neural", type=Path, default=None, help="folder of extra AI clips (e.g. modern neural TTS)")
    ap.add_argument("--conds", default=",".join(CONDS), help="only these conditions, e.g. speaker")
    a = ap.parse_args()
    for c in [c for c in CONDS if c not in a.conds.split(",")]:
        CONDS.pop(c)
    sets = {"real": unseen_real(), "ai": unseen_fake(a.neural)}
    print(f"real voices: {len(sets['real'])}  AI voices: {len(sets['ai'])}  conditions: {list(CONDS)}", flush=True)
    rows = []
    t0 = time.time()
    for label, items in sets.items():
        for i, (name, path) in enumerate(items):
            y = load_audio(path.read_bytes())[: SR * 20]
            for cond, fn in CONDS.items():
                r = analyze_voice(fn(y, 100 + i), do_asr=False, room_audio=cond == "speaker")
                if not r.get("ok"):
                    rows.append((label, name, cond, None, None, None, r.get("error")))
                    continue
                rows.append((label, name, cond, r["ai_voice"]["fake_prob"], r["fingerprints"]["score"],
                             r["risk"]["score"], r["risk"]["level"]))
            print(f"  {label:4s} {name:34s} " + "  ".join(f"{c}:{x[3]:.2f}/{x[6][0]}" if x[3] is not None else f"{c}:--"
                                                         for c, x in zip(CONDS, rows[-len(CONDS):])), flush=True)
    print(f"\n{len(rows)} analyses in {time.time() - t0:.0f}s\n")
    print("condition  | AI voices caught by detector | AI risk danger/caution/safe | real false alarms (detector) | real risk danger/caution/safe")
    for cond in CONDS:
        ai = [r for r in rows if r[0] == "ai" and r[2] == cond and r[3] is not None]
        re = [r for r in rows if r[0] == "real" and r[2] == cond and r[3] is not None]
        lv = lambda rs, l: sum(1 for r in rs if r[6] == l)  # noqa: E731
        print(f"{cond:10s} | {sum(r[3] >= 0.5 for r in ai):2d}/{len(ai):2d} ({100 * np.mean([r[3] >= 0.5 for r in ai]):3.0f}%)"
              f"            | {lv(ai, 'danger')}/{lv(ai, 'caution')}/{lv(ai, 'safe')}"
              f"                      | {sum(r[3] >= 0.5 for r in re):2d}/{len(re):2d} ({100 * np.mean([r[3] >= 0.5 for r in re]):3.0f}%)"
              f"             | {lv(re, 'danger')}/{lv(re, 'caution')}/{lv(re, 'safe')}")
    print("\nmistakes:")
    for r in rows:
        if r[3] is None:
            print(f"  no result   {r[0]:4s} {r[1]:34s} {r[2]:9s} {r[6]}")
        elif (r[0] == "ai") != (r[3] >= 0.5):
            print(f"  {'MISSED AI ' if r[0] == 'ai' else 'FALSE ALARM'} {r[1]:34s} {r[2]:9s} detector {r[3]:.2f}  "
                  f"fingerprints {r[4] if r[4] is not None else '-'}  risk {r[5]} {r[6]}")


if __name__ == "__main__":
    main()
