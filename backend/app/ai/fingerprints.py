"""Reverse Engineering Engine (feature 4): acoustic fingerprints that survive a phone call.

All five checks live in the 300-3400 Hz telephone band, so they still work on 8 kHz calls:
  pitch_shake   jitter / shimmer   - real vocal folds wobble, synthetic voices are too steady
  breathing     breath events       - humans breathe; AI often never does, or pastes one breath
  rhythm        pause regularity   - human pauses are uneven, generated speech is metronomic
  silence       noise between words - real rooms hiss, synthetic gaps are digitally clean
  mouth         formant movement   - articulators move smoothly; vocoders sometimes jump
Each check returns an "AI-likeness" score in [0, 1] plus a plain-language reason.
"""
import numpy as np
import parselmouth
from parselmouth.praat import call

from .audio_io import frame_signal

SR = 16000


def _runs(mask: np.ndarray):
    """Yield (value, start, length) for runs of equal values."""
    if len(mask) == 0:
        return []
    change = np.flatnonzero(np.diff(mask.astype(np.int8))) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(mask)]])
    return [(bool(mask[s]), int(s), int(e - s)) for s, e in zip(starts, ends)]


def _smooth(mask: np.ndarray, value: bool, max_len: int) -> np.ndarray:
    """Flip runs of `value` shorter than max_len (fill gaps / drop blips)."""
    out = mask.copy()
    runs = _runs(mask)
    for i, (v, s, n) in enumerate(runs):
        if v == value and n < max_len and 0 < i < len(runs) - 1:
            out[s:s + n] = not value
    return out


def speech_mask(y: np.ndarray, sr: int = SR):
    frames, hop_s = frame_signal(y, sr)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    thr = max(floor + 0.35 * (peak - floor), floor + 10)
    mask = db > thr
    mask = _smooth(mask, False, int(0.12 / hop_s))   # bridge tiny gaps inside words
    mask = _smooth(mask, True, int(0.08 / hop_s))    # drop clicks
    return mask, db, frames, hop_s, floor, peak, thr


def _flatness(frames: np.ndarray) -> np.ndarray:
    win = np.hanning(frames.shape[1])
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2 + 1e-12
    return np.exp(np.mean(np.log(spec), axis=1)) / np.mean(spec, axis=1)


def _band(score: float) -> str:
    return "ai-like" if score >= 0.6 else ("unclear" if score >= 0.35 else "human-like")


