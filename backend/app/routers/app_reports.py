"""Data and security from the app on every phone, for the Admin Console.

Each phone sends a small report (on start, when it reconnects, and every 6 hours): device and app version, whether
VoiceGuard's protection is fully on (roles, permissions), device-security signals (rooted, debugger, unknown app
copy, no screen lock ...), activity COUNTS (calls screened, spam warned, AI checks ...), app crashes, and the mobile
network it runs on (operator names of the SIMs, 4G/5G/Wi-Fi, signal bars, how many server calls worked and how fast)
for the Team Portal's Network page. No phone numbers, contacts, messages or recordings are sent.

New risks and protection being switched off become castle Layer 7 (app & device trust) incidents and alert the admins;
app crashes are Layer 5 (bugs).
"""
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, select

from .. import security as sec
from ..auth import current_user
from ..db import User, get_session, now
from .admin_console import admin
from .common import iso

router = APIRouter(tags=["app-reports"])

COUNTERS = ("calls_screened", "spam_warned", "auto_blocked", "ai_checks", "danger_results", "family_alerts",
            "panic_pauses", "caller_id_lookups", "live_location_views",
            # how the app's connection to the server works on this phone's network
            "api_ok", "api_server_errors", "api_unreachable", "api_gets", "api_get_ms", "ws_connects", "ws_drops")
BIG_COUNTERS = ("api_get_ms",)    # milliseconds add up quickly
PROTECTION = ("call_screening", "default_dialer", "microphone", "phone", "contacts", "sms", "location", "notifications",
              "overlay", "accessibility", "battery_unrestricted")
RISKS = {   # key -> (severity, what it means)
    "unknown_app": ("high", "app signature is not the genuine VoiceGuard app (modified or re-signed copy)"),
    "rooted": ("high", "phone is rooted - other apps can read VoiceGuard's data"),
    "debugger": ("high", "a debugger is attached to the app"),
    "debug_build": ("medium", "a debug build of the app is installed"),
    "emulator": ("medium", "the app runs on an emulator"),
    "no_screen_lock": ("low", "phone has no screen lock"),
    "usb_debugging": ("low", "USB debugging is switched on"),
}


class AppDevice(SQLModel, table=True):
    __tablename__ = "app_device"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    install_id: str = Field(index=True)
    model: str = ""
    android: str = ""
    app_version: str = ""
    cert: str = ""
    risks: str = "[]"
    protection: str = "{}"
    settings: str = "{}"
    first_seen: dt.datetime = Field(default_factory=now)
    last_report: dt.datetime = Field(default_factory=now, index=True)


class AppCounter(SQLModel, table=True):
    __tablename__ = "app_counter"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    install_id: str = Field(index=True)
    day: str = Field(index=True)
    counts: str = "{}"


class AppCrash(SQLModel, table=True):
    __tablename__ = "app_crash"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    install_id: str = ""
    at: dt.datetime = Field(default_factory=now, index=True)
    app_version: str = ""
    android: str = ""
    model: str = ""
    exception: str = ""
    message: str = ""
    stack: str = ""
    status: str = Field(default="open", index=True)


class AppNetwork(SQLModel, table=True):
    """The mobile network each phone is on (latest report)."""
    __tablename__ = "app_network"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    install_id: str = Field(index=True)
    operator: str = Field(default="Unknown", index=True)   # normalised: Jio / Airtel / Vi / BSNL / MTNL / ...
    operator_raw: str = ""
    sims: str = "[]"                                        # [{carrier, operator, mcc_mnc, slot, data}]
    transport: str = ""                                     # cellular / wifi / none
    gen: str = ""                                           # 5G / 4G / 3G / 2G
    signal: int | None = None                               # 0-4 bars
    roaming: bool = False
    country: str = ""
    downlink_kbps: int | None = None
    updated: dt.datetime = Field(default_factory=now, index=True)


