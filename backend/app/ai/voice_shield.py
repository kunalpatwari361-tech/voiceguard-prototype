"""Voice Shield (feature 30, future): protect your own voice notes before sharing them.

Prototype: embeds an inaudible spread-spectrum watermark keyed to the user. If a clone is later made
from shielded audio, the watermark helps prove which recordings leaked. (A production version would
add adversarial perturbations that degrade cloning models; that needs research-grade training.)
"""
import hashlib

import numpy as np

STRENGTH = 0.0025  # ~ -52 dBFS, below hearing under speech


def _carrier(user_id: str, n: int) -> np.ndarray:
    seed = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed).choice([-1.0, 1.0], size=n).astype(np.float32)


def protect(y: np.ndarray, user_id: str) -> np.ndarray:
    env = np.convolve(np.abs(y), np.ones(400) / 400, mode="same")  # follow speech loudness
    mark = _carrier(user_id, len(y)) * STRENGTH * (0.3 + env / (env.max() + 1e-9))
    return np.clip(y + mark, -1, 1).astype(np.float32)


def detect(y: np.ndarray, user_id: str) -> dict:
    c = _carrier(user_id, len(y))
    hp = np.diff(y, prepend=y[:1])            # whiten: speech is low-pass, the mark is white
    cc = np.diff(c, prepend=c[:1])
    corr = float(np.dot(hp, cc) / (np.linalg.norm(hp) * np.linalg.norm(cc) + 1e-9))
    z = float(corr * np.sqrt(len(y)))
    return {"z_score": round(z, 2), "watermark_found": bool(z > 6)}
