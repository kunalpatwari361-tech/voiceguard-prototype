"""Daily reports and the AI check log for the Team Portal.

Every AI check (voice note, live call, HD call) is logged with its result and how long it took (portal_ai_check).
Once a day (just after midnight India time) the report for the day that ended is built and stored
(portal_daily_report): security, evidence, AI, fraud, the phones' networks and the team. The admins get a short
summary in the app. Today's report is built live whenever someone opens it.
"""
import collections
import datetime as dt
import html
import json
import logging
import os
import threading
import time

from sqlmodel import Field, Session, SQLModel, func, select

from . import security as sec
from .db import Evidence, RiskEvent, ScamReport, ScamVoice, engine, now

log = logging.getLogger("voiceguard.reports")
TZ = dt.timezone(dt.timedelta(minutes=int(os.getenv("VG_REPORT_TZ_MIN", "330"))))   # India Standard Time
TZ_NAME = os.getenv("VG_REPORT_TZ_NAME", "IST")


class AiCheck(SQLModel, table=True):
    __tablename__ = "portal_ai_check"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    user_id: str = Field(default="", index=True)
    source: str = ""                         # voice_note / live_call / demo_call / hd_call / portal_test
    ok: bool = True
    error: str = ""                          # too_short / only_owner / warming_up / crash
    level: str = ""
    score: int | None = None
    fake_prob: float | None = None
    made_by: str = ""                        # Source Tracer: human / ai_speech / dsp_changer / ...
    model: str = ""
    seconds: float | None = None
    narrowband: bool = False                 # phone-quality audio
    scam_voice: bool = False                 # matched a known scammer's voice


class DailyReport(SQLModel, table=True):
    __tablename__ = "portal_daily_report"
    day: str = Field(primary_key=True)       # YYYY-MM-DD in TZ
    created_at: dt.datetime = Field(default_factory=now)
    data: str = "{}"


def log_check(user_id: str | None, source: str, result: dict | None, seconds: float | None = None, error: str = ""):
    """Never breaks a check: logging problems are only written to the server log."""
    try:
        r = result or {}
        ok = bool(r.get("ok")) and not error
        risk, ai = r.get("risk") or {}, r.get("ai_voice") or {}
        rev = r.get("reverse_engineering") or {}
        total = seconds if seconds is not None else (r.get("timings_s") or {}).get("total")
        with Session(engine) as s:
            s.add(AiCheck(user_id=user_id or "", source=source[:20], ok=ok, error=(error or ("" if ok else str(r.get("error") or "failed")))[:40],
                          level=str(risk.get("level") or "")[:12], score=risk.get("score"),
                          fake_prob=ai.get("fake_prob"), made_by=str(rev.get("kind") or "")[:24], model=str(ai.get("model") or "")[:40],
                          seconds=round(float(total), 2) if isinstance(total, (int, float)) else None,
                          narrowband=bool((r.get("phone_quality") or {}).get("narrowband")), scam_voice=bool(r.get("voice_id"))))
            s.commit()
    except Exception:
        log.exception("could not log the AI check")


# ------------------------------------------------------------------ building a report
def today() -> dt.date:
    return now().astimezone(TZ).date()


def bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(), TZ).astimezone(dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def _in(col, a, b):
    return (col >= a) & (col < b)


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)


