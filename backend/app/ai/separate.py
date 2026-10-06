"""Live call check: keep only the CALLER's voice.

During a phone call the microphone hears both people (the call is on speaker). The phone owner's own voice is real,
so it would hide an AI caller - and it is not the voice we want to compare with the claimed person's voice print.
Every 1.5-second piece that matches the owner's saved voice print ("My Voice Print") is cut out before the checks
run. Without a saved voice print the clip is used as it is.
"""
import numpy as np

from . import models

SEG_S = 1.5
VOICED_DB = -45.0     # quieter pieces are pauses: kept (they carry the line's noise floor), never compared
OWNER_SIM = 0.82      # cosine to the owner's print; short pieces score a bit lower than a full-length clip


def keep_caller(y: np.ndarray, owner_print: np.ndarray | None, sr: int = 16000) -> tuple[np.ndarray, dict]:
    seg = int(SEG_S * sr)
    n = len(y) // seg
    if owner_print is None or n == 0:
        return y, {"used": False}
    pieces = [y[i * seg:(i + 1) * seg] for i in range(n)]
    tail = y[n * seg:]
    db = np.array([20 * np.log10(np.sqrt(np.mean(p ** 2)) + 1e-9) for p in pieces])
    voiced = np.flatnonzero(db > VOICED_DB)
    owner = set()
    sims = []
    if len(voiced):
        emb = models.speaker().embed_many([pieces[i] for i in voiced], sr)
        sims = emb @ (owner_print / (np.linalg.norm(owner_print) + 1e-9))
        owner = {int(i) for i, s in zip(voiced, sims) if s >= OWNER_SIM}
    kept = [p for i, p in enumerate(pieces) if i not in owner] + ([tail] if len(tail) else [])
    out = np.concatenate(kept) if kept else y[:0]
    return out, {"used": True, "owner_s": round(len(owner) * SEG_S, 1),
                 "caller_s": round((len(voiced) - len(owner)) * SEG_S, 1),
                 "owner_similarity": [round(float(s), 2) for s in sims]}
