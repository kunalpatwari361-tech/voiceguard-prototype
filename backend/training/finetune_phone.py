"""Pitch idea #2: fine-tune the AI Voice Detector on phone-quality audio.

Data layout (make it with phone_augment.py):
  data_dir/real/*.wav   data_dir/fake/*.wav

Usage:
  python training/finetune_phone.py data_dir out_dir [--epochs 2] [--lr 1e-5]
Then point the server at it:  set VG_DEEPFAKE_MODEL=out_dir

CPU works for a few hundred clips; use a GPU for real datasets (ASVspoof 2019/2021 LA + DF, In-the-Wild).
"""
import argparse
import random
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402,F401  (sets HF_HOME)
from app.ai.audio_io import load_audio  # noqa: E402
from app.ai.phone_channel import phone_channel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--base", default=config.DEEPFAKE_MODEL)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--batch", type=int, default=4)
    a = ap.parse_args()

    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    fe = AutoFeatureExtractor.from_pretrained(a.base)
    model = AutoModelForAudioClassification.from_pretrained(a.base)
    labels = {v.lower(): int(k) for k, v in model.config.id2label.items()}
    items = [(p, labels["real"]) for p in Path(a.data_dir, "real").glob("*.wav")] + \
            [(p, labels["fake"]) for p in Path(a.data_dir, "fake").glob("*.wav")]
    random.shuffle(items)
    split = max(1, len(items) // 10)
    val, train = items[:split], items[split:]
    print(f"train={len(train)} val={len(val)}")

    def batch_audio(batch, augment):
        xs = []
        for p, _ in batch:
            y = load_audio(p.read_bytes())[: 16000 * 4]
            if augment and random.random() < 0.5:  # on-the-fly phone channel
                y = phone_channel(y, codec=random.choice(["g711", "lowbitrate"]), packet_loss=random.choice([0, 0.03]))
            xs.append(y)
        inp = fe(xs, sampling_rate=16000, return_tensors="pt", padding=True)
        return inp, torch.tensor([lbl for _, lbl in batch])

    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    for ep in range(a.epochs):
        model.train()
        for i in range(0, len(train), a.batch):
            inp, lbl = batch_audio(train[i:i + a.batch], augment=True)
            loss = model(**inp, labels=lbl).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            if (i // a.batch) % 20 == 0:
                print(f"epoch {ep} step {i // a.batch} loss {loss.item():.4f}")
        model.eval()
        correct = 0
        with torch.inference_mode():
            for i in range(0, len(val), a.batch):
                inp, lbl = batch_audio(val[i:i + a.batch], augment=True)
                correct += int((model(**inp).logits.argmax(-1) == lbl).sum())
        print(f"epoch {ep} phone-quality val accuracy {correct / max(1, len(val)):.3f}")
    model.save_pretrained(a.out_dir)
    fe.save_pretrained(a.out_dir)
    print("saved to", a.out_dir)


if __name__ == "__main__":
    np.random.seed(0)
    main()