def build(day: dt.date) -> dict:
    from .portal import EvidenceCase, PortalAudit, TelecomEvent
    from .routers.app_reports import AppCounter, AppCrash, AppNetwork
    a, b = bounds(day)
    with Session(engine) as s:
        # ---- security
        events = s.exec(select(sec.SecEvent).where(_in(sec.SecEvent.at, a, b))).all()
        opened = s.exec(select(sec.SecIncident).where(_in(sec.SecIncident.at, a, b)).order_by(sec.SecIncident.at)).all()
        resolved = s.exec(select(sec.SecIncident).where(sec.SecIncident.status == "resolved", sec.SecIncident.handled_at != None,   # noqa: E711
                                                        _in(sec.SecIncident.handled_at, a, b))).all()
        blocks = s.exec(select(sec.SecBlock).where(_in(sec.SecBlock.created_at, a, b))).all()
        still_open = s.exec(select(func.count()).select_from(sec.SecIncident).where(sec.SecIncident.status != "resolved")).one()
        lock_kinds = {"manual_lockdown", "portal_lockdown", "files_changed_live", "db_damaged"}
        security = {
            "events": len(events), "by_layer": {str(l["id"]): 0 for l in sec.LAYERS} | dict(collections.Counter(str(e.layer) for e in events)),
            "by_severity": dict(collections.Counter(e.severity for e in events)),
            "top_kinds": collections.Counter(e.kind for e in events).most_common(8),
            "incidents_opened": len(opened), "incidents_resolved": len(resolved), "open_now": still_open,
            "serious": sum(1 for i in opened if sec.SEVERITY.get(i.severity, 0) >= 2),
            "incidents": [{"at": i.at.isoformat(), "layer": i.layer, "severity": i.severity, "title": i.title, "count": i.count,
                           "action": i.action, "status": i.status} for i in opened[:15]],
            "blocks": len(blocks), "block_reasons": dict(collections.Counter(x.reason for x in blocks).most_common(5)),
            "lockdowns": sum(1 for e in events if e.kind in lock_kinds),
            "integrity_clean": sec.integrity_status()["clean"],
        }
        # ---- evidence
        ev = s.exec(select(Evidence).where(_in(Evidence.created_at, a, b)).order_by(Evidence.created_at)).all()
        case_audit = s.exec(select(PortalAudit).where(_in(PortalAudit.at, a, b), PortalAudit.action.in_(
            ["evidence case", "viewed evidence", "listened to evidence"]))).all()
        total_ev = s.exec(select(func.count()).select_from(Evidence)).one()
        closed = s.exec(select(func.count()).select_from(EvidenceCase).where(EvidenceCase.status == "closed")).one()
        evidence = {
            "new": len(ev), "by_level": dict(collections.Counter(e.level or "?" for e in ev)),
            "by_source": dict(collections.Counter(e.source for e in ev)),
            "items": [{"id": e.id, "at": e.created_at.isoformat(), "level": e.level, "score": e.risk_score, "number": e.number,
                       "claimed": e.claimed_name} for e in ev[:15]],
            "case_updates": sum(1 for x in case_audit if x.action == "evidence case"),
            "sent_to_police": sum(1 for x in case_audit if x.action == "evidence case" and "sent_to_police" in x.detail),
            "closed": sum(1 for x in case_audit if x.action == "evidence case" and "status closed" in x.detail),
            "views": sum(1 for x in case_audit if x.action != "evidence case"),
            "open_cases": total_ev - closed,
        }
        # ---- AI
        checks = s.exec(select(AiCheck).where(_in(AiCheck.at, a, b))).all()
        ok = [c for c in checks if c.ok]
        secs = [c.seconds for c in ok if c.seconds is not None]
        risk = s.exec(select(RiskEvent).where(_in(RiskEvent.created_at, a, b))).all()
        switches = s.exec(select(PortalAudit).where(_in(PortalAudit.at, a, b), PortalAudit.action == "switched AI model")).all()
        ai = {
            "checks": len(checks), "ok": len(ok), "errors": dict(collections.Counter(c.error for c in checks if not c.ok)),
            "by_level": dict(collections.Counter(c.level for c in ok)), "by_source": dict(collections.Counter(c.source for c in checks)),
            "ai_voices": sum(1 for c in ok if (c.fake_prob or 0) >= 0.5),
            "made_by": dict(collections.Counter(c.made_by for c in ok if c.made_by)),
            "avg_seconds": round(sum(secs) / len(secs), 2) if secs else None, "p95_seconds": _pct(secs, 0.95),
            "slowest_seconds": max(secs) if secs else None,
            "phone_quality_share": round(sum(c.narrowband for c in ok) / len(ok), 2) if ok else None,
            "scam_voice_matches": sum(1 for c in ok if c.scam_voice),
            "new_scam_voices": s.exec(select(func.count()).select_from(ScamVoice).where(_in(ScamVoice.created_at, a, b))).one(),
            "risky_events": dict(collections.Counter(r.level for r in risk)),
            "models": sorted({c.model for c in ok if c.model}),
            "switches": [{"by": x.member, "what": x.target, "why": x.detail} for x in switches],
        }
        # ---- fraud & telecom
        reports = s.exec(select(ScamReport).where(_in(ScamReport.created_at, a, b))).all()
        tel = s.exec(select(TelecomEvent).where(_in(TelecomEvent.at, a, b))).all()
        fraud = {
            "reports": len(reports), "by_source": dict(collections.Counter(r.source for r in reports)),
            "numbers": len({r.number for r in reports}),
            "top_numbers": collections.Counter(r.number for r in reports).most_common(5),
            "telecom_flags": dict(collections.Counter(r.reason[1:r.reason.index("]")] if r.reason.startswith("[") and "]" in r.reason else "?"
                                                      for r in reports if r.source == "telecom")),
            "operator_calls": dict(collections.Counter(f"{e.partner} {e.kind}" for e in tel)),
        }
        # ---- phones & networks (phone counters are kept per UTC day)
        nets = {n.install_id: n.operator for n in s.exec(select(AppNetwork)).all()}
        per_op: dict[str, dict] = {}
        for r in s.exec(select(AppCounter).where(AppCounter.day == day.isoformat())).all():
            c = json.loads(r.counts)
            d = per_op.setdefault(nets.get(r.install_id, "Unknown"), collections.Counter(phones=0))
            d["phones"] += 1
            d.update({k: v for k, v in c.items() if isinstance(v, int)})
        network = {op: {"phones": d["phones"], "calls_screened": d.get("calls_screened", 0), "ai_checks": d.get("ai_checks", 0),
                        "api_success": round(d.get("api_ok", 0) / max(1, d.get("api_ok", 0) + d.get("api_server_errors", 0) + d.get("api_unreachable", 0)), 3)
                        if d.get("api_ok", 0) + d.get("api_server_errors", 0) + d.get("api_unreachable", 0) else None,
                        "avg_ms": round(d.get("api_get_ms", 0) / d["api_gets"]) if d.get("api_gets") else None,
                        "ws_drops": d.get("ws_drops", 0)} for op, d in per_op.items()}
        crashes = s.exec(select(func.count()).select_from(AppCrash).where(_in(AppCrash.at, a, b))).one()
        # ---- team
        audit = s.exec(select(PortalAudit).where(_in(PortalAudit.at, a, b))).all()
        team = {"sign_ins": sum(1 for x in audit if x.action == "login"), "members_active": len({x.member for x in audit if x.member}),
                "refused": sum(1 for x in audit if x.action == "denied"), "exports": sum(1 for x in audit if x.action.startswith("exported")),
                "changes": len(audit)}
    end = now() >= b
    return {"day": day.isoformat(), "tz": TZ_NAME, "complete": end, "generated_at": now().isoformat(),
            "headline": {"incidents": security["incidents_opened"], "serious": security["serious"], "evidence": evidence["new"],
                         "ai_checks": ai["checks"], "danger": ai["by_level"].get("danger", 0), "ai_voices": ai["ai_voices"],
                         "scam_reports": fraud["reports"], "app_crashes": crashes},
            "security": security, "evidence": evidence, "ai": ai, "fraud": fraud, "network": network, "app_crashes": crashes,
            "team": team}