def analyze(y: np.ndarray, sr: int = SR, narrowband: bool = False) -> dict:
    """narrowband=True when the audio came through a phone line: line noise fills the gaps, so the
    silence check is no longer meaningful and gets almost no weight."""
    duration = len(y) / sr
    mask, db, frames, hop_s, floor, peak, thr = speech_mask(y, sr)
    speech_s = float(mask.sum() * hop_s)
    runs = _runs(mask)
    features: dict[str, dict] = {}

    # ---------- pitch shake (jitter / shimmer / HNR) ----------
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
    pitch = snd.to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=600)
    f0 = pitch.selected_array["frequency"]
    voiced = f0[f0 > 0]
    voiced_fraction = float(len(voiced) / max(len(f0), 1))
    f0_median = float(np.median(voiced)) if len(voiced) else 0.0
    f0_std_st = float(np.std(12 * np.log2(voiced / f0_median))) if len(voiced) > 10 else 0.0
    jitter = shimmer = hnr = None
    if len(voiced) >= 40:
        pp = call([snd, pitch], "To PointProcess (cc)")
        jitter = call(pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3) * 100
        shimmer = call([snd, pp], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6) * 100
        hnr = call(snd.to_harmonicity_cc(time_step=0.01, minimum_pitch=75), "Get mean", 0, 0)
        jitter = None if np.isnan(jitter) else float(jitter)
        shimmer = None if np.isnan(shimmer) else float(shimmer)
        hnr = None if np.isnan(hnr) else float(hnr)
    if jitter is not None and shimmer is not None:
        sj = 0.8 if jitter < 0.4 else 0.45 if jitter < 0.7 else 0.15
        ss = 0.8 if shimmer < 2.5 else 0.45 if shimmer < 4.0 else 0.15
        s = (sj + ss) / 2
        features["pitch_shake"] = {
            "label": "Pitch shake", "score": round(s, 3), "band": _band(s),
            "jitter_pct": round(jitter, 3), "shimmer_pct": round(shimmer, 3),
            "hnr_db": round(hnr, 1) if hnr is not None else None,
            "detail": ("Voice is unnaturally steady (very low jitter/shimmer)." if s >= 0.6 else
                       "Natural small wobble in pitch and loudness." if s < 0.35 else
                       "Pitch wobble is on the low side."),
            "detail_hi": ("आवाज़ ज़रूरत से ज़्यादा स्थिर है।" if s >= 0.6 else
                          "आवाज़ में प्राकृतिक हल्का कंपन है।" if s < 0.35 else "आवाज़ का कंपन थोड़ा कम है।"),
        }

    # ---------- pauses / rhythm ----------
    pauses = [n * hop_s for i, (v, s_, n) in enumerate(runs)
              if not v and 0 < i < len(runs) - 1 and n * hop_s >= 0.15]
    segments = [n * hop_s for v, s_, n in runs if v]
    if len(pauses) >= 3:
        cv = float(np.std(pauses) / (np.mean(pauses) + 1e-9))
        s = 0.8 if cv < 0.25 else 0.45 if cv < 0.4 else 0.15
        features["rhythm"] = {
            "label": "Rhythm & pauses", "score": round(s, 3), "band": _band(s),
            "pauses": len(pauses), "pause_mean_s": round(float(np.mean(pauses)), 3),
            "pause_cv": round(cv, 3),
            "detail": ("Pauses are almost identical in length, like a machine." if s >= 0.6 else
                       "Pauses are uneven, like a real person thinking." if s < 0.35 else
                       "Pauses are fairly regular."),
            "detail_hi": ("रुकावटें लगभग एक जैसी हैं, मशीन जैसी।" if s >= 0.6 else
                          "रुकावटें असमान हैं, इंसान जैसी।" if s < 0.35 else "रुकावटें काफ़ी नियमित हैं।"),
        }

    # ---------- silence noise floor ----------
    gap_frames = frames[~mask]
    if len(gap_frames) >= 20:
        gap_db = db[~mask]
        floor_dbfs = float(np.median(gap_db))
        zero_frac = float(np.mean(np.max(np.abs(gap_frames), axis=1) < 1e-4))
        flat = float(np.median(_flatness(gap_frames)))
        if floor_dbfs < -80 or zero_frac > 0.3:
            s = 0.85
        elif floor_dbfs < -65:
            s = 0.5
        else:
            s = 0.15
        features["silence"] = {
            "label": "Silence noise", "score": round(s, 3), "band": _band(s),
            "noise_floor_dbfs": round(floor_dbfs, 1), "digital_zero_fraction": round(zero_frac, 3),
            "gap_flatness": round(flat, 3), "snr_db": round(peak - floor_dbfs, 1),
            "detail": ("Gaps between words are digitally silent - no room or line noise." if s >= 0.6 else
                       "Real background noise between words." if s < 0.35 else
                       "Background is unusually quiet."),
            "detail_hi": ("शब्दों के बीच बिल्कुल डिजिटल सन्नाटा है।" if s >= 0.6 else
                          "शब्दों के बीच असली कमरे/लाइन का शोर है।" if s < 0.35 else "बैकग्राउंड बहुत शांत है।"),
        }

    # ---------- breathing ----------
    breaths = []
    if len(frames):
        flat_all = _flatness(frames)
        cand = (~mask) & (db > floor + 5) & (db < thr) & (flat_all > 0.12)
        for v, s_, n in _runs(cand):
            if v and 0.12 <= n * hop_s <= 1.0:
                seg = frames[s_:s_ + n]
                spec = np.log(np.mean(np.abs(np.fft.rfft(seg * np.hanning(seg.shape[1]), axis=1)) ** 2, axis=0) + 1e-12)
                breaths.append(spec[: len(spec) // 2])
    if speech_s >= 6:
        identical = False
        if len(breaths) >= 2:
            c = np.corrcoef(np.stack(breaths))
            off = c[~np.eye(len(breaths), dtype=bool)]
            identical = bool(np.mean(off) > 0.985)
        per_min = len(breaths) / (duration / 60)
        if identical:
            s = 0.85
        elif len(breaths) == 0:
            s = 0.65 if speech_s >= 10 else 0.5
        else:
            s = 0.15
        features["breathing"] = {
            "label": "Breathing", "score": round(s, 3), "band": _band(s),
            "breaths": len(breaths), "per_minute": round(per_min, 1), "identical_breaths": identical,
            "detail": ("The same breath sound repeats - looks copy-pasted." if identical else
                       f"No breathing heard in {speech_s:.0f} s of speech." if not breaths else
                       f"{len(breaths)} natural breath(s) heard."),
            "detail_hi": ("एक ही साँस की आवाज़ बार-बार दोहराई गई है।" if identical else
                          "बोलते समय कोई साँस सुनाई नहीं दी।" if not breaths else "प्राकृतिक साँसें सुनाई दीं।"),
        }

    # ---------- mouth movement (formant smoothness) ----------
    if len(voiced) >= 60:
        formant = snd.to_formant_burg(time_step=0.01, max_number_of_formants=5, maximum_formant=5000)
        times = pitch.xs()
        f2_prev = None
        jumps = pairs = 0
        for t, f in zip(times[:3000], f0[:3000]):
            if f <= 0:
                f2_prev = None
                continue
            f2 = formant.get_value_at_time(2, t)
            if np.isnan(f2):
                f2_prev = None
                continue
            if f2_prev is not None:
                pairs += 1
                if abs(f2 - f2_prev) > 300:
                    jumps += 1
            f2_prev = f2
        if pairs >= 40:
            rate = jumps / pairs
            s = 0.75 if rate > 0.18 else 0.45 if rate > 0.12 else 0.15
            features["mouth"] = {
                "label": "Mouth movement", "score": round(s, 3), "band": _band(s),
                "f2_jump_rate": round(rate, 3),
                "detail": ("Mouth shapes jump unnaturally between sounds." if s >= 0.6 else
                           "Mouth movement changes smoothly." if s < 0.35 else
                           "Some sudden mouth-shape jumps."),
                "detail_hi": ("मुँह की बनावट अचानक बदल रही है।" if s >= 0.6 else
                              "मुँह की हलचल सहज है।" if s < 0.35 else "कुछ अचानक बदलाव हैं।"),
            }

    weights = {"pitch_shake": 0.25, "breathing": 0.2, "rhythm": 0.2, "silence": 0.2, "mouth": 0.15}
    if narrowband and "silence" in features:
        weights["silence"] = 0.03
        features["silence"]["note"] = "Phone line noise hides this sign - low weight on phone audio."
    tot = sum(weights[k] for k in features)
    score = sum(features[k]["score"] * weights[k] for k in features) / tot if tot else None
    return {
        "score": round(score, 3) if score is not None else None,
        "features": features,
        "narrowband": narrowband,
        "stats": {
            "duration_s": round(duration, 2), "speech_s": round(speech_s, 2),
            "voiced_fraction": round(voiced_fraction, 3), "f0_median_hz": round(f0_median, 1),
            "f0_std_semitones": round(f0_std_st, 2), "speech_segments": len(segments),
            "level_peak_dbfs": round(peak, 1),
        },
    }


def whisper_check(y: np.ndarray, sr: int = SR) -> dict:
    """Voice Test helper: whispered speech has almost no voicing."""
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
    f0 = snd.to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=600).selected_array["frequency"]
    mask, *_ = speech_mask(y, sr)
    n = min(len(mask), len(f0))
    sp = mask[:n]
    voiced_in_speech = float(np.mean(f0[:n][sp] > 0)) if sp.any() else 0.0
    return {"voiced_in_speech": round(voiced_in_speech, 3), "is_whisper": voiced_in_speech < 0.25,
            "speech_s": round(float(sp.sum() * 0.01), 2)}


def laugh_check(y: np.ndarray, sr: int = SR) -> dict:
    """Laughter = bursts of energy repeating 3-7 times per second."""
    frames, hop_s = frame_signal(y, sr)
    env = np.sqrt(np.mean(frames ** 2, axis=1))
    env = env - env.mean()
    if len(env) < 50:
        return {"is_laugh": False, "burst_rate_hz": 0.0, "strength": 0.0}
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    freqs = np.fft.rfftfreq(len(env), hop_s)
    band = (freqs >= 3) & (freqs <= 7)
    ref = (freqs >= 0.5) & (freqs <= 12)
    strength = float(spec[band].sum() / (spec[ref].sum() + 1e-9))
    rate = float(freqs[band][np.argmax(spec[band])]) if band.any() else 0.0
    return {"is_laugh": strength > 0.45, "burst_rate_hz": round(rate, 2), "strength": round(strength, 3)}


def sing_check(y: np.ndarray, sr: int = SR) -> dict:
    """Singing = long steady voiced notes and a wide pitch range."""
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
    f0 = snd.to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=900).selected_array["frequency"]
    voiced = f0 > 0
    longest = max([n for v, s, n in _runs(voiced) if v] or [0]) * 0.01
    v = f0[voiced]
    rng = float(12 * np.log2(np.percentile(v, 95) / np.percentile(v, 5))) if len(v) > 20 else 0.0
    return {"is_singing": longest > 0.6 and rng > 4, "longest_note_s": round(longest, 2),
            "pitch_range_semitones": round(rng, 1)}
