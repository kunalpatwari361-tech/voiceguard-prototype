"""Add more Hindi MMS-TTS fakes to the training set (FLEURS Hindi dev only has 239 sentences).

Uses only the TEXT of FLEURS Hindi train sentences - no extra audio download.
Usage: python training/add_mms_hindi.py K:\\vgtools\\data 150
"""
import csv
import random
import sys
from pathlib import Path

import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402,F401
from app.ai.audio_io import resample  # noqa: E402
from training.build_dataset import clip  # noqa: E402


@torch.inference_mode()
def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    n_want = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    out = data / "vgset"
    rows = list(csv.reader(open(data / "fleurs" / "hi_in_train.tsv", encoding="utf-8"), delimiter="\t"))
    seen, sents = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            sents.append((r[0], r[2]))
    random.Random(5).shuffle(sents)
    from transformers import AutoTokenizer, VitsModel
    tok = AutoTokenizer.from_pretrained("facebook/mms-tts-hin")
    model = VitsModel.from_pretrained("facebook/mms-tts-hin").eval()
    added = []
    for sid, text in sents[:n_want]:
        model.speaking_rate = random.uniform(0.85, 1.2)
        model.noise_scale = random.uniform(0.45, 0.85)
        torch.manual_seed(random.randint(0, 10_000))
        ids = tok(text[:300], return_tensors="pt")
        if ids["input_ids"].shape[1] < 3:
            continue
        w = resample(model(**ids).waveform[0].numpy(), model.config.sampling_rate, 16000)
        p = out / "fake" / f"mmsx_hi_{sid}.wav"
        sf.write(p, clip(w), 16000, subtype="PCM_16")
        added.append({"path": str(p), "label": "fake", "source": "mms_tts", "lang": "hi", "group": f"mmsx_hi_{sid}"})
    with open(out / "manifest.csv", "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group"]).writerows(added)
    print("added", len(added))


if __name__ == "__main__":
    main()