class AppNetSample(SQLModel, table=True):
    """One row per report (kept 60 days): network over time."""
    __tablename__ = "app_net_sample"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    install_id: str = Field(index=True)
    user_id: str = ""
    operator: str = Field(default="Unknown", index=True)
    transport: str = ""
    gen: str = ""
    signal: int | None = None
    api_ok: int = 0
    api_failed: int = 0
    avg_ms: int | None = None


_OPERATORS = [   # India: names seen on SIMs / networks -> one operator name
    ("Jio", ("jio", "reliance")), ("Airtel", ("airtel", "bharti")), ("Vi", ("vodafone", "idea", "vi india", "vi ")),
    ("BSNL", ("bsnl", "cellone")), ("MTNL", ("mtnl", "dolphin")),
]
_MCCMNC = {"405857": "Jio", "405840": "Jio", "405854": "Jio", "405855": "Jio", "405856": "Jio", "405872": "Jio",
           "405874": "Jio", "40410": "Airtel", "40445": "Airtel", "40449": "Airtel", "40490": "Airtel", "40492": "Airtel",
           "40493": "Airtel", "40494": "Airtel", "40495": "Airtel", "40496": "Airtel", "40497": "Airtel", "40498": "Airtel",
           "40420": "Vi", "40411": "Vi", "40486": "Vi", "40422": "Vi", "40444": "Vi", "40484": "Vi", "40488": "Vi",
           "40438": "BSNL", "40453": "BSNL", "40455": "BSNL", "40457": "BSNL", "40458": "BSNL", "40459": "BSNL",
           "40468": "MTNL", "40469": "MTNL"}


def operator_name(raw: str | None, mcc_mnc: str | None = None) -> str:
    low = f" {(raw or '').strip().lower()} "
    for name, keys in _OPERATORS:
        if any(k in low for k in keys) or low.strip() == name.lower():
            return name
    if mcc_mnc and mcc_mnc in _MCCMNC:
        return _MCCMNC[mcc_mnc]
    return (raw or "").strip()[:30] or "Unknown"


def _save_network(s: Session, me: User, iid: str, net: dict, counts: dict):
    if not isinstance(net, dict) or not net:
        return
    sims = []
    for x in (net.get("sims") or [])[:4]:
        if isinstance(x, dict):
            sims.append({"carrier": _t(x.get("carrier"), 40), "mcc_mnc": _t(x.get("mcc_mnc"), 8), "slot": x.get("slot") if isinstance(x.get("slot"), int) else None,
                         "data": x.get("data") is True, "operator": operator_name(x.get("carrier"), _t(x.get("mcc_mnc"), 8))})
    data_sim = next((x for x in sims if x["data"]), sims[0] if sims else None)
    raw = _t(net.get("operator"), 40) or (data_sim["carrier"] if data_sim else "")
    op = operator_name(raw, data_sim["mcc_mnc"] if data_sim else _t(net.get("mcc_mnc"), 8))
    row = s.exec(select(AppNetwork).where(AppNetwork.user_id == me.id, AppNetwork.install_id == iid)).first() or AppNetwork(user_id=me.id, install_id=iid)
    sig = net.get("signal")
    kbps = net.get("downlink_kbps")
    row.operator, row.operator_raw, row.sims = op, raw, json.dumps(sims)
    row.transport = _t(net.get("transport"), 12)
    row.gen = _t(net.get("gen"), 8)
    row.signal = sig if isinstance(sig, int) and 0 <= sig <= 4 else None
    row.roaming = net.get("roaming") is True
    row.country = _t(net.get("country"), 4)
    row.downlink_kbps = kbps if isinstance(kbps, int) and 0 <= kbps <= 10_000_000 else None
    row.updated = now()
    s.add(row)
    ok = counts.get("api_ok", 0)
    failed = counts.get("api_server_errors", 0) + counts.get("api_unreachable", 0)
    gets = counts.get("api_gets", 0)
    s.add(AppNetSample(install_id=iid, user_id=me.id, operator=op, transport=row.transport, gen=row.gen, signal=row.signal,
                       api_ok=ok, api_failed=failed, avg_ms=round(counts.get("api_get_ms", 0) / gets) if gets else None))
    old = now() - dt.timedelta(days=60)
    for r in s.exec(select(AppNetSample).where(AppNetSample.at < old).limit(500)).all():
        s.delete(r)


