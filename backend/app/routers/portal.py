"""VoiceGuard Team Portal (confidential): run the service from one place - AI, security, evidence, telecom data, fraud.

Page: /portal (holds no data; strict content-security policy, never cached). API: /api/portal/* - team members only,
signed in with phone OTP + authenticator code, every request checked against the member's role. Sensitive views,
exports and every change go to the portal audit log; failures go to the castle security and alert the admins.
"""
import asyncio
import collections
import csv
import datetime as dt
import io
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, func, select

from .. import auth, daily_report, portal as P, security as sec
from ..ai import models
from ..ai.audio_io import load_audio
from ..ai.number_info import normalize
from ..caller_identity import valid_number
from ..ai.pipeline import analyze_voice
from ..config import EVIDENCE_DIR
from ..db import Evidence, Family, RiskEvent, ScamReport, ScamVoice, User, get_session, now
from .app_reports import AppCrash, AppDevice, RISKS
from .common import iso

router = APIRouter(tags=["team-portal"], include_in_schema=False)   # not listed in the public API docs
STATIC = Path(__file__).resolve().parents[1] / "static"
A = "/api/portal"
GENERIC = "Sign-in failed. Check the codes and try again. If it keeps failing, ask a portal owner."
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "media-src blob:; connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff", "Cross-Origin-Opener-Policy": "same-origin", "X-Robots-Tag": "noindex, nofollow",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


def _row(o) -> dict:
    return {k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in ((k, getattr(o, k)) for k in type(o).model_fields)}


def _static(name: str, media: str, request: Request) -> Response:
    P.guard(request)
    return Response((STATIC / name).read_bytes(), media_type=media, headers=HEADERS)


@router.get("/portal", response_class=HTMLResponse)
def page(request: Request):
    return _static("portal.html", "text/html; charset=utf-8", request)


@router.get("/portal/app.js")
def page_js(request: Request):
    return _static("portal.js", "application/javascript; charset=utf-8", request)


@router.get("/portal/app.css")
def page_css(request: Request):
    return _static("portal.css", "text/css; charset=utf-8", request)


# ------------------------------------------------------------------ sign-in
class LoginStart(BaseModel):
    phone: str


class LoginVerify(BaseModel):
    phone: str
    otp: str
    code: str = ""                 # authenticator app (empty the first time: set-up follows)


class Code(BaseModel):
    code: str


def _member(s: Session, phone: str) -> P.TeamMember | None:
    try:
        n = normalize(phone)
    except Exception:
        return None
    return s.exec(select(P.TeamMember).where(P.TeamMember.phone == n)).first()


def _usable(m: P.TeamMember | None) -> bool:
    return bool(m and m.active and not (m.locked_until and m.locked_until > now()))


@router.post(A + "/login/start")
def login_start(body: LoginStart, request: Request, s: Session = Depends(get_session)):
    """Sends the phone code to team members only. The answer is the same for every number (no member list leak)."""
    P.guard(request)
    P.ensure_owners()
    ip = P.client_ip(request)
    m = _member(s, body.phone)
    if _usable(m):
        try:
            auth.send_otp(s, m.phone)
        except HTTPException as e:
            sec.log.info("team portal code not sent for member %s: %s", m.id, e.detail)
    else:
        sec.record(2, "portal_non_member", "medium" if not m else "low",
                   f"Team portal sign-in asked for {P.mask(normalize(body.phone) if valid_number(body.phone) else body.phone[:16], False)}"
                   f" - {'not a team member' if not m else 'account inactive or locked'}",
                   ip=ip, action="no code sent", title="Team portal: sign-in by someone outside the team")
    return {"sent": True, "message": "If this number belongs to a team member, a code is on its way by SMS. "
                                     "Wait a minute before asking again."}


