"""Push-notification registration (Firebase Cloud Messaging), so alerts reach a phone whose app is closed."""
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import push
from ..auth import bearer, current_user, token_hash
from ..db import PushToken, User, get_session

router = APIRouter(prefix="/api/push", tags=["push"])


class PushReg(BaseModel):
    token: str
    platform: str = "android"


@router.get("/config")
def push_config(me: User = Depends(current_user)):
    """Firebase settings the app needs to get a push token (public values; the server key never leaves the laptop)."""
    on = push.enabled()
    return {"enabled": on, "status": push.status(), "android": push.android_config() if on else None}


@router.post("/register")
def register(body: PushReg, authorization: str | None = Header(None), me: User = Depends(current_user),
             s: Session = Depends(get_session)):
    tok = body.token.strip()
    if not 20 <= len(tok) <= 4096:
        raise HTTPException(400, "invalid push token")
    login = bearer(authorization)
    # Tied to this login, so signing out stops the pushes too.
    push.save_token(s, me.id, tok, token_hash(login) if login else None)
    return {"ok": True, "enabled": push.enabled()}


@router.post("/unregister")
def unregister(body: PushReg, me: User = Depends(current_user), s: Session = Depends(get_session)):
    p = s.get(PushToken, body.token.strip())
    if p and p.user_id == me.id:
        s.delete(p)
        s.commit()
    return {"ok": True}


@router.post("/test")
async def test(me: User = Depends(current_user)):
    """Settings > "Test push": a harmless notification to every phone signed in as me."""
    if not push.enabled():
        return {"ok": False, "phones": 0, "status": push.status()}
    n = await push.send(me.id, {"type": "alert", "alert": {
        "id": f"test-{int(time.time())}", "kind": "test", "from_user_id": None, "payload": {},
        "title": "VoiceGuard push works",
        "body": "Family alerts will reach this phone even when the app is closed."}})
    return {"ok": n > 0, "phones": n, "status": push.status()}