class Crash(BaseModel):
    at: int | None = None          # epoch ms on the phone
    exception: str = ""
    message: str = ""
    stack: str = ""


class Report(BaseModel):
    install_id: str
    app_version: str = ""
    cert: str = ""
    model: str = ""
    android: str = ""
    security: dict = {}
    protection: dict = {}
    settings: dict = {}
    counters: dict = {}
    crashes: list[Crash] = []
    network: dict = {}


def _t(v, n=120) -> str:
    return str(v or "")[:n]


def _risks(r: Report) -> list[str]:
    sx = r.security or {}
    out = []
    cert = (r.cert or "").lower()
    if sec.APP_CERTS and cert and cert not in sec.APP_CERTS:
        out.append("unknown_app")
    for key, flag in (("rooted", "rooted"), ("debugger", "debugger_attached"), ("debug_build", "debuggable"),
                      ("emulator", "emulator"), ("usb_debugging", "adb_enabled")):
        if sx.get(flag) is True:
            out.append(key)
    if sx.get("screen_lock") is False:
        out.append("no_screen_lock")
    return out


@router.post("/api/app/report")
def app_report(body: Report, me: User = Depends(current_user), s: Session = Depends(get_session)):
    iid = _t(body.install_id, 64)
    if len(iid) < 8:
        raise HTTPException(400, "install_id missing")
    dev = s.exec(select(AppDevice).where(AppDevice.user_id == me.id, AppDevice.install_id == iid)).first()
    old_risks = set(json.loads(dev.risks)) if dev else set()
    old_prot = json.loads(dev.protection) if dev else {}
    dev = dev or AppDevice(user_id=me.id, install_id=iid)
    risks = _risks(body)
    prot = {k: bool(body.protection.get(k)) for k in PROTECTION if k in body.protection}
    dev.model, dev.android, dev.app_version, dev.cert = _t(body.model, 80), _t(body.android, 40), _t(body.app_version, 30), _t(body.cert, 80)
    dev.risks, dev.protection = json.dumps(risks), json.dumps(prot)
    dev.settings = json.dumps({k: v for k, v in list(body.settings.items())[:20] if isinstance(v, (bool, int, str))})
    dev.last_report = now()
    s.add(dev)
    # activity counts since the last report -> today's totals for this phone
    day = now().date().isoformat()
    row = s.exec(select(AppCounter).where(AppCounter.install_id == iid, AppCounter.user_id == me.id, AppCounter.day == day)).first()
    row = row or AppCounter(user_id=me.id, install_id=iid, day=day)
    counts = json.loads(row.counts)
    fresh = {}
    for k in COUNTERS:
        v = body.counters.get(k)
        if isinstance(v, int) and 0 < v <= (100_000_000 if k in BIG_COUNTERS else 100_000):
            counts[k] = counts.get(k, 0) + v
            fresh[k] = v
    row.counts = json.dumps(counts)
    s.add(row)
    _save_network(s, me, iid, body.network, fresh)
    for c in body.crashes[:5]:
        s.add(AppCrash(user_id=me.id, install_id=iid, app_version=dev.app_version, android=dev.android, model=dev.model,
                       exception=_t(c.exception, 120), message=_t(c.message, 300), stack=_t(c.stack, 4000),
                       at=dt.datetime.fromtimestamp(c.at / 1000, dt.timezone.utc) if c.at else now()))
    s.commit()
    who = f"{me.name}'s phone ({dev.model or 'unknown model'})"
    for k in sorted(set(risks) - old_risks):   # only NEW risks alert the admins
        sev, what = RISKS[k]
        sec.record(7, "device_" + k, sev, f"{who}: {what}", user_id=me.id, action="recorded",
                   title=f"Risky device: {what.split(' - ')[0]}")
    lost = [k for k in ("call_screening", "default_dialer", "microphone", "notifications") if old_prot.get(k) and not prot.get(k, True)]
    if lost:
        sec.record(7, "protection_off", "medium", f"{who}: protection switched off ({', '.join(lost)})", user_id=me.id,
                   action="recorded", title="VoiceGuard protection switched off on a phone")
    for c in body.crashes[:5]:
        sec.record(5, "app_crash", "medium", f"App crash {_t(c.exception, 60)} in v{dev.app_version} on {dev.model}",
                   user_id=me.id, title="App crash")
    missing = [k for k in ("call_screening", "microphone", "notifications") if prot.get(k) is False]
    return {"ok": True, "risks": risks, "missing_protection": missing}


