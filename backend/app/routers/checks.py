"""Call checking: AI Voice Detector (3), fingerprints (4), Source Tracing (5), Live Call / Voice Note
Check (6, 7), Voice Print Match (10), Voice Test (12), Reply Delay (13), Scam Words (14),
Number Info (15), Final Risk Score (16), Spam / Block / Community list (24-26)."""
import asyncio
import datetime as dt
import json
import logging
import time

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from ..ai import challenge, fingerprints, models, number_info, reply_delay, separate
from ..ai.audio_io import load_audio
from ..ai.pipeline import _clean, analyze_voice
from ..auth import current_user, require_family, require_self
from ..db import BlockedNumber, RiskEvent, ScamReport, User, get_session, now
from ..hub import hub
from .common import age_min, family_ids, iso, push_alert

router = APIRouter(prefix="/api", tags=["checks"], dependencies=[Depends(current_user)])  # login required
log = logging.getLogger("voiceguard.checks")


def _claimed(s: Session, claimed_user_id: str | None):
    if not claimed_user_id:
        return None, None, None
    c = s.get(User, claimed_user_id)
    if not c:
        return None, None, None
    vp = np.array(json.loads(c.voiceprint), dtype=np.float32) if c.voiceprint else None
    loc = None
    if c.lat is not None and c.loc_at and (now() - c.loc_at) < dt.timedelta(minutes=30):
        loc = {"place": c.place, "age_min": age_min(c.loc_at)}
    return c, vp, loc


def _number_score(s: Session, number: str | None, user: User | None) -> tuple[float | None, dict | None]:
    if not number:
        return None, None
    ni = lookup_number(s, number, user)
    return ni["spam_score"], ni


def lookup_number(s: Session, number: str, user: User | None) -> dict:
    n = number_info.normalize(number)
    reports = s.exec(select(func.count()).select_from(ScamReport).where(ScamReport.number == n)).one()
    fam = family_ids(s, user.family_id) if user else []
    blocked = bool(fam) and s.exec(select(BlockedNumber).where(BlockedNumber.number == n,
                                                               BlockedNumber.user_id.in_(fam))).first() is not None
    member = s.exec(select(User).where(User.phone == n, User.family_id == user.family_id)).first() \
        if user and user.family_id else None
    return number_info.info(n, community_reports=reports, blocked_by_family=blocked,
                            in_family=member.name if member else None)


