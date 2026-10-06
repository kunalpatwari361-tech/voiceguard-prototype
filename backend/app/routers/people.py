"""Users, Family Circle (feature 2), Voice Print enrolment (10), Family Location (9), Family Alert (17).
Every route needs the login token from the OTP sign-up (app/auth.py); accounts are created only there."""
import asyncio
import json

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, or_, select

from ..ai import models
from ..ai.audio_io import load_audio
from ..ai.fingerprints import speech_mask
from ..auth import current_user, require_family, require_self
from ..db import Alert, Family, User, get_session, now
from ..hub import hub
from .common import alert_dict, family_ids, public_user, push_alert

router = APIRouter(prefix="/api", tags=["people"])


@router.get("/users/{user_id}")
def get_user(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    u = s.get(User, user_id)
    require_family(me, u)
    return public_user(u)


# ------------------------------------------------------------------ family circle
class NewFamily(BaseModel):
    user_id: str
    name: str = "My Family"


class JoinFamily(BaseModel):
    user_id: str
    code: str


def _family_view(s: Session, f: Family) -> dict:
    members = s.exec(select(User).where(User.family_id == f.id)).all()
    return {"id": f.id, "name": f.name, "invite_code": f.invite_code, "owner_id": f.owner_id,
            "members": [public_user(m) for m in members]}


@router.post("/family")
def create_family(body: NewFamily, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.user_id)
    u = me
    f = Family(name=body.name, owner_id=u.id)
    s.add(f)
    s.commit()
    s.refresh(f)
    u.family_id = f.id
    s.add(u)
    s.commit()
    return _family_view(s, f)


@router.post("/family/join")
async def join_family(body: JoinFamily, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.user_id)
    u = me
    f = s.exec(select(Family).where(Family.invite_code == body.code.strip())).first()
    if not f:
        raise HTTPException(404, "Invite code not found")
    u.family_id = f.id
    s.add(u)
    s.commit()
    await hub.send_many(family_ids(s, f.id), {"type": "family_updated"}, exclude=u.id)
    return _family_view(s, f)


@router.get("/family/{family_id}")
def get_family(family_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    if me.family_id != family_id:
        raise HTTPException(403, "You are not in this family circle.")
    f = s.get(Family, family_id)
    if not f:
        raise HTTPException(404, "family not found")
    return _family_view(s, f)


@router.post("/family/leave/{user_id}")
def leave_family(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    u = me
    u.family_id = None
    s.add(u)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ voice print
@router.post("/voiceprint/{user_id}")
async def enroll_voiceprint(user_id: str, file: UploadFile = File(...), reset: bool = Form(False),
                            me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Save the voice as numbers (a 512-d x-vector), never the recording itself. Only your own voice."""
    require_self(me, user_id)
    u = me
    y = load_audio(await file.read())
    mask, *_ = speech_mask(y)
    if mask.sum() * 0.01 < 3:
        raise HTTPException(400, "Please speak for at least 3 seconds.")
    emb = await asyncio.to_thread(models.speaker().embed, y)
    if u.voiceprint and not reset:
        old = np.array(json.loads(u.voiceprint), dtype=np.float32)
        n = u.voiceprint_samples
        emb = (old * n + emb) / (n + 1)
        emb = emb / np.linalg.norm(emb)
        u.voiceprint_samples = n + 1
    else:
        u.voiceprint_samples = 1
    u.voiceprint = json.dumps([round(float(x), 6) for x in emb])
    u.voiceprint_at = now()
    s.add(u)
    s.commit()
    return {"ok": True, "samples": u.voiceprint_samples,
            "message": "Voice print saved (as numbers, not a recording)."}


@router.delete("/voiceprint/{user_id}")
def delete_voiceprint(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    u = me
    u.voiceprint, u.voiceprint_samples, u.voiceprint_at = None, 0, None
    s.add(u)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ location
class LocationRequest(BaseModel):
    asker_id: str
    target_id: str


@router.post("/location/request")
async def request_location(body: LocationRequest, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Ask the target phone for a fresh fix; fall back to the last known one. Family only."""
    require_self(me, body.asker_id)
    target = s.get(User, body.target_id)
    require_family(me, target)
    asker = me
    fresh = False
    if hub.online(target.id):
        rid = f"loc-{target.id}-{now().timestamp()}"
        fut = hub.expect(rid, owner=target.id)
        await hub.send(target.id, {"type": "location_request", "request_id": rid,
                                   "from": {"id": asker.id, "name": asker.name}})
        try:
            await asyncio.wait_for(fut, timeout=12)
            fresh = True
        except asyncio.TimeoutError:
            hub.forget(rid)
        s.refresh(target)
    return {"fresh": fresh, "user": public_user(target)}


# ------------------------------------------------------------------ alerts
class NewAlert(BaseModel):
    from_user_id: str
    kind: str
    title: str
    body: str = ""
    payload: dict = {}
    to_user_id: str | None = None


@router.post("/alerts")
async def create_alert(body: NewAlert, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.from_user_id)
    if body.to_user_id:
        require_family(me, s.get(User, body.to_user_id))
    u = me
    return await push_alert(s, family_id=u.family_id, from_user_id=u.id, kind=body.kind, title=body.title,
                            body=body.body, payload=body.payload | {"from_name": u.name}, to_user_id=body.to_user_id)


@router.get("/alerts/{user_id}")
def list_alerts(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    u = me
    q = select(Alert).where(or_(Alert.to_user_id == u.id,
                                (Alert.family_id == u.family_id) & (Alert.to_user_id == None))  # noqa: E711
                            ).order_by(Alert.created_at.desc()).limit(50)
    return [alert_dict(a) for a in s.exec(q).all() if u.family_id or a.to_user_id == u.id]
