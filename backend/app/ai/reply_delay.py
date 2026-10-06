"""Reply Delay Check (feature 13).

A live AI scam call is a pipeline: hear you -> speech-to-text -> LLM writes a reply -> TTS speaks.
That adds a long and *steady* pause before every answer. Humans answer fast, overlap, and vary.
Timing survives any phone codec because it is not about sound quality at all.
"""
import numpy as np


def score_gaps(gaps: list[float]) -> dict:
    """gaps: seconds between the other person finishing and the caller starting (negative = overlap)."""
    g = np.array([x for x in gaps if -2 < x < 10], dtype=float)
    if len(g) < 3:
        return {"score": None, "turns": int(len(g)), "detail": "Need at least 3 replies to judge timing.",
                "detail_hi": "समय जाँचने के लिए कम से कम 3 जवाब चाहिए।"}
    mean = float(g.mean())
    std = float(g.std())
    cv = std / (abs(mean) + 1e-6)
    overlaps = int((g < 0.05).sum())
    if mean > 0.9 and cv < 0.35 and overlaps == 0:
        s = 0.8
        d = (f"Every reply comes after ~{mean:.1f}s with almost the same delay - typical of an AI pipeline.",
             f"हर जवाब लगभग {mean:.1f} सेकंड बाद, एक जैसी देरी से आता है - AI जैसा।")
    elif mean > 0.7 and cv < 0.5 and overlaps == 0:
        s = 0.5
        d = (f"Replies are slow (~{mean:.1f}s) and fairly steady.", f"जवाब धीमे (~{mean:.1f} सेकंड) और काफ़ी एक जैसे हैं।")
    else:
        s = 0.15
        d = ("Reply timing varies naturally, like a real conversation.", "जवाब देने का समय स्वाभाविक रूप से बदलता है।")
    return {"score": s, "turns": int(len(g)), "mean_s": round(mean, 2), "std_s": round(std, 2),
            "cv": round(cv, 2), "overlaps": overlaps, "detail": d[0], "detail_hi": d[1]}


class TurnTracker:
    """Follows two live audio streams (HD call) and measures each side's reply gaps."""

    def __init__(self, frame_s: float = 0.02, hangover_s: float = 0.35):
        self.frame_s = frame_s
        self.hang = int(hangover_s / frame_s)
        self.t = {}           # side -> frames seen
        self.quiet = {}       # side -> consecutive quiet frames
        self.speaking = {}    # side -> bool
        self.last_end = {}    # side -> time speech ended
        self.noise = {}       # side -> running noise floor (dB)
        self.gaps: dict[str, list[float]] = {}

    def push(self, side: str, other: str, rms_db: float):
        t = self.t.get(side, 0) * self.frame_s
        self.t[side] = self.t.get(side, 0) + 1
        nf = self.noise.get(side, rms_db)
        nf = min(rms_db, nf + 0.02) if rms_db > nf else 0.95 * nf + 0.05 * rms_db
        self.noise[side] = nf
        active = rms_db > max(nf + 12, -50)
        if active:
            self.quiet[side] = 0
            if not self.speaking.get(side):
                self.speaking[side] = True
                end = self.last_end.get(other)
                if end is not None and not self.speaking.get(other):
                    self.gaps.setdefault(side, []).append(round(t - end, 3))
                elif self.speaking.get(other):
                    self.gaps.setdefault(side, []).append(-0.2)  # talked over the other person
        else:
            self.quiet[side] = self.quiet.get(side, 0) + 1
            if self.speaking.get(side) and self.quiet[side] >= self.hang:
                self.speaking[side] = False
                self.last_end[side] = t - self.hang * self.frame_s
