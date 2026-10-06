"""Phone-number OTP sign-up + login tokens.

Flow: the app asks for a code (send_otp) -> the user types the 6-digit SMS code (check_otp) -> the server
creates/loads the account and returns a login token -> every API call and WebSocket must carry that token
(`Authorization: Bearer <token>`, or `?token=` for WebSockets and links opened in the browser).

Code delivery:
  twilio   real SMS through Twilio Verify (Twilio generates, sends and checks the code)
  console  demo mode: code printed in the server window and data/dev_otp.log (no SMS cost)
  test     fixed codes for numbers listed in VG_OTP_TEST_NUMBERS (judges, simulator)
Only keyed hashes of codes and tokens are stored.
"""
import datetime as dt
import hashlib
import hmac
import logging
import secrets

import httpx
from fastapi import Depends, Header, HTTPException, Query
from sqlmodel import Session, select

from . import config
from .ai.number_info import normalize
from .db import AuthToken, OtpRequest, User, get_session, now

log = logging.getLogger("voiceguard.auth")
TEST_NUMBERS = {normalize(k): v for k, v in config.OTP_TEST_NUMBERS.items()}


def _hash(*parts: str) -> str:
    return hmac.new(config.SECRET, "|".join(parts).encode(), hashlib.sha256).hexdigest()


def valid_phone(phone: str) -> bool:
    digits = phone.lstrip("+")
    return phone.startswith("+") and digits.isdigit() and 10 <= len(digits) <= 15


# ------------------------------------------------------------------ delivery
def _twilio(path: str, data: dict) -> dict:
    url = f"https://verify.twilio.com/v2/Services/{config.TWILIO_VERIFY_SID}/{path}"
    try:
        r = httpx.post(url, data=data, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=15)
    except httpx.HTTPError as e:
        raise HTTPException(503, "SMS service unreachable. Try again in a minute.") from e
    if r.status_code == 404 and path == "VerificationCheck":
        return {"status": "expired"}
    if r.status_code >= 400:
        log.error("Twilio %s failed: %s %s", path, r.status_code, r.text[:300])
        raise HTTPException(502, "Could not send the SMS code. Check the number and try again.")
    return r.json()


def _console(phone: str, code: str):
    line = f"{now():%Y-%m-%d %H:%M:%S} UTC  OTP for {phone}: {code}"
    log.warning("DEMO OTP (no SMS sent) – %s", line)
    with open(config.DATA_DIR / "dev_otp.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------ OTP
def send_otp(s: Session, phone: str) -> dict:
    t = now()
    rec = s.get(OtpRequest, phone)
    if rec and rec.last_sent_at and (t - rec.last_sent_at).total_seconds() < config.OTP_RESEND_S:
        wait = config.OTP_RESEND_S - int((t - rec.last_sent_at).total_seconds())
        raise HTTPException(429, f"Please wait {wait} s before asking for a new code.")
    if rec and rec.window_start and (t - rec.window_start) < dt.timedelta(hours=1):
        if rec.send_count >= config.OTP_MAX_PER_HOUR:
            raise HTTPException(429, "Too many codes requested for this number. Try again in an hour.")
        count = rec.send_count + 1
        window = rec.window_start
    else:
        count, window = 1, t

    code = None
    if phone in TEST_NUMBERS:
        provider, code = "test", TEST_NUMBERS[phone]
    elif config.OTP_PROVIDER == "twilio":
        if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_VERIFY_SID):
            raise HTTPException(500, "SMS is not configured on the server (Twilio keys missing).")
        _twilio("Verifications", {"To": phone, "Channel": "sms"})
        provider = "twilio"
    else:
        provider, code = "console", f"{secrets.randbelow(10 ** 6):06d}"
        _console(phone, code)

    rec = rec or OtpRequest(phone=phone)
    rec.code_hash = _hash("otp", phone, code) if code else None
    rec.provider = provider
    rec.expires_at = t + dt.timedelta(seconds=config.OTP_TTL_S)
    rec.attempts = 0
    rec.last_sent_at = t
    rec.window_start = window
    rec.send_count = count
    s.add(rec)
    s.commit()
    return {"sent": True, "provider": provider, "expires_in": config.OTP_TTL_S, "resend_after": config.OTP_RESEND_S}


def check_otp(s: Session, phone: str, code: str) -> None:
    """Raises HTTPException unless the code is right. A code works only once."""
    code = "".join(ch for ch in code if ch.isdigit())
    rec = s.get(OtpRequest, phone)
    if not rec or not rec.expires_at or now() > rec.expires_at:
        raise HTTPException(400, "This code has expired. Ask for a new one.")
    if rec.attempts >= config.OTP_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many wrong codes. Ask for a new one.")
    rec.attempts += 1
    s.add(rec)
    s.commit()
    if rec.provider == "twilio":
        ok = _twilio("VerificationCheck", {"To": phone, "Code": code}).get("status") == "approved"
    else:
        ok = len(code) == 6 and hmac.compare_digest(rec.code_hash or "", _hash("otp", phone, code))
    if not ok:
        left = config.OTP_MAX_ATTEMPTS - rec.attempts
        raise HTTPException(400, f"Wrong code. {left} tr{'y' if left == 1 else 'ies'} left." if left else
                            "Wrong code. Ask for a new one.")
    s.delete(rec)
    s.commit()


# ------------------------------------------------------------------ tokens
def issue_token(s: Session, user_id: str) -> str:
    tok = secrets.token_urlsafe(32)
    s.add(AuthToken(user_id=user_id, token_hash=_hash("tok", tok),
                    expires_at=now() + dt.timedelta(days=config.TOKEN_TTL_DAYS)))
    s.commit()
    return tok


def user_for_token(s: Session, tok: str | None) -> User | None:
    if not tok:
        return None
    row = s.exec(select(AuthToken).where(AuthToken.token_hash == _hash("tok", tok))).first()
    if not row or row.revoked or now() > row.expires_at:
        return None
    return s.get(User, row.user_id)


def token_hash(tok: str) -> str:
    return _hash("tok", tok)


def revoke_token(s: Session, tok: str) -> None:
    row = s.exec(select(AuthToken).where(AuthToken.token_hash == _hash("tok", tok))).first()
    if row:
        row.revoked = True
        s.add(row)
        s.commit()


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


# ------------------------------------------------------------------ FastAPI dependencies / guards
def current_user(authorization: str | None = Header(None), token: str | None = Query(None),
                 s: Session = Depends(get_session)) -> User:
    """Every protected route depends on this. Header for the app, ?token= for browser links."""
    u = user_for_token(s, bearer(authorization) or token)
    if not u:
        raise HTTPException(401, "Please verify your phone number again (not signed in).")
    return u


def require_self(me: User, user_id: str | None) -> None:
    if user_id and user_id != me.id:
        raise HTTPException(403, "You can only do this for your own account.")


def require_family(me: User, other: User | None) -> None:
    if other is None:
        raise HTTPException(404, "user not found")
    if other.id != me.id and (not me.family_id or other.family_id != me.family_id):
        raise HTTPException(403, "That person is not in your family circle.")
