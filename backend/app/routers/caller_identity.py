"""Caller Identity & Verification API (new, namespaced; existing endpoints are not changed).

User:  /api/caller-identity/*          signed-in users, rate-limited
Admin: /api/admin/caller-identity/*    only accounts listed in VG_CALLER_ID_ADMIN_PHONES, checked on the server
Page:  /admin/caller-identity          the admin dashboard (signs in with the normal OTP login, then uses the admin API)
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, or_, select

from .. import caller_identity as cid
from ..auth import current_user
from ..db import User, get_session, now
from .checks import lookup_number
from .common import iso

router = APIRouter(tags=["caller-identity"])
ADMIN_PAGE = Path(__file__).resolve().parents[1] / "static" / "caller_identity_admin.html"


def _row(o) -> dict:
    # getattr (not model_dump) so attributes expired by a later commit (e.g. the audit entry) are re-loaded
    d = {k: getattr(o, k) for k in type(o).model_fields}
    return {k: iso(v) if hasattr(v, "isoformat") else v for k, v in d.items()}


def _limit(s: Session, me: User, bucket: str, limit: int, per_s: int):
    if not cid.limiter.allow(me.id, bucket, limit, per_s):
        cid.log_event(s, "error", me.id, None, 429, f"rate limit {bucket}")
        raise HTTPException(429, "Too many requests - please wait a little and try again.")


def _number(s: Session, me: User, raw: str) -> str:
    n = cid.valid_number(raw)
    if not n:
        cid.log_event(s, "error", me.id, None, 400, "malformed number")
        raise HTTPException(400, "That is not a valid phone number.")
    return n


# ------------------------------------------------------------------ users
@router.get("/api/caller-identity/lookup/{number}")
def lookup(number: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Identity (with source + status) and, separately, the existing spam risk (read only)."""
    _limit(s, me, "lookup", 120, 3600)
    n = _number(s, me, number)
    cid.purge(s)
    res = cid.resolve(s, n)
    try:
        sp = lookup_number(s, n, me)
        res["spam"] = {"score": sp["spam_score"], "label": sp.get("label"), "community_reports": sp["community_reports"]}
    except Exception:
        res["spam"] = None
    cid.log_event(s, "lookup", me.id, n, detail=(res["identity"] or {}).get("source", "unknown"))
    return res


class NameReport(BaseModel):
    number: str
    name: str
    note: str = ""


class SpamReportIn(BaseModel):
    number: str
    reason: str = ""


def _submit(s: Session, me: User, kind: str, number: str, name: str | None, reason: str) -> dict:
    # one open submission per user, number and kind: a repeat replaces it (no stuffing the queue)
    row = s.exec(select(cid.CidSubmission).where(cid.CidSubmission.user_id == me.id, cid.CidSubmission.number == number,
                                                 cid.CidSubmission.kind == kind, cid.CidSubmission.status == "pending")).first()
    row = row or cid.CidSubmission(kind=kind, number=number, user_id=me.id)
    row.name, row.reason, row.created_at = name, reason, now()
    s.add(row)
    s.commit()
    s.refresh(row)
    cid.log_event(s, "submit", me.id, number, detail=kind)
    return {"ok": True, "id": row.id, "status": row.status,
            "message": "Thank you - a moderator will review it. It is shown to others only as an unverified community "
                       "suggestion, never as a verified identity."}


@router.post("/api/caller-identity/report-name")
def report_name(body: NameReport, me: User = Depends(current_user), s: Session = Depends(get_session)):
    _limit(s, me, "submit", 20, 86_400)
    n = _number(s, me, body.number)
    name = cid.clean_name(body.name)
    if not name:
        cid.log_event(s, "error", me.id, n, 400, "bad name")
        raise HTTPException(400, "Please enter a real name (2-60 letters, no symbols).")
    return _submit(s, me, "name", n, name, cid.clean_text(body.note))


@router.post("/api/caller-identity/report-spam")
def report_spam(body: SpamReportIn, me: User = Depends(current_user), s: Session = Depends(get_session)):
    _limit(s, me, "submit", 20, 86_400)
    n = _number(s, me, body.number)
    return _submit(s, me, "spam", n, None, cid.clean_text(body.reason))


@router.get("/api/caller-identity/my-submissions")
def my_submissions(me: User = Depends(current_user), s: Session = Depends(get_session)):
    rows = s.exec(select(cid.CidSubmission).where(cid.CidSubmission.user_id == me.id)
                  .order_by(cid.CidSubmission.created_at.desc())).all()
    return [{"id": r.id, "kind": r.kind, "number": r.number, "name": r.name, "reason": r.reason, "status": r.status,
             "created_at": iso(r.created_at)} for r in rows]


