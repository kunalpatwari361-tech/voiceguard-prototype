"""Admin Console: one place to keep VoiceGuard healthy - security (the seven layers), data, logs, reports, updates.

Page: /admin/console (holds no data). API: /api/admin/console/* - only accounts in VG_ADMIN_PHONES, checked on the
server for every request; every change is recorded as a security event.
"""
import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, SQLModel, func, select

from .. import config, security as sec
from ..ai import models
from ..auth import current_user
from ..db import Alert, Evidence, Family, RiskEvent, ScamReport, User, engine, get_session, now
from ..hub import hub
from .common import iso

router = APIRouter(tags=["admin-console"])
PAGE = Path(__file__).resolve().parents[1] / "static" / "admin_console.html"
C = "/api/admin/console"
SecIncident = sec.SecIncident


def admin(me: User = Depends(current_user)) -> User:
    if sec.normalize(me.phone) not in sec.ADMIN_PHONES:
        raise HTTPException(403, "Administrators only.")
    return me


def _row(o) -> dict:
    return {k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in ((k, getattr(o, k)) for k in type(o).model_fields)}


def _audit(admin_user: User, what: str):
    sec.record(3, "admin_action", "low", f"{admin_user.name}: {what}", user_id=admin_user.id)


@router.get("/admin/console", response_class=HTMLResponse, include_in_schema=False)
def page():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get(C + "/me")
def me(a: User = Depends(admin)):
    return {"name": a.name, "phone": a.phone}


# ------------------------------------------------------------------ security
@router.get(C + "/status")
def status(a: User = Depends(admin), s: Session = Depends(get_session)):
    day = now() - dt.timedelta(days=1)
    layers = []
    for l in sec.LAYERS:
        open_inc = s.exec(select(SecIncident).where(SecIncident.layer == l["id"], SecIncident.status != "resolved")).all()
        events = s.exec(select(func.count()).select_from(sec.SecEvent).where(sec.SecEvent.layer == l["id"], sec.SecEvent.at > day)).one()
        worst = max((sec.SEVERITY[i.severity] for i in open_inc), default=-1)
        layers.append(l | {"open_incidents": len(open_inc), "events_24h": events,
                           "state": "breached" if worst >= 2 else "warning" if worst >= 1 else "secure"})
    blocks = s.exec(select(sec.SecBlock)).all()
    active = [b for b in blocks if b.until is None or b.until > now()]
    integ = sec.integrity_status()
    return {"layers": layers, "lockdown": sec.lockdown(), "active_blocks": len(active),
            "admins": len(sec.admin_users(s)), "admin_phones_configured": bool(sec.ADMIN_PHONES),
            "integrity_clean": integ["clean"], "app_trust_mode": sec.APP_TRUST_MODE, "app_certs_configured": bool(sec.APP_CERTS),
            "phones_online": sum(1 for v in hub.conns.values() if v), "time": iso(now())}



@router.get(C + "/incidents")
def incidents(state: str = "open", a: User = Depends(admin), s: Session = Depends(get_session)):
    stmt = select(SecIncident)
    if state == "open":
        stmt = stmt.where(SecIncident.status != "resolved")
    elif state != "all":
        stmt = stmt.where(SecIncident.status == state)
    return [_row(i) for i in s.exec(stmt.order_by(SecIncident.last_at.desc()).limit(300)).all()]


@router.post(C + "/incidents/{iid}/{action}")
def incident_action(iid: int, action: str, a: User = Depends(admin), s: Session = Depends(get_session)):
    i = s.get(SecIncident, iid)
    if not i:
        raise HTTPException(404, "incident not found")
    if action not in ("acknowledge", "resolve", "reopen"):
        raise HTTPException(400, "action must be acknowledge, resolve or reopen")
    i.status = {"acknowledge": "acknowledged", "resolve": "resolved", "reopen": "open"}[action]
    i.handled_by, i.handled_at = a.name, now()
    s.add(i)
    s.commit()
    _audit(a, f"incident {iid} {action}")
    return _row(i)


@router.get(C + "/events")
def events(layer: int = 0, limit: int = 300, a: User = Depends(admin), s: Session = Depends(get_session)):
    stmt = select(sec.SecEvent)
    if layer:
        stmt = stmt.where(sec.SecEvent.layer == layer)
    return [_row(e) for e in s.exec(stmt.order_by(sec.SecEvent.at.desc()).limit(max(1, min(limit, 2000)))).all()]


@router.get(C + "/blocks")
def blocks(a: User = Depends(admin), s: Session = Depends(get_session)):
    out = []
    for b in s.exec(select(sec.SecBlock).order_by(sec.SecBlock.created_at.desc())).all():
        r = _row(b)
        r["active"] = b.until is None or b.until > now()
        if b.kind == "token":
            r["value"] = b.value[:10] + "…"
        out.append(r)
    return out


class NewBlock(BaseModel):
    kind: str = "ip"
    value: str
    minutes: int | None = None
    reason: str = ""


