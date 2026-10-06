"""Compare candidate AI-voice detectors on real vs AI speech, clean and through a phone line.

  real: LibriSpeech dummy + Narsil/asr_dummy clips (human recordings)
  fake: MMS-TTS demo turns (neural) + Windows SAPI voices (classic TTS)
Usage: .venv\\Scripts\\python scripts\\eval_detectors.py K:\\vgtools\\eval
"""
import io
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402,F401
from app.ai import fingerprints, models  # noqa: E402
from app.ai.audio_io import load_audio  # noqa: E402
from app.ai.phone_channel import phone_channel  # noqa: E402

CANDIDATES = ["MelodyMachine/Deepfake-audio-detection-V2", "mo-thecreator/Deepfake-audio-detection",
              "garystafford/wav2vec2-deepfake-voice-detector"]


def load_set(eval_dir: Path):
    real, fake = [], []
    for p in sorted((eval_dir / "real").glob("asr_*")):
        y = load_audio(p.read_bytes())
        real.append((p.name, y[: 16000 * 12]))
    pq = eval_dir / "real" / "libri.parquet"
    if pq.exists():
        import pyarrow.parquet as pqm
        t = pqm.read_table(pq).to_pylist()
        for row in t[:25]:
            real.append((row["id"], load_audio(row["audio"]["bytes"])))
    for p in sorted((eval_dir / "fake").glob("*.wav")):
        fake.append((p.name, load_audio(p.read_bytes())))
    for p in sorted((ROOT / "demo_audio").glob("*_t*_hd.wav")):
        fake.append((p.name, load_audio(p.read_bytes())))
    return real, fake


def auc(pos, neg):
    pos, neg = np.array(pos), np.array(neg)
    return float(((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()))


def main():
    real, fake = load_set(Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\eval"))
    print(f"real={len(real)} fake={len(fake)}")
    for cond in ("clean", "phone"):
        tf = (lambda y: y) if cond == "clean" else (lambda y: phone_channel(y, codec="g711", packet_loss=0.02, seed=1))
        print(f"\n=== {cond.upper()} ===")
        for mid in CANDIDATES:
            det = models.deepfake(mid)
            t = time.time()
            pr = [det.predict(tf(y))["fake_prob"] for _, y in real]
            pf = [det.predict(tf(y))["fake_prob"] for _, y in fake]
            acc = (np.mean(np.array(pr) < 0.5) * len(pr) + np.mean(np.array(pf) >= 0.5) * len(pf)) / (len(pr) + len(pf))
            print(f"{mid:55s} AUC={auc(pf, pr):.3f} acc@0.5={acc:.3f} "
                  f"real_mean={np.mean(pr):.2f} fake_mean={np.mean(pf):.2f} ({time.time() - t:.0f}s)")
        fr = [fingerprints.analyze(tf(y))["score"] for _, y in real]
        ff = [fingerprints.analyze(tf(y))["score"] for _, y in fake]
        fr = [x if x is not None else 0.4 for x in fr]
        ff = [x if x is not None else 0.4 for x in ff]
        print(f"{'fingerprints (rule engine)':55s} AUC={auc(ff, fr):.3f} real_mean={np.mean(fr):.2f} fake_mean={np.mean(ff):.2f}")


if __name__ == "__main__":
    main()