@router.post("/api/caller-identity/my-submissions/delete")
def delete_my_submissions(me: User = Depends(current_user), s: Session = Depends(get_session)):
    _limit(s, me, "delete", 5, 86_400)
    req = cid.delete_user_submissions(s, me.id)
    cid.log_event(s, "delete", me.id, None, detail=f"deleted {req.deleted}, kept {req.kept_anonymised}")
    return {"ok": True, "deleted": req.deleted, "kept_anonymised": req.kept_anonymised,
            "message": "Your caller-ID submissions were deleted." + (
                f" {req.kept_anonymised} under a legal hold were kept without your name." if req.kept_anonymised else "")}


@router.get("/api/caller-identity/sources")
def sources(me: User = Depends(current_user)):
    return {"order": ["contact", "verified_business", "telecom", "voiceguard_user", "community", "unknown"],
            "telecom_configured": bool(cid.TELECOM_URL),
            "sources": {
                "contact": "Your own saved contact or family member - matched on your phone, works offline, never uploaded.",
                "verified_business": "A business identity VoiceGuard verified, with a record of how.",
                "telecom": "An authorised telecom identity provider." + ("" if cid.TELECOM_URL else " Not available yet."),
                "voiceguard_user": "The number was confirmed by OTP to a VoiceGuard account; the name is self-declared.",
                "community": "A name suggested by VoiceGuard users. Not verified.",
                "unknown": "No reliable identity. Unknown callers are never blocked for that alone."}}


# ------------------------------------------------------------------ administrators (server-side permission)
def admin_user(me: User = Depends(current_user), s: Session = Depends(get_session)) -> User:
    if not cid.is_admin(me):
        cid.log_event(s, "denied", me.id, None, 403, "admin endpoint")
        raise HTTPException(403, "Administrators only.")
    _limit(s, me, "admin", 600, 3600)
    return me


A = "/api/admin/caller-identity"


@router.get(A + "/me")
def admin_me(admin: User = Depends(admin_user)):
    return {"admin": True, "name": admin.name, "phone": admin.phone}


