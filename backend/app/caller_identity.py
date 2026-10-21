"""Caller Identity & Verification - an isolated, additive module.

Answers "who is this number?" from allowed sources and says how far each answer can be trusted. It is separate from
the voice AI and from the existing scam scoring: nothing here feeds them; the existing number lookup is only read.

Sources, strongest first. Every result names its source; a weaker source is never presented as verified.
  verified_business  a business identity an administrator verified, with a note of how it was verified
  telecom            an authorised telecom identity provider - used only when one is configured
                     (VG_TELECOM_ID_URL / VG_TELECOM_ID_KEY, server side only). None is configured in this build,
                     so results say "not available". No private telecom / KYC database is ever accessed.
  voiceguard_user    the number was proven by OTP to a VoiceGuard account; the NAME is what that user typed
  community          names suggested by users: unverified, and "conflicting" when users disagree
Saved contacts and family are matched on the phone, offline, and are never uploaded.

Number verification (STIR/SHAKEN, Android Call.Details.getCallerNumberVerificationStatus) is read on the phone.
None of this says who is physically speaking: number, identity and AI-voice checks are separate assessments.
"""
import csv
import datetime as dt
import io
import os
import re
import threading
import time
import unicodedata

import httpx
from sqlmodel import Field, Session, SQLModel, func, select

from . import config  # noqa: F401  (loads .env before the settings below are read)
from .ai.number_info import normalize
from .db import CallerIdOptOut, User, now

ADMIN_PHONES = {normalize(p) for p in os.getenv("VG_CALLER_ID_ADMIN_PHONES", "").split(",") if p.strip()}
RETENTION_DAYS = int(os.getenv("VG_CALLER_ID_RETENTION_DAYS", "365"))      # user submissions
EVENT_RETENTION_DAYS = int(os.getenv("VG_CALLER_ID_EVENT_DAYS", "90"))     # API activity / error log
TELECOM_URL = os.getenv("VG_TELECOM_ID_URL", "").strip()
TELECOM_KEY = os.getenv("VG_TELECOM_ID_KEY", "").strip()
TELECOM_TIMEOUT_S = float(os.getenv("VG_TELECOM_ID_TIMEOUT", "2.0"))
COMMUNITY_MIN_USERS = 2   # an unreviewed community name is shown only when at least this many users agree


# ------------------------------------------------------------------ feature-specific tables (new; nothing existing altered)
class CidBusiness(SQLModel, table=True):
    """A business caller identity. Shown as verified only after an administrator verified it (and said how)."""
    __tablename__ = "cid_business"
    id: int | None = Field(default=None, primary_key=True)
    number: str = Field(index=True)
    name: str
    category: str = ""
    verified: bool = False
    verification_note: str = ""
    verified_by: str | None = None
    verified_at: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=now)


class CidSubmission(SQLModel, table=True):
    """A user's name correction or spam report made through this feature (moderated; not fed to other systems)."""
    __tablename__ = "cid_submission"
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)                    # name / spam
    number: str = Field(index=True)
    name: str | None = None
    reason: str = ""
    user_id: str | None = Field(default=None, index=True)   # None once anonymised
    status: str = Field(default="pending", index=True)      # pending / approved / rejected / flagged
    legal_hold: bool = False
    review_note: str = ""
    reviewed_by: str | None = None
    reviewed_at: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=now)


class CidEvent(SQLModel, table=True):
    """Feature API activity and errors (no names or contact data)."""
    __tablename__ = "cid_event"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    kind: str = Field(index=True)                    # lookup / submit / delete / error / denied
    user_id: str | None = None
    number: str | None = None
    status: int = 200
    detail: str = ""


class CidAudit(SQLModel, table=True):
    """Every administrative action, append-only."""
    __tablename__ = "cid_audit"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    admin_id: str
    action: str
    target: str = ""
    detail: str = ""


class CidDeletion(SQLModel, table=True):
    """A user's request to delete their submissions to this feature."""
    __tablename__ = "cid_deletion"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    requested_at: dt.datetime = Field(default_factory=now)
    status: str = "completed"
    deleted: int = 0
    kept_anonymised: int = 0


