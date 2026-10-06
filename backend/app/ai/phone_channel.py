"""Telephone channel simulator (pitch idea #2: train and test on phone-quality audio).

Turns clean 16 kHz audio into what a real mobile call delivers:
  1. down-sample to 8 kHz (narrowband)
  2. 300-3400 Hz band-pass (the telephone band)
  3. codec: G.711 mu-law (exact) or a low-bitrate approximation of AMR-NB
  4. optional packet loss with simple repeat-last-frame concealment
  5. light line noise
The output is resampled back to 16 kHz so the same models can consume it.
"""
import numpy as np

from .audio_io import resample


def _bandpass(x: np.ndarray, sr: int, lo: float, hi: float, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth-shaped band-pass done in the frequency domain (NumPy only).
    SciPy is avoided on purpose: Windows Smart App Control can block its compiled DLLs."""
    n = len(x)
    if n == 0:
        return x
    f = np.fft.rfftfreq(n, 1 / sr)
    f[0] = 1e-6
    h = 1 / np.sqrt(1 + (f / hi) ** (2 * order)) / np.sqrt(1 + (lo / f) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(x) * h, n).astype(np.float32)


def _mulaw(x: np.ndarray, mu: int = 255) -> np.ndarray:
    x = np.clip(x, -1, 1)
    enc = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    q = np.round((enc + 1) / 2 * mu) / mu * 2 - 1  # 8-bit quantisation
    return np.sign(q) * (np.power(1 + mu, np.abs(q)) - 1) / mu


def _lowbitrate(x: np.ndarray, sr: int = 8000) -> np.ndarray:
    """Rough AMR-NB stand-in: tighter band, coarse quantisation, smeared spectrum."""
    x = _bandpass(x, sr, 250, 3000, order=6)
    peak = np.max(np.abs(x)) + 1e-9
    x = np.round(x / peak * 31) / 31 * peak  # ~6-bit
    return x


def phone_channel(y: np.ndarray, sr: int = 16000, codec: str = "g711",
                  packet_loss: float = 0.0, noise_db: float = -55.0,
                  seed: int | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = resample(y, sr, 8000)
    x = _bandpass(x, 8000, 300, 3400)
    if codec == "g711":
        x = _mulaw(x)
    elif codec == "lowbitrate":
        x = _lowbitrate(x)
    if packet_loss > 0:
        flen = 160  # 20 ms frames at 8 kHz
        for start in range(flen, len(x) - flen, flen):
            if rng.random() < packet_loss:
                x[start:start + flen] = x[start - flen:start] * 0.6
    if noise_db is not None:
        x = x + rng.normal(0, 10 ** (noise_db / 20), size=x.shape)
    return resample(x.astype(np.float32), 8000, sr)


def codec_roundtrip(y: np.ndarray, sr: int = 16000, codec: str = "opus", level: float = 0.95,
                    codec_sr: int | None = None) -> np.ndarray:
    """Encode + decode with a REAL codec (libsndfile): Opus (WhatsApp / VoIP voice notes), Vorbis or MP3.
    level 0..1 = compression strength (1.0 = lowest bitrate)."""
    import io

    import soundfile as sf
    from .audio_io import load_audio
    csr = codec_sr or sr
    x = resample(y, sr, csr) if csr != sr else y
    buf = io.BytesIO()
    if codec == "mp3":
        sf.write(buf, x, csr, format="MP3", subtype="MPEG_LAYER_III", compression_level=level, bitrate_mode="CONSTANT")
    else:
        sf.write(buf, x, csr, format="OGG", subtype="OPUS" if codec == "opus" else "VORBIS", compression_level=level)
    out = load_audio(buf.getvalue(), target_sr=sr)
    n = len(y)
    return np.pad(out, (0, max(0, n - len(out))))[:n].astype(np.float32)


def is_narrowband(y: np.ndarray, sr: int = 16000) -> bool:
    """True when almost no energy exists above 4 kHz, i.e. audio already came through a phone line."""
    spec = np.abs(np.fft.rfft(y[: sr * 10])) ** 2
    freqs = np.fft.rfftfreq(min(len(y), sr * 10), 1 / sr)
    hi = spec[freqs > 4000].sum()
    return bool(hi / (spec.sum() + 1e-12) < 0.002)
