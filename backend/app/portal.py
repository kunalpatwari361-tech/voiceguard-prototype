"""VoiceGuard Team Portal - confidential maintenance portal for the team (core: accounts, sign-in, roles, audit).

Sign-in has two steps: the normal VoiceGuard OTP to the member's phone, then a 6-digit code from an authenticator
app (TOTP, RFC 6238). Portal sessions are separate from app logins, short (30 min idle / 8 h max), stored only as a
hash, and revocable. Every view of sensitive data and every change is written to the portal audit log. Failed
sign-ins feed the castle security (layer 2) and lock the account after repeated failures.

Owners come from VG_PORTAL_OWNERS (default: VG_ADMIN_PHONES); owners add the other members and their roles.
"""
import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import os
import secrets
import struct
import time
import urllib.parse

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlmodel import Field, Session, SQLModel, select

from . import config, security as sec
from .ai.number_info import normalize
from .db import engine, get_session, now

_ALL = {"dashboard", "fraud", "evidence", "telecom", "network", "ai", "security", "team", "audit", "reports", "logs", "pii"}
ROLES = {   # reports: everyone, but each part of a report only for the roles that may open that section
    "owner": set(_ALL),
    "admin": set(_ALL),
    "fraud_analyst": {"dashboard", "fraud", "evidence", "telecom", "network", "reports", "logs", "pii"},
    "security": {"dashboard", "security", "telecom", "network", "audit", "reports", "logs"},
    "ai_engineer": {"dashboard", "ai", "network", "reports", "logs"},
    "viewer": {"dashboard", "reports"},
}
ROLE_NAMES = {"owner": "Owner", "admin": "Admin", "fraud_analyst": "Fraud analyst", "security": "Security",
              "ai_engineer": "AI engineer", "viewer": "Viewer"}
OWNERS = {normalize(p) for p in (os.getenv("VG_PORTAL_OWNERS") or os.getenv("VG_ADMIN_PHONES", "")).split(",") if p.strip()}
IDLE_MIN = int(os.getenv("VG_PORTAL_IDLE_MIN", "30"))
MAX_HOURS = int(os.getenv("VG_PORTAL_MAX_HOURS", "8"))
LOCK_AFTER, LOCK_MIN = 5, 30


# ------------------------------------------------------------------ tables (new)
class TeamMember(SQLModel, table=True):
    __tablename__ = "portal_member"
    id: int | None = Field(default=None, primary_key=True)
    phone: str = Field(index=True, unique=True)
    name: str
    role: str = "viewer"
    active: bool = True
    totp_sealed: str | None = None
    totp_confirmed: bool = False
    totp_last_step: int = 0
    failed: int = 0
    locked_until: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=now)
    created_by: str = ""
    last_login: dt.datetime | None = None


class PortalSession(SQLModel, table=True):
    __tablename__ = "portal_session"
    id: int | None = Field(default=None, primary_key=True)
    member_id: int = Field(index=True)
    token_hash: str = Field(index=True, unique=True)
    scope: str = "full"                         # full / enroll (2-step setup not finished)
    created_at: dt.datetime = Field(default_factory=now)
    last_used: dt.datetime = Field(default_factory=now)
    expires_at: dt.datetime
    ip: str = ""
    agent: str = ""
    revoked: bool = False


class PortalAudit(SQLModel, table=True):
    __tablename__ = "portal_audit"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    member_id: int | None = None
    member: str = ""
    action: str
    target: str = ""
    detail: str = ""
    ip: str = ""


