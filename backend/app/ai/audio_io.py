"""Audio loading helpers. Everything inside the engine works on mono float32 at 16 kHz."""
import io

import numpy as np
import soundfile as sf
import soxr

from ..config import SAMPLE_RATE


def load_audio(data: bytes, target_sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode WAV/FLAC/OGG/MP3 bytes to mono float32 at target_sr."""
    y, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if sr != target_sr:
        y = soxr.resample(y, sr, target_sr).astype(np.float32)
    return y


def resample(y: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return y
    return soxr.resample(y, sr_in, sr_out).astype(np.float32)


def pcm16_to_float(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(y: np.ndarray) -> bytes:
    return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


def to_wav_bytes(y: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, y, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def frame_signal(y: np.ndarray, sr: int = SAMPLE_RATE, frame_ms: float = 25, hop_ms: float = 10):
    """Return (frames[n, frame_len], hop_seconds)."""
    flen = int(sr * frame_ms / 1000)
    hop = int(sr * hop_ms / 1000)
    if len(y) < flen:
        y = np.pad(y, (0, flen - len(y)))
    n = 1 + (len(y) - flen) // hop
    idx = np.arange(flen)[None, :] + hop * np.arange(n)[:, None]
    return y[idx], hop / sr


def frame_db(y: np.ndarray, sr: int = SAMPLE_RATE) -> tuple[np.ndarray, float]:
    frames, hop_s = frame_signal(y, sr)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12), hop_s
