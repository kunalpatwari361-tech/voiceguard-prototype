"""Family Safety & Live Location: groups, invitations, consent, sharing, map, realtime, SOS, arrivals, safe zones,
low battery, delivery retries, retention, migration up/down, feature flag, access control.

Own temporary database. Run: .venv\\Scripts\\python scripts\\family_safety_test.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ.update({
    "VG_WARMUP": "0", "VG_DISCOVERY": "0", "VG_DAILY_REPORT": "0", "VG_DB": str(TMP / "fs.db"), "VG_OTP_PROVIDER": "console",
    "TWILIO_WHATSAPP_FROM": "", "VG_FIREBASE_CREDENTIALS": str(TMP / "none.json"), "VG_ADMIN_PHONES": "+919876544009",
    "VG_OTP_TEST_NUMBERS": "+919876544001:111111,+919876544002:222222,+919876544003:333333,+919876544004:444444",
})
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import family_safety as fs, security as sec  # noqa: E402
from app.db import engine, now  # noqa: E402
from app.hub import hub  # noqa: E402
from app.main import app  # noqa: E402

sec.BASELINE, sec.BACKUP_DIR = TMP / "baseline.json", TMP / "backups"
fs.MIN_INTERVAL_S = 0          # tests send fixes quickly; the 5 s limit is checked on its own below
delivered, live_msgs, failing = [], [], set()


async def fake_deliver(uid, msg):
    if uid in failing:
        return False
    delivered.append((uid, msg))
    return True


async def fake_send(uid, msg):
    live_msgs.append((uid, msg))
    return True
hub.deliver, hub.send = fake_deliver, fake_send
ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        raise SystemExit(1)
    ok += 1


def ms(t: dt.datetime | None = None) -> int:
    return int((t or now()).timestamp() * 1000)


def alerts_for(uid, kind):
    return [m for u, m in delivered if u == uid and m["type"] == "alert" and m["alert"]["kind"] == kind]


HOME = (19.0760, 72.8777)          # Mumbai
SCHOOL = (19.0896, 72.8656)        # ~2 km away


def offset(p, north_m=0.0, east_m=0.0):
    import math
    return p[0] + north_m / 111_320, p[1] + east_m / (111_320 * math.cos(math.radians(p[0])))


with TestClient(app) as c:
    def login(phone, code, name):
        c.post("/api/auth/otp/start", json={"phone": phone})
        r = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code, "name": name}).json()
        return r["user"], {"Authorization": "Bearer " + r["token"]}

    papa, hp = login("9876544001", "111111", "Papa")
    rahul, hr = login("9876544002", "222222", "Rahul")
    mum, hm = login("9876544003", "333333", "Mum")
    stranger, hx = login("9876544004", "444444", "Stranger")
    A = "/api/family-safety"

    def fix(h, p, acc=10.0, t=None, battery=None, charging=None):
        return c.post(A + "/location", headers=h, json={"lat": p[0], "lon": p[1], "accuracy": acc, "recorded_at": ms(t),
                                                        "battery": battery, "charging": charging})

    # ---- feature flag
    fs.ENABLED = False
    check("feature off: status says so", c.get(A + "/status", headers=hp).json() == {"enabled": False})
    check("feature off: API answers 404", c.get(A + "/map", headers=hp).status_code == 404)
    check("feature off: rest of VoiceGuard works", c.get("/api/health").status_code == 200)
    fs.ENABLED = True

    # ---- family group and invitations
    check("no circle yet -> 409", c.get(A + "/group", headers=hp).status_code == 409)
    g = c.post(A + "/group", headers=hp, json={"name": "Sharma family"}).json()
    check("owner creates the family circle", g["i_am_owner"] and len(g["members"]) == 1)
    check("the circle is the existing family circle (old API sees it)", c.get(f"/api/family/{g['id']}", headers=hp).status_code == 200)
    inv = c.post(A + "/group/invites", headers=hp, json={"expires_hours": 2, "max_uses": 1}).json()
    check("secure invite token made (shown once)", inv["token"].startswith("vgfam_") and len(inv["token"]) > 30)
    with Session(engine) as s:
        row = s.get(fs.FsInvite, inv["id"])
    check("only a hash of the token is stored", row.token_hash != inv["token"] and inv["token"] not in row.token_hash)
    pv = c.post(A + "/invites/preview", headers=hr, json={"token": inv["token"]}).json()
    check("preview shows the circle without joining", pv["family_name"] == "Sharma family" and c.get(A + "/group", headers=hr).status_code == 409)
    check("signed-out person cannot accept", c.post(A + "/invites/accept", json={"token": inv["token"]}).status_code == 401)
    g = c.post(A + "/invites/accept", headers=hr, json={"token": inv["token"]}).json()
    check("Rahul accepts and joins", len(g["members"]) == 2)
    check("used invite cannot be used again", c.post(A + "/invites/accept", headers=hm, json={"token": inv["token"]}).status_code == 404)
    check("wrong token refused", c.post(A + "/invites/accept", headers=hm, json={"token": "vgfam_" + "x" * 30}).status_code == 404)
    inv2 = c.post(A + "/group/invites", headers=hr, json={"expires_hours": 1, "max_uses": 3}).json()
    with Session(engine) as s:
        r2 = s.get(fs.FsInvite, inv2["id"])
        r2.expires_at = now() - dt.timedelta(minutes=1)
        s.add(r2)
        s.commit()
    check("expired invite refused", c.post(A + "/invites/accept", headers=hm, json={"token": inv2["token"]}).status_code == 404)
    inv3 = c.post(A + "/group/invites", headers=hp, json={}).json()
    c.delete(f"{A}/group/invites/{inv3['id']}", headers=hp)
    check("cancelled invite refused", c.post(A + "/invites/accept", headers=hm, json={"token": inv3["token"]}).status_code == 404)
    inv4 = c.post(A + "/group/invites", headers=hp, json={}).json()
    check("Mum joins with a fresh invite", len(c.post(A + "/invites/accept", headers=hm, json={"token": inv4["token"]}).json()["members"]) == 3)
    check("stranger cannot see the circle", c.get(A + "/group", headers=hx).status_code == 409)

    # ---- consent
    info = c.get(A + "/consent", headers=hr).json()
    check("consent explains what, why, who, when", {"what", "why", "who", "when", "keep"} <= set(info["text"]))
    check("location refused before consent (409, nothing stored)", fix(hr, SCHOOL).status_code == 409)
    check("old consent text refused", c.post(A + "/consent", headers=hr, json={"version": "1999", "accept": True}).status_code == 409)
    check("consent needs an explicit yes", c.post(A + "/consent", headers=hr, json={"version": info["version"], "accept": False}).status_code == 400)
    st = c.post(A + "/consent", headers=hr, json={"version": info["version"], "accept": True, "share_battery": True, "zone_alerts": True}).json()
    check("Rahul grants consent (location + battery)", st["sharing"] == "sharing" and st["scope"]["battery"])
    check("Papa did not turn on Rahul's sharing for him - only Rahul can",
          c.post(A + "/consent", headers=hp, json={"version": info["version"], "accept": True}).json()["sharing"] == "sharing"
          and c.get(A + "/status", headers=hm).json()["sharing"] == "off")
    with Session(engine) as s:
        hist = s.exec(select(fs.FsConsent).where(fs.FsConsent.user_id == rahul["id"])).all()
    check("consent history: version, scope and grant time recorded", hist[0].version == info["version"] and hist[0].granted_at and hist[0].scope_battery)

    # ---- location updates and the map
    check("valid fix accepted", fix(hr, SCHOOL, battery=80).json()["accepted"])
    check("invalid coordinates refused", c.post(A + "/location", headers=hr, json={"lat": 123, "lon": 72, "accuracy": 5, "recorded_at": ms()}).status_code == 422)
    check("fix from the future refused", fix(hr, SCHOOL, t=now() + dt.timedelta(minutes=10)).status_code == 422)
    check("stale fix not stored", fix(hr, SCHOOL, t=now() - dt.timedelta(minutes=30)).json()["reason"] == "stale")
    t0 = now()
    fix(hr, SCHOOL, t=t0)
    check("duplicate / replayed fix ignored", fix(hr, SCHOOL, t=t0).json()["reason"] == "duplicate")
    check("out-of-order older fix ignored", fix(hr, SCHOOL, t=t0 - dt.timedelta(seconds=30)).json()["reason"] == "duplicate")
    fs.MIN_INTERVAL_S = 5
    fs.limiter._d.clear()
    check("fix after a pause accepted", fix(hr, SCHOOL, t=now(), battery=80).json()["accepted"])
    check("more than one fix every 5 s is refused", fix(hr, SCHOOL, t=now() + dt.timedelta(seconds=1)).json()["reason"] == "too_soon")
    fs.MIN_INTERVAL_S = 0
    mp = {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}
    check("map: Rahul live with accuracy and time", mp["Rahul"]["status"] == "live" and mp["Rahul"]["location"]["accuracy"] == 10.0
          and mp["Rahul"]["age_s"] is not None)
    check("map: battery shown only because Rahul shares it", mp["Rahul"]["battery"]["level"] == 80)
    check("map: Mum not sharing -> no location", mp["Mum"]["location"] is None and mp["Mum"]["status"] == "off")
    check("stranger cannot open the family map", c.get(A + "/map", headers=hx).status_code == 409)
    with Session(engine) as s:
        loc = s.get(fs.FsLocation, rahul["id"])
        loc.received_at = now() - dt.timedelta(minutes=20)
        s.add(loc)
        s.commit()
    mp = {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}
    check("old location shown as stale, not as current", mp["Rahul"]["status"] == "stale")
    with Session(engine) as s:
        loc = s.get(fs.FsLocation, rahul["id"])
        loc.received_at = now() - dt.timedelta(hours=3)
        s.add(loc)
        s.commit()
    check("very old location shown as offline", {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}["Rahul"]["status"] == "offline")
    with Session(engine) as s:
        hist_rows = len(s.exec(select(fs.FsLocation).where(fs.FsLocation.user_id == rahul["id"])).all())
    check("only the latest location is kept (no movement history)", hist_rows == 1)

    # ---- realtime
    c.post(A + "/map/watch", headers=hp)
    live_msgs.clear()
    fix(hr, offset(SCHOOL, 50), t=now())
    got = [m for u, m in live_msgs if u == papa["id"] and m["type"] == "family_safety_member"]
    check("realtime: Papa (map open) gets Rahul's new position", got and got[-1]["member"]["location"] is not None)
    check("realtime: Mum (map closed) gets nothing", not [m for u, m in live_msgs if u == mum["id"]])

    # ---- pause / resume
    c.post(A + "/sharing/pause", headers=hr, json={"minutes": 30})
    mp = {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}
    check("paused: family sees 'paused', no location", mp["Rahul"]["status"] == "paused" and mp["Rahul"]["location"] is None)
    check("paused: phone's fixes refused", fix(hr, SCHOOL).status_code == 409)
    c.post(A + "/sharing/resume", headers=hr)
    check("resumed: fixes accepted again", fix(hr, SCHOOL, t=now()).json()["accepted"])

    # ---- 409 for 'not sharing' does not get anyone signed out by the castle (only real 403s count)
    c.post(A + "/consent", headers=hm, json={"version": info["version"], "accept": True})
    c.post(A + "/consent/revoke", headers=hm)
    for _ in range(12):
        fix(hm, HOME)
    check("phone sending after stop is refused but not punished", c.get(A + "/status", headers=hm).status_code == 200)

    # ---- SOS
    delivered.clear()
    s1 = c.post(A + "/sos", headers=hr, json={"client_key": "press-0001-abc", "message": "Car broke down",
                                               "lat": SCHOOL[0], "lon": SCHOOL[1], "accuracy": 8, "recorded_at": ms()}).json()
    check("SOS creates a uniquely identified event", s1["id"].startswith("SE-") and s1["status"] == "active" and not s1["duplicate"])
    check("SOS reaches every other family member", len(alerts_for(papa["id"], "family_sos")) == 1 and len(alerts_for(mum["id"], "family_sos")) == 1)
    body = alerts_for(papa["id"], "family_sos")[0]["alert"]
    check("SOS carries location and says police were NOT called", body["payload"]["location"]["lat"] == SCHOOL[0] and "NOT contacted police" in body["body"])
    check("stranger got nothing", not [m for u, m in delivered if u == stranger["id"]])
    s1b = c.post(A + "/sos", headers=hr, json={"client_key": "press-0001-abc"}).json()
    check("same SOS sent twice (retry) -> same event, no second alert", s1b["id"] == s1["id"] and s1b["duplicate"] and len(alerts_for(papa["id"], "family_sos")) == 1)
    s1c = c.post(A + "/sos", headers=hr, json={"client_key": "press-0002-def"}).json()
    check("double press within 2 minutes -> no second alert", s1c["id"] == s1["id"] and s1c["duplicate"])
    d = c.get(f"{A}/events/{s1['id']}", headers=hr).json()
    check("sender sees delivery status per person", {x["status"] for x in d["deliveries"]} == {"sent"})
    c.post(f"{A}/events/{s1['id']}/ack", headers=hp)
    d = c.get(f"{A}/events/{s1['id']}", headers=hr).json()
    check("Papa's phone confirmed -> delivered", sorted(x["status"] for x in d["deliveries"]) == ["delivered", "sent"])
    check("stranger cannot read the SOS (no IDOR)", c.get(f"{A}/events/{s1['id']}", headers=hx).status_code == 403)
    # retries: Mum's phone did not confirm -> SOS is sent again with back-off
    with Session(engine) as s:
        dm = s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == s1["id"], fs.FsDelivery.recipient_id == mum["id"])).first()
        dm.next_attempt_at = now() - dt.timedelta(seconds=1)
        s.add(dm)
        s.commit()
        before = len(alerts_for(mum["id"], "family_sos"))
        asyncio.run(fs.process_deliveries(s))
        s.refresh(dm)
    check("unconfirmed SOS retried", len(alerts_for(mum["id"], "family_sos")) == before + 1 and dm.attempts == 2)
    check("retry re-sends the same alert (phone shows it once)", len({m["alert"]["id"] for m in alerts_for(mum["id"], "family_sos")}) == 1)
    check("only the sender can cancel", c.post(f"{A}/events/{s1['id']}/cancel", headers=hp, json={}).status_code == 403)
    r = c.post(f"{A}/events/{s1['id']}/resolve", headers=hp, json={"note": "Picked him up"}).json()
    check("family member marks the SOS resolved", r["status"] == "resolved" and r["closed_by"] == "Papa")
    with Session(engine) as s:
        dm = s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == s1["id"], fs.FsDelivery.recipient_id == mum["id"])).first()
    check("resolved SOS stops retrying", dm.next_attempt_at is None)
    s2 = c.post(A + "/sos", headers=hr, json={"client_key": "press-0003-ghi"}).json()
    check("new SOS after the first is closed", s2["id"] != s1["id"] and not s2["duplicate"])
    check("sender cancels", c.post(f"{A}/events/{s2['id']}/cancel", headers=hr, json={}).json()["status"] == "cancelled")
    # delivery failure recovery: a phone that cannot be reached is retried, then marked failed
    failing.add(mum["id"])
    with Session(engine) as s:
        ev = fs.FsEvent(family_id=g["id"], user_id=rahul["id"], kind="battery_low", status="info", detail="test")
        s.add(ev)
        s.commit()
        asyncio.run(fs.notify(s, ev, [mum["id"]], {"level": 9}))
        d3 = s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == ev.id)).first()
        check("unreachable phone: kept pending for retry", d3.status == "pending" and d3.next_attempt_at is not None)
        for _ in range(5):
            if d3.status != "pending":
                break
            d3.next_attempt_at = now() - dt.timedelta(seconds=1)
            s.add(d3)
            s.commit()
            asyncio.run(fs.process_deliveries(s))
            s.refresh(d3)
        check("after all retries: marked failed (shown in the portal)", d3.status == "failed" and d3.next_attempt_at is None)
    failing.clear()

    # ---- arrival notification
    delivered.clear()
    ar = c.post(A + "/arrivals", headers=hr, json={"name": "Home", "lat": HOME[0], "lon": HOME[1], "radius_m": 150,
                                                    "recipients": [papa["id"]], "expires_hours": 2}).json()
    check("arrival alert set with chosen recipient", ar["status"] == "active" and ar["recipients"] == [papa["id"]])
    check("recipient outside the family refused", c.post(A + "/arrivals", headers=hr, json={"name": "X", "lat": HOME[0], "lon": HOME[1],
                                                                                          "recipients": [stranger["id"]]}).status_code == 403)
    fix(hr, offset(HOME, 1500), t=now())
    check("far away: no arrival", not alerts_for(papa["id"], "family_arrival"))
    fix(hr, offset(HOME, 60), acc=400, t=now())
    check("vague fix near home ignored (GPS drift)", not alerts_for(papa["id"], "family_arrival"))
    r = fix(hr, offset(HOME, 40), acc=15, t=now()).json()
    check("arrived: Papa told once", r["events"] == ["arrival"] and len(alerts_for(papa["id"], "family_arrival")) == 1)
    check("Mum (not chosen) not told", not alerts_for(mum["id"], "family_arrival"))
    fix(hr, offset(HOME, 20), acc=10, t=now())
    check("still at home: no repeat", len(alerts_for(papa["id"], "family_arrival")) == 1)

    # ---- safe zones
    delivered.clear()
    z = c.post(A + "/zones", headers=hp, json={"name": "School", "lat": SCHOOL[0], "lon": SCHOOL[1], "radius_m": 200,
                                                "subjects": [rahul["id"]], "recipients": [papa["id"], mum["id"]]}).json()
    check("zone created", z["enabled"] and z["subjects"] == [rahul["id"]])
    check("watched member can see the zone about them", any(x["id"] == z["id"] and x["watches_me"] for x in c.get(A + "/zones", headers=hr).json()))
    check("stranger cannot add zones to this family", c.post(A + "/zones", headers=hx, json={"name": "x", "lat": 1, "lon": 1,
                                                                                           "subjects": [rahul["id"]], "recipients": [stranger["id"]]}).status_code in (403, 409))
    fix(hr, offset(SCHOOL, 1000), t=now())            # first fix: outside, sets the state, no alert
    check("first fix only sets the state", not alerts_for(papa["id"], "family_zone_enter"))
    fix(hr, offset(SCHOOL, 50), acc=10, t=now())
    check("entered school -> both recipients told", len(alerts_for(papa["id"], "family_zone_enter")) == 1 and len(alerts_for(mum["id"], "family_zone_enter")) == 1)
    fix(hr, offset(SCHOOL, 215), acc=10, t=now())     # just past the edge: inside the hysteresis band
    check("GPS wobble at the edge: no exit alert", not alerts_for(papa["id"], "family_zone_exit"))
    fix(hr, offset(SCHOOL, 400), acc=10, t=now())
    check("clearly outside -> exit alert", len(alerts_for(papa["id"], "family_zone_exit")) == 1)
    fix(hr, offset(SCHOOL, 30), acc=10, t=now())
    check("back inside within 10 min: same alert cooled down, no flood", len(alerts_for(papa["id"], "family_zone_enter")) == 1)
    fix(hr, offset(SCHOOL, 450), acc=10, t=now())
    check("leaving again within 10 min: no repeated exit alert", len(alerts_for(papa["id"], "family_zone_exit")) == 1)
    check("only the owner can change the zone", c.patch(f"{A}/zones/{z['id']}", headers=hr, json={"enabled": False}).status_code == 403)
    c.patch(f"{A}/zones/{z['id']}", headers=hp, json={"enabled": False})
    with Session(engine) as s:
        for zs in s.exec(select(fs.FsZoneState)).all():
            zs.last_event_at = zs.last_enter_at = zs.last_exit_at = now() - dt.timedelta(hours=1)
            s.add(zs)
        s.commit()
    fix(hr, offset(SCHOOL, 600), acc=10, t=now())
    check("disabled zone sends nothing", len(alerts_for(papa["id"], "family_zone_exit")) == 1)
    check("watched member can take themself out of a zone", c.post(f"{A}/zones/{z['id']}/leave", headers=hr).json()["ok"])
    check("owner deletes the zone", c.delete(f"{A}/zones/{z['id']}", headers=hp).json()["ok"])

    # ---- low battery
    delivered.clear()
    c.post(A + "/sharing/settings", headers=hr, json={"battery_threshold": 15})
    fix(hr, SCHOOL, battery=50, t=now())
    fix(hr, SCHOOL, battery=14, charging=False, t=now())
    check("battery crosses 15% -> family told", len(alerts_for(papa["id"], "family_battery_low")) == 1)
    fix(hr, SCHOOL, battery=12, charging=False, t=now())
    check("still low: no repeat", len(alerts_for(papa["id"], "family_battery_low")) == 1)
    fix(hr, SCHOOL, battery=None, t=now())
    check("unknown battery is not treated as full", len(alerts_for(papa["id"], "family_battery_low")) == 1)
    fix(hr, SCHOOL, battery=22, t=now())
    fix(hr, SCHOOL, battery=14, charging=False, t=now())
    check("re-armed but inside the 6 h cool-down: no alert", len(alerts_for(papa["id"], "family_battery_low")) == 1)
    with Session(engine) as s:
        b = s.get(fs.FsBattery, rahul["id"])
        b.last_alert_at, b.below = now() - dt.timedelta(hours=7), False
        s.add(b)
        s.commit()
    fix(hr, SCHOOL, battery=13, charging=True, t=now())
    check("charging: no alert", len(alerts_for(papa["id"], "family_battery_low")) == 1)
    fix(hr, SCHOOL, battery=13, charging=False, t=now())
    check("after the cool-down: alert again", len(alerts_for(papa["id"], "family_battery_low")) == 2)

    # ---- revoke, removal, leaving: access ends at once
    c.post(A + "/map/watch", headers=hp)
    live_msgs.clear()
    c.post(A + "/consent/revoke", headers=hr)
    mp = {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}
    check("revoked: family can no longer see Rahul", mp["Rahul"]["location"] is None and mp["Rahul"]["status"] == "off")
    with Session(engine) as s:
        check("revoked: latest location deleted from the server", s.get(fs.FsLocation, rahul["id"]) is None)
        hist = s.exec(select(fs.FsConsent).where(fs.FsConsent.user_id == rahul["id"])).all()
    check("revoke time recorded in the consent history", all(h.revoked_at for h in hist))
    check("realtime: watcher told sharing stopped", any(m["member"]["status"] == "off" for u, m in live_msgs if u == papa["id"]))
    check("revoked: new fixes refused", fix(hr, SCHOOL).status_code == 409)
    # Mum shares, then leaves through the OLD family screen: consent ends at once anyway
    c.post(A + "/consent", headers=hm, json={"version": info["version"], "accept": True})
    fix(hm, HOME, t=now())
    c.post(f"/api/family/leave/{mum['id']}", headers=hm)
    mp = {m["name"]: m for m in c.get(A + "/map", headers=hp).json()["members"]}
    check("left via the old screen: no longer on the map", "Mum" not in mp)
    with Session(engine) as s:
        check("left: consent revoked and location deleted on next check",
              fs.active_consent(s, s.get(fs.User, mum["id"])) is None and s.get(fs.FsLocation, mum["id"]) is None)
    check("non-owner cannot remove members", c.post(f"{A}/group/members/{papa['id']}/remove", headers=hr).status_code == 403)
    c.post(A + "/consent", headers=hr, json={"version": info["version"], "accept": True})
    fix(hr, SCHOOL, t=now())
    g = c.post(f"{A}/group/members/{rahul['id']}/remove", headers=hp).json()
    check("owner removes a member", [m["name"] for m in g["members"]] == ["Papa"])
    with Session(engine) as s:
        check("removed: their location deleted, consent revoked", s.get(fs.FsLocation, rahul["id"]) is None
              and fs.active_consent(s, s.get(fs.User, rahul["id"])) is None)
    check("removed member cannot read the old circle", c.get(A + "/map", headers=hr).status_code == 409)

    # ---- audit, retention
    with Session(engine) as s:
        rows = s.exec(select(fs.FsAudit)).all()
    text = " ".join(f"{a.action} {a.target} {a.detail}" for a in rows)
    check("audit log records membership, consent, SOS, zones", all(k in text for k in ("group created", "consent granted", "consent revoked",
                                                                                       "sos sent", "zone created", "member removed")))
    check("audit log never contains coordinates", "19.0" not in text and "72.8" not in text)
    with Session(engine) as s:
        c.post(A + "/consent", headers=hp, json={"version": info["version"], "accept": True})
        s.add(fs.FsLocation(user_id=papa["id"], family_id=g["id"], lat=1.5, lon=1.5, accuracy=5, recorded_at=now(),
                            received_at=now() - dt.timedelta(hours=fs.LOCATION_RETENTION_H + 1)))
        old = fs.FsEvent(family_id=g["id"], user_id=papa["id"], kind="battery_low", status="info",
                         created_at=now() - dt.timedelta(days=fs.EVENT_RETENTION_D + 1))
        s.add(old)
        s.commit()
        gone = fs.cleanup(s)
    check("retention: old locations and old events deleted", gone["locations"] >= 1 and gone["events"] >= 1)

    # ---- portal monitoring has numbers, never locations
    with Session(engine) as s:
        from app.routers import portal as rp
        snap = rp.family_safety_health_data(s)
    import json as _json
    blob = _json.dumps(snap)
    check("portal health: counts only, no names or coordinates", "location_accepted" in blob and "Rahul" not in blob and "19.0" not in blob)

# ---- migration down / up on a copy of the database
from scripts import family_safety_migrate as mig  # noqa: E402
import sqlite3  # noqa: E402
db2 = TMP / "copy.db"
src = sqlite3.connect(TMP / "fs.db")
dst = sqlite3.connect(db2)
src.backup(dst)
src.close()
dst.close()
mig.down(str(db2), backup=False)
con = sqlite3.connect(db2)
names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
con.close()
check("rollback drops only the fs_ tables", not any(n.startswith("fs_") for n in names) and {"user", "family", "alert"} <= names)
mig.up(str(db2))
con = sqlite3.connect(db2)
names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
con.close()
check("migration recreates them", {"fs_consent", "fs_location", "fs_event", "fs_delivery"} <= names)
print(f"\nALL {ok} FAMILY SAFETY CHECKS PASSED")
