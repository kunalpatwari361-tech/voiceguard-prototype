"""Lazy, thread-safe holders for the neural models (feature 3, 10, 14).

  AI Voice Detector  - VoiceGuard head on WavLM, trained on phone audio (or an HF model id)
  Source Tracer      - VoiceGuard 5-class head on the same WavLM features: human / AI TTS / robotic TTS /
                       AI voice changer / voice-changer app (training/train_tracer.py)
  Voice Print Match  - Microsoft WavLM-Base-Plus-SV x-vectors     (config.SPEAKER_MODEL)
  Speech-to-text     - OpenAI Whisper small, Hindi + English      (config.ASR_MODEL)

All inference is serialised through one lock so a laptop CPU never runs two big models at once.
"""
import logging
from pathlib import Path
import threading
import time

import numpy as np
import torch

from .. import config

log = logging.getLogger("voiceguard.models")
torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))
INFER_LOCK = threading.Lock()
_load_lock = threading.Lock()
_cache: dict[str, object] = {}


def _get(name: str, factory):
    if name in _cache:
        return _cache[name]
    with _load_lock:
        if name not in _cache:
            t = time.time()
            _cache[name] = factory()
            log.info("loaded %s in %.1fs", name, time.time() - t)
    return _cache[name]


def status() -> dict:
    return {k: True for k in _cache} | {"claude": bool(config.ANTHROPIC_API_KEY)}


_warming = threading.Event()


def starting_up() -> bool:
    """True while the start-up warm-up is still loading the voice models."""
    return _warming.is_set() and "speaker" not in _cache


# ------------------------------------------------------------------ AI voice detector
class DeepfakeDetector:
    FAKE_NAMES = ("fake", "spoof", "deepfake", "ai", "synthetic", "bonafide_no")

    def __init__(self, model_id: str):
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        self.model_id = model_id
        self.fe = AutoFeatureExtractor.from_pretrained(model_id)
        self.model = AutoModelForAudioClassification.from_pretrained(model_id).eval()
        labels = {int(k): v.lower() for k, v in self.model.config.id2label.items()}
        self.fake_idx = next(i for i, name in labels.items() if name in self.FAKE_NAMES)

    @torch.inference_mode()
    def predict(self, y: np.ndarray, sr: int = 16000, win_s: float = 4.0, hop_s: float = 2.0,
                max_windows: int = 8) -> dict:
        n, h = int(win_s * sr), int(hop_s * sr)
        if len(y) <= n:
            chunks = [y]
        else:
            chunks = [y[s:s + n] for s in range(0, len(y) - n + 1, h)]
            # keep the windows that actually contain speech
            energy = np.array([np.sqrt(np.mean(c ** 2)) for c in chunks])
            keep = energy >= 0.25 * energy.max()
            chunks = [c for c, k in zip(chunks, keep) if k]
            if len(chunks) > max_windows:
                idx = np.linspace(0, len(chunks) - 1, max_windows).round().astype(int)
                chunks = [chunks[i] for i in idx]
        with INFER_LOCK:
            inputs = self.fe(chunks, sampling_rate=sr, return_tensors="pt", padding=True)
            logits = self.model(**inputs).logits
        p = torch.softmax(logits, dim=-1)[:, self.fake_idx].numpy()
        return {"fake_prob": round(float(p.mean()), 4), "max_window": round(float(p.max()), 4),
                "windows": [round(float(x), 3) for x in p], "model": self.model_id}