# ------------------------------------------------------------------ input checks
_BAD = set('<>{}[]\\/|`~^*=+$#@%;"')


def valid_number(raw: str | None) -> str | None:
    """Digits with optional +, spaces, dashes, dots or brackets only; 5-15 digits after normalising."""
    if not re.fullmatch(r"[\d+\-\s().]{5,25}", raw or ""):
        return None
    n = normalize(raw)
    return n if re.fullmatch(r"\+?\d{5,15}", n) else None


def clean_name(raw: str | None) -> str | None:
    """2-60 characters, any script, at least one letter, no markup or control characters."""
    name = re.sub(r"\s+", " ", (raw or "")).strip()
    if not 2 <= len(name) <= 60 or any(c in _BAD for c in name):
        return None
    if any(unicodedata.category(c).startswith("C") for c in name):
        return None
    if not any(unicodedata.category(c).startswith("L") for c in name):
        return None
    return name


def clean_text(raw: str | None, limit: int = 200) -> str:
    t = "".join(c for c in (raw or "") if not unicodedata.category(c).startswith("C"))
    return re.sub(r"\s+", " ", t).strip()[:limit]


# ------------------------------------------------------------------ rate limiting (per user, per action)
class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str, bucket: str, limit: int, per_s: int) -> bool:
        t = time.time()
        with self._lock:
            hits = [h for h in self._hits.get((user_id, bucket), []) if t - h < per_s]
            ok = len(hits) < limit
            if ok:
                hits.append(t)
            self._hits[(user_id, bucket)] = hits
            return ok


limiter = RateLimiter()


# ------------------------------------------------------------------ logging / audit
def log_event(s: Session, kind: str, user_id: str | None, number: str | None, status: int = 200, detail: str = ""):
    s.add(CidEvent(kind=kind, user_id=user_id, number=number, status=status, detail=detail[:200]))
    s.commit()


def audit(s: Session, admin: User, action: str, target: str = "", detail: str = ""):
    s.add(CidAudit(admin_id=admin.id, action=action, target=str(target)[:80], detail=detail[:300]))
    s.commit()


def is_admin(u: User) -> bool:
    return bool(ADMIN_PHONES) and normalize(u.phone) in ADMIN_PHONES


# ------------------------------------------------------------------ the authorised telecom source (adapter)
def telecom_lookup(number: str) -> dict:
    """Only an authorised provider configured on the server is ever asked; it must answer
    {"name": ..., "verified": true/false}. Without one, the answer is "not available"."""
    if not TELECOM_URL:
        return {"available": False, "reason": "No authorised telecom identity provider is configured."}
    try:
        r = httpx.get(TELECOM_URL, params={"number": number}, headers={"Authorization": f"Bearer {TELECOM_KEY}"},
                      timeout=TELECOM_TIMEOUT_S)
        r.raise_for_status()
        d = r.json()
        name = clean_name(d.get("name"))
        return {"available": True, "name": name, "verified": bool(d.get("verified")) and name is not None}
    except httpx.TimeoutException:
        return {"available": False, "reason": "The telecom identity provider did not answer in time."}
    except Exception:
        return {"available": False, "reason": "The telecom identity provider is unavailable."}


# ------------------------------------------------------------------ resolving an identity
def community_names(s: Session, number: str) -> list[dict]:
    """Names users suggested for this number: approved ones first, then the most supported."""
    rows = s.exec(select(CidSubmission).where(CidSubmission.number == number, CidSubmission.kind == "name",
                                              CidSubmission.status.in_(["pending", "approved"]))).all()
    names: dict[str, dict] = {}
    for r in rows:
        key = (r.name or "").casefold()
        e = names.setdefault(key, {"name": r.name, "users": set(), "approved": False})
        e["users"].add(r.user_id or f"anon-{r.id}")
        e["approved"] = e["approved"] or r.status == "approved"
    out = [{"name": e["name"], "supporters": len(e["users"]), "approved": e["approved"]} for e in names.values()]
    return sorted(out, key=lambda e: (not e["approved"], -e["supporters"]))


