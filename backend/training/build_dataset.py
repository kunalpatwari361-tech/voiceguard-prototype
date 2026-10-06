"""Build the VoiceGuard real-vs-AI training set.

real : Google FLEURS dev (Hindi + English, many speakers), LibriSpeech dummy, small public clips
fake : 1) real FLEURS clips re-synthesised through a neural vocoder (SpeechT5 HiFi-GAN) - same speaker,
          same words, only the vocoder artefacts differ, so the model cannot cheat on voice or language
       2) Meta MMS-TTS (VITS) reading FLEURS sentences in Hindi and English, random speed / noise
       3) Windows SAPI voices (made separately by make_sapi.ps1, picked up from <out>/fake/sapi_*.wav)

Usage: python training/build_dataset.py K:\\vgtools\\data
"""
import csv
import random
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402,F401
from app.ai.audio_io import load_audio, resample  # noqa: E402

SR = 16000
MAX_S = 6.0
random.seed(0)
np.random.seed(0)


def clip(y: np.ndarray) -> np.ndarray:
    n = int(MAX_S * SR)
    if len(y) > n:
        start = random.randint(0, len(y) - n)
        y = y[start:start + n]
    return (y / (np.abs(y).max() + 1e-9) * random.uniform(0.3, 0.9)).astype(np.float32)


def fleurs(data: Path, lang: str, limit: int):
    rows = list(csv.reader(open(data / "fleurs" / f"{lang}_dev.tsv", encoding="utf-8"), delimiter="\t"))
    random.shuffle(rows)
    out = []
    for r in rows:
        wav = next((data / "fleurs" / lang).rglob(r[1]), None)
        if wav:
            out.append({"id": r[0], "path": wav, "text": r[2]})
        if len(out) >= limit:
            break
    return out


@torch.inference_mode()
def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    out = data / "vgset"
    (out / "real").mkdir(parents=True, exist_ok=True)
    (out / "fake").mkdir(parents=True, exist_ok=True)
    manifest = []

    def add(y, label, source, lang, group, name):
        p = out / label / f"{name}.wav"
        sf.write(p, clip(y), SR, subtype="PCM_16")
        manifest.append({"path": str(p), "label": label, "source": source, "lang": lang, "group": group})

    # ---------------- real ----------------
    fl = {"hi": fleurs(data, "hi_in", 320), "en": fleurs(data, "en_us", 320)}
    for lang, items in fl.items():
        for it in items:
            add(load_audio(it["path"].read_bytes()), "real", "fleurs", lang, f"fl_{lang}_{it['id']}", f"fl_{lang}_{it['path'].stem}")
    pq = data.parent / "eval" / "real" / "libri.parquet"
    if pq.exists():
        import pyarrow.parquet as pqm
        for row in pqm.read_table(pq).to_pylist():
            add(load_audio(row["audio"]["bytes"]), "real", "librispeech", "en", f"libri_{row['speaker_id']}", f"libri_{row['id']}")
    print("real:", len(manifest))

    # ---------------- fake 1: vocoded real ----------------
    from transformers import SpeechT5FeatureExtractor, SpeechT5HifiGan
    fe = SpeechT5FeatureExtractor()
    voc = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan").eval()
    n_voc = 0
    for lang, items in fl.items():
        for it in items[:220]:
            y = load_audio(it["path"].read_bytes())[: int(MAX_S * SR)]
            mel = fe(audio_target=y, sampling_rate=SR, return_tensors="pt")["input_values"]
            w = voc(mel[0]).numpy()
            add(w, "fake", "vocoder_hifigan", lang, f"fl_{lang}_{it['id']}", f"voc_{lang}_{it['path'].stem}")
            n_voc += 1
    print("vocoded:", n_voc)

    # ---------------- fake 2: MMS-TTS ----------------
    from transformers import AutoTokenizer, VitsModel
    n_tts = 0
    for lang, code in (("hi", "hin"), ("en", "eng")):
        tok = AutoTokenizer.from_pretrained(f"facebook/mms-tts-{code}")
        model = VitsModel.from_pretrained(f"facebook/mms-tts-{code}").eval()
        for it in fl[lang][220:320]:
            model.speaking_rate = random.uniform(0.85, 1.2)
            model.noise_scale = random.uniform(0.45, 0.85)
            torch.manual_seed(random.randint(0, 10_000))
            ids = tok(it["text"][:300], return_tensors="pt")
            if ids["input_ids"].shape[1] < 3:
                continue
            w = model(**ids).waveform[0].numpy()
            add(resample(w, model.config.sampling_rate, SR), "fake", "mms_tts", lang, f"mms_{lang}_{it['id']}", f"mms_{lang}_{it['id']}_{n_tts}")
            n_tts += 1
    print("mms:", n_tts)

    # ---------------- fake 3: SAPI (pre-generated) ----------------
    for p in sorted((out / "fake").glob("sapi_*.wav")):
        manifest.append({"path": str(p), "label": "fake", "source": "sapi", "lang": "en", "group": p.stem.rsplit("_", 1)[0]})

    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group"])
        w.writeheader()
        w.writerows(manifest)
    print("total:", len(manifest))
    # sentences for the SAPI script
    (out / "sapi_sentences.txt").write_text("\n".join(it["text"] for it in fl["en"][:80]), encoding="utf-8")


if __name__ == "__main__":
    main()