# ------------------------------------------------------------------ Admin Console
def _mask(phone: str) -> str:
    return phone[:5] + "•••" + phone[-4:] if phone and len(phone) > 9 else phone


@router.get("/api/admin/console/devices")
def devices(a: User = Depends(admin), s: Session = Depends(get_session)):
    users = {u.id: u for u in s.exec(select(User)).all()}
    out, day = [], now() - dt.timedelta(days=1)
    for d in s.exec(select(AppDevice).order_by(AppDevice.last_report.desc())).all():
        u = users.get(d.user_id)
        prot = json.loads(d.protection)
        out.append({"id": d.id, "user": u.name if u else "?", "phone": _mask(u.phone) if u else "", "model": d.model,
                    "android": d.android, "app_version": d.app_version, "last_report": iso(d.last_report),
                    "active_24h": d.last_report > day, "risks": json.loads(d.risks), "protection": prot,
                    "protection_off": [k for k, v in prot.items() if not v], "settings": json.loads(d.settings)})
    versions: dict[str, int] = {}
    for d in out:
        versions[d["app_version"] or "?"] = versions.get(d["app_version"] or "?", 0) + 1
    return {"devices": out, "summary": {
        "phones": len(out), "active_24h": sum(d["active_24h"] for d in out),
        "at_risk": sum(1 for d in out if any(RISKS[r][0] != "low" for r in d["risks"])),
        "fully_protected": sum(1 for d in out if all(d["protection"].get(k) for k in ("call_screening", "microphone", "notifications"))),
        "versions": versions}, "risk_meaning": {k: v[1] for k, v in RISKS.items()}}


@router.get("/api/admin/console/app-activity")
def app_activity(days: int = 7, a: User = Depends(admin), s: Session = Depends(get_session)):
    since = (now() - dt.timedelta(days=max(1, min(days, 90)) - 1)).date().isoformat()
    per_day: dict[str, dict] = {}
    totals = {k: 0 for k in COUNTERS}
    for r in s.exec(select(AppCounter).where(AppCounter.day >= since)).all():
        c = json.loads(r.counts)
        d = per_day.setdefault(r.day, {k: 0 for k in COUNTERS})
        for k, v in c.items():
            if k in totals:
                d[k] += v
                totals[k] += v
    return {"totals": totals, "per_day": [{"day": k} | v for k, v in sorted(per_day.items(), reverse=True)]}


@router.get("/api/admin/console/crashes")
def crashes(state: str = "open", a: User = Depends(admin), s: Session = Depends(get_session)):
    stmt = select(AppCrash)
    if state != "all":
        stmt = stmt.where(AppCrash.status == state)
    return [{k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in ((k, getattr(c, k)) for k in AppCrash.model_fields)}
            for c in s.exec(stmt.order_by(AppCrash.at.desc()).limit(200)).all()]


@router.post("/api/admin/console/crashes/{cid}/fixed")
def crash_fixed(cid: int, a: User = Depends(admin), s: Session = Depends(get_session)):
    c = s.get(AppCrash, cid)
    if not c:
        raise HTTPException(404, "crash not found")
    c.status = "fixed"
    s.add(c)
    s.commit()
    return {"ok": True}