@torch.inference_mode()
def layer_stats(spk: "SpeakerVerifier", y: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Mean+std of every WavLM layer over time -> [layers, 2*768]. Used by the VoiceGuard detector."""
    return layer_stats_many(spk, [y], sr)[0]


@torch.inference_mode()
def layer_stats_many(spk: "SpeakerVerifier", ys: list[np.ndarray], sr: int = 16000) -> list[np.ndarray]:
    """layer_stats of several clips in ONE WavLM pass. Equal-length clips batch exactly (no padding), which is
    what the detector's 6-second windows are; clips of different lengths fall back to one pass each."""
    ys = [y[: sr * 6] for y in ys]
    if len({len(y) for y in ys}) > 1:
        return [layer_stats_many(spk, [y], sr)[0] for y in ys]
    with INFER_LOCK:
        inputs = spk.fe(ys, sampling_rate=sr, return_tensors="pt")
        hs = spk.model.wavlm(inputs["input_values"], output_hidden_states=True).hidden_states
    return [np.stack([torch.cat([h[i].mean(0), h[i].std(0)]).numpy() for h in hs]) for i in range(len(ys))]


class VoiceGuardDetector:
    """Our phone-robust detector: frozen WavLM features + logistic head trained on clean and
    phone-channel audio (training/train_detector.py)."""

    def __init__(self, weights_path, model_id: str = "voiceguard-wavlm-phone-v4"):
        w = np.load(weights_path)
        self.layers = [int(x) for x in w["layers"]]
        self.mean, self.scale = w["mean"], w["scale"]
        self.coef, self.intercept = w["coef"], float(w["intercept"][0])
        self.model_id = model_id

    def _prob(self, stats: np.ndarray) -> float:
        st = stats[self.layers].reshape(-1)
        z = float(((st - self.mean) / self.scale) @ self.coef + self.intercept)
        return 1 / (1 + np.exp(-z))

    def predict(self, y: np.ndarray, sr: int = 16000, win_s: float = 6.0, max_windows: int = 4,
                keep_stats: bool = False) -> dict:
        n = int(win_s * sr)
        if len(y) <= n:
            chunks = [y]
        else:
            starts = np.linspace(0, len(y) - n, min(max_windows, 1 + len(y) // n)).astype(int)
            chunks = [y[s:s + n] for s in starts]
            energy = np.array([np.sqrt(np.mean(c ** 2)) for c in chunks])
            chunks = [c for c, e in zip(chunks, energy) if e >= 0.25 * energy.max()]
        stats = layer_stats_many(speaker(), chunks)
        p = np.array([self._prob(st) for st in stats])
        out = {"fake_prob": round(float(p.mean()), 4), "max_window": round(float(p.max()), 4),
               "windows": [round(float(x), 3) for x in p], "model": self.model_id}
        if keep_stats:  # reused by the Source Tracer (same WavLM pass); the caller removes it before replying
            out["_stats"] = stats
        return out


class SourceTracer:
    """Which kind of generator made the voice: softmax head on the detector's WavLM features."""

    def __init__(self, weights_path, model_id: str):
        w = np.load(weights_path)
        self.classes = [str(c) for c in w["classes"]]
        self.layers = [int(x) for x in w["layers"]]
        self.mean, self.scale, self.W, self.b = w["mean"], w["scale"], w["W"], w["b"]
        self.model_id = model_id

    def probs(self, stats: list[np.ndarray]) -> dict[str, float]:
        out = []
        for st in stats:
            z = ((st[self.layers].reshape(-1) - self.mean) / self.scale) @ self.W + self.b
            z = np.exp(z - z.max())
            out.append(z / z.sum())
        p = np.mean(out, axis=0)
        return {c: round(float(v), 4) for c, v in zip(self.classes, p)}


TRACER = Path(__file__).resolve().parent / "weights" / "vg_tracer.npz"
TRACER_ROOM = Path(__file__).resolve().parent / "weights" / "vg_tracer_room.npz"


def tracer(room: bool = False) -> SourceTracer | None:
    """None until training/train_tracer.py has produced the weights (then the rule-based tracer is used)."""
    path = TRACER_ROOM if room and TRACER_ROOM.exists() else TRACER
    if not path.exists():
        return None
    return _get(f"tracer:{path.stem}", lambda: SourceTracer(path, path.stem.replace("vg_", "voiceguard-")))


WEIGHTS = Path(__file__).resolve().parent / "weights" / "vg_detector.npz"
# Live Call Check hears the caller through the loudspeaker + room: a separate head trained on that condition only
# (one head for both made clean-audio results worse).
ROOM_WEIGHTS = Path(__file__).resolve().parent / "weights" / "vg_detector_room.npz"


def deepfake(model_id: str | None = None, room: bool = False):
    mid = model_id or config.DEEPFAKE_MODEL
    if mid == "voiceguard" and room and ROOM_WEIGHTS.exists():
        return _get("deepfake:voiceguard-room", lambda: VoiceGuardDetector(ROOM_WEIGHTS, "voiceguard-wavlm-room-v2"))
    if mid == "voiceguard" and WEIGHTS.exists():
        return _get("deepfake:voiceguard", lambda: VoiceGuardDetector(WEIGHTS))
    if mid == "voiceguard":  # not trained yet -> best public model from scripts/eval_detectors.py
        mid = "garystafford/wav2vec2-deepfake-voice-detector"
    return _get(f"deepfake:{mid}", lambda: DeepfakeDetector(mid))


# ------------------------------------------------------------------ voice print
class SpeakerVerifier:
    # WavLM-SV model card: cosine >= 0.86 means "same speaker" on clean audio.
    SAME = 0.86
    MAYBE = 0.75

    def __init__(self, model_id: str):
        from transformers import AutoFeatureExtractor, WavLMForXVector
        self.fe = AutoFeatureExtractor.from_pretrained(model_id)
        self.model = WavLMForXVector.from_pretrained(model_id).eval()

    @torch.inference_mode()
    def embed(self, y: np.ndarray, sr: int = 16000) -> np.ndarray:
        y = y[: sr * 20]
        with INFER_LOCK:
            inputs = self.fe(y, sampling_rate=sr, return_tensors="pt", padding=True)
            emb = self.model(**inputs).embeddings
        emb = torch.nn.functional.normalize(emb, dim=-1)[0].numpy()
        return emb.astype(np.float32)

    @torch.inference_mode()
    def embed_many(self, ys: list[np.ndarray], sr: int = 16000) -> np.ndarray:
        """One embedding per equal-length piece, in a single batch (used to find the phone owner's own voice)."""
        with INFER_LOCK:
            inputs = self.fe(list(ys), sampling_rate=sr, return_tensors="pt", padding=True)
            emb = self.model(**inputs).embeddings
        return torch.nn.functional.normalize(emb, dim=-1).numpy().astype(np.float32)

    def compare(self, a: np.ndarray, b: np.ndarray) -> dict:
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        verdict = "match" if sim >= self.SAME else "uncertain" if sim >= self.MAYBE else "different"
        return {"similarity": round(sim, 4), "verdict": verdict}


def speaker() -> SpeakerVerifier:
    return _get("speaker", lambda: SpeakerVerifier(config.SPEAKER_MODEL))


# ------------------------------------------------------------------ speech to text
class Transcriber:
    def __init__(self, model_id: str):
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        self.processor = WhisperProcessor.from_pretrained(model_id)
        self.model = WhisperForConditionalGeneration.from_pretrained(model_id).eval()

    @torch.inference_mode()
    def transcribe(self, y: np.ndarray, sr: int = 16000, language: str | None = None) -> dict:
        y = y[: sr * 30]  # one Whisper window keeps CPU latency reasonable
        with INFER_LOCK:
            feats = self.processor(y, sampling_rate=sr, return_tensors="pt").input_features
            # The encoder (always 30 s of audio) is most of the cost: run it once for both language detection and
            # transcription instead of once each (~45 % faster, identical text).
            enc = self.model.get_encoder()(feats)
            if language is None:
                lang_ids = self.model.detect_language(encoder_outputs=enc)
                tok = self.processor.tokenizer.convert_ids_to_tokens(int(lang_ids[0]))
                language = tok.strip("<|>")
                if language in ("ur", "pa", "mr", "ne"):  # Whisper often labels Hindi speech as Urdu
                    language = "hi" if language == "ur" else language
            ids = self.model.generate(encoder_outputs=enc, language=language, task="transcribe", max_new_tokens=220)
        text = self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        return {"text": text, "language": language}


def asr() -> Transcriber:
    return _get("asr", lambda: Transcriber(config.ASR_MODEL))


def warmup():
    """Load everything once at startup so the first phone request is fast."""
    silence = np.random.default_rng(0).normal(0, 0.01, 16000 * 2).astype(np.float32)
    _warming.set()
    for name, fn in (("deepfake", lambda: deepfake().predict(silence)),
                     ("speaker", lambda: speaker().embed(silence)),
                     ("asr", lambda: asr().transcribe(silence, language="en"))):
        try:
            fn()
        except Exception:  # keep the server up even if one model is missing
            log.exception("warmup failed for %s", name)
    _warming.clear()
    log.info("AI models ready")
