"""Add modern neural-TTS voices (Microsoft Edge read-aloud voices) as AI examples for the detector.

The evaluation set (scripts/eval_pipeline.py --neural) uses OTHER voices – hi-IN Madhur/Swara, en-IN Prabhat/Neerja,
ta-IN Valluvar, te-IN Mohan, mr-IN Manohar, en-US Guy – so its numbers stay honest. Hindi here comes from the
"Multilingual" voices, which can read Devanagari.

Usage: python training/add_neural.py K:\\vgtools\\data      (needs edge-tts and an internet connection)
"""
import asyncio
import csv
import io
import random
import sys
from pathlib import Path

import edge_tts
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ai.audio_io import load_audio  # noqa: E402

SR, MAX_S = 16000, 8.0
HELD_OUT = {"hi-IN-MadhurNeural", "hi-IN-SwaraNeural", "en-IN-PrabhatNeural", "en-IN-NeerjaNeural",
            "ta-IN-ValluvarNeural", "te-IN-MohanNeural", "mr-IN-ManoharNeural", "en-US-GuyNeural"}
# (transcript file, lang, voices, sentences per voice)
PLAN = [
    ("hi_in_dev.tsv", "hi", ["en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural", "en-US-BrianMultilingualNeural",
                             "en-US-EmmaMultilingualNeural", "en-AU-WilliamMultilingualNeural"], 8),
    ("en_us_dev.tsv", "en", ["en-IN-NeerjaExpressiveNeural", "en-US-AriaNeural", "en-US-JennyNeural", "en-US-ChristopherNeural",
                             "en-GB-RyanNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural", "en-US-MichelleNeural"], 4),
    ("ta_in_dev.tsv", "ta", ["ta-IN-PallaviNeural", "ta-MY-KaniNeural", "ta-MY-SuryaNeural", "ta-SG-AnbuNeural",
                             "ta-SG-VenbaNeural", "ta-LK-KumarNeural", "ta-LK-SaranyaNeural"], 4),
    ("te_in_dev.tsv", "te", ["te-IN-ShrutiNeural"], 8),
    ("mr_in_dev.tsv", "mr", ["mr-IN-AarohiNeural"], 8),
]
SCAM_LINES = [
    ("hi", "पापा मैं मुसीबत में हूँ, पुलिस ने पकड़ लिया है, अभी पैसे भेज दो, किसी को मत बताना।"),
    ("hi", "मम्मी यह मेरा नया नंबर है, पुराना फ़ोन टूट गया, जल्दी से यूपीआई पर बीस हज़ार भेजो।"),
    ("en", "Hello, I am calling from your bank. Your account will be blocked today. Please tell me the OTP you just received."),
    ("en", "Dad, it's me, I had an accident and the hospital needs payment right now. Don't tell mom."),
]


def clip(y: np.ndarray) -> np.ndarray:
    y = y[: int(MAX_S * SR)]
    return (y / (np.max(np.abs(y)) + 1e-9) * 0.8).astype(np.float32)


async def tts(text: str, voice: str) -> np.ndarray:
    buf = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return load_audio(buf.getvalue())


async def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    out = data / "vgset"
    (out / "fake").mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.csv"
    have = {r["path"] for r in csv.DictReader(open(manifest, encoding="utf-8"))}
    jobs = []
    for tsv, lang, voices, per in PLAN:
        rows = list(csv.reader(open(data / "fleurs" / tsv, encoding="utf-8"), delimiter="\t"))
        random.Random(23).shuffle(rows)
        texts = [r[2].strip('"') for r in rows if len(r) > 2 and 40 < len(r[2]) < 220]
        for vi, voice in enumerate(voices):
            assert voice not in HELD_OUT
            for k in range(per):
                jobs.append((lang, voice, texts[(vi * per + k) % len(texts)], f"nt_{voice}_{k}"))
    multi = [v for v in PLAN[0][2]]
    for i, (lang, text) in enumerate(SCAM_LINES):
        jobs.append((lang, multi[i % len(multi)], text, f"nt_scam_{i}"))
    new = []
    for lang, voice, text, group in jobs:
        path = out / "fake" / f"{group}.wav"
        if str(path) in have:
            continue
        try:
            y = await tts(text, voice)
        except Exception as e:  # one voice failing must not stop the rest
            print("skip", voice, type(e).__name__, e)
            continue
        if len(y) < SR:
            continue
        sf.write(path, clip(y), SR, subtype="PCM_16")
        new.append({"path": str(path), "label": "fake", "source": "neural_tts", "lang": lang, "group": group})
        print(f"  {len(new):3d} {voice:34s} {lang}  {len(y) / SR:.1f}s", flush=True)
    with open(manifest, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group"])
        w.writerows(new)
    print(f"added {len(new)} neural-TTS clips to {manifest}")


if __name__ == "__main__":
    asyncio.run(main())
