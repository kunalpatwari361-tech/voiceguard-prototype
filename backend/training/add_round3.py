"""Round 3 of training data: more speakers and sentences from the sources that already work, while always leaving
clips untouched for scripts/eval_pipeline.py.

real : FLEURS clips already on disk that no model has used (Marathi, Tamil, Telugu, Punjabi, Hindi test) - at least
       KEEP_FREE per language stay unused - plus 50 more streamed per new language (Bengali, Gujarati, Kannada,
       Malayalam; 10 of them stay unused)
fake : vocoded copies of half the new real clips, more Edge neural sentences for the Indian voices, more Google TTS
Held-out evaluation voices (add_more_voices.EDGE_EVAL, gTTS com.au, add_neural.HELD_OUT) are never used.

Usage: python training/add_round3.py K:\\vgtools\\data [steps]     steps: disk,stream,edge,gtts (default: all)
"""
import asyncio
import csv
import io
import random
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training.add_more_voices import FLEURS, GTTS_TRAIN, Run, stream_tar, texts  # noqa: E402
from app.ai.audio_io import load_audio  # noqa: E402
from training.build_dataset import SR  # noqa: E402

KEEP_FREE = 40
# (folder, tsv, tag, real clips, vocoded copies)
DISK_PLAN = [("mr_in", "mr_in_dev.tsv", "mr", 30, 15), ("ta_in", "ta_in_dev.tsv", "ta", 30, 15),
             ("te_in", "te_in_dev.tsv", "te", 30, 15), ("pa_in", "pa_in_dev.tsv", "pa", 30, 15),
             ("hi_in_test", "hi_in_test.tsv", "hi", 40, 20)]
# (FLEURS language, tag, new clips to fetch, of which used for training, vocoded copies)
STREAM_PLAN = [("bn_in", "bn", 50, 40, 20), ("gu_in", "gu", 50, 40, 20), ("kn_in", "kn", 50, 40, 20),
               ("ml_in", "ml", 50, 40, 20)]
EDGE_MORE = [("bn-IN-TanishaaNeural", "bn"), ("gu-IN-DhwaniNeural", "gu"), ("kn-IN-SapnaNeural", "kn"),
             ("ml-IN-SobhanaNeural", "ml"), ("ta-IN-PallaviNeural", "ta"), ("te-IN-ShrutiNeural", "te"),
             ("mr-IN-AarohiNeural", "mr"), ("en-IN-NeerjaExpressiveNeural", "en")]


def used_stems(run: Run) -> set[str]:
    return {Path(r["path"]).stem.split("_", 2)[-1] for r in csv.DictReader(open(run.manifest, encoding="utf-8"))}


def disk(run: Run):
    used = used_stems(run)
    for folder, tsv, tag, n_real, n_voc in DISK_PLAN:
        ids = {r[1]: r[0] for r in csv.reader(open(run.data / "fleurs" / tsv, encoding="utf-8"), delimiter="\t") if len(r) > 1}
        free = sorted(p for p in (run.data / "fleurs" / folder).rglob("*.wav") if p.stem not in used and p.name in ids)
        take = free[: max(0, len(free) - KEEP_FREE)][:n_real]
        for i, p in enumerate(take):
            y = load_audio(p.read_bytes())
            group = f"flz_{tag}_{ids[p.name]}"
            run.add(y, "real", "fleurs", tag, group, f"flz_{tag}_{p.stem}")
            if i < n_voc:
                run.add(run.vocode(y), "fake", "vocoder_hifigan", tag, group, f"voc_{tag}z_{p.stem}")
        print(f"disk {folder}: {len(free)} unused, trained on {len(take)}, {len(free) - len(take)} left for the test", flush=True)
        run.save()


def stream(run: Run):
    for lang, tag, n_new, n_train, n_voc in STREAM_PLAN:
        tsv = run.data / "fleurs" / f"{lang}_dev.tsv"
        ids = {r[1]: r[0] for r in csv.reader(open(tsv, encoding="utf-8"), delimiter="\t") if len(r) > 1}
        folder = run.data / "fleurs" / lang / "dev"
        have = {p.name for p in folder.glob("*.wav")}
        new = []
        t = time.time()
        for name, b in stream_tar(f"{FLEURS}/{lang}/audio/dev.tar.gz", ".wav"):
            name = Path(name).name
            if name in ids and name not in have:
                (folder / name).write_bytes(b)
                new.append(folder / name)
            if len(new) >= n_new:
                break
        for i, p in enumerate(new[:n_train]):   # the rest stay unused for the test
            y = load_audio(p.read_bytes())
            group = f"flz_{tag}_{ids[p.name]}"
            run.add(y, "real", "fleurs", tag, group, f"flz_{tag}_{p.stem}")
            if i < n_voc:
                run.add(run.vocode(y), "fake", "vocoder_hifigan", tag, group, f"voc_{tag}z_{p.stem}")
        print(f"stream {lang}: {len(new)} new clips, trained on {min(n_train, len(new))}  {time.time() - t:.0f}s", flush=True)
        run.save()


async def edge(run: Run):
    import edge_tts
    have = {v["ShortName"] for v in await edge_tts.list_voices()}
    rnd = random.Random(31)
    n = 0
    for vi, (voice, lang) in enumerate(EDGE_MORE):
        if voice not in have:
            print("  no such voice", voice)
            continue
        for j, text in enumerate(texts(run.data, lang, 8, seed=500 + vi)):
            try:
                buf = io.BytesIO()
                comm = edge_tts.Communicate(text, voice, rate=f"{rnd.randint(-15, 20):+d}%", pitch=f"{rnd.randint(-8, 8):+d}Hz")
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        buf.write(ch["data"])
                y = load_audio(buf.getvalue())
            except Exception as e:  # one failure must not stop the rest
                print("  skip", voice, type(e).__name__)
                continue
            if len(y) >= SR:
                run.add(y, "fake", "neural_tts", lang, f"edge_{voice}", f"ed3_{voice}_{lang}_{j}")
                n += 1
    print(f"edge: {n} clips", flush=True)
    run.save()


def gtts(run: Run):
    import urllib3.util.connection
    from gtts import gTTS
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET   # IPv6 to Google hangs on this network
    n = 0
    for gi, (lang, tld) in enumerate(GTTS_TRAIN):
        for j, text in enumerate(texts(run.data, lang, 6, seed=700 + gi)):
            for attempt in range(3):
                try:
                    buf = io.BytesIO()
                    gTTS(text, lang=lang, tld=tld).write_to_fp(buf)
                    y = load_audio(buf.getvalue())
                    break
                except Exception as e:
                    print("  gtts retry", lang, tld, type(e).__name__, flush=True)
                    y = None
                    time.sleep(10 * (attempt + 1))
            time.sleep(0.7)
            if y is not None and len(y) >= SR:
                run.add(y, "fake", "gtts", lang, f"gtts_{lang}_{tld}", f"gt3_{lang}_{tld}_{j}")
                n += 1
    print(f"gtts: {n} clips", flush=True)
    run.save()


def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else r"K:\vgtools\data")
    steps = sys.argv[2].split(",") if len(sys.argv) > 2 else ["disk", "stream", "edge", "gtts"]
    run = Run(data)
    for s in steps:
        t = time.time()
        try:
            asyncio.run(edge(run)) if s == "edge" else globals()[s](run)
        except Exception as e:
            print(f"STEP {s} FAILED: {type(e).__name__}: {e}", flush=True)
            run.save()
        print(f"step {s} finished in {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