@router.post(C + "/blocks")
def add_block(body: NewBlock, a: User = Depends(admin)):
    if body.kind not in ("ip", "phone"):
        raise HTTPException(400, "kind must be ip or phone")
    value = sec.normalize(body.value) if body.kind == "phone" else body.value.strip()[:64]
    if not value:
        raise HTTPException(400, "value needed")
    b = sec.block(body.kind, value, body.minutes, body.reason or "blocked by admin", by=a.name)
    _audit(a, f"blocked {body.kind} {value}")
    return _row(b)


@router.delete(C + "/blocks/{bid}")
def remove_block(bid: int, a: User = Depends(admin)):
    sec.unblock(bid)
    _audit(a, f"removed block {bid}")
    return {"ok": True}


class Lockdown(BaseModel):
    on: bool
    reason: str = ""


@router.post(C + "/lockdown")
def lockdown(body: Lockdown, a: User = Depends(admin)):
    sec.set_lockdown(body.on, body.reason or ("Lockdown by admin" if body.on else ""), by=a.name)
    if body.on:
        sec.record(6, "manual_lockdown", "high", f"{a.name} put VoiceGuard in lockdown: {body.reason}", user_id=a.id,
                   action="lockdown", title="Lockdown switched on by an admin")
    _audit(a, "lockdown " + ("ON" if body.on else "off"))
    return {"lockdown": sec.lockdown()}


@router.post(C + "/test-alert")
def test_alert(a: User = Depends(admin)):
    iid = sec.record(7, "test_alert", "medium", f"Test alert sent by {a.name} from the Admin Console", user_id=a.id,
                     title="Test alert (no action needed)")
    return {"ok": True, "incident_id": iid}


# ------------------------------------------------------------------ data
@router.get(C + "/data")
def data(a: User = Depends(admin)):
    counts = {}
    with engine.connect() as con:
        for t in SQLModel.metadata.sorted_tables:
            try:
                counts[t.name] = con.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar()
            except Exception:
                counts[t.name] = None
    db = Path(config.DB_PATH)
    backups = sorted(sec.BACKUP_DIR.glob("voiceguard-*.db"), reverse=True) if sec.BACKUP_DIR.exists() else []
    return {"tables": counts, "db_size_mb": round(db.stat().st_size / 1_048_576, 2) if db.exists() else None,
            "backups": [{"name": b.name, "size_mb": round(b.stat().st_size / 1_048_576, 2)} for b in backups]}


@router.post(C + "/data/check")
def data_check(a: User = Depends(admin)):
    r = sec.db_check()
    if r != "ok":
        sec.record(5, "db_damaged", "critical", f"Database check by {a.name}: {r}", action="recorded", title="Database damaged")
    _audit(a, "database check")
    return {"result": r}


@router.post(C + "/data/backup")
def data_backup(a: User = Depends(admin)):
    p = sec.backup_db()
    _audit(a, f"backup {p.name}")
    return {"ok": True, "file": p.name}


# ------------------------------------------------------------------ logs, reports, updates
@router.get(C + "/logs")
def logs(lines: int = 300, q: str = "", a: User = Depends(admin)):
    return {"lines": sec.tail_log(lines, q)}


@router.get(C + "/reports")
def reports(a: User = Depends(admin), s: Session = Depends(get_session)):
    week = now() - dt.timedelta(days=7)
    top = s.exec(select(ScamReport.number, func.count()).group_by(ScamReport.number).order_by(func.count().desc()).limit(10)).all()
    by_kind = s.exec(select(Alert.kind, func.count()).where(Alert.created_at > week).group_by(Alert.kind)).all()
    risk = s.exec(select(RiskEvent.level, func.count()).where(RiskEvent.created_at > week).group_by(RiskEvent.level)).all()
    return {"users": s.exec(select(func.count()).select_from(User)).one(),
            "families": s.exec(select(func.count()).select_from(Family)).one(),
            "scam_reports": s.exec(select(func.count()).select_from(ScamReport)).one(),
            "evidence_reports": s.exec(select(func.count()).select_from(Evidence)).one(),
            "top_reported_numbers": [{"number": n, "reports": c} for n, c in top],
            "alerts_7d": dict(by_kind), "risk_checks_7d": dict(risk),
            "incidents_7d": s.exec(select(func.count()).select_from(SecIncident).where(SecIncident.at > week)).one()}


@router.get(C + "/updates")
def updates(a: User = Depends(admin)):
    def f(p: Path):
        return {"file": p.name, "updated": iso(dt.datetime.fromtimestamp(p.stat().st_mtime)), "size_mb": round(p.stat().st_size / 1_048_576, 2)} if p.exists() else None
    weights = [f(p) for p in sorted(Path(models.WEIGHTS).parent.glob("vg_*.npz")) if not p.name.endswith(("_backup.npz", "_candidate.npz"))]
    apk = config.BASE_DIR.parents[1] / "VoiceGuard-apk" / "VoiceGuard-0.1.apk"
    return {"server_version": "0.1.0", "ai_models": {"detector": "voiceguard-wavlm-phone-v4", "speaker": config.SPEAKER_MODEL,
                                                    "speech_to_text": config.ASR_MODEL},
            "weights": weights, "app_apk": f(apk), "integrity": sec.integrity_status()}


@router.post(C + "/updates/approve")
def approve(a: User = Depends(admin)):
    r = sec.approve_baseline(a.name)
    _audit(a, "approved current server files as the trusted version")
    return r
