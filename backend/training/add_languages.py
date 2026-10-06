"""Add real speech in more Indian languages (+ vocoded fakes of the same clips) to the training set.

Why: a detector trained mostly on Hindi/English read speech flags unfamiliar languages as "AI".
Vocoded copies of the same clips keep labels balanced per language, so the model learns vocoder
artefacts, not "language X = real".

Usage: python training/add_languages.py K:\\vgtools\\data
Expects FLEURS folders: fleurs/<lang>/ with <lang>_<split>.tsv (see download in README).
"""
import csv
import random
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402,F401
from app.ai.audio_io import load_audio  # noqa: E402
from training.build_dataset import MAX_S, SR, clip  # noqa: E402

import soundfile as sf  # noqa: E402

# (folder, tsv, language tag, real clips, vocoded clips)
PLAN = [
    ("hi_in_test", "hi_in_test.tsv", "hi", 200, 120),
    ("mr_in", "mr_in_dev.tsv", "mr", 120, 80),
    ("ta_in", "ta_in_dev.tsv", "ta", 120, 80),
    ("te_in", "te_in_dev.tsv", "te", 120, 80),
    ("pa_in", "pa_in_dev.tsv", "pa", 120, 80),
]


@torch.inference_mode()
def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    out = data / "vgset"
    from transformers import SpeechT5FeatureExtractor, SpeechT5HifiGan
    fe = SpeechT5FeatureExtractor()
    voc = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan").eval()
    rows_out = []
    for folder, tsv, lang, n_real, n_voc in PLAN:
        tsv_path = data / "fleurs" / tsv
        if not tsv_path.exists():
            print("skip", folder)
            continue
        rows = list(csv.reader(open(tsv_path, encoding="utf-8"), delimiter="\t"))
        random.Random(11).shuffle(rows)
        wavs = {p.name: p for p in (data / "fleurs" / folder).rglob("*.wav")}
        items = [(r[0], wavs[r[1]]) for r in rows if r[1] in wavs][:n_real]
        for i, (sid, p) in enumerate(items):
            y = load_audio(p.read_bytes())
            name = f"fl_{lang}x_{p.stem}"
            sf.write(out / "real" / f"{name}.wav", clip(y), SR, subtype="PCM_16")
            rows_out.append({"path": str(out / "real" / f"{name}.wav"), "label": "real", "source": "fleurs",
                             "lang": lang, "group": f"flx_{lang}_{sid}"})
            if i < n_voc:
                mel = fe(audio_target=y[: int(MAX_S * SR)], sampling_rate=SR, return_tensors="pt")["input_values"]
                w = voc(mel[0]).numpy()
                sf.write(out / "fake" / f"voc_{lang}x_{p.stem}.wav", clip(w), SR, subtype="PCM_16")
                rows_out.append({"path": str(out / "fake" / f"voc_{lang}x_{p.stem}.wav"), "label": "fake",
                                 "source": "vocoder_hifigan", "lang": lang, "group": f"flx_{lang}_{sid}"})
        print(folder, "real", len(items), "vocoded", min(n_voc, len(items)), flush=True)
    with open(out / "manifest.csv", "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group"]).writerows(rows_out)
    print("added", len(rows_out))


if __name__ == "__main__":
    main()