@router.post(A + "/login/verify")
def login_verify(body: LoginVerify, request: Request, s: Session = Depends(get_session)):
    P.guard(request)
    ip = P.client_ip(request)
    m = _member(s, body.phone)
    if not _usable(m):
        P.failed_login(s, m, ip, "not a team member" if not m else "account inactive or locked")
        raise HTTPException(400, GENERIC)
    if m.totp_confirmed:
        if not body.code.strip():
            raise HTTPException(400, "Enter the 6-digit code from your authenticator app.")
        if not P.totp_check(m, body.code):      # checked first: a wrong one does not use up the SMS code
            P.failed_login(s, m, ip, "wrong authenticator code")
            raise HTTPException(400, GENERIC)
        s.add(m)
        s.commit()
    try:
        auth.check_otp(s, m.phone, body.otp)
    except HTTPException:
        P.failed_login(s, m, ip, "wrong or expired phone code")
        raise HTTPException(400, GENERIC)
    m.failed, m.last_login = 0, now()
    s.add(m)
    s.commit()
    if m.totp_confirmed:
        token = P.new_session(s, m, request)
        P.audit(s, m, "login", ip=ip)
        return {"token": token, "stage": "ready"}
    # first sign-in (or after a reset): set up the authenticator app before anything else
    secret = P.new_secret()
    P.seal(m, secret)
    m.totp_last_step = 0
    s.add(m)
    s.commit()
    token = P.new_session(s, m, request, scope="enroll")
    P.audit(s, m, "2-step setup started", ip=ip)
    url = P.otpauth_url(m, secret)
    return {"token": token, "stage": "setup", "qr_svg": P.qr_svg(url), "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))}


@router.post(A + "/totp/confirm")
def totp_confirm(body: Code, request: Request, ctx: P.Ctx = Depends(P.enrolling), s: Session = Depends(get_session)):
    m = s.get(P.TeamMember, ctx.m.id)
    if ctx.sess.scope != "enroll" or m.totp_confirmed:
        raise HTTPException(400, "The authenticator app is already set up.")
    if not P.totp_check(m, body.code):
        P.failed_login(s, m, ctx.ip, "wrong code while setting up the authenticator")
        raise HTTPException(400, "That code did not match. Check the phone's time is automatic and try the newest code.")
    m.totp_confirmed = True
    s.add(m)
    sess = s.get(P.PortalSession, ctx.sess.id)
    sess.revoked = True
    s.add(sess)
    s.commit()
    P.audit(s, m, "2-step set up", ip=ctx.ip)
    return {"token": P.new_session(s, m, request), "stage": "ready"}


@router.post(A + "/logout")
def logout(ctx: P.Ctx = Depends(P.enrolling), s: Session = Depends(get_session)):
    sess = s.get(P.PortalSession, ctx.sess.id)
    sess.revoked = True
    s.add(sess)
    s.commit()
    P.audit(s, ctx.m, "logout", ip=ctx.ip)
    return {"ok": True}


@router.get(A + "/me")
def me(ctx: P.Ctx = Depends(P.member)):
    return {"name": ctx.m.name, "phone": ctx.m.phone, "role": ctx.m.role, "role_name": P.ROLE_NAMES.get(ctx.m.role, ctx.m.role),
            "can": sorted(P.ROLES.get(ctx.m.role, set())), "idle_minutes": P.IDLE_MIN,
            "session_ends": iso(ctx.sess.expires_at), "manager": _manager(ctx)}


def _manager(ctx: P.Ctx) -> bool:
    return ctx.m.role in ("owner", "admin")


def _need_manager(ctx: P.Ctx, s: Session, what: str):
    if not _manager(ctx):
        P.audit(s, ctx.m, "denied", what, ip=ctx.ip)
        raise HTTPException(403, "Only an owner or admin can do this.")


# ------------------------------------------------------------------ dashboard
def _count(s: Session, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return s.exec(stmt).one()


def _report(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@router.get(A + "/dashboard")
def dashboard(ctx: P.Ctx = Depends(P.need("dashboard")), s: Session = Depends(get_session)):
    t = now()
    day, week = t - dt.timedelta(days=1), t - dt.timedelta(days=7)
    start = (t - dt.timedelta(days=13)).date()
    series = {(start + dt.timedelta(days=i)).isoformat(): {"danger": 0, "suspicious": 0, "reports": 0} for i in range(14)}
    for when, level in s.exec(select(RiskEvent.created_at, RiskEvent.level).where(RiskEvent.created_at >= dt.datetime.combine(start, dt.time(), dt.timezone.utc))).all():
        d = series.get(when.date().isoformat())
        if d and level in ("danger", "suspicious"):
            d[level] += 1
    for when in s.exec(select(ScamReport.created_at).where(ScamReport.created_at >= dt.datetime.combine(start, dt.time(), dt.timezone.utc))).all():
        d = series.get(when.date().isoformat())
        if d:
            d["reports"] += 1
    closed = {c.evidence_id for c in s.exec(select(P.EvidenceCase).where(P.EvidenceCase.status == "closed")).all()}
    det = _report(models.WEIGHTS.with_name("vg_detector_report.json"))
    open_inc = s.exec(select(sec.SecIncident).where(sec.SecIncident.status != "resolved")).all()
    return {
        "people": {"users": _count(s, User), "families": _count(s, Family), "phones": _count(s, AppDevice),
                   "phones_active_24h": _count(s, AppDevice, AppDevice.last_report > day)},
        "fraud": {"reported_numbers": s.exec(select(func.count(func.distinct(ScamReport.number)))).one(),
                  "reports_7d": _count(s, ScamReport, ScamReport.created_at > week),
                  "telecom_flags_7d": _count(s, ScamReport, ScamReport.source == "telecom", ScamReport.created_at > week),
                  "scam_voices": _count(s, ScamVoice),
                  "danger_calls_24h": _count(s, RiskEvent, RiskEvent.level == "danger", RiskEvent.created_at > day),
                  "checks_7d": _count(s, RiskEvent, RiskEvent.created_at > week)},
        "evidence": {"total": _count(s, Evidence), "open_cases": _count(s, Evidence) - len(closed)},
        "security": {"open_incidents": len(open_inc), "serious": sum(1 for i in open_inc if sec.SEVERITY[i.severity] >= 2),
                     "lockdown": sec.lockdown(), "integrity_clean": sec.integrity_status()["clean"],
                     "open_crashes": _count(s, AppCrash, AppCrash.status == "open")},
        "ai": {"detector_auc": det.get("auc_all"), "trained_clips": det.get("clips"), "ready": not models.starting_up(),
               "candidates": len(list(models.WEIGHTS.parent.glob("vg_detector*_candidate.npz")))},
        "telecom": {"partners": _count(s, P.TelecomPartner, P.TelecomPartner.active == True)},   # noqa: E712
        "series": [{"day": k} | v for k, v in series.items()], "time": iso(t)}


# ------------------------------------------------------------------ fraud & scam
@router.get(A + "/fraud/numbers")
def fraud_numbers(q: str = "", ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    stmt = select(ScamReport.number, func.count(), func.max(ScamReport.created_at)).group_by(ScamReport.number)
    if q.strip():
        stmt = stmt.where(ScamReport.number.contains("".join(ch for ch in q if ch.isdigit() or ch == "+")))
    rows = s.exec(stmt.order_by(func.count().desc()).limit(300)).all()
    out = []
    for n, c, last in rows:
        reps = s.exec(select(ScamReport).where(ScamReport.number == n).order_by(ScamReport.created_at.desc()).limit(20)).all()
        out.append({"number": n, "reports": c, "last": iso(last), "sources": sorted({r.source for r in reps}),
                    "reason": next((r.reason for r in reps if r.reason), ""),
                    "label": "Suspected fraud" if c >= 2 else "Unverified"})
    return out


@router.get(A + "/fraud/reports")
def fraud_reports(number: str = "", source: str = "", ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    stmt = select(ScamReport)
    if number:
        stmt = stmt.where(ScamReport.number == normalize(number))
    if source:
        stmt = stmt.where(ScamReport.source == source)
    return [_row(r) | {"reporter_id": None} for r in s.exec(stmt.order_by(ScamReport.created_at.desc()).limit(300)).all()]


class Flag(BaseModel):
    number: str
    reason: str


@router.post(A + "/fraud/flag")
def fraud_flag(body: Flag, ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    if not valid_number(body.number) or not body.reason.strip():
        raise HTTPException(400, "Give a valid phone number and a reason.")
    n = normalize(body.number)
    s.add(ScamReport(number=n, reason=f"[VoiceGuard team] {body.reason.strip()[:200]}", source="team"))
    s.commit()
    P.audit(s, ctx.m, "flagged number", n, body.reason, ip=ctx.ip)
    return {"ok": True, "number": n}


class Clear(BaseModel):
    reason: str


@router.post(A + "/fraud/numbers/{number}/clear")
def fraud_clear(number: str, body: Clear, ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    """False alarm: removes every report for the number (people will no longer be warned about it)."""
    if not body.reason.strip():
        raise HTTPException(400, "Say why this number is safe.")
    n = normalize(number)
    rows = s.exec(select(ScamReport).where(ScamReport.number == n)).all()
    for r in rows:
        s.delete(r)
    s.commit()
    P.audit(s, ctx.m, "cleared number", n, f"{len(rows)} report(s) removed: {body.reason}", ip=ctx.ip)
    sec.record(3, "portal_number_cleared", "low", f"{ctx.m.name} cleared {n} ({len(rows)} reports): {body.reason[:80]}", ip=ctx.ip)
    return {"ok": True, "removed": len(rows)}


@router.get(A + "/fraud/voices")
def fraud_voices(ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    return [{k: v for k, v in _row(v).items() if k != "embedding"}
            for v in s.exec(select(ScamVoice).order_by(ScamVoice.last_seen.desc()).limit(300)).all()]


@router.get(A + "/fraud/risk")
def fraud_risk(level: str = "", days: int = 7, ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    stmt = select(RiskEvent).where(RiskEvent.created_at > now() - dt.timedelta(days=max(1, min(days, 90))))
    if level:
        stmt = stmt.where(RiskEvent.level == level)
    users = {u.id: u for u in s.exec(select(User)).all()}
    show = ctx.can("pii")
    return [_row(e) | {"user": users[e.user_id].name if e.user_id in users and show else "(hidden)",
                       "user_phone": P.mask(users[e.user_id].phone if e.user_id in users else "", show)}
            for e in s.exec(stmt.order_by(RiskEvent.created_at.desc()).limit(500)).all()]


@router.get(A + "/fraud/export.csv")
def fraud_export(ctx: P.Ctx = Depends(P.need("fraud")), s: Session = Depends(get_session)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["number", "reports", "sources", "last_report", "latest_reason"])
    for r in fraud_numbers("", ctx, s):
        w.writerow([r["number"], r["reports"], " ".join(r["sources"]), r["last"], r["reason"]])
    P.audit(s, ctx.m, "exported fraud numbers", ip=ctx.ip)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="voiceguard-fraud-numbers-{now():%Y%m%d}.csv"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ evidence
def _case(s: Session, eid: str) -> P.EvidenceCase:
    return s.get(P.EvidenceCase, eid) or P.EvidenceCase(evidence_id=eid)


@router.get(A + "/evidence")
def evidence_list(status: str = "", ctx: P.Ctx = Depends(P.need("evidence")), s: Session = Depends(get_session)):
    cases = {c.evidence_id: c for c in s.exec(select(P.EvidenceCase)).all()}
    users = {u.id: u for u in s.exec(select(User)).all()}
    out = []
    for e in s.exec(select(Evidence).order_by(Evidence.created_at.desc()).limit(500)).all():
        c = cases.get(e.id)
        st = c.status if c else "new"
        if status and st != status:
            continue
        u = users.get(e.user_id)
        out.append({"id": e.id, "created_at": iso(e.created_at), "number": e.number, "claimed_name": e.claimed_name,
                    "source": e.source, "risk_score": e.risk_score, "level": e.level, "has_audio": bool(e.audio_file),
                    "user": u.name if u else "?", "user_phone": P.mask(u.phone if u else "", ctx.can("pii")),
                    "status": st, "assigned_to": c.assigned_to if c else ""})
    return out


@router.get(A + "/evidence/{eid}")
def evidence_detail(eid: str, ctx: P.Ctx = Depends(P.need("evidence")), s: Session = Depends(get_session)):
    e = s.get(Evidence, eid)
    if not e:
        raise HTTPException(404, "Evidence not found")
    u = s.get(User, e.user_id)
    c = _case(s, eid)
    P.audit(s, ctx.m, "viewed evidence", eid, ip=ctx.ip)
    return {"id": e.id, "created_at": iso(e.created_at), "number": e.number, "claimed_name": e.claimed_name,
            "source": e.source, "risk_score": e.risk_score, "level": e.level, "transcript": e.transcript, "notes": e.notes,
            "report": json.loads(e.report_json or "{}"), "has_audio": bool(e.audio_file),
            "user": u.name if u else "?", "user_phone": P.mask(u.phone if u else "", ctx.can("pii")),
            "case": {"status": c.status, "assigned_to": c.assigned_to, "notes": json.loads(c.notes), "updated_at": iso(c.updated_at)}}


@router.get(A + "/evidence/{eid}/audio")
def evidence_audio(eid: str, ctx: P.Ctx = Depends(P.need("evidence")), s: Session = Depends(get_session)):
    e = s.get(Evidence, eid)
    if not e or not e.audio_file:
        raise HTTPException(404, "No recording for this evidence")
    path = (EVIDENCE_DIR / e.audio_file).resolve()
    if EVIDENCE_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "No recording for this evidence")
    P.audit(s, ctx.m, "listened to evidence", eid, ip=ctx.ip)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


class CaseUpdate(BaseModel):
    status: str | None = None
    assign_to_me: bool = False
    note: str = ""


@router.post(A + "/evidence/{eid}/case")
def evidence_case(eid: str, body: CaseUpdate, ctx: P.Ctx = Depends(P.need("evidence")), s: Session = Depends(get_session)):
    if not s.get(Evidence, eid):
        raise HTTPException(404, "Evidence not found")
    c = _case(s, eid)
    did = []
    if body.status:
        if body.status not in P.CASE_STATES:
            raise HTTPException(400, "status must be one of " + ", ".join(P.CASE_STATES))
        c.status = body.status
        did.append("status " + body.status)
    if body.assign_to_me:
        c.assigned_to = ctx.m.name
        did.append("assigned to " + ctx.m.name)
    if body.note.strip():
        notes = json.loads(c.notes)
        notes.append({"at": iso(now()), "by": ctx.m.name, "text": body.note.strip()[:1000]})
        c.notes = json.dumps(notes[-100:])
        did.append("note added")
    if not did:
        raise HTTPException(400, "Nothing to change")
    c.updated_at = now()
    s.add(c)
    s.commit()
    P.audit(s, ctx.m, "evidence case", eid, ", ".join(did), ip=ctx.ip)
    return {"status": c.status, "assigned_to": c.assigned_to, "notes": json.loads(c.notes), "updated_at": iso(c.updated_at)}


# ------------------------------------------------------------------ telecom
@router.get(A + "/telecom/flags")
def telecom_flags(ctx: P.Ctx = Depends(P.need("telecom")), s: Session = Depends(get_session)):
    return [_row(r) for r in s.exec(select(ScamReport).where(ScamReport.source == "telecom")
                                    .order_by(ScamReport.created_at.desc()).limit(300)).all()]


@router.get(A + "/telecom/partners")
def telecom_partners(ctx: P.Ctx = Depends(P.need("telecom")), s: Session = Depends(get_session)):
    return [{k: v for k, v in _row(p).items() if k != "key_hash"} for p in s.exec(select(P.TelecomPartner).order_by(P.TelecomPartner.created_at.desc())).all()]


class Partner(BaseModel):
    name: str


@router.post(A + "/telecom/partners")
def telecom_partner_add(body: Partner, ctx: P.Ctx = Depends(P.need("telecom")), s: Session = Depends(get_session)):
    _need_manager(ctx, s, "create telecom key")
    name = body.name.strip()[:60]
    if not name:
        raise HTTPException(400, "Give the operator's name")
    key = P.new_partner_key()
    p = P.TelecomPartner(name=name, key_hash=P.partner_key_hash(key), key_hint=key[:10] + "…" + key[-4:], created_by=ctx.m.name)
    s.add(p)
    s.commit()
    s.refresh(p)
    P.audit(s, ctx.m, "created telecom key", name, p.key_hint, ip=ctx.ip)
    sec.record(3, "portal_partner_key", "medium", f"{ctx.m.name} created a telecom API key for {name}", ip=ctx.ip,
               title="New telecom partner key created")
    return {"id": p.id, "name": p.name, "key": key, "note": "Copy this key now - it is shown only once."}


@router.post(A + "/telecom/partners/{pid}/revoke")
def telecom_partner_revoke(pid: int, ctx: P.Ctx = Depends(P.need("telecom")), s: Session = Depends(get_session)):
    _need_manager(ctx, s, "revoke telecom key")
    p = s.get(P.TelecomPartner, pid)
    if not p:
        raise HTTPException(404, "Partner not found")
    p.active = False
    s.add(p)
    s.commit()
    P.audit(s, ctx.m, "revoked telecom key", p.name, ip=ctx.ip)
    return {"ok": True}


@router.get(A + "/telecom/events")
def telecom_events(ctx: P.Ctx = Depends(P.need("telecom")), s: Session = Depends(get_session)):
    return [_row(e) for e in s.exec(select(P.TelecomEvent).order_by(P.TelecomEvent.at.desc()).limit(300)).all()]


# ------------------------------------------------------------------ AI
WDIR = models.WEIGHTS.parent
SLOTS = {"detector": "vg_detector", "room": "vg_detector_room"}   # normal checks / Live Call Check (loudspeaker)


def _report_for(npz: Path) -> dict:
    """vg_x_candidate.npz -> vg_x_candidate_report.json; older backups vg_x_v3_backup.npz -> vg_x_v3_report.json"""
    rep = _report(npz.with_name(npz.stem + "_report.json"))
    if not rep and npz.stem.endswith("_backup"):
        rep = _report(npz.with_name(npz.stem[:-len("_backup")] + "_report.json"))
    return rep


def _model_info(npz: Path) -> dict:
    rep = _report_for(npz)
    return {"file": npz.name, "updated": iso(dt.datetime.fromtimestamp(npz.stat().st_mtime, dt.timezone.utc)),
            "head": rep.get("head", "logreg"), "auc": rep.get("auc_all"), "clips": rep.get("clips"),
            "by_condition": rep.get("by_condition", {}), "activated_by": rep.get("activated_by"),
            "activated_from": rep.get("activated_from")}


@router.get(A + "/ai")
def ai_status(ctx: P.Ctx = Depends(P.need("ai")), s: Session = Depends(get_session)):
    out = {}
    for slot, stem in SLOTS.items():
        cur = WDIR / f"{stem}.npz"
        alts = sorted(p for p in WDIR.glob(f"{stem}_*.npz") if p.stem.endswith(("_candidate", "_backup"))
                      and (slot == "room" or not p.stem.startswith("vg_detector_room")))
        out[slot] = {"active": _model_info(cur) if cur.exists() else None, "choices": [_model_info(p) for p in alts]}
    week = now() - dt.timedelta(days=7)
    levels = dict(s.exec(select(RiskEvent.level, func.count()).where(RiskEvent.created_at > week).group_by(RiskEvent.level)).all())
    tracer = _report(WDIR / "vg_tracer_report.json")
    return {"slots": out, "checks_7d": levels, "ready": not models.starting_up(), "loaded": models.status(),
            "speaker_model": models.config.SPEAKER_MODEL, "speech_model": models.config.ASR_MODEL,
            "tracer": {"balanced_accuracy": tracer.get("balanced_accuracy"), "classes": tracer.get("classes")}}


@router.post(A + "/ai/test")
async def ai_test(file: UploadFile = File(...), ctx: P.Ctx = Depends(P.need("ai")), s: Session = Depends(get_session)):
    """Try a clip with the live AI (no transcript, nothing stored)."""
    if models.starting_up():
        raise HTTPException(503, "The AI is still starting. Try again in a few seconds.")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Clip too large (15 MB max)")
    try:
        y = load_audio(data)
    except Exception:
        raise HTTPException(400, "Could not read this audio file (use WAV, FLAC, OGG or MP3).")
    r = await asyncio.to_thread(analyze_voice, y, do_asr=False)
    daily_report.log_check("portal:" + ctx.m.name, "portal_test", r)
    P.audit(s, ctx.m, "tested a clip", (file.filename or "")[:80], ip=ctx.ip)
    if not r.get("ok"):
        return r
    return {"ok": True, "risk": r["risk"], "ai_voice": {k: r["ai_voice"].get(k) for k in ("fake_prob", "label", "model")},
            "reverse_engineering": r.get("reverse_engineering"), "seconds": len(y) / 16000, "timings_s": r.get("timings_s")}


class Activate(BaseModel):
    slot: str                  # detector / room
    file: str                  # one of the choices (candidate or backup)
    reason: str


@router.post(A + "/ai/activate")
def ai_activate(body: Activate, ctx: P.Ctx = Depends(P.need("ai")), s: Session = Depends(get_session)):
    """Switch the live AI model to a trained candidate (or roll back to a backup). The current model is kept as a
    backup, the change is approved as trusted at once (no tamper lockdown) and the admins are told."""
    _need_manager(ctx, s, "switch AI model")
    stem = SLOTS.get(body.slot)
    src = WDIR / Path(body.file).name
    if not stem or not body.reason.strip():
        raise HTTPException(400, "Choose the model and say why")
    if not src.exists() or not src.name.startswith(stem + "_") or not src.stem.endswith(("_candidate", "_backup")) \
            or (body.slot == "detector" and src.name.startswith("vg_detector_room")):
        raise HTTPException(400, "Unknown model file")
    cur, cur_rep = WDIR / f"{stem}.npz", WDIR / f"{stem}_report.json"
    stamp = now().strftime("%Y%m%d-%H%M%S")

    def change():
        if cur.exists():
            shutil.copy2(cur, WDIR / f"{stem}_{stamp}_backup.npz")
            if cur_rep.exists():
                shutil.copy2(cur_rep, WDIR / f"{stem}_{stamp}_backup_report.json")
        shutil.copy2(src, cur)
        rep = _report_for(src)
        rep |= {"activated_by": ctx.m.name, "activated_from": src.name, "activated_at": iso(now()), "reason": body.reason[:200]}
        cur_rep.write_text(json.dumps(rep, indent=1), encoding="utf-8")
        for k in [k for k in models._cache if k.startswith("deepfake:")]:
            models._cache.pop(k, None)
    sec.planned_change(f"{ctx.m.name} via team portal (AI model switch)", change)
    P.audit(s, ctx.m, "switched AI model", f"{body.slot}: {src.name}", body.reason, ip=ctx.ip)
    sec.record(6, "ai_model_switched", "medium", f"{ctx.m.name} switched the {body.slot} AI model to {src.name}: {body.reason[:80]}",
               ip=ctx.ip, action="approved by team member", title="AI model switched from the team portal")
    return {"ok": True, "active": _model_info(cur), "backup": f"{stem}_{stamp}_backup.npz"}


# ------------------------------------------------------------------ security
@router.get(A + "/security")
def security_view(ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    day = now() - dt.timedelta(days=1)
    layers = []
    for l in sec.LAYERS:
        open_inc = s.exec(select(sec.SecIncident).where(sec.SecIncident.layer == l["id"], sec.SecIncident.status != "resolved")).all()
        worst = max((sec.SEVERITY[i.severity] for i in open_inc), default=-1)
        layers.append(l | {"open_incidents": len(open_inc),
                           "events_24h": _count(s, sec.SecEvent, sec.SecEvent.layer == l["id"], sec.SecEvent.at > day),
                           "state": "breached" if worst >= 2 else "warning" if worst >= 1 else "secure"})
    incidents = s.exec(select(sec.SecIncident).where(sec.SecIncident.status != "resolved").order_by(sec.SecIncident.last_at.desc()).limit(100)).all()
    blocks = [b for b in s.exec(select(sec.SecBlock)).all() if b.until is None or b.until > now()]
    devices = s.exec(select(AppDevice)).all()
    risky = [d for d in devices if any(RISKS.get(r, ("low",))[0] != "low" for r in json.loads(d.risks))]
    return {"layers": layers, "lockdown": sec.lockdown(), "incidents": [_row(i) for i in incidents],
            "blocks": [{"id": b.id, "kind": b.kind, "value": b.value[:10] + "…" if b.kind == "token" else b.value,
                        "reason": b.reason, "until": iso(b.until), "by": b.by} for b in blocks],
            "integrity": sec.integrity_status(), "phones": {"total": len(devices), "at_risk": len(risky)},
            "app_trust_mode": sec.APP_TRUST_MODE}


class Lock(BaseModel):
    on: bool
    reason: str = ""


@router.post(A + "/security/lockdown")
def security_lockdown(body: Lock, ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    if body.on and not body.reason.strip():
        raise HTTPException(400, "Say why you are locking VoiceGuard down")
    sec.set_lockdown(body.on, body.reason.strip() or "", by=f"{ctx.m.name} (team portal)")
    sec.record(6, "portal_lockdown", "high" if body.on else "medium",
               f"{ctx.m.name} switched lockdown {'ON' if body.on else 'off'} from the team portal: {body.reason[:80]}",
               ip=ctx.ip, action="lockdown" if body.on else "lockdown lifted",
               title="Lockdown switched " + ("on" if body.on else "off") + " from the team portal")
    P.audit(s, ctx.m, "lockdown " + ("on" if body.on else "off"), detail=body.reason, ip=ctx.ip)
    return {"lockdown": sec.lockdown()}


@router.post(A + "/security/incidents/{iid}/{action}")
def security_incident(iid: int, action: str, ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    i = s.get(sec.SecIncident, iid)
    if not i or action not in ("acknowledge", "resolve", "reopen"):
        raise HTTPException(404 if not i else 400, "Incident not found" if not i else "Unknown action")
    i.status = {"acknowledge": "acknowledged", "resolve": "resolved", "reopen": "open"}[action]
    i.handled_by, i.handled_at = ctx.m.name + " (portal)", now()
    s.add(i)
    s.commit()
    P.audit(s, ctx.m, "incident " + action, str(iid), i.title, ip=ctx.ip)
    return _row(i)


@router.post(A + "/security/blocks/{bid}/remove")
def security_unblock(bid: int, ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    sec.unblock(bid)
    P.audit(s, ctx.m, "removed block", str(bid), ip=ctx.ip)
    return {"ok": True}


# ------------------------------------------------------------------ team
ROLE_RANK = {"viewer": 0, "ai_engineer": 1, "security": 1, "fraud_analyst": 1, "admin": 2, "owner": 3}


def _member_view(s: Session, m: P.TeamMember) -> dict:
    live = _count(s, P.PortalSession, P.PortalSession.member_id == m.id, P.PortalSession.revoked == False,   # noqa: E712
                  P.PortalSession.expires_at > now())
    return {"id": m.id, "name": m.name, "phone": m.phone, "role": m.role, "role_name": P.ROLE_NAMES.get(m.role, m.role),
            "active": m.active, "two_step": m.totp_confirmed, "locked": bool(m.locked_until and m.locked_until > now()),
            "last_login": iso(m.last_login), "created_by": m.created_by, "sessions": live}


def _revoke_all(s: Session, member_id: int) -> int:
    rows = s.exec(select(P.PortalSession).where(P.PortalSession.member_id == member_id, P.PortalSession.revoked == False)).all()  # noqa: E712
    for r in rows:
        r.revoked = True
        s.add(r)
    s.commit()
    return len(rows)


@router.get(A + "/team")
def team(ctx: P.Ctx = Depends(P.need("team")), s: Session = Depends(get_session)):
    return {"members": [_member_view(s, m) for m in s.exec(select(P.TeamMember).order_by(P.TeamMember.created_at)).all()],
            "roles": [{"key": k, "name": P.ROLE_NAMES[k], "can": sorted(v - {"pii"}), "sees_phone_numbers": "pii" in v}
                      for k, v in P.ROLES.items()]}


class NewMember(BaseModel):
    phone: str
    name: str
    role: str


def _may_grant(ctx: P.Ctx, role: str) -> bool:
    return role in P.ROLES and (ctx.m.role == "owner" or ROLE_RANK[role] < ROLE_RANK[ctx.m.role])


@router.post(A + "/team")
def team_add(body: NewMember, ctx: P.Ctx = Depends(P.need("team")), s: Session = Depends(get_session)):
    if not valid_number(body.phone) or not body.name.strip():
        raise HTTPException(400, "Give a valid phone number and a name.")
    if not _may_grant(ctx, body.role):
        raise HTTPException(403, "You cannot give this role.")
    n = normalize(body.phone)
    if _member(s, n):
        raise HTTPException(409, "This number is already a team member.")
    m = P.TeamMember(phone=n, name=body.name.strip()[:60], role=body.role, created_by=ctx.m.name)
    s.add(m)
    s.commit()
    s.refresh(m)
    P.audit(s, ctx.m, "added team member", f"{m.name} ({P.ROLE_NAMES[m.role]})", ip=ctx.ip)
    sec.record(3, "portal_member_added", "medium", f"{ctx.m.name} added {m.name} to the team portal as {P.ROLE_NAMES[m.role]}",
               ip=ctx.ip, title="Team portal: new member")
    return _member_view(s, m)


class EditMember(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None


@router.post(A + "/team/{mid}")
def team_edit(mid: int, body: EditMember, ctx: P.Ctx = Depends(P.need("team")), s: Session = Depends(get_session)):
    m = s.get(P.TeamMember, mid)
    if not m:
        raise HTTPException(404, "Member not found")
    if m.id == ctx.m.id and (body.role or body.active is not None):
        raise HTTPException(400, "You cannot change your own role or switch yourself off.")
    if not (ctx.m.role == "owner" or ROLE_RANK[m.role] < ROLE_RANK[ctx.m.role]):
        raise HTTPException(403, "You cannot change this member.")
    did = []
    if body.name and body.name.strip():
        m.name = body.name.strip()[:60]
        did.append("renamed")
    if body.role and body.role != m.role:
        if not _may_grant(ctx, body.role):
            raise HTTPException(403, "You cannot give this role.")
        if m.role == "owner" and _count(s, P.TeamMember, P.TeamMember.role == "owner", P.TeamMember.active == True) <= 1:   # noqa: E712
            raise HTTPException(400, "The portal needs at least one owner.")
        did.append(f"role {m.role} -> {body.role}")
        m.role = body.role
    if body.active is not None and body.active != m.active:
        m.active = body.active
        did.append("switched " + ("on" if m.active else "off"))
        if not m.active:
            _revoke_all(s, m.id)
    if not did:
        raise HTTPException(400, "Nothing to change")
    s.add(m)
    s.commit()
    P.audit(s, ctx.m, "changed team member", m.name, ", ".join(did), ip=ctx.ip)
    sec.record(3, "portal_member_changed", "medium", f"{ctx.m.name} changed {m.name}: {', '.join(did)}", ip=ctx.ip,
               title="Team portal: member changed")
    return _member_view(s, m)


@router.post(A + "/team/{mid}/{action}")
def team_action(mid: int, action: str, ctx: P.Ctx = Depends(P.need("team")), s: Session = Depends(get_session)):
    """reset-2fa (lost phone: set up the authenticator again), unlock, sign-out (end all sessions)."""
    m = s.get(P.TeamMember, mid)
    if not m or action not in ("reset-2fa", "unlock", "sign-out"):
        raise HTTPException(404 if not m else 400, "Member not found" if not m else "Unknown action")
    if m.id != ctx.m.id and not (ctx.m.role == "owner" or ROLE_RANK[m.role] < ROLE_RANK[ctx.m.role]):
        raise HTTPException(403, "You cannot change this member.")
    if action == "reset-2fa":
        m.totp_sealed, m.totp_confirmed, m.totp_last_step = None, False, 0
    if action == "unlock":
        m.locked_until, m.failed = None, 0
    s.add(m)
    s.commit()
    n = _revoke_all(s, m.id) if action in ("reset-2fa", "sign-out") else 0
    P.audit(s, ctx.m, action, m.name, f"{n} session(s) ended" if n else "", ip=ctx.ip)
    if action == "reset-2fa":
        sec.record(3, "portal_2fa_reset", "medium", f"{ctx.m.name} reset the authenticator of {m.name}", ip=ctx.ip,
                   title="Team portal: authenticator reset")
    return _member_view(s, m)


@router.get(A + "/sessions")
def sessions(ctx: P.Ctx = Depends(P.member), s: Session = Depends(get_session)):
    stmt = select(P.PortalSession).where(P.PortalSession.revoked == False, P.PortalSession.expires_at > now())   # noqa: E712
    if not ctx.can("team"):
        stmt = stmt.where(P.PortalSession.member_id == ctx.m.id)
    names = {m.id: m.name for m in s.exec(select(P.TeamMember)).all()}
    return [{"id": x.id, "member": names.get(x.member_id, "?"), "scope": x.scope, "started": iso(x.created_at),
             "last_used": iso(x.last_used), "ip": x.ip, "agent": x.agent, "this": x.id == ctx.sess.id}
            for x in s.exec(stmt.order_by(P.PortalSession.last_used.desc())).all()]


@router.post(A + "/sessions/{sid}/end")
def session_end(sid: int, ctx: P.Ctx = Depends(P.member), s: Session = Depends(get_session)):
    x = s.get(P.PortalSession, sid)
    if not x or (x.member_id != ctx.m.id and not ctx.can("team")):
        raise HTTPException(404, "Session not found")
    x.revoked = True
    s.add(x)
    s.commit()
    P.audit(s, ctx.m, "ended session", str(sid), ip=ctx.ip)
    return {"ok": True}


# ------------------------------------------------------------------ audit
@router.get(A + "/audit")
def audit_log(q: str = "", limit: int = 300, ctx: P.Ctx = Depends(P.need("audit")), s: Session = Depends(get_session)):
    stmt = select(P.PortalAudit)
    if q.strip():
        like = f"%{q.strip()[:40]}%"
        stmt = stmt.where(P.PortalAudit.member.like(like) | P.PortalAudit.action.like(like) | P.PortalAudit.target.like(like))
    return [_row(a) for a in s.exec(stmt.order_by(P.PortalAudit.at.desc()).limit(max(1, min(limit, 2000)))).all()]


# ------------------------------------------------------------------ logs
@router.get(A + "/logs/server")
def logs_server(lines: int = 300, q: str = "", ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    P.audit(s, ctx.m, "read server log", q[:40], ip=ctx.ip)
    return {"lines": sec.tail_log(max(20, min(lines, 2000)), q[:60])}


@router.get(A + "/logs/security")
def logs_security(layer: int = 0, severity: str = "", q: str = "", limit: int = 300,
                  ctx: P.Ctx = Depends(P.need("security")), s: Session = Depends(get_session)):
    stmt = select(sec.SecEvent)
    if layer:
        stmt = stmt.where(sec.SecEvent.layer == layer)
    if severity:
        stmt = stmt.where(sec.SecEvent.severity == severity)
    if q.strip():
        like = f"%{q.strip()[:40]}%"
        stmt = stmt.where(sec.SecEvent.kind.like(like) | sec.SecEvent.detail.like(like) | sec.SecEvent.ip.like(like))
    return [_row(e) for e in s.exec(stmt.order_by(sec.SecEvent.at.desc()).limit(max(1, min(limit, 2000)))).all()]


@router.get(A + "/logs/ai")
def logs_ai(source: str = "", level: str = "", problems: bool = False, limit: int = 300,
            ctx: P.Ctx = Depends(P.need("ai")), s: Session = Depends(get_session)):
    stmt = select(daily_report.AiCheck)
    if source:
        stmt = stmt.where(daily_report.AiCheck.source == source)
    if level:
        stmt = stmt.where(daily_report.AiCheck.level == level)
    if problems:
        stmt = stmt.where(daily_report.AiCheck.ok == False)   # noqa: E712
    users = {u.id: u.name for u in s.exec(select(User)).all()}
    show = ctx.can("pii")
    return [_row(c) | {"user": (users.get(c.user_id) or c.user_id) if show or c.user_id.startswith("portal:") else "(hidden)"}
            for c in s.exec(stmt.order_by(daily_report.AiCheck.at.desc()).limit(max(1, min(limit, 2000)))).all()]


@router.get(A + "/logs/evidence")
def logs_evidence(limit: int = 300, ctx: P.Ctx = Depends(P.need("evidence")), s: Session = Depends(get_session)):
    """New evidence from the phones and everything the team did with it, newest first."""
    limit = max(1, min(limit, 1000))
    users = {u.id: u.name for u in s.exec(select(User)).all()}
    out = [{"at": iso(e.created_at), "who": users.get(e.user_id, "?") if ctx.can("pii") else "(app user)", "what": "evidence saved",
            "item": e.id, "detail": f"{e.level or '?'} {e.risk_score or ''} · caller {e.number or 'unknown'}"}
           for e in s.exec(select(Evidence).order_by(Evidence.created_at.desc()).limit(limit)).all()]
    out += [{"at": iso(a.at), "who": a.member, "what": a.action, "item": a.target, "detail": a.detail}
            for a in s.exec(select(P.PortalAudit).where(P.PortalAudit.action.in_(["evidence case", "viewed evidence", "listened to evidence"]))
                            .order_by(P.PortalAudit.at.desc()).limit(limit)).all()]
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)[:limit]


# ------------------------------------------------------------------ daily reports
REPORT_PARTS = {"security": "security", "evidence": "evidence", "ai": "ai", "fraud": "fraud", "network": "network", "team": "team"}


def _report_for_member(r: dict, ctx: P.Ctx) -> dict:
    """Each part of a report only for the roles that may open that section."""
    out = {k: v for k, v in r.items() if k not in REPORT_PARTS}
    for part, perm in REPORT_PARTS.items():
        if part in r and ctx.can(perm):
            out[part] = r[part]
    return out


def _day(day: str) -> dt.date:
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Day must look like 2026-10-21")
    if d > daily_report.today() or d < daily_report.today() - dt.timedelta(days=400):
        raise HTTPException(404, "No report for that day")
    return d


@router.get(A + "/reports")
def reports(ctx: P.Ctx = Depends(P.need("reports"))):
    t = daily_report.today()
    return {"today": {"day": t.isoformat(), "headline": daily_report.build(t)["headline"], "live": True},
            "days": daily_report.stored_days(), "tz": daily_report.TZ_NAME}


@router.get(A + "/reports/{day}")
def report_day(day: str, ctx: P.Ctx = Depends(P.need("reports")), s: Session = Depends(get_session)):
    r = _report_for_member(daily_report.get(_day(day)), ctx)
    P.audit(s, ctx.m, "opened daily report", day, ip=ctx.ip)
    return r


@router.get(A + "/reports/{day}/download")
def report_download(day: str, ctx: P.Ctx = Depends(P.need("reports")), s: Session = Depends(get_session)):
    r = _report_for_member(daily_report.get(_day(day)), ctx)
    P.audit(s, ctx.m, "downloaded daily report", day, ip=ctx.ip)
    return Response(daily_report.to_html(r), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="voiceguard-report-{day}.html"', "Cache-Control": "no-store"})


@router.post(A + "/reports/{day}/rebuild")
def report_rebuild(day: str, ctx: P.Ctx = Depends(P.need("reports")), s: Session = Depends(get_session)):
    _need_manager(ctx, s, "rebuild daily report")
    d = _day(day)
    if d >= daily_report.today():
        raise HTTPException(400, "Today's report is always live")
    daily_report.save(d)
    P.audit(s, ctx.m, "rebuilt daily report", day, ip=ctx.ip)
    return {"ok": True}


# ------------------------------------------------------------------ network: how the app works on each operator
def _health(success: float | None, avg_ms: int | None, drops_per_phone_day: float | None) -> str:
    if success is None:
        return "no data"
    if success >= 0.98 and (avg_ms or 0) <= 800 and (drops_per_phone_day or 0) <= 6:
        return "good"
    if success >= 0.9 and (avg_ms or 0) <= 2000:
        return "fair"
    return "poor"


@router.get(A + "/network")
def network(days: int = 7, ctx: P.Ctx = Depends(P.need("network")), s: Session = Depends(get_session)):
    from .app_reports import AppCounter, AppNetSample, AppNetwork, operator_name
    days = max(1, min(days, 60))
    t = now()
    since = t - dt.timedelta(days=days)
    nets = s.exec(select(AppNetwork)).all()
    devices = {(d.user_id, d.install_id): d for d in s.exec(select(AppDevice)).all()}
    users = {u.id: u for u in s.exec(select(User)).all()}
    op_of_install = {n.install_id: n.operator for n in nets}
    op_of_user = {n.user_id: n.operator for n in sorted(nets, key=lambda n: n.updated)}
    ops: dict[str, dict] = {}

    def op(name):
        return ops.setdefault(name, {"operator": name, "phones": 0, "active_24h": 0, "gens": collections.Counter(), "signal": [],
                                     "roaming": 0, "downlink": [], "protected": 0, "counts": collections.Counter(),
                                     "ai": {"checks": 0, "danger": 0, "seconds": [], "phone_quality": 0},
                                     "telecom": {"partner": None, "flags": 0, "lookups": 0}})
    for n in nets:
        o = op(n.operator)
        o["phones"] += 1
        o["active_24h"] += n.updated > t - dt.timedelta(days=1)
        o["gens"]["Wi-Fi" if n.transport == "wifi" else (n.gen or "?")] += 1
        if n.signal is not None:
            o["signal"].append(n.signal)
        o["roaming"] += n.roaming
        if n.downlink_kbps:
            o["downlink"].append(n.downlink_kbps)
        d = devices.get((n.user_id, n.install_id))
        if d and all(json.loads(d.protection).get(k) for k in ("call_screening", "microphone", "notifications")):
            o["protected"] += 1
    for r in s.exec(select(AppCounter).where(AppCounter.day >= since.date().isoformat())).all():
        op(op_of_install.get(r.install_id, "Unknown"))["counts"].update({k: v for k, v in json.loads(r.counts).items() if isinstance(v, int)})
    for c in s.exec(select(daily_report.AiCheck).where(daily_report.AiCheck.at > since, daily_report.AiCheck.ok == True)).all():   # noqa: E712
        if c.user_id in op_of_user:
            a = op(op_of_user[c.user_id])["ai"]
            a["checks"] += 1
            a["danger"] += c.level == "danger"
            a["phone_quality"] += c.narrowband
            if c.seconds is not None:
                a["seconds"].append(c.seconds)
    partners = {operator_name(p.name): p for p in s.exec(select(P.TelecomPartner)).all() if p.active}
    for e in s.exec(select(P.TelecomEvent).where(P.TelecomEvent.at > since)).all():
        o = ops.get(operator_name(e.partner))
        if o and e.kind in ("flag", "lookup"):
            o["telecom"]["flags" if e.kind == "flag" else "lookups"] += 1
    out = []
    for name, o in ops.items():
        c = o.pop("counts")
        tried = c["api_ok"] + c["api_server_errors"] + c["api_unreachable"]
        success = round(c["api_ok"] / tried, 4) if tried else None
        avg_ms = round(c["api_get_ms"] / c["api_gets"]) if c["api_gets"] else None
        drops = round(c["ws_drops"] / max(1, o["phones"]) / days, 1) if o["phones"] else None
        secs = o["ai"].pop("seconds")
        p = partners.get(name)
        o["telecom"]["partner"] = p.name if p else None
        sig, down = o.pop("signal"), o.pop("downlink")
        out.append(o | {
            "gens": dict(o["gens"]), "signal": round(sum(sig) / len(sig), 1) if sig else None,
            "downlink_mbps": round(sum(down) / len(down) / 1000, 1) if down else None,
            "server_calls": tried, "api_success": success, "api_server_errors": c["api_server_errors"], "api_unreachable": c["api_unreachable"],
            "avg_ms": avg_ms, "ws_drops_per_phone_day": drops,
            "calls_screened": c["calls_screened"], "spam_warned": c["spam_warned"], "auto_blocked": c["auto_blocked"],
            "danger_results": c["danger_results"], "caller_id_lookups": c["caller_id_lookups"],
            "ai": o["ai"] | {"avg_seconds": round(sum(secs) / len(secs), 2) if secs else None},
            "health": _health(success, avg_ms, drops)})
    order = {"Jio": 0, "Airtel": 1, "Vi": 2, "BSNL": 3, "MTNL": 4}
    out.sort(key=lambda o: (order.get(o["operator"], 9), -o["phones"]))
    show = ctx.can("pii")
    phones = []
    for n in sorted(nets, key=lambda n: n.updated, reverse=True)[:300]:
        d = devices.get((n.user_id, n.install_id))
        u = users.get(n.user_id)
        samples = s.exec(select(AppNetSample).where(AppNetSample.install_id == n.install_id, AppNetSample.at > since)).all()
        good, bad = sum(x.api_ok for x in samples), sum(x.api_failed for x in samples)
        phones.append({"user": u.name if u and show else "(hidden)", "phone": P.mask(u.phone if u else "", show),
                       "model": d.model if d else "", "app_version": d.app_version if d else "", "operator": n.operator,
                       "sims": [x.get("operator") or x.get("carrier") for x in json.loads(n.sims)], "transport": n.transport, "gen": n.gen,
                       "signal": n.signal, "roaming": n.roaming, "updated": iso(n.updated),
                       "api_success": round(good / (good + bad), 4) if good + bad else None})
    P.audit(s, ctx.m, "opened network page", f"{days} days", ip=ctx.ip)
    return {"days": days, "operators": out, "phones": phones, "time": iso(t)}