@router.get(A + "/summary")
def admin_summary(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    return cid.counts(s) | {"telecom_configured": bool(cid.TELECOM_URL), "retention_days": cid.RETENTION_DAYS}


@router.get(A + "/records")
def admin_records(q: str = "", admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    q = cid.clean_text(q, 40)
    cid.audit(s, admin, "search", q)
    if not q:
        return {"businesses": [], "submissions": []}
    like = f"%{q}%"
    biz = s.exec(select(cid.CidBusiness).where(or_(cid.CidBusiness.number.like(like), cid.CidBusiness.name.like(like))).limit(100)).all()
    sub = s.exec(select(cid.CidSubmission).where(or_(cid.CidSubmission.number.like(like), cid.CidSubmission.name.like(like)))
                 .order_by(cid.CidSubmission.created_at.desc()).limit(200)).all()
    n = cid.valid_number(q)
    return {"businesses": [_row(b) for b in biz], "submissions": [_row(r) for r in sub],
            "resolved": cid.resolve(s, n) if n else None}


@router.get(A + "/submissions")
def admin_submissions(status: str = "pending", kind: str = "", admin: User = Depends(admin_user),
                      s: Session = Depends(get_session)):
    stmt = select(cid.CidSubmission).where(cid.CidSubmission.status == status)
    if kind:
        stmt = stmt.where(cid.CidSubmission.kind == kind)
    return [_row(r) for r in s.exec(stmt.order_by(cid.CidSubmission.created_at).limit(300)).all()]


class Review(BaseModel):
    action: str             # approve / reject / flag / correct
    name: str | None = None
    note: str = ""
    legal_hold: bool | None = None


@router.post(A + "/submissions/{sid}/review")
def admin_review(sid: int, body: Review, admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    r = s.get(cid.CidSubmission, sid)
    if not r:
        raise HTTPException(404, "submission not found")
    if body.action not in ("approve", "reject", "flag", "correct"):
        raise HTTPException(400, "action must be approve, reject, flag or correct")
    if body.action == "correct":
        name = cid.clean_name(body.name)
        if r.kind != "name" or not name:
            raise HTTPException(400, "a valid corrected name is needed")
        r.name, r.status = name, "approved"
    else:
        r.status = {"approve": "approved", "reject": "rejected", "flag": "flagged"}[body.action]
    if body.legal_hold is not None:
        r.legal_hold = body.legal_hold
    r.review_note, r.reviewed_by, r.reviewed_at = cid.clean_text(body.note), admin.id, now()
    s.add(r)
    s.commit()
    cid.audit(s, admin, f"submission.{body.action}", sid, f"{r.kind} {r.number} -> {r.status}"
              + (" (legal hold)" if r.legal_hold else ""))
    return _row(r)


class NewBusiness(BaseModel):
    number: str
    name: str
    category: str = ""


class VerifyNote(BaseModel):
    note: str


@router.get(A + "/businesses")
def admin_businesses(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    return [_row(b) for b in s.exec(select(cid.CidBusiness).order_by(cid.CidBusiness.created_at.desc()).limit(300)).all()]


@router.post(A + "/businesses")
def admin_add_business(body: NewBusiness, admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    n, name = cid.valid_number(body.number), cid.clean_name(body.name)
    if not n or not name:
        raise HTTPException(400, "a valid number and business name are needed")
    b = cid.CidBusiness(number=n, name=name, category=cid.clean_text(body.category, 40))
    s.add(b)
    s.commit()
    s.refresh(b)
    cid.audit(s, admin, "business.add", b.id, f"{n} {name} (unverified)")
    return _row(b)


@router.post(A + "/businesses/{bid}/verify")
def admin_verify_business(bid: int, body: VerifyNote, admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    b = s.get(cid.CidBusiness, bid)
    if not b:
        raise HTTPException(404, "business not found")
    note = cid.clean_text(body.note, 300)
    if len(note) < 10:
        raise HTTPException(400, "describe how the business was verified (documents checked, call-back to the listed number, ...)")
    b.verified, b.verification_note, b.verified_by, b.verified_at = True, note, admin.id, now()
    s.add(b)
    s.commit()
    cid.audit(s, admin, "business.verify", bid, note)
    return _row(b)


@router.post(A + "/businesses/{bid}/unverify")
def admin_unverify_business(bid: int, body: VerifyNote, admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    b = s.get(cid.CidBusiness, bid)
    if not b:
        raise HTTPException(404, "business not found")
    b.verified = False
    s.add(b)
    s.commit()
    cid.audit(s, admin, "business.unverify", bid, cid.clean_text(body.note, 300))
    return _row(b)


@router.get(A + "/conflicts")
def admin_conflicts(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    numbers = s.exec(select(cid.CidSubmission.number).where(cid.CidSubmission.kind == "name",
                                                            cid.CidSubmission.status.in_(["pending", "approved"])).distinct()).all()
    out = []
    for n in numbers:
        names = cid.community_names(s, n)
        if len(names) > 1:
            out.append({"number": n, "names": names})
    return out


@router.get(A + "/events")
def admin_events(kind: str = "", limit: int = 200, admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    stmt = select(cid.CidEvent)
    if kind:
        stmt = stmt.where(cid.CidEvent.kind == kind)
    return [_row(e) for e in s.exec(stmt.order_by(cid.CidEvent.at.desc()).limit(max(1, min(limit, 1000)))).all()]


@router.get(A + "/deletions")
def admin_deletions(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    return [_row(d) for d in s.exec(select(cid.CidDeletion).order_by(cid.CidDeletion.requested_at.desc()).limit(300)).all()]


@router.post(A + "/retention/purge")
def admin_purge(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    res = cid.purge(s, force=True)
    cid.audit(s, admin, "retention.purge", "", str(res))
    return res


@router.get(A + "/export", response_class=PlainTextResponse)
def admin_export(what: str = "submissions", admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    table = {"submissions": cid.CidSubmission, "businesses": cid.CidBusiness, "audit": cid.CidAudit}.get(what)
    if not table:
        raise HTTPException(400, "what must be submissions, businesses or audit")
    rows = [_row(r) for r in s.exec(select(table)).all()]
    if what == "submissions":   # no account ids in exports
        for r in rows:
            r.pop("user_id", None)
    cid.audit(s, admin, "export", what, f"{len(rows)} rows")
    return PlainTextResponse(cid.to_csv(rows), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="caller-identity-{what}.csv"'})


@router.get(A + "/audit")
def admin_audit(admin: User = Depends(admin_user), s: Session = Depends(get_session)):
    return [_row(a) for a in s.exec(select(cid.CidAudit).order_by(cid.CidAudit.at.desc()).limit(500)).all()]


@router.get("/admin/caller-identity", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    """The page itself holds no data; everything it shows comes from the admin API after sign-in."""
    return HTMLResponse(ADMIN_PAGE.read_text(encoding="utf-8"))
