"""Pitch idea #2: build a phone-quality training set.

Usage:
  python training/phone_augment.py <input_dir> <output_dir> [--copies 3]

For every WAV/FLAC in input_dir (keep sub-folders like real/ and fake/), writes copies passed
through the telephone channel: G.711, low-bitrate (AMR-like) and packet loss variants.
"""
import argparse
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai.audio_io import load_audio  # noqa: E402
from app.ai.phone_channel import phone_channel  # noqa: E402

VARIANTS = [
    ("g711", dict(codec="g711", packet_loss=0.0)),
    ("lowbit", dict(codec="lowbitrate", packet_loss=0.0)),
    ("g711loss", dict(codec="g711", packet_loss=0.05)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir")
    ap.add_argument("output_dir")
    ap.add_argument("--copies", type=int, default=3)
    a = ap.parse_args()
    src, dst = Path(a.input_dir), Path(a.output_dir)
    files = [p for p in src.rglob("*") if p.suffix.lower() in (".wav", ".flac", ".ogg", ".mp3")]
    for i, p in enumerate(files, 1):
        y = load_audio(p.read_bytes())
        rel = p.relative_to(src).with_suffix("")
        for name, kw in VARIANTS[: a.copies]:
            out = dst / f"{rel}__{name}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(out, phone_channel(y, seed=i, **kw), 16000, subtype="PCM_16")
        print(f"[{i}/{len(files)}] {p.name}")


if __name__ == "__main__":
    main()
