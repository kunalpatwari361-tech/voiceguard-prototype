"""Round 2 of training data: more real human voices AND more AI voices.

real : FLEURS English test split + Bengali / Gujarati / Kannada / Malayalam dev (streamed: only the clips we use
       are downloaded), LibriSpeech dev-clean (40 English speakers, audiobook microphones – the old model flagged
       too many of these as AI)
fake : vocoded copies of the new-language clips (same speaker and words, only vocoder artefacts differ, so the
       model cannot learn "language X = real"), Microsoft Edge neural voices in the new languages plus more
       English and multilingual voices (random speed and pitch), and Google Translate TTS (gTTS) – a different
       TTS engine – in 9 Indian languages and 3 English accents

Held out for scripts/eval_pipeline.py (never trained on): the Edge male voices of bn/gu/kn/ml, gTTS Australian
English, some LibriSpeech speakers, and every FLEURS clip after the first N of each language.

Usage: python training/add_more_voices.py K:\\vgtools\\data [steps]   (needs internet; edge-tts and gTTS installed)
       steps: comma list of fleurs,libri,edge,gtts (default: all)
"""
import asyncio
import csv
import io
import random
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402,F401
from app.ai.audio_io import load_audio  # noqa: E402
from training.build_dataset import MAX_S, SR, clip  # noqa: E402

FLEURS = "https://huggingface.co/datasets/google/fleurs/resolve/main/data"
LIBRI = "https://www.openslr.org/resources/12/dev-clean.tar.gz"
EVAL = Path(r"K:\vgtools\eval")
# (FLEURS language, split, tag, clips for training, extra clips kept for the evaluation, vocoded copies)
FLEURS_PLAN = [
    ("en_us", "test", "en", 150, 12, 0),
    ("bn_in", "dev", "bn", 80, 10, 40),
    ("gu_in", "dev", "gu", 80, 10, 40),
    ("kn_in", "dev", "kn", 80, 10, 40),
    ("ml_in", "dev", "ml", 80, 10, 40),
]
# (voice, language of the sentences, sentences)
EDGE_TRAIN = (
    [(v, l, 20) for v, l in [("bn-IN-TanishaaNeural", "bn"), ("gu-IN-DhwaniNeural", "gu"),
                             ("kn-IN-SapnaNeural", "kn"), ("ml-IN-SobhanaNeural", "ml")]]
    + [(v, l, 5) for v in ["de-DE-FlorianMultilingualNeural", "de-DE-SeraphinaMultilingualNeural",
                           "fr-FR-RemyMultilingualNeural", "fr-FR-VivienneMultilingualNeural",
                           "it-IT-GiuseppeMultilingualNeural", "en-US-AndrewMultilingualNeural"] for l in ("hi", "en")]
    + [(v, "en", 4) for v in ["en-US-AvaNeural", "en-US-AndrewNeural", "en-US-EmmaNeural", "en-US-BrianNeural",
                              "en-GB-LibbyNeural", "en-GB-ThomasNeural", "en-AU-WilliamNeural", "en-CA-ClaraNeural",
                              "en-CA-LiamNeural", "en-IE-ConnorNeural", "en-IE-EmilyNeural", "en-NZ-MitchellNeural",
                              "en-ZA-LukeNeural", "en-SG-WayneNeural", "en-PH-RosaNeural", "en-KE-ChilembaNeural",
                              "en-NG-AbeoNeural"]]
)
EDGE_EVAL = [("bn-IN-BashkarNeural", "bn"), ("gu-IN-NiranjanNeural", "gu"), ("kn-IN-GaganNeural", "kn"),
             ("ml-IN-MidhunNeural", "ml")]
GTTS_TRAIN = [("hi", "co.in"), ("en", "co.in"), ("en", "com"), ("en", "co.uk"), ("ta", "com"), ("te", "com"),
              ("mr", "com"), ("bn", "com"), ("gu", "com"), ("kn", "com"), ("ml", "com"), ("pa", "com")]
GTTS_EVAL = [("en", "com.au")]
TEXT_TSV = {"hi": "hi_in_dev.tsv", "en": "en_us_dev.tsv", "ta": "ta_in_dev.tsv", "te": "te_in_dev.tsv",
            "mr": "mr_in_dev.tsv", "pa": "pa_in_dev.tsv", "bn": "bn_in_dev.tsv", "gu": "gu_in_dev.tsv",
            "kn": "kn_in_dev.tsv", "ml": "ml_in_dev.tsv"}