def get(day: dt.date) -> dict:
    """Stored report for a finished day (built now if it is missing); today's is always live."""
    if day >= today():
        return build(day)
    with Session(engine) as s:
        row = s.get(DailyReport, day.isoformat())
        if row:
            return json.loads(row.data)
    return save(day)


def save(day: dt.date) -> dict:
    data = build(day)
    with Session(engine) as s:
        row = s.get(DailyReport, day.isoformat()) or DailyReport(day=day.isoformat())
        row.data, row.created_at = json.dumps(data), now()
        s.add(row)
        s.commit()
    return data


def stored_days(limit: int = 60) -> list[dict]:
    with Session(engine) as s:
        rows = s.exec(select(DailyReport).order_by(DailyReport.day.desc()).limit(limit)).all()
        return [{"day": r.day, "headline": json.loads(r.data).get("headline", {})} for r in rows]


def summary_text(r: dict) -> str:
    h = r["headline"]
    return (f"{h['incidents']} security incident(s) ({h['serious']} serious), {h['evidence']} new evidence, "
            f"{h['ai_checks']} AI checks ({h['danger']} danger, {h['ai_voices']} AI voices), {h['scam_reports']} scam reports, "
            f"{h['app_crashes']} app crash(es).")


# ------------------------------------------------------------------ the downloadable report (plain HTML, no scripts)
def to_html(r: dict) -> str:
    e = lambda v: html.escape(str(v if v is not None else "—"))   # noqa: E731

    def kv(d: dict) -> str:
        return "".join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in d.items()) or "<tr><td colspan=2>none</td></tr>"

    def sec_(title, body):
        return f"<h2>{e(title)}</h2>{body}" if body else ""
    h = r["headline"]
    parts = [f"<h1>VoiceGuard daily report · {e(r['day'])} ({e(r.get('tz'))})</h1>",
             f"<p class=m>{'Complete day' if r.get('complete') else 'Day in progress'} · generated {e(r['generated_at'][:19])} UTC · CONFIDENTIAL</p>",
             "<table class=h>" + "".join(f"<td><b>{e(v)}</b><br>{e(k.replace('_', ' '))}</td>" for k, v in h.items()) + "</table>"]
    if "security" in r:
        x = r["security"]
        parts.append(sec_("Security", f"<p>{x['events']} events · {x['incidents_opened']} incidents opened ({x['serious']} serious) · "
                                      f"{x['incidents_resolved']} resolved · {x['open_now']} open now · {x['blocks']} blocks · {x['lockdowns']} lockdown events · "
                                      f"server files {'trusted' if x['integrity_clean'] else 'CHANGED'}</p>"
                                      "<h3>Events by layer</h3><table>" + kv({f"Layer {k}": v for k, v in x["by_layer"].items()}) + "</table>"
                                      "<h3>Incidents</h3><table><tr><th>Time</th><th>Layer</th><th>Severity</th><th>What</th><th>Times</th><th>Action</th><th>Status</th></tr>" +
                                      "".join(f"<tr><td>{e(i['at'][11:16])}</td><td>{e(i['layer'])}</td><td>{e(i['severity'])}</td><td>{e(i['title'])}</td>"
                                              f"<td>{e(i['count'])}</td><td>{e(i['action'] or '—')}</td><td>{e(i['status'])}</td></tr>" for i in x["incidents"]) + "</table>"))
    if "evidence" in r:
        x = r["evidence"]
        parts.append(sec_("Evidence", f"<p>{x['new']} new · {x['case_updates']} case updates · {x['sent_to_police']} sent to police · "
                                      f"{x['closed']} closed · {x['open_cases']} cases open</p><table><tr><th>Time</th><th>Level</th><th>Score</th><th>Caller</th><th>Claimed</th></tr>" +
                                      "".join(f"<tr><td>{e(i['at'][11:16])}</td><td>{e(i['level'])}</td><td>{e(i['score'])}</td><td>{e(i['number'])}</td><td>{e(i['claimed'])}</td></tr>"
                                              for i in x["items"]) + "</table>"))
    if "ai" in r:
        x = r["ai"]
        parts.append(sec_("AI", f"<p>{x['checks']} checks · {x['ok']} finished · {x['ai_voices']} AI voices found · average {e(x['avg_seconds'])} s, "
                                f"95% under {e(x['p95_seconds'])} s · {x['scam_voice_matches']} known scammer voices · {x['new_scam_voices']} new scam voices</p>"
                                "<table>" + kv({"Results: " + k: v for k, v in x["by_level"].items()} | {"Check type: " + k: v for k, v in x["by_source"].items()}
                                               | {"Problem: " + k: v for k, v in x["errors"].items()} | {"Made by: " + k: v for k, v in x["made_by"].items()}) + "</table>"
                                + "".join(f"<p>Model switched by {e(w['by'])}: {e(w['what'])} ({e(w['why'])})</p>" for w in x["switches"])))
    if "fraud" in r:
        x = r["fraud"]
        parts.append(sec_("Fraud & telecom", f"<p>{x['reports']} scam reports about {x['numbers']} numbers</p><table>" +
                          kv({"From " + k: v for k, v in x["by_source"].items()} | {"Operator " + k: v for k, v in x["telecom_flags"].items()}) + "</table>"))
    if "network" in r:
        def ok_pct(d):
            return None if d["api_success"] is None else f"{d['api_success'] * 100:.1f}%"
        rows = "".join(f"<tr><td>{e(op)}</td><td>{e(d['phones'])}</td><td>{e(ok_pct(d))}</td>"
                       f"<td>{e(d['avg_ms'])}</td><td>{e(d['ws_drops'])}</td><td>{e(d['calls_screened'])}</td></tr>" for op, d in r["network"].items())
        parts.append(sec_("Phones by operator", "<table><tr><th>Operator</th><th>Phones</th><th>Server calls OK</th><th>Avg ms</th><th>Connection drops</th><th>Calls screened</th></tr>"
                          + (rows or "<tr><td colspan=6>no phone reports</td></tr>") + "</table>"))
    if "team" in r:
        parts.append(sec_("Team portal", "<table>" + kv(r["team"]) + "</table>"))
    css = ("body{font:14px system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#0f172a}h1{font-size:20px}"
           "h2{font-size:16px;margin-top:22px;border-bottom:2px solid #2563eb}table{border-collapse:collapse;width:100%;margin:6px 0}"
           "td,th{border:1px solid #cbd5e1;padding:4px 7px;text-align:left;font-size:13px}.h td{text-align:center}.m{color:#64748b}")
    return f"<!doctype html><html><head><meta charset=utf-8><title>VoiceGuard report {e(r['day'])}</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


# ------------------------------------------------------------------ once a day
async def _notify(r: dict):
    from .hub import hub
    with Session(engine) as s:
        admins = [u.id for u in sec.admin_users(s)]
    for uid in admins:
        await hub.deliver(uid, {"type": "alert", "alert": {
            "id": f"report-{r['day']}", "kind": "daily_report", "title": f"📊 VoiceGuard daily report · {r['day']}",
            "body": summary_text(r) + "\nOpen the Team Portal → Daily reports.", "from_user_id": None,
            "payload": {"day": r["day"]}, "created_at": now().isoformat()}})


def run_due() -> str | None:
    """Builds yesterday's report if it is not stored yet (and tells the admins). Returns the day built."""
    y = today() - dt.timedelta(days=1)
    with Session(engine) as s:
        if s.get(DailyReport, y.isoformat()):
            return None
    r = save(y)
    sec._schedule(_notify(r))
    log.info("daily report %s: %s", y, summary_text(r))
    return y.isoformat()


def _loop():
    while True:
        try:
            run_due()
        except Exception:
            log.exception("daily report failed")
        time.sleep(600)


def start():
    if os.getenv("VG_DAILY_REPORT", "1") == "1":
        threading.Thread(target=_loop, daemon=True, name="daily-report").start()