@router.post("/analyze")
async def analyze(file: UploadFile = File(...),
                  user_id: str | None = Form(None),
                  claimed_user_id: str | None = Form(None),
                  number: str | None = Form(None),
                  source: str = Form("voice_note"),          # voice_note / live_call / demo_call / hd_call
                  transcript: str = Form(""),
                  reply_gaps: str | None = Form(None),       # JSON list of seconds
                  really_calling: str | None = Form(None),   # yes / no / no_answer
                  voice_test_passed: bool | None = Form(None),
                  hd_call: str | None = Form(None),          # verified / refused
                  simulate_phone: bool = Form(False),
                  skip_asr: bool = Form(False),
                  me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    if claimed_user_id:
        require_family(me, s.get(User, claimed_user_id))
    if models.starting_up():
        # answer at once instead of making the phone wait for the models to load
        return {"ok": False, "error": "warming_up",
                "message": "The AI is still starting on the laptop (a few seconds after the server starts). Checking again shortly…",
                "message_hi": "लैपटॉप पर AI अभी शुरू हो रहा है (सर्वर चालू होने के कुछ सेकंड बाद)। थोड़ी देर में फिर जाँचेंगे…"}
    y = load_audio(await file.read())
    user = me
    claimed, vp, loc = _claimed(s, claimed_user_id)
    heard_s = len(y) / 16000
    focus = None
    if source == "live_call" and me.voiceprint:
        # The phone's mic hears both people: cut out the owner's own (real) voice, check only the caller.
        y, focus = await asyncio.to_thread(separate.keep_caller, y, np.array(json.loads(me.voiceprint), dtype=np.float32))
        if focus["used"] and focus["caller_s"] < 1.0:
            log.info("analyze %s: %.1fs heard, only the owner's voice", source, heard_s)
            return {"ok": False, "error": "only_owner", "caller_focus": focus,
                    "message": "Only your own voice was heard. Keep the call on speaker and let the caller talk.",
                    "message_hi": "सिर्फ़ आपकी आवाज़ सुनाई दी। कॉल स्पीकर पर रखें और कॉलर को बोलने दें।"}
    nscore, ninfo = _number_score(s, number, user)
    result = await asyncio.to_thread(
        analyze_voice, y,
        claimed_name=claimed.name if claimed else None, claimed_print=vp, transcript_hint=transcript,
        do_asr=not skip_asr, simulate_phone=simulate_phone,
        reply_gaps=json.loads(reply_gaps) if reply_gaps else None, number_score=nscore,
        really_calling=really_calling,
        voice_test=None if voice_test_passed is None else {"passed": voice_test_passed},
        hd_call=hd_call, location=loc)
    if not result.get("ok"):
        log.info("analyze %s: %.1fs heard, %s", source, heard_s, result.get("error"))
        return result | {"caller_focus": focus}
    log.info("analyze %s: %.1fs heard%s -> risk %s (%s), AI voice %.2f", source, heard_s,
             f", owner voice removed {focus['owner_s']}s" if focus and focus.get("used") else "",
             result["risk"]["score"], result["risk"]["level"], result["ai_voice"]["fake_prob"])
    result["caller_focus"] = focus
    result["number_info"] = ninfo
    result["claimed"] = {"id": claimed.id, "name": claimed.name, "voiceprint_enrolled": vp is not None,
                         "location": loc} if claimed else None
    result["source"] = source
    if user and result["risk"]["level"] != "safe":
        s.add(RiskEvent(user_id=user.id, score=result["risk"]["score"], level=result["risk"]["level"],
                        number=number_info.normalize(number) if number else None, source=source))
        s.commit()
    if user and user.family_id and result["risk"]["level"] == "danger" and _should_alert(user.id, number):
        # Family Alert (17): everyone in the circle hears about it at once, without the victim doing anything.
        what = {"voice_note": "received a suspicious voice note", "demo_call": "is on a (demo) scam call"}.get(
            source, "is on a suspected scam call")
        body = f"Risk {result['risk']['score']}/100" + (f" from {number_info.normalize(number)}" if number else "") +             (f". The caller pretends to be {claimed.name}" if claimed else "") + f". Call {user.name} now."
        await push_alert(s, family_id=user.family_id, from_user_id=user.id, kind="scam_call",
                         title=f"{user.name} {what}", body=body,
                         payload={"number": number, "score": result["risk"]["score"], "victim_phone": user.phone,
                                  "from_name": user.name})
    return result


_last_alert: dict[tuple[str, str], float] = {}


def _should_alert(user_id: str, number: str | None) -> bool:
    """One automatic family alert per caller every 10 minutes (each analysis pass would otherwise re-alert)."""
    key = (user_id, number or "")
    now_s = time.time()
    if now_s - _last_alert.get(key, 0) < 600:
        return False
    _last_alert[key] = now_s
    return True


# ------------------------------------------------------------------ voice test
@router.get("/challenge/new")
def new_challenge(kind: str | None = None):
    return challenge.new_challenge(kind)


@router.post("/challenge/{challenge_id}/verify")
async def verify_challenge(challenge_id: str, file: UploadFile = File(...),
                           claimed_user_id: str | None = Form(None), me: User = Depends(current_user),
                           s: Session = Depends(get_session)):
    if claimed_user_id:
        require_family(me, s.get(User, claimed_user_id))
    c = challenge.get(challenge_id)
    if not c:
        raise HTTPException(404, "challenge expired - start a new one")
    y = load_audio(await file.read())
    claimed, vp, _ = _claimed(s, claimed_user_id)

    def run():
        out = {"challenge": c}
        df = models.deepfake().predict(y)
        out["ai_voice"] = df
        kind = c["kind"]
        task = {}
        if kind == "laugh":
            task = fingerprints.laugh_check(y)
            done = task["is_laugh"]
        elif kind == "whisper":
            task = fingerprints.whisper_check(y)
            tr = models.asr().transcribe(y, language="hi")
            task["heard"] = tr["text"]
            done = task["is_whisper"]
        elif kind == "sing":
            task = fingerprints.sing_check(y)
            done = task["is_singing"]
        elif kind == "phrase":
            tr = models.asr().transcribe(y)
            task = {"heard": tr["text"], "similarity": challenge.text_match(c["expect"], tr["text"])}
            done = task["similarity"] >= 0.5
        else:  # family_question / dialect: a human judges the answer; we still check the voice
            tr = models.asr().transcribe(y)
            task = {"heard": tr["text"], "language": tr["language"], "needs_human_judgement": True}
            done = True
        out["task"] = task | {"done": bool(done)}
        if vp is not None:
            out["voice_print"] = models.speaker().compare(models.speaker().embed(y), vp) | {"name": claimed.name}
        ai_bad = df["fake_prob"] >= 0.5
        vp_bad = out.get("voice_print", {}).get("verdict") == "different"
        out["passed"] = bool(done and not ai_bad and not vp_bad)
        why = []
        if not done:
            why.append(("Caller could not do the task.", "कॉलर यह काम नहीं कर पाया।"))
        if ai_bad:
            why.append((f"Response sounds AI-generated ({df['fake_prob'] * 100:.0f}%).",
                        f"जवाब AI से बना लगता है ({df['fake_prob'] * 100:.0f}%)।"))
        if vp_bad:
            why.append(("Voice does not match the saved voice print.", "आवाज़ वॉइस प्रिंट से मेल नहीं खाती।"))
        if not why:
            why.append(("Caller completed the task with a natural human voice.", "कॉलर ने काम पूरा किया, आवाज़ इंसानी है।"))
        out["reasons"] = [{"en": a, "hi": b} for a, b in why]
        return out

    return _clean(await asyncio.to_thread(run))


class Gaps(BaseModel):
    gaps: list[float]


@router.post("/reply-delay")
def check_reply_delay(body: Gaps):
    return reply_delay.score_gaps(body.gaps)


# ------------------------------------------------------------------ numbers
@router.get("/numbers/{number}")
def number_lookup(number: str, user_id: str | None = None, me: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    require_self(me, user_id)
    user = me
    ni = lookup_number(s, number, user)
    reasons = s.exec(select(ScamReport.reason).where(ScamReport.number == ni["number"])
                     .order_by(ScamReport.created_at.desc()).limit(3)).all()
    ni["recent_reasons"] = [r for r in reasons if r]
    return ni


@router.get("/scamlist")
def scam_list(s: Session = Depends(get_session)):
    rows = s.exec(select(ScamReport.number, func.count(), func.max(ScamReport.created_at))
                  .group_by(ScamReport.number).order_by(func.count().desc())).all()
    out = []
    for number, count, last in rows:
        reason = s.exec(select(ScamReport.reason).where(ScamReport.number == number)
                        .order_by(ScamReport.created_at.desc())).first()
        out.append({"number": number, "reports": count, "last_reason": reason, "last_reported": iso(last)})
    return out


class Report(BaseModel):
    number: str
    reporter_id: str | None = None
    reason: str = ""


@router.post("/scamlist/report")
async def report_number(body: Report, me: User = Depends(current_user), s: Session = Depends(get_session)):
    body.reporter_id = me.id
    n = number_info.normalize(body.number)
    s.add(ScamReport(number=n, reporter_id=body.reporter_id, reason=body.reason))
    s.commit()
    for uid in list(hub.conns):  # everyone refreshes their offline copy of the list
        await hub.send(uid, {"type": "scamlist_updated", "number": n})
    return {"ok": True, "number": n}


class Block(BaseModel):
    user_id: str
    number: str


@router.get("/blocked/{user_id}")
def blocked_list(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    u = me
    ids = family_ids(s, u.family_id) or [u.id]
    rows = s.exec(select(BlockedNumber).where(BlockedNumber.user_id.in_(ids))).all()
    names = {x.id: x.name for x in s.exec(select(User).where(User.id.in_(ids))).all()}
    return [{"number": r.number, "by": names.get(r.user_id), "mine": r.user_id == u.id, "at": iso(r.created_at)}
            for r in rows]


@router.post("/blocked")
def block_number(body: Block, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.user_id)
    n = number_info.normalize(body.number)
    if not s.exec(select(BlockedNumber).where(BlockedNumber.user_id == body.user_id, BlockedNumber.number == n)).first():
        s.add(BlockedNumber(user_id=body.user_id, number=n))
        s.commit()
    return {"ok": True, "number": n}


@router.delete("/blocked/{user_id}/{number}")
def unblock_number(user_id: str, number: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    n = number_info.normalize(number)
    for r in s.exec(select(BlockedNumber).where(BlockedNumber.user_id == user_id, BlockedNumber.number == n)).all():
        s.delete(r)
    s.commit()
    return {"ok": True}


@router.get("/risk/recent/{user_id}")
def recent_risk(user_id: str, minutes: int = 30, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    since = now() - dt.timedelta(minutes=minutes)
    ev = s.exec(select(RiskEvent).where(RiskEvent.user_id == user_id, RiskEvent.created_at >= since)
                .order_by(RiskEvent.score.desc())).first()
    return {"risky": ev is not None and ev.level == "danger",
            "event": {"score": ev.score, "level": ev.level, "number": ev.number, "at": iso(ev.created_at)} if ev else None}
