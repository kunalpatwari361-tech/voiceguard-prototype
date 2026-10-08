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


def _fftconv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    n = len(x) + len(h)
    return np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(h, n), n)[: len(x)]


def speaker_room(y: np.ndarray, seed: int, sr: int = 16000) -> np.ndarray:
    """What the Live Call Check really hears: the call (phone codec) played by the phone's own loudspeaker into a
    room and picked up again by its microphone. Everything is randomised – codec, speaker size and resonance,
    distortion, early reflections, reverb time, noise type and level, mic gain – so the detector learns the voice
    and not one room. (scripts/eval_pipeline.py tests with a differently built simulator.)"""
    rng = np.random.default_rng(seed)
    codec = ["g711", "g711", "lowbitrate"][rng.integers(3)]
    x = phone_channel(y, sr, codec=codec, packet_loss=float(rng.uniform(0, 0.04)), seed=seed)
    # loudspeaker: small driver (bass roll-off), one resonance, soft clipping
    f = np.fft.rfftfreq(len(x), 1 / sr)
    fc, order = rng.uniform(250, 700), int(rng.integers(1, 4))
    h = 1 / np.sqrt(1 + (fc / np.maximum(f, 1)) ** (2 * order))
    h *= 10 ** (rng.uniform(0, 8) * np.exp(-0.5 * ((f - rng.uniform(1500, 4500)) / rng.uniform(300, 1500)) ** 2) / 20)
    x = np.fft.irfft(np.fft.rfft(x) * h, len(x))
    drive = rng.uniform(0.5, 3.0)
    x = np.tanh(drive * x / (np.max(np.abs(x)) + 1e-9)) / np.tanh(drive)
    # room: direct sound, a few early reflections (2-25 ms), diffuse decaying tail
    rt60 = rng.uniform(0.15, 0.8)
    n = int(rt60 * sr)
    ir = np.zeros(n)
    ir[0] = 1.0
    for _ in range(int(rng.integers(3, 9))):
        ir[int(rng.uniform(0.002, 0.025) * sr)] += rng.uniform(-0.5, 0.5)
    tail = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / sr / rt60) * rng.uniform(0.01, 0.12)
    tail[: int(0.005 * sr)] = 0
    x = _fftconv(x, ir + tail)
    # noise: white hiss, brown rumble, mains hum, or speech-shaped babble
    kind = int(rng.integers(4))
    m = len(x)
    if kind == 0:
        noise = rng.normal(0, 1, m)
    elif kind == 1:
        noise = np.cumsum(rng.normal(0, 1, m))
        noise -= np.convolve(noise, np.ones(800) / 800, "same")
    elif kind == 2:
        t = np.arange(m) / sr
        hz = [50, 60][rng.integers(2)]
        noise = sum(np.sin(2 * np.pi * hz * k * t + rng.uniform(0, 6.3)) / k for k in (1, 2, 3)) + 0.3 * rng.normal(0, 1, m)
    else:
        noise = _bandpass(rng.normal(0, 1, m).astype(np.float32), sr, 200, 3500, order=2)
        noise *= 0.5 + np.abs(np.convolve(rng.normal(0, 1, m), np.ones(1600) / 40, "same"))
    frames = x[: m // 400 * 400].reshape(-1, 400)
    p = np.mean(frames ** 2, axis=1)
    speech_p = np.mean(p[p > 0.01 * p.max()]) if p.max() > 0 else 1e-6
    snr = rng.uniform(5, 30)
    x = x + noise * np.sqrt(speech_p / (np.mean(noise ** 2) * 10 ** (snr / 10) + 1e-12))
    # microphone: rumble filter, then the phone's own gain
    x = _bandpass(x.astype(np.float32), sr, 80, 7600, order=2)
    return (x / (np.max(np.abs(x)) + 1e-9) * rng.uniform(0.2, 0.9)).astype(np.float32)
