"""Sign-up / sign-in with a phone-number OTP. The only way to create an account."""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..ai.number_info import normalize
from ..auth import bearer, check_otp, current_user, issue_token, revoke_token, send_otp, token_hash, valid_phone
from ..db import FamilyInvite, PushToken, User, get_session
from .common import public_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class OtpStart(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    code: str
    name: str
    role: str = "member"


def _phone(raw: str) -> str:
    phone = normalize(raw)
    if not valid_phone(phone):
        raise HTTPException(400, "Enter a valid mobile number (10 digits, or with country code).")
    return phone


@router.post("/otp/start")
def otp_start(body: OtpStart, s: Session = Depends(get_session)):
    phone = _phone(body.phone)
    return send_otp(s, phone) | {"phone": phone}


@router.post("/otp/verify")
def otp_verify(body: OtpVerify, s: Session = Depends(get_session)):
    phone = _phone(body.phone)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Enter your name.")
    check_otp(s, phone, body.code)
    u = s.exec(select(User).where(User.phone == phone)).first()
    if u:  # same number re-installing the app keeps its account and family
        u.name, u.role = name, body.role
    else:
        u = User(name=name, phone=phone, role=body.role)
    s.add(u)
    s.commit()
    s.refresh(u)
    invites = s.exec(select(FamilyInvite).where(FamilyInvite.phone == phone, FamilyInvite.status == "pending")).all()
    return {"token": issue_token(s, u.id), "user": public_user(u), "verified": True,
            "invites": len([i for i in invites if i.family_id != u.family_id])}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return public_user(user)


@router.post("/logout")
def logout(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    tok = bearer(authorization)
    if tok:
        revoke_token(s, tok)
        for p in s.exec(select(PushToken).where(PushToken.auth_hash == token_hash(tok))).all():
            s.delete(p)   # a signed-out phone must not get family alerts any more
        s.commit()
    return {"ok": True}