# ------------------------------------------------------------------ authenticator codes (TOTP, RFC 6238)
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, t: float, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", int(t // step)), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_check(m: TeamMember, code: str) -> bool:
    """Accepts the current code or one step either side; a code can be used only once (no replay)."""
    secret = unseal(m)
    code = "".join(ch for ch in code or "" if ch.isdigit())
    if not secret or len(code) != 6:
        return False
    t = time.time()
    for d in (-1, 0, 1):
        step = int(t // 30) + d
        if step > m.totp_last_step and hmac.compare_digest(totp_at(secret, step * 30), code):
            m.totp_last_step = step
            return True
    return False


def otpauth_url(m: TeamMember, secret: str) -> str:
    label = urllib.parse.quote(f"VoiceGuard Portal:{m.name}")
    return f"otpauth://totp/{label}?secret={secret}&issuer=VoiceGuard%20Portal&digits=6&period=30"


def qr_svg(text: str) -> str:
    import segno
    # omitsize -> a viewBox, so the page can scale the code without cutting off its edges; low error correction
    # = fewer, bigger squares (easier for a phone camera)
    return segno.make(text, error="l").svg_inline(scale=1, dark="#000000", light="#ffffff", omitsize=True)


# secrets are stored sealed with a key derived from the server secret (useless without it)
def _stream(member_id: int, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(config.SECRET, f"totp:{member_id}:{i}".encode(), hashlib.sha256).digest()
        i += 1
    return out[:n]


def seal(m: TeamMember, secret: str):
    raw = secret.encode()
    m.totp_sealed = base64.b64encode(bytes(a ^ b for a, b in zip(raw, _stream(m.id, len(raw))))).decode()


def unseal(m: TeamMember) -> str | None:
    if not m.totp_sealed:
        return None
    raw = base64.b64decode(m.totp_sealed)
    return bytes(a ^ b for a, b in zip(raw, _stream(m.id, len(raw)))).decode()


# ------------------------------------------------------------------ sessions
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    return sec.real_ip(request.client.host if request.client else "?", request.headers)


def new_session(s: Session, m: TeamMember, request: Request, scope: str = "full") -> str:
    token = secrets.token_urlsafe(32)
    s.add(PortalSession(member_id=m.id, token_hash=_h(token), scope=scope, ip=client_ip(request),
                        agent=(request.headers.get("user-agent") or "")[:160],
                        expires_at=now() + (dt.timedelta(minutes=15) if scope == "enroll" else dt.timedelta(hours=MAX_HOURS))))
    s.commit()
    return token


def ensure_owners():
    with Session(engine) as s:
        for p in OWNERS:
            if not s.exec(select(TeamMember).where(TeamMember.phone == p)).first():
                s.add(TeamMember(phone=p, name="Owner", role="owner", created_by="VG_PORTAL_OWNERS"))
        s.commit()


def audit(s: Session, m: TeamMember | None, action: str, target: str = "", detail: str = "", ip: str = ""):
    s.add(PortalAudit(member_id=m.id if m else None, member=m.name if m else "", action=action, target=str(target)[:120],
                      detail=detail[:400], ip=ip))
    s.commit()


def no_store(response: Response):
    """Confidential: never cached by browsers or proxies."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"


class Ctx:
    def __init__(self, member: TeamMember, session: PortalSession, ip: str):
        self.m, self.sess, self.ip = member, session, ip

    def can(self, perm: str) -> bool:
        return perm in ROLES.get(self.m.role, set())


def _current(request: Request, authorization: str | None, s: Session, allow_enroll: bool) -> Ctx:
    guard(request)
    tok =authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    if not tok:
        raise HTTPException(401, "Please sign in to the team portal.")
    sess = s.exec(select(PortalSession).where(PortalSession.token_hash == _h(tok))).first()
    t = now()
    if not sess or sess.revoked or sess.expires_at < t or (t - sess.last_used) > dt.timedelta(minutes=IDLE_MIN):
        raise HTTPException(401, "Your portal session has ended. Please sign in again.")
    if sess.scope != "full" and not allow_enroll:
        raise HTTPException(403, "Finish setting up your authenticator app first.")
    m = s.get(TeamMember, sess.member_id)
    if not m or not m.active or (m.locked_until and m.locked_until > t):
        raise HTTPException(401, "This team account is not active.")
    if (t - sess.last_used) > dt.timedelta(seconds=60):
        sess.last_used = t
        s.add(sess)
        s.commit()
    return Ctx(m, sess, client_ip(request))


def member(request: Request, response: Response, authorization: str | None = Header(None), s: Session = Depends(get_session)) -> Ctx:
    no_store(response)
    return _current(request, authorization, s, allow_enroll=False)


def enrolling(request: Request, response: Response, authorization: str | None = Header(None), s: Session = Depends(get_session)) -> Ctx:
    no_store(response)
    return _current(request, authorization, s, allow_enroll=True)


def need(perm: str):
    def dep(ctx: Ctx = Depends(member), s: Session = Depends(get_session)) -> Ctx:
        if not ctx.can(perm):
            audit(s, ctx.m, "denied", perm, ip=ctx.ip)
            sec.record(3, "portal_forbidden", "medium", f"Team member {ctx.m.name} ({ROLE_NAMES.get(ctx.m.role)}) tried '{perm}'",
                       ip=ctx.ip, title="Team portal: access outside a member's role")
            raise HTTPException(403, "Your role does not include this section.")
        return ctx
    return dep


def failed_login(s: Session, m: TeamMember | None, ip: str, why: str):
    """Wrong OTP / authenticator code: counts toward the account lock, the address limit and the castle (layer 2)."""
    if sec.win.hit("otp_ip", "portal:" + ip) == sec.LIMITS["otp_ip"][0]:
        sec.block("ip", ip, sec.BAN_MIN, "team portal sign-in guessing")
        sec.record(2, "portal_guessing", "high", f"{sec.LIMITS['otp_ip'][0]} failed team portal sign-ins from one address",
                   ip=ip, action=f"address blocked for {sec.BAN_MIN} min", title="Team portal sign-in guessing")
    if m:
        m.failed += 1
        if m.failed >= LOCK_AFTER:
            m.locked_until, m.failed = now() + dt.timedelta(minutes=LOCK_MIN), 0
            s.add(m)
            s.commit()
            sec.record(2, "portal_account_locked", "high", f"Team account {m.name} locked after {LOCK_AFTER} failed sign-ins ({why})",
                       ip=ip, action=f"account locked {LOCK_MIN} min", title="Team portal account locked")
            return
        s.add(m)
        s.commit()
    sec.record(2, "portal_login_failed", "low", f"Team portal sign-in failed: {why}", ip=ip)


def mask(phone: str | None, show: bool) -> str:
    if not phone or show or len(phone) < 9:
        return phone or ""
    return phone[:5] + "•••" + phone[-4:]


# ------------------------------------------------------------------ who may reach the portal at all
ALLOW_NETS = [ipaddress.ip_network(n.strip(), strict=False) for n in os.getenv("VG_PORTAL_ALLOW_IPS", "").split(",") if n.strip()]
ALLOW_HTTP = os.getenv("VG_PORTAL_ALLOW_HTTP", "0") == "1"
VIA_TUNNEL = os.getenv("VG_PORTAL_VIA_TUNNEL", "0") == "1"    # the public Cloudflare tunnel is for the app, not the portal
LOCAL = ("127.0.0.1", "::1", "localhost", "testclient")


def reachable(request: Request) -> str | None:
    """None if this request may use the portal, else why not. Off this computer the portal needs HTTPS (a sniffed
    session would expose everything) and, when VG_PORTAL_ALLOW_IPS is set, an address from that list."""
    ip = client_ip(request)
    if sec.via_tunnel(request.client.host if request.client else "?", request.headers):
        return None if VIA_TUNNEL and (not ALLOW_NETS or _allowed(ip)) else "address not allowed"
    if ip in LOCAL:
        return None
    if ALLOW_NETS and not _allowed(ip):
        return "address not allowed"
    proto = request.headers.get("x-forwarded-proto", "") if sec.TRUST_PROXY else ""
    if not ALLOW_HTTP and (proto or request.url.scheme) != "https":
        return "plain http"
    return None


def _allowed(ip: str) -> bool:
    try:
        return any(ipaddress.ip_address(ip) in n for n in ALLOW_NETS)
    except ValueError:
        return False


def guard(request: Request):
    why = reachable(request)
    if why == "address not allowed":
        sec.record(1, "portal_outside_address", "medium", f"Team portal opened from an address that is not allowed ({request.url.path})",
                   ip=client_ip(request), path=request.url.path, action="refused", title="Team portal: unknown address")
        raise HTTPException(404, "Not Found")
    if why == "plain http":
        raise HTTPException(403, "The team portal only works over HTTPS (or on the server computer itself).")


# ------------------------------------------------------------------ evidence cases, telecom partners (new tables)
CASE_STATES = ("new", "reviewing", "sent_to_police", "closed")


class EvidenceCase(SQLModel, table=True):
    __tablename__ = "portal_evidence_case"
    evidence_id: str = Field(primary_key=True)
    status: str = Field(default="new", index=True)
    assigned_to: str = ""
    notes: str = "[]"                           # JSON [{at, by, text}]
    updated_at: dt.datetime = Field(default_factory=now)


class TelecomPartner(SQLModel, table=True):
    __tablename__ = "portal_telecom_partner"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    key_hash: str = Field(index=True, unique=True)
    key_hint: str = ""
    active: bool = True
    created_at: dt.datetime = Field(default_factory=now)
    created_by: str = ""
    last_used: dt.datetime | None = None
    calls: int = 0


class TelecomEvent(SQLModel, table=True):
    __tablename__ = "portal_telecom_event"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    partner: str = ""
    kind: str = ""                              # flag / lookup / bad_key
    number: str = ""
    detail: str = ""
    ip: str = ""


def new_partner_key() -> str:
    return "vgtel_" + secrets.token_urlsafe(30)


def partner_key_hash(key: str) -> str:
    return hmac.new(config.SECRET, ("telecom|" + key).encode(), hashlib.sha256).hexdigest()


def telecom_partner(s: Session, key: str | None) -> TelecomPartner | None:
    """The active partner behind an API key (keys are stored only as a keyed hash)."""
    if not key:
        return None
    p = s.exec(select(TelecomPartner).where(TelecomPartner.key_hash == partner_key_hash(key))).first()
    return p if p and p.active else None


def telecom_log(s: Session, partner: str, kind: str, number: str = "", detail: str = "", ip: str = ""):
    s.add(TelecomEvent(partner=partner, kind=kind, number=number, detail=detail[:300], ip=ip))
    s.commit()