SCAM_LINES = {
    "hi": ["बेटा मैं तुम्हारा मामा बोल रहा हूँ, अस्पताल में हूँ, अभी दस हज़ार गूगल पे कर दो।",
           "आपका पार्सल कस्टम में पकड़ा गया है, इसमें ड्रग्स मिले हैं, अभी वीडियो कॉल पर आइए वरना गिरफ़्तारी होगी।",
           "मैं बैंक से बोल रहा हूँ, आपका केवाईसी अपडेट नहीं हुआ, जो कोड आया है वो बता दीजिए।"],
    "en": ["Mom, I lost my wallet and phone, this is my friend's number, please send money on this UPI quickly.",
           "This is the cyber crime department. A case is registered on your Aadhaar. Stay on the call and do not disconnect.",
           "Congratulations, you have won a lottery of twenty five lakh rupees. Pay the processing fee to claim it."],
}


def stream_tar(url, suffix):
    """Read a .tar.gz while it downloads, so only the part we use is fetched. curl, not httpx: on this laptop
    Python's client hangs on the Hugging Face CDN while curl streams at full speed."""
    proc = subprocess.Popen(["curl", "-sfL", "--retry", "3", url], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tf:
            for m in tf:
                if m.isfile() and m.name.endswith(suffix):
                    yield m.name, tf.extractfile(m).read()
    finally:
        proc.kill()
        proc.wait()


def texts(data: Path, lang: str, n: int, seed: int) -> list[str]:
    tsv = data / "fleurs" / TEXT_TSV[lang]
    if not tsv.exists():
        return []
    rows = list(csv.reader(open(tsv, encoding="utf-8"), delimiter="\t"))
    out = sorted({r[2].strip('"') for r in rows if len(r) > 2 and 30 < len(r[2]) < 200})
    random.Random(seed).shuffle(out)
    return out[:n]


class Run:
    def __init__(self, data: Path):
        self.data, self.out = data, data / "vgset"
        self.manifest = self.out / "manifest.csv"
        self.have = {r["path"] for r in csv.DictReader(open(self.manifest, encoding="utf-8"))}
        self.rows = []

    def add(self, y, label, source, lang, group, name):
        p = self.out / label / f"{name}.wav"
        if str(p) in self.have:
            return
        sf.write(p, clip(y), SR, subtype="PCM_16")
        self.rows.append({"path": str(p), "label": label, "source": source, "lang": lang, "group": group})
        self.have.add(str(p))

    def save(self):
        with open(self.manifest, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=["path", "label", "source", "lang", "group"]).writerows(self.rows)
        print(f"  manifest +{len(self.rows)}", flush=True)
        self.rows = []

    # ---------------- real: FLEURS ----------------
    def fleurs(self):
        for lang, split, tag, n_train, n_eval, n_voc in FLEURS_PLAN:
            tsv = self.data / "fleurs" / f"{lang}_{split}.tsv"
            if not tsv.exists():
                subprocess.run(["curl", "-sfL", "-o", str(tsv), f"{FLEURS}/{lang}/{split}.tsv"], check=True)
            ids = {r[1]: r[0] for r in csv.reader(open(tsv, encoding="utf-8"), delimiter="\t") if len(r) > 1}
            folder = self.data / "fleurs" / (lang if split == "dev" else f"{lang}_{split}") / split
            folder.mkdir(parents=True, exist_ok=True)
            kept = sorted(p for p in folder.glob("*.wav") if p.name in ids)
            t = time.time()
            if len(kept) < n_train + n_eval:
                kept = []
                for name, b in stream_tar(f"{FLEURS}/{lang}/audio/{split}.tar.gz", ".wav"):
                    name = Path(name).name
                    if name in ids:
                        (folder / name).write_bytes(b)
                        kept.append(folder / name)
                    if len(kept) >= n_train + n_eval:
                        break
            for i, p in enumerate(kept[:n_train]):   # the rest stay unseen for the evaluation
                y = load_audio(p.read_bytes())
                group = f"fly_{tag}_{ids[p.name]}"
                self.add(y, "real", "fleurs", tag, group, f"fly_{tag}_{p.stem}")
                if i < n_voc:
                    self.add(self.vocode(y), "fake", "vocoder_hifigan", tag, group, f"voc_{tag}y_{p.stem}")
            print(f"fleurs {lang}/{split}: {len(kept)} clips ({min(n_train, len(kept))} train, vocoded "
                  f"{min(n_voc, len(kept))})  {time.time() - t:.0f}s", flush=True)
            self.save()

    @torch.inference_mode()
    def vocode(self, y):
        if not hasattr(self, "_voc"):
            from transformers import SpeechT5FeatureExtractor, SpeechT5HifiGan
            self._fe = SpeechT5FeatureExtractor()
            self._voc = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan").eval()
        mel = self._fe(audio_target=y[: int(MAX_S * SR)], sampling_rate=SR, return_tensors="pt")["input_values"]
        return self._voc(mel[0]).numpy()

    # ---------------- real: LibriSpeech dev-clean ----------------
    def libri(self):
        import pyarrow.parquet as pqm
        dummy = {str(r["speaker_id"]) for r in pqm.read_table(EVAL / "real" / "libri.parquet").to_pylist()}
        held = EVAL / "libri_heldout"
        held.mkdir(exist_ok=True)
        per_chapter, per_speaker = {}, {}
        t = time.time()
        for name, b in stream_tar(LIBRI, ".flac"):
            spk, chap = Path(name).parts[-3], Path(name).parts[-2]
            heldout = int(spk) % 5 == 0 and spk not in dummy
            limit = 2 if heldout else 4
            if per_chapter.get((spk, chap), 0) >= 2 or per_speaker.get(spk, 0) >= limit:
                continue
            per_chapter[(spk, chap)] = per_chapter.get((spk, chap), 0) + 1
            per_speaker[spk] = per_speaker.get(spk, 0) + 1
            if heldout:
                (held / Path(name).name).write_bytes(b)
            else:
                self.add(load_audio(b), "real", "librispeech", "en", f"libri_{spk}", f"libri2_{Path(name).stem}")
        print(f"librispeech: {len(per_speaker)} speakers, held out {len(list(held.glob('*.flac')))} clips  "
              f"{time.time() - t:.0f}s", flush=True)
        self.save()

    # ---------------- fake: Edge neural voices ----------------
    async def edge(self):
        import edge_tts
        have = {v["ShortName"] for v in await edge_tts.list_voices()}
        rnd = random.Random(7)

        async def tts(text, voice):
            rate, pitch = rnd.randint(-15, 20), rnd.randint(-8, 8)
            buf = io.BytesIO()
            async for ch in edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz").stream():
                if ch["type"] == "audio":
                    buf.write(ch["data"])
            return load_audio(buf.getvalue())

        n = 0
        for vi, (voice, lang, k) in enumerate(EDGE_TRAIN):
            if voice not in have:
                print("  no such voice", voice)
                continue
            lines = texts(self.data, lang, k, seed=100 + vi)
            if lines and lang in SCAM_LINES and vi % 3 == 0:
                lines[0] = SCAM_LINES[lang][vi % len(SCAM_LINES[lang])]
            for j, text in enumerate(lines):
                try:
                    y = await tts(text, voice)
                except Exception as e:  # one failure must not stop the rest
                    print("  skip", voice, type(e).__name__)
                    continue
                if len(y) >= SR:
                    self.add(y, "fake", "neural_tts", lang, f"edge_{voice}", f"ed_{voice}_{lang}_{j}")
                    n += 1
        print(f"edge voices: {n} clips", flush=True)
        self.save()
        out = EVAL / "neural"
        for voice, lang in EDGE_EVAL:
            if voice not in have:
                continue
            for j, text in enumerate(texts(self.data, lang, 40, seed=999)[-3:]):
                sf.write(out / f"{voice}_{j}.wav", clip(await tts(text, voice)), SR, subtype="PCM_16")
        print("edge held-out voices written to", out, flush=True)

    # ---------------- fake: Google Translate TTS ----------------
    def gtts(self):
        import socket

        import urllib3.util.connection
        from gtts import gTTS
        # IPv6 to Google hangs ~20 s per request on this network before falling back: go straight to IPv4
        urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

        def say(text, lang, tld):
            for attempt in range(3):
                try:
                    buf = io.BytesIO()
                    gTTS(text, lang=lang, tld=tld).write_to_fp(buf)
                    time.sleep(0.7)
                    return load_audio(buf.getvalue())
                except Exception as e:
                    print("  gtts retry", lang, tld, type(e).__name__, flush=True)
                    time.sleep(10 * (attempt + 1))
            return None

        n = 0
        for gi, (lang, tld) in enumerate(GTTS_TRAIN):
            for j, text in enumerate(texts(self.data, lang, 18, seed=300 + gi)):
                y = say(text, lang, tld)
                if y is not None and len(y) >= SR:
                    self.add(y, "fake", "gtts", lang, f"gtts_{lang}_{tld}", f"gt_{lang}_{tld}_{j}")
                    n += 1
            print(f"  gtts {lang}/{tld} done ({n})", flush=True)
        print(f"gtts: {n} clips", flush=True)
        self.save()
        for lang, tld in GTTS_EVAL:
            for j, text in enumerate(texts(self.data, lang, 40, seed=999)[-3:]):
                y = say(text, lang, tld)
                if y is not None:
                    sf.write(EVAL / "neural" / f"gtts_{lang}_{tld}_{j}.wav", clip(y), SR, subtype="PCM_16")


def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    steps = sys.argv[2].split(",") if len(sys.argv) > 2 else ["fleurs", "libri", "edge", "gtts"]
    run = Run(data)
    for s in steps:   # each step saves its own manifest rows, so a failed step can simply be re-run
        t = time.time()
        try:
            asyncio.run(run.edge()) if s == "edge" else getattr(run, s)()
        except Exception as e:
            print(f"STEP {s} FAILED: {type(e).__name__}: {e}", flush=True)
            run.save()
        print(f"step {s} finished in {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
