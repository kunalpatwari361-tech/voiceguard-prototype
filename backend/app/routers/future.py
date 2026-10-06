"""Future features as working prototypes:
Police Join the Call (28), Telecom Partnership (29), Voice Shield (30), Smart Learning (31),
Bank and Enterprise APIs (32). Auto Check Every Call (27) lives in the app."""
import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from ..ai import voice_shield
from ..ai.audio_io import load_audio, to_wav_bytes
from ..ai.number_info import normalize
from ..config import ENTERPRISE_API_KEY
from ..db import RiskEvent, ScamReport, User, get_session, now
from ..training import federated
from ..auth import current_user, require_self
from .common import iso, push_alert

router = APIRouter(tags=["future"])


# ---------------------------------------------------------------- 28 police join the call
class PoliceJoin(BaseModel):
    user_id: str
    number: str | None = None
    evidence_id: str | None = None


@router.post("/api/future/police-join")
async def police_join(body: PoliceJoin, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Simulation: in production a cyber-cell officer would be bridged into the live call by the telecom."""
    require_self(me, body.user_id)
    u = me
    ticket = "CC-" + uuid.uuid4().hex[:6].upper()
    await push_alert(s, family_id=u.family_id, from_user_id=u.id, kind="police_join",
                     title=f"{u.name} requested police to join a suspicious call",
                     body=f"Ticket {ticket}. Caller {body.number or 'unknown'}.",
                     payload={"ticket": ticket, "number": body.number, "evidence_id": body.evidence_id})
    return {"ticket": ticket, "simulated": True, "steps": [
        {"t": 0, "en": "Request sent to cyber-cell duty officer", "hi": "साइबर सेल ड्यूटी ऑफ़िसर को अनुरोध भेजा"},
        {"t": 3, "en": "Officer reviewing VoiceGuard evidence", "hi": "ऑफ़िसर सबूत देख रहे हैं"},
        {"t": 6, "en": "Officer joining the call (conference bridge)", "hi": "ऑफ़िसर कॉल से जुड़ रहे हैं"},
        {"t": 9, "en": "Officer connected - caller has been warned", "hi": "ऑफ़िसर जुड़ गए - कॉलर को चेतावनी दी गई"},
    ]}


# ---------------------------------------------------------------- 29 telecom partnership
TELECOM_KEY = "demo-telecom-key"


class TelecomFlag(BaseModel):
    number: str
    reason: str = "network-level fraud signal"
    operator: str = "DemoTel"


@router.post("/api/v1/telecom/flag")
def telecom_flag(body: TelecomFlag, x_api_key: str = Header(...), s: Session = Depends(get_session)):
    if x_api_key != TELECOM_KEY:
        raise HTTPException(401, "bad API key")
    n = normalize(body.number)
    s.add(ScamReport(number=n, reason=f"[{body.operator}] {body.reason}", source="telecom"))
    s.commit()
    return {"ok": True, "number": n}


@router.get("/api/v1/telecom/lookup/{number}")
def telecom_lookup(number: str, s: Session = Depends(get_session)):
    """What an operator would query to show 'Suspected fraud' on the caller ID before the phone rings."""
    n = normalize(number)
    reports = s.exec(select(func.count()).select_from(ScamReport).where(ScamReport.number == n)).one()
    return {"number": n, "reports": reports, "label": "Suspected fraud" if reports >= 2 else "Unverified" if reports else "No reports"}


# ---------------------------------------------------------------- 30 voice shield
@router.post("/api/future/voice-shield/protect")
async def shield_protect(user_id: str = Form(...), file: UploadFile = File(...), me: User = Depends(current_user)):
    require_self(me, user_id)
    y = load_audio(await file.read())
    return Response(to_wav_bytes(voice_shield.protect(y, user_id)), media_type="audio/wav",
                    headers={"Content-Disposition": 'attachment; filename="shielded.wav"'})


@router.post("/api/future/voice-shield/detect")
async def shield_detect(user_id: str = Form(...), file: UploadFile = File(...), me: User = Depends(current_user)):
    require_self(me, user_id)
    return voice_shield.detect(load_audio(await file.read()), user_id)


# ---------------------------------------------------------------- 31 smart learning (federated)
@router.get("/api/future/federated")
def federated_demo(rounds: int = 8, clients: int = 5, me: User = Depends(current_user)):
    return federated.simulate(rounds=rounds, n_clients=clients)


# ---------------------------------------------------------------- 32 bank & enterprise API
class TxnCheck(BaseModel):
    customer_phone: str
    amount: float
    beneficiary: str = ""
    channel: str = "UPI"


@router.post("/api/v1/enterprise/transaction-check")
async def transaction_check(body: TxnCheck, x_api_key: str = Header(...), s: Session = Depends(get_session)):
    """A bank calls this before releasing a payment: was the customer just on a risky call?"""
    if x_api_key != ENTERPRISE_API_KEY:
        raise HTTPException(401, "bad API key")
    phone = normalize(body.customer_phone)
    u = s.exec(select(User).where(User.phone == phone)).first()
    if not u:
        return {"action": "allow", "reason": "Customer not enrolled in VoiceGuard", "voiceguard_user": False}
    since = now() - dt.timedelta(minutes=60)
    ev = s.exec(select(RiskEvent).where(RiskEvent.user_id == u.id, RiskEvent.created_at >= since)
                .order_by(RiskEvent.score.desc())).first()
    if ev and ev.level == "danger":
        action, reason = "hold", f"Customer had a high-risk call ({ev.score}/100) at {iso(ev.created_at)}"
    elif ev:
        action, reason = "call_customer", f"Customer had a suspicious call ({ev.score}/100)"
    else:
        action, reason = "allow", "No risky calls in the last 60 minutes"
    if action != "allow":
        await push_alert(s, family_id=u.family_id, from_user_id=u.id, kind="bank_hold",
                         title=f"Bank paused a ₹{body.amount:,.0f} payment from {u.name}",
                         body=f"{reason}. Beneficiary: {body.beneficiary or 'unknown'}.",
                         payload={"amount": body.amount, "beneficiary": body.beneficiary, "action": action})
    return {"action": action, "reason": reason, "voiceguard_user": True,
            "risk_event": {"score": ev.score, "level": ev.level, "number": ev.number} if ev else None}
