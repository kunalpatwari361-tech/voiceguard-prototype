"""Scam Voice ID: link scam calls by the caller's VOICE, not only by the number.

Scammers change SIM cards all the time, but the voice they use - their own, a voice-changer preset, or the voice
they cloned - stays much the same. When a family confirms a call was a scam (Block & report, or "Are you really
calling?" answered NO), the caller's voice embedding is saved under an ID such as SV-3F9A21. Every later check
compares the caller with these IDs; a match links the calls ("same voice as 3 earlier scam calls from 2 numbers"),
raises the risk and goes into the evidence report, so 1930 / the cyber cell can connect the cases.

Privacy: only the 512-number WavLM-SV embedding is stored, never audio. Embeddings from risky checks that nobody
confirms are deleted after 7 days. A voice that matches a family member's own voice print (and does not sound
AI-made) is never saved as a scam voice.
"""
import datetime as dt
import json
import threading

import numpy as np
from sqlalchemy import delete
from sqlmodel import Session, select

from .db import ScamVoice, User, VoiceSample, engine, now
from .routers.common import iso

MATCH = 0.86          # WavLM-SV "same speaker" threshold (model card)
STRONG = 0.90
KEEP_DAYS = 7
MERGE_MIN = 30        # checks of the same call within 30 minutes update one sample instead of adding new ones

_lock = threading.Lock()
_ids: list[str] = []
_mat = np.zeros((0, 512), np.float32)
_info: dict[str, dict] = {}
_loaded = False


def _vec(v) -> np.ndarray:
    a = np.asarray(json.loads(v) if isinstance(v, str) else v, dtype=np.float32)
    return a / (np.linalg.norm(a) + 1e-9)


def _js(v: np.ndarray) -> str:
    return json.dumps([round(float(x), 5) for x in v])


def summary(s: Session, v: ScamVoice) -> dict:
    rows = s.exec(select(VoiceSample.number, VoiceSample.user_id).where(VoiceSample.voice_id == v.id)).all()
    users = {r[1] for r in rows}
    fams = {fam or f"user:{uid}" for uid, fam in s.exec(select(User.id, User.family_id).where(User.id.in_(users))).all()}         if users else set()
    return {"id": v.id, "kind": v.kind, "clone_of": v.clone_of, "calls": len(rows),
            "numbers": sorted({r[0] for r in rows if r[0]}), "families": len(fams),
            "confirmations": v.confirmations, "first_seen": iso(v.created_at), "last_seen": iso(v.last_seen),
            "reason": v.reason}


def refresh() -> None:
    global _ids, _mat, _info, _loaded
    with Session(engine) as s:
        voices = s.exec(select(ScamVoice)).all()
        info = {v.id: summary(s, v) for v in voices}
        mat = np.stack([_vec(v.embedding) for v in voices]) if voices else np.zeros((0, 512), np.float32)
    with _lock:
        _ids, _mat, _info, _loaded = [v.id for v in voices], mat, info, True


def match(emb: np.ndarray) -> dict | None:
    """Closest Scam Voice ID for this caller embedding (no database access - safe from the AI thread)."""
    if not _loaded:
        refresh()
    with _lock:
        if not _ids:
            return None
        sims = _mat @ _vec(emb)
        i = int(np.argmax(sims))
        if sims[i] < MATCH:
            return None
        return dict(_info[_ids[i]]) | {"similarity": round(float(sims[i]), 4), "strong": bool(sims[i] >= STRONG)}


def get(voice_id: str) -> dict | None:
    if not _loaded:
        refresh()
    with _lock:
        return dict(_info[voice_id]) if voice_id in _info else None


def all_voices() -> list[dict]:
    if not _loaded:
        refresh()
    with _lock:
        return sorted(_info.values(), key=lambda v: (-v["calls"], v["id"]))


def remember(s: Session, user_id: str, number: str | None, emb: np.ndarray, result: dict, source: str) -> None:
    """Keep the caller's voice from a risky check (or one that matched a Scam Voice ID)."""
    sv = result.get("voice_id")
    linked = sv["id"] if sv and not sv.get("ignored") else None
    level = result["risk"]["level"]
    if level == "safe" and not linked:
        return
    t = now()
    q = select(VoiceSample).where(VoiceSample.user_id == user_id,
                                  VoiceSample.created_at >= t - dt.timedelta(minutes=MERGE_MIN))
    q = q.where(VoiceSample.number == number) if number else q.where(VoiceSample.number.is_(None))
    smp = s.exec(q.order_by(VoiceSample.created_at.desc())).first()
    if smp and smp.voice_id in (None, linked):
        # same call, another window: average the voices (a steadier embedding), keep the highest risk
        smp.embedding = _js(_vec(_vec(smp.embedding) + _vec(emb)))
        smp.risk_score = max(smp.risk_score or 0, result["risk"]["score"])
        smp.level = "danger" if "danger" in (smp.level, level) else level
        smp.fake_prob = max(smp.fake_prob or 0, result["ai_voice"]["fake_prob"])
        smp.voice_id = smp.voice_id or linked
    else:
        smp = VoiceSample(user_id=user_id, number=number, embedding=_js(_vec(emb)), risk_score=result["risk"]["score"],
                          level=level, source=source, kind=result.get("reverse_engineering", {}).get("kind"),
                          fake_prob=result["ai_voice"]["fake_prob"], voice_id=linked,
                          similarity=sv.get("similarity") if linked else None)
    s.add(smp)
    if linked and (v := s.get(ScamVoice, linked)):
        v.last_seen = t
        s.add(v)
    s.exec(delete(VoiceSample).where(VoiceSample.voice_id.is_(None),
                                     VoiceSample.created_at < t - dt.timedelta(days=KEEP_DAYS)))
    s.commit()
    if linked:
        refresh()


