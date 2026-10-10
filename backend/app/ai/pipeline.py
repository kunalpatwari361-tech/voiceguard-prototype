"""One call that runs every voice check on a clip and fuses the result."""
import copy
import hashlib
import threading
import time
from collections import OrderedDict

import numpy as np

from . import fingerprints, models, phone_channel, reply_delay, scam_text, source_trace
from .risk import fuse

# The call screen sends the same clip twice: a quick pass (voice checks only), then again with speech-to-text.
# Keep the voice-check results of the last few clips so the second pass only adds speech-to-text.
_RECENT: OrderedDict = OrderedDict()
_RECENT_LOCK = threading.Lock()
_RECENT_MAX = 8


def _clean(o):
    """Make results JSON-safe (NumPy scalars/arrays -> Python types)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return o


def analyze_voice(y: np.ndarray, *, claimed_name: str | None = None, claimed_print: np.ndarray | None = None,
                  transcript_hint: str = "", do_asr: bool = True, simulate_phone: bool = False,
                  reply_gaps: list[float] | None = None, number_score: float | None = None,
                  really_calling: str | None = None, voice_test: dict | None = None,
                  hd_call: str | None = None, location: dict | None = None, room_audio: bool = False,
                  voice_lookup=None, return_embedding: bool = False) -> dict:
    """room_audio=True for the Live Call Check: the caller was heard through the loudspeaker and the room.
    Room echo and noise hide the voice fingerprints (tests: ~0.15 for real AND AI voices), so they are reported
    but not counted in the risk score; the trained AI-voice detector covers this condition.
    voice_lookup(embedding) -> Scam Voice ID match or None (app/voice_id.py); return_embedding adds "_embedding"
    (the caller's voice, for the server to keep - never sent to the phone)."""
    t0 = time.time()
    timings = {}
    if simulate_phone:
        y = phone_channel.phone_channel(y, codec="g711", packet_loss=0.02, seed=1)
    y = y[: 16000 * 60]
    narrow = simulate_phone or room_audio or phone_channel.is_narrowband(y)
    need_emb = claimed_print is not None or bool(voice_lookup) or return_embedding
    key = hashlib.blake2b(y.tobytes() + repr((narrow, room_audio)).encode(), digest_size=16).hexdigest()
    with _RECENT_LOCK:
        hit = copy.deepcopy(_RECENT.get(key))
    if hit and (hit["emb"] is not None or not need_emb):
        fp, df, probs, tracer_id, emb = hit["fp"], hit["df"], hit["probs"], hit["tracer_id"], hit["emb"]
        timings["reused_voice_checks"] = True
    else:
        fp = fingerprints.analyze(y, narrowband=narrow)
        timings["fingerprints"] = round(time.time() - t0, 2)
        if fp["stats"]["speech_s"] < 1.0:
            return {"ok": False, "error": "too_short",
                    "message": "Not enough speech in this clip. Record at least 3 seconds of the caller talking.",
                    "message_hi": "इस क्लिप में बोली बहुत कम है। कम से कम 3 सेकंड की आवाज़ रिकॉर्ड करें।"}

        t = time.time()
        det = models.deepfake(room=room_audio)
        df = det.predict(y, keep_stats=True) if isinstance(det, models.VoiceGuardDetector) else det.predict(y)
        stats = df.pop("_stats", None)
        timings["deepfake"] = round(time.time() - t, 2)

        t = time.time()
        tracer = models.tracer(room=room_audio)
        tracer_id = tracer.model_id if tracer else None
        probs = None
        if tracer:
            probs = tracer.probs(stats or [models.layer_stats(models.speaker(), y)])
        timings["tracer"] = round(time.time() - t, 2)

        # The caller's voice embedding: Voice Print Match + Scam Voice ID
        t = time.time()
        emb = models.speaker().embed(y) if need_emb else None
        timings["embedding"] = round(time.time() - t, 2)
        with _RECENT_LOCK:
            _RECENT[key] = copy.deepcopy({"fp": fp, "df": df, "probs": probs, "tracer_id": tracer_id, "emb": emb})
            while len(_RECENT) > _RECENT_MAX:
                _RECENT.popitem(last=False)

    t = time.time()
    vp = None
    if claimed_print is not None:
        vp = models.speaker().compare(emb, claimed_print)
        vp["name"] = claimed_name
    sv = voice_lookup(emb) if voice_lookup else None
    if sv and vp and vp["verdict"] == "match" and df["fake_prob"] < 0.5:
        # sounds like the real family member and not AI-made: do not accuse them because a clone of them was reported
        sv["ignored"] = True
    timings["voiceprint"] = round(time.time() - t, 2)

    tr = None
    if do_asr:
        t = time.time()
        tr = models.asr().transcribe(y)
        timings["asr"] = round(time.time() - t, 2)
    text = " ".join(x for x in (transcript_hint, tr["text"] if tr else "") if x).strip()
    sw = scam_text.analyze(text, claimed_name) if text else None

    rd = reply_delay.score_gaps(reply_gaps) if reply_gaps else None
    st = source_trace.trace(df["fake_prob"], fp, rd, probs, tracer_id, room_audio)
    sv_used = sv if sv and not sv.get("ignored") else None
    rev = source_trace.combine(df["fake_prob"], st, fp, vp, rd, sv_used, room_audio)
    p_changer = (probs or {}).get("dsp_changer", 0.0)

    loc_signal = None
    if location and sw and set(sw["rules"]["categories"]) & {"emergency_story", "new_number"}:
        loc_signal = {"mismatch": True,
                      "en": f"{claimed_name}'s phone is at {location.get('place') or 'a normal place'} "
                            f"(updated {location.get('age_min', '?')} min ago) - does not match the emergency story.",
                      "hi": f"{claimed_name} का फ़ोन {location.get('place') or 'सामान्य जगह'} पर है - "
                            f"इमरजेंसी की कहानी से मेल नहीं खाता।"}

    risk = fuse({
        "deepfake": df["fake_prob"], "fingerprints": None if room_audio else fp["score"], "voiceprint": vp,
        "scam_words": sw["score"] if sw else None,
        "scam_words_tips": sw["rules"]["tips"] if sw else None,
        "reply_delay": rd["score"] if rd else None, "number": number_score,
        "really_calling": really_calling, "voice_test": voice_test, "hd_call": hd_call,
        "location": loc_signal,
        "voice_changer": p_changer if (not room_audio and p_changer >= source_trace.CHANGER_RISK
                                       and rev["kind"] == "dsp_changer") else None,
        "scam_voice": sv_used,
    })
    if room_audio:
        fp["note"] = "Heard through the loudspeaker: room sound hides these signs, so they are not counted in the score."
        fp["note_hi"] = "स्पीकर से सुनी आवाज़: कमरे की आवाज़ इन संकेतों को छुपा देती है, इसलिए स्कोर में नहीं गिने गए।"
    out = _clean({
        "ok": True,
        "risk": risk,
        "reverse_engineering": rev,
        "ai_voice": df,
        "fingerprints": fp,
        "source_trace": st,
        "voice_id": sv,
        "voice_print": vp,
        "transcript": tr,
        "scam_words": sw,
        "reply_delay": rd,
        "phone_quality": {"narrowband": narrow, "simulated_phone_line": simulate_phone},
        "timings_s": timings | {"total": round(time.time() - t0, 2)},
    })
    if return_embedding:
        out["_embedding"] = emb
    return out