def resolve(s: Session, number: str) -> dict:
    """The best allowed identity for a number, with its source, status and confidence - or none."""
    biz = s.exec(select(CidBusiness).where(CidBusiness.number == number, CidBusiness.verified == True)  # noqa: E712
                 .order_by(CidBusiness.verified_at.desc())).first()
    telecom = telecom_lookup(number)
    identity = None
    if biz:
        identity = {"name": biz.name, "source": "verified_business", "status": "verified", "confidence": "high",
                    "detail": f"Business verified by VoiceGuard ({biz.category or 'business'}) on "
                              f"{biz.verified_at.date().isoformat() if biz.verified_at else '-'}."}
    elif telecom.get("available") and telecom.get("name"):
        identity = {"name": telecom["name"], "source": "telecom",
                    "status": "verified" if telecom.get("verified") else "unverified",
                    "confidence": "high" if telecom.get("verified") else "medium",
                    "detail": "Name from the authorised telecom identity provider."}
    else:
        user = s.exec(select(User).where(User.phone == number)).first()
        if user and not s.get(CallerIdOptOut, user.id):
            identity = {"name": user.name, "source": "voiceguard_user", "status": "number_confirmed",
                        "confidence": "medium",
                        "detail": "This number was confirmed by OTP to a VoiceGuard account. The name is what that "
                                  "user entered - it is not verified."}
        else:
            names = community_names(s, number)
            shown = [n for n in names if n["approved"] or n["supporters"] >= COMMUNITY_MIN_USERS]
            if shown:
                top = shown[0]
                conflict = len([n for n in names if n["supporters"] >= COMMUNITY_MIN_USERS or n["approved"]]) > 1
                identity = {"name": top["name"], "source": "community",
                            "status": "conflicting" if conflict else "unverified", "confidence": "low",
                            "detail": (f"Suggested by {top['supporters']} VoiceGuard user(s). Not verified."
                                       + (" Users suggested different names." if conflict else ""))}
    return {"number": number, "identity": identity, "telecom": telecom,
            "notice": "Caller identity is not proof of who is speaking. Use the AI voice check and family "
                      "verification for that."}


# ------------------------------------------------------------------ retention
_last_purge = [0.0]


def purge(s: Session, force: bool = False) -> dict:
    """Delete submissions older than RETENTION_DAYS (except legal hold) and events older than EVENT_RETENTION_DAYS."""
    if not force and time.time() - _last_purge[0] < 86_400:
        return {"skipped": True}
    _last_purge[0] = time.time()
    sub_cut = now() - dt.timedelta(days=RETENTION_DAYS)
    ev_cut = now() - dt.timedelta(days=EVENT_RETENTION_DAYS)
    old = s.exec(select(CidSubmission).where(CidSubmission.created_at < sub_cut, CidSubmission.legal_hold == False)).all()  # noqa: E712
    events = s.exec(select(CidEvent).where(CidEvent.at < ev_cut)).all()
    for r in old + events:
        s.delete(r)
    s.commit()
    return {"submissions_deleted": len(old), "events_deleted": len(events)}


def delete_user_submissions(s: Session, user_id: str) -> CidDeletion:
    """A user's deletion request: their submissions are deleted; ones under legal hold are kept anonymised."""
    rows = s.exec(select(CidSubmission).where(CidSubmission.user_id == user_id)).all()
    deleted = kept = 0
    for r in rows:
        if r.legal_hold:
            r.user_id = None
            s.add(r)
            kept += 1
        else:
            s.delete(r)
            deleted += 1
    req = CidDeletion(user_id=user_id, deleted=deleted, kept_anonymised=kept)
    s.add(req)
    s.commit()
    s.refresh(req)
    return req


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


def counts(s: Session) -> dict:
    def n(stmt):
        return s.exec(stmt).one()
    return {"pending": n(select(func.count()).select_from(CidSubmission).where(CidSubmission.status == "pending")),
            "flagged": n(select(func.count()).select_from(CidSubmission).where(CidSubmission.status == "flagged")),
            "businesses_verified": n(select(func.count()).select_from(CidBusiness).where(CidBusiness.verified == True)),  # noqa: E712
            "errors_24h": n(select(func.count()).select_from(CidEvent).where(
                CidEvent.kind == "error", CidEvent.at > now() - dt.timedelta(days=1)))}