def confirm(s: Session, me: User, number: str | None, reason: str, minutes: int = 24 * 60) -> dict:
    """The family confirmed a scam: save (or strengthen) the caller's Scam Voice ID."""
    q = select(VoiceSample).where(VoiceSample.user_id == me.id,
                                  VoiceSample.created_at >= now() - dt.timedelta(minutes=minutes))
    if number:
        q = q.where(VoiceSample.number == number)
    smp = s.exec(q.order_by(VoiceSample.created_at.desc())).first()
    if not smp:
        return {"saved": False, "why": "no_voice",
                "en": "No voice was checked for this call, so only the number was reported.",
                "hi": "इस कॉल की आवाज़ जाँची नहीं गई थी, इसलिए सिर्फ़ नंबर रिपोर्ट हुआ।"}
    emb = _vec(smp.embedding)
    clone_of = None
    members = s.exec(select(User).where(User.family_id == me.family_id)).all() if me.family_id else [me]
    for u in members:
        if u.voiceprint and float(emb @ _vec(u.voiceprint)) >= MATCH:
            if (smp.fake_prob or 0) < 0.5:   # sounds like the real family member, not a clone: never store it
                return {"saved": False, "why": "family_voice", "name": u.name,
                        "en": f"This voice matches {u.name}'s own voice print, so it was not saved as a scam voice.",
                        "hi": f"यह आवाज़ {u.name} के अपने वॉइस प्रिंट से मिलती है, इसलिए ठग की आवाज़ के रूप में सेव नहीं हुई।"}
            clone_of = u.name
    if smp.confirmed and smp.voice_id:   # this call was already confirmed (e.g. every check after a "no")
        return {"saved": True, "new": False, **(get(smp.voice_id) or {"id": smp.voice_id}),
                "en": f"Caller's voice is saved as Scam Voice ID {smp.voice_id}.",
                "hi": f"कॉलर की आवाज़ Scam Voice ID {smp.voice_id} के रूप में सेव है।"}
    new = False
    v = s.get(ScamVoice, smp.voice_id) if smp.voice_id else None
    if v is None:
        m = match(emb)
        v = s.get(ScamVoice, m["id"]) if m else None
    if v is None:
        v = ScamVoice(embedding=_js(emb), kind=smp.kind, clone_of=clone_of, reason=reason[:200])
        new = True
    else:
        if smp.voice_id != v.id:   # a new call joins this voice: move its embedding a little towards it
            v.embedding = _js(_vec(_vec(v.embedding) * v.samples + emb))
            v.samples += 1
        v.confirmations += 1
        v.last_seen = now()
        v.clone_of = v.clone_of or clone_of
        v.reason = reason[:200] or v.reason
    s.add(v)
    s.commit()
    smp.voice_id, smp.confirmed = v.id, True
    s.add(smp)
    s.commit()
    refresh()
    info = get(v.id)
    n_other = max(0, info["calls"] - 1)
    return {"saved": True, "new": new, **info,
            "en": (f"Caller's voice saved as Scam Voice ID {v.id}. If this voice calls anyone using VoiceGuard - "
                   f"from any number - they will be warned." if new else
                   f"This voice is already known as {v.id}: now linked to {n_other} other scam call(s) "
                   f"from {len(info['numbers'])} number(s)."),
            "hi": (f"कॉलर की आवाज़ Scam Voice ID {v.id} के रूप में सेव हुई। यह आवाज़ किसी भी नंबर से VoiceGuard "
                   f"इस्तेमाल करने वाले किसी को कॉल करे, तो उन्हें चेतावनी मिलेगी।" if new else
                   f"यह आवाज़ पहले से {v.id} के नाम से जानी जाती है: अब {n_other} और ठगी कॉल से जुड़ी है।")}


def for_call(s: Session, user_id: str, number: str | None, around: dt.datetime) -> dict | None:
    """Scam Voice ID linked to a saved call (for the evidence report)."""
    q = select(VoiceSample).where(VoiceSample.user_id == user_id, VoiceSample.voice_id.is_not(None),
                                  VoiceSample.created_at >= around - dt.timedelta(days=1),
                                  VoiceSample.created_at <= around + dt.timedelta(days=1))
    if number:
        q = q.where(VoiceSample.number == number)
    smp = s.exec(q.order_by(VoiceSample.created_at.desc())).first()
    return get(smp.voice_id) if smp else None
