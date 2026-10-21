"""Team Portal: sign-in (SMS code + authenticator), roles, every section, telecom keys, AI switch, lockdown, audit.

Own temporary database, weights folder and security baseline - the real AI files and data are never touched.
Run: .venv\\Scripts\\python scripts\\portal_test.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ.update({
    "VG_WARMUP": "0", "VG_DISCOVERY": "0", "VG_DAILY_REPORT": "0", "VG_DB": str(TMP / "portal.db"), "VG_OTP_PROVIDER": "console",
    "TWILIO_WHATSAPP_FROM": "", "VG_FIREBASE_CREDENTIALS": str(TMP / "none.json"), "VG_TRUST_PROXY": "1",
    "VG_OTP_TEST_NUMBERS": "+919876533001:111111,+919876533002:222222,+919876533003:333333,+919876533004:444444,"
                           "+919876533009:999999",
    "VG_ADMIN_PHONES": "+919876533001", "VG_PORTAL_OWNERS": "", "VG_SEC_OTP_IP": "6", "VG_SEC_BAD_TOKEN": "500",
    "VG_SEC_FORBIDDEN_IP": "500", "VG_SEC_FORBIDDEN_USER": "500",
})
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import config, daily_report, portal as P, security as sec  # noqa: E402
from app.db import Evidence, User, engine, now  # noqa: E402
from app.hub import hub  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import portal as rp  # noqa: E402

config.OTP_RESEND_S, config.OTP_MAX_PER_HOUR = 0, 1000
sec.BASELINE, sec.BACKUP_DIR = TMP / "baseline.json", TMP / "backups"
W = TMP / "weights"
W.mkdir()
(W / "vg_detector.npz").write_bytes(b"current-model")
(W / "vg_detector_report.json").write_text('{"auc_all": 0.98, "clips": 100, "by_condition": {"clean": {"auc": 0.99, "acc": 0.97}}}')
(W / "vg_detector_mlp_candidate.npz").write_bytes(b"better-model")
(W / "vg_detector_mlp_candidate_report.json").write_text('{"head": "mlp", "auc_all": 0.99, "clips": 120, "by_condition": {}}')
rp.WDIR = W
sent = []


async def fake_deliver(uid, msg):
    sent.append((uid, msg))
    return True
hub.deliver = fake_deliver

ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        raise SystemExit(1)
    ok += 1


def net(n):   # a computer on the internet, reaching the portal through an HTTPS proxy
    return {"X-Forwarded-For": f"10.9.0.{n}", "X-Forwarded-Proto": "https"}


def alerts_about(word):
    time.sleep(0.4)
    return [m for _, m in sent if word.lower() in (m["alert"]["title"] + m["alert"]["body"]).lower()]


secrets_, used = {}, {}


def code(phone, wrong=False):
    """Next authenticator code for a member (a code works once, so step forward like a real app would)."""
    st = int(time.time() // 30)
    if wrong:
        return f"{(int(P.totp_at(secrets_[phone], st * 30)) + 1) % 1_000_000:06d}"
    for d in (-1, 0, 1):
        if st + d > used.get(phone, 0):
            used[phone] = st + d
            return P.totp_at(secrets_[phone], (st + d) * 30)
    raise RuntimeError("no fresh code")


with TestClient(app) as c:
    # app accounts: the owner (also the admin who receives alerts) and a family user with a saved scam call
    c.post("/api/auth/otp/start", json={"phone": "9876533001"})
    admin = c.post("/api/auth/otp/verify", json={"phone": "9876533001", "code": "111111", "name": "Kunal"}).json()["user"]
    c.post("/api/auth/otp/start", json={"phone": "9876533004"})
    ra = c.post("/api/auth/otp/verify", json={"phone": "9876533004", "code": "444444", "name": "Asha"}).json()
    asha, hasha = ra["user"], {"Authorization": "Bearer " + ra["token"]}
    with Session(engine) as s:
        s.add(Evidence(id="ev1", user_id=asha["id"], number="+919000000777", claimed_name="Rahul", risk_score=91, level="danger",
                       transcript="Papa I am in trouble, send money now", report_json='{"risk": {"reasons": ["AI voice"]}}'))
        s.commit()

    def sign_in(phone, otp, n, setup=None):
        c.post("/api/portal/login/start", json={"phone": phone}, headers=net(n))
        if phone not in secrets_:
            r = c.post("/api/portal/login/verify", json={"phone": phone, "otp": otp}, headers=net(n)).json()
            assert r["stage"] == "setup", r
            if setup:
                setup(r)
            secrets_[phone] = r["secret"].replace(" ", "")
            r = c.post("/api/portal/totp/confirm", json={"code": code(phone)}, headers={"Authorization": "Bearer " + r["token"], **net(n)}).json()
        else:
            r = c.post("/api/portal/login/verify", json={"phone": phone, "otp": otp, "code": code(phone)}, headers=net(n)).json()
        return {"Authorization": "Bearer " + r["token"], **net(n)}

    # ---- the page itself
    r = c.get("/portal")
    csp = r.headers.get("content-security-policy", "")
    check("portal page served with a strict policy", r.status_code == 200 and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
          and r.headers.get("x-frame-options") == "DENY" and r.headers.get("cache-control") == "no-store")
    check("page has no inline script", "<script>" not in r.text and "onclick" not in r.text)
    check("plain http from another computer is refused", c.get("/portal", headers={"X-Forwarded-For": "10.9.0.99"}).status_code == 403)
    check("over HTTPS it opens", c.get("/portal", headers=net(99)).status_code == 200 and c.get("/portal/app.js", headers=net(99)).status_code == 200)
    check("portal script is covered by the integrity check", "static/portal.js" in sec.file_hashes())
    check("portal hidden from the public internet tunnel", c.get("/portal", headers={"CF-Connecting-IP": "203.0.113.9"}).status_code == 404
          and c.post("/api/portal/login/start", json={"phone": "9876533001"}, headers={"CF-Connecting-IP": "203.0.113.9"}).status_code in (403, 404))
    check("portal hidden from the tunnel even when the server already sees the visitor's address",
          c.get("/portal", headers={"X-Forwarded-For": "49.43.43.7", "X-Forwarded-Proto": "https", "CF-Connecting-IP": "49.43.43.7"}).status_code == 404)

    # ---- outsiders
    r1 = c.post("/api/portal/login/start", json={"phone": "9876533009"}, headers=net(1)).json()
    r2 = c.post("/api/portal/login/start", json={"phone": "9876533001"}, headers=net(1)).json()
    check("same answer for members and outsiders (no member list leak)", r1 == r2)
    check("admins alerted: outsider tried the portal", len(alerts_about("outside the team")) >= 1)
    r = c.post("/api/portal/login/verify", json={"phone": "9876533009", "otp": "999999"}, headers=net(1))
    check("outsider with a valid app SMS code still cannot sign in", r.status_code == 400 and "Sign-in failed" in r.json()["detail"])
    check("no token -> 401", c.get("/api/portal/dashboard", headers=net(1)).status_code == 401)

    # ---- owner first sign-in: authenticator set-up
    seen = {}
    ho = sign_in("9876533001", "111111", 2, setup=lambda r: seen.update(r))
    check("first sign-in shows a QR code to set up the authenticator", seen["qr_svg"].lstrip().startswith("<svg") and len(seen["secret"]) > 20)
    check("QR code scales without being cut off (viewBox, no fixed size)", "viewBox" in seen["qr_svg"] and 'width="' not in seen["qr_svg"].split(">")[0])
    with Session(engine) as s:
        m = s.exec(select(P.TeamMember).where(P.TeamMember.phone == "+919876533001")).first()
    check("authenticator key is stored sealed, not readable", m.totp_sealed and seen["secret"].replace(" ", "") not in m.totp_sealed)
    me = c.get("/api/portal/me", headers=ho).json()
    check("owner signed in with every section", me["role"] == "owner" and {"team", "ai", "security", "evidence", "telecom"} <= set(me["can"]))
    r = c.post("/api/portal/login/verify", json={"phone": "9876533001", "otp": "111111"}, headers=net(2))
    check("after set-up the authenticator code is required", r.status_code == 400 and "authenticator" in r.json()["detail"])

    # set-up token cannot open data
    c.post("/api/portal/team", json={"phone": "9876533002", "name": "Priya", "role": "fraud_analyst"}, headers=ho)
    c.post("/api/portal/login/start", json={"phone": "9876533002"}, headers=net(3))
    st = c.post("/api/portal/login/verify", json={"phone": "9876533002", "otp": "222222"}, headers=net(3)).json()
    hset = {"Authorization": "Bearer " + st["token"], **net(3)}
    check("a half-finished set-up cannot open data", c.get("/api/portal/dashboard", headers=hset).status_code == 403)
    check("wrong set-up code refused", c.post("/api/portal/totp/confirm", json={"code": "000000"}, headers=hset).status_code == 400)
    secrets_["9876533002"] = st["secret"].replace(" ", "")
    r = c.post("/api/portal/totp/confirm", json={"code": code("9876533002")}, headers=hset).json()
    hp = {"Authorization": "Bearer " + r["token"], **net(3)}
    check("analyst finished set-up", c.get("/api/portal/me", headers=hp).json()["role"] == "fraud_analyst")
    check("code replay refused", not P.totp_check(m, P.totp_at(seen["secret"].replace(" ", ""), used["9876533001"] * 30)))

    # ---- team
    check("admins alerted: new team member", len(alerts_about("new member")) >= 1)
    r = c.post("/api/portal/team", json={"phone": "9876533003", "name": "Dev", "role": "viewer"}, headers=ho)
    check("owner adds a viewer", r.status_code == 200 and r.json()["two_step"] is False)
    check("same number twice refused", c.post("/api/portal/team", json={"phone": "9876533003", "name": "X", "role": "viewer"}, headers=ho).status_code == 409)
    check("bad role refused", c.post("/api/portal/team", json={"phone": "9876533005", "name": "X", "role": "god"}, headers=ho).status_code == 403)
    owner_id = c.get("/api/portal/team", headers=ho).json()["members"][0]["id"]
    check("owner cannot switch themself off", c.post(f"/api/portal/team/{owner_id}", json={"active": False}, headers=ho).status_code == 400)
    hv = sign_in("9876533003", "333333", 4)

    # ---- roles
    check("viewer sees the dashboard", c.get("/api/portal/dashboard", headers=hv).status_code == 200)
    check("viewer cannot open fraud, evidence, team, AI", all(c.get("/api/portal/" + p, headers=hv).status_code == 403
                                                               for p in ("fraud/numbers", "evidence", "team", "ai", "security", "audit")))
    check("admins alerted: access outside a role", len(alerts_about("outside a member's role")) >= 1)
    check("analyst cannot open team or AI", c.get("/api/portal/team", headers=hp).status_code == 403 and c.get("/api/portal/ai", headers=hp).status_code == 403)

    # ---- dashboard
    d = c.get("/api/portal/dashboard", headers=ho).json()
    check("dashboard totals", d["people"]["users"] == 2 and d["evidence"]["total"] == 1 and len(d["series"]) == 14)

    # ---- fraud
    r = c.post("/api/portal/fraud/flag", json={"number": "9000000888", "reason": "Fake KYC calls"}, headers=hp)
    check("analyst flags a number", r.status_code == 200 and r.json()["number"] == "+919000000888")
    nums = c.get("/api/portal/fraud/numbers?q=9000000888", headers=hp).json()
    check("number listed with its reason", nums and nums[0]["sources"] == ["team"] and "Fake KYC" in nums[0]["reason"])
    check("app lookup now warns about it", c.get("/api/v1/telecom/lookup/9000000888").json()["reports"] == 1)
    check("clearing needs a reason", c.post("/api/portal/fraud/numbers/+919000000888/clear", json={"reason": " "}, headers=hp).status_code == 400)
    r = c.post("/api/portal/fraud/numbers/+919000000888/clear", json={"reason": "bank's real number"}, headers=hp).json()
    check("false alarm cleared", r["removed"] == 1 and c.get("/api/v1/telecom/lookup/9000000888").json()["reports"] == 0)
    exp = c.get("/api/portal/fraud/export.csv", headers=hp)
    check("CSV export works", exp.status_code == 200 and exp.text.startswith("number,reports"))

    # ---- evidence
    ev = c.get("/api/portal/evidence", headers=hp).json()
    check("evidence listed with case status", len(ev) == 1 and ev[0]["status"] == "new" and ev[0]["user"] == "Asha")
    e = c.get("/api/portal/evidence/ev1", headers=hp).json()
    check("evidence detail with transcript", "trouble" in e["transcript"] and e["user_phone"] == "+919876533004")
    r = c.post("/api/portal/evidence/ev1/case", json={"status": "sent_to_police", "assign_to_me": True, "note": "Complaint 1930-123"}, headers=hp).json()
    check("case moved, assigned and noted", r["status"] == "sent_to_police" and r["assigned_to"] == "Priya" and r["notes"][0]["text"] == "Complaint 1930-123")
    check("bad case status refused", c.post("/api/portal/evidence/ev1/case", json={"status": "deleted"}, headers=hp).status_code == 400)
    check("no recording -> 404", c.get("/api/portal/evidence/ev1/audio", headers=hp).status_code == 404)
    check("phone numbers hidden from roles without access", P.mask("+919876533004", False) == "+9198•••3004")

    # ---- telecom
    r = c.post("/api/portal/telecom/partners", json={"name": "Jio"}, headers=hp)
    check("only owner/admin create operator keys", r.status_code == 403)
    key = c.post("/api/portal/telecom/partners", json={"name": "Jio"}, headers=ho).json()["key"]
    check("admins alerted: new telecom key", len(alerts_about("telecom partner key")) >= 1)
    parts = c.get("/api/portal/telecom/partners", headers=hp).json()
    check("key stored as a hash only", parts[0]["key_hint"].startswith(key[:10]) and "key_hash" not in parts[0] and "key" not in parts[0])
    r = c.post("/api/v1/telecom/flag", json={"number": "9000000999", "reason": "SIM box", "operator": "Fake"}, headers={"X-API-Key": key})
    check("operator key works", r.status_code == 200)
    flags = c.get("/api/portal/telecom/flags", headers=hp).json()
    check("operator name comes from the key, not the request", flags[0]["reason"].startswith("[Jio]"))
    c.get("/api/v1/telecom/lookup/9000000999", headers={"X-API-Key": key})
    check("bad key refused", c.post("/api/v1/telecom/flag", json={"number": "9000000999"}, headers={"X-API-Key": "vgtel_wrong"}).status_code == 401)
    evs = c.get("/api/portal/telecom/events", headers=hp).json()
    check("operator activity logged (flag, lookup, bad key)", {"flag", "lookup", "bad_key"} <= {x["kind"] for x in evs})
    pid = parts[0]["id"]
    c.post(f"/api/portal/telecom/partners/{pid}/revoke", headers=ho)
    check("revoked key stops working", c.post("/api/v1/telecom/flag", json={"number": "9000000999"}, headers={"X-API-Key": key}).status_code == 401)
    check("demo key still works", c.post("/api/v1/telecom/flag", json={"number": "9000000999"}, headers={"X-API-Key": "demo-telecom-key"}).status_code == 200)

    # ---- AI
    a = c.get("/api/portal/ai", headers=ho).json()
    check("AI page lists live model and candidate", a["slots"]["detector"]["active"]["auc"] == 0.98
          and [x["file"] for x in a["slots"]["detector"]["choices"]] == ["vg_detector_mlp_candidate.npz"])
    check("switch needs a known file", c.post("/api/portal/ai/activate", json={"slot": "detector", "file": "../../main.py", "reason": "x"}, headers=ho).status_code == 400)
    r = c.post("/api/portal/ai/activate", json={"slot": "detector", "file": "vg_detector_mlp_candidate.npz", "reason": "better on unseen voices"}, headers=ho).json()
    check("candidate made live, old one kept", (W / "vg_detector.npz").read_bytes() == b"better-model" and (W / r["backup"]).read_bytes() == b"current-model")
    check("switch approved as trusted (no tamper lockdown)", sec.lockdown() is None and sec.baseline().get("approved_by", "").endswith("(AI model switch)"))
    check("admins alerted: AI model switched", len(alerts_about("AI model switched")) >= 1)
    a = c.get("/api/portal/ai", headers=ho).json()
    check("live model shows who switched it", a["slots"]["detector"]["active"]["activated_by"] == "Owner")

    # ---- network: how the app works on each operator
    from app.routers.app_reports import operator_name
    check("operator names normalised", [operator_name(x) for x in ("JIO 4G", "Vi India", "airtel", "BSNL Mobile", "Vivo", "")]
          == ["Jio", "Vi", "Airtel", "BSNL", "Vivo", "Unknown"] and operator_name("", "40445") == "Airtel")
    rep = {"install_id": "install-asha-1", "app_version": "0.1", "model": "samsung SM-F711B", "android": "15",
           "protection": {"call_screening": True, "microphone": True, "notifications": True},
           "counters": {"api_ok": 98, "api_server_errors": 1, "api_unreachable": 1, "api_gets": 50, "api_get_ms": 15000, "ws_drops": 2,
                        "calls_screened": 7},
           "network": {"operator": "JIO 4G", "mcc_mnc": "405857", "transport": "cellular", "gen": "4G", "signal": 3, "roaming": False,
                       "country": "in", "downlink_kbps": 25000,
                       "sims": [{"carrier": "Jio 4G", "mcc_mnc": "405857", "slot": 0, "data": True},
                                {"carrier": "airtel", "mcc_mnc": "40445", "slot": 1, "data": False}]}}
    check("phone sends its network with the report", c.post("/api/app/report", json=rep, headers=hasha).status_code == 200)
    daily_report.log_check(asha["id"], "voice_note", {"ok": True, "risk": {"level": "danger", "score": 88},
                                                     "ai_voice": {"fake_prob": 0.93, "model": "voiceguard-wavlm-phone-v4"},
                                                     "reverse_engineering": {"kind": "ai_speech"}, "timings_s": {"total": 3.4},
                                                     "phone_quality": {"narrowband": True}})
    daily_report.log_check(asha["id"], "live_call", None, error="only_owner")
    nw = c.get("/api/portal/network?days=7", headers=ho).json()
    jio = next((o for o in nw["operators"] if o["operator"] == "Jio"), None)
    check("network page: phone counted under Jio on 4G", jio is not None and jio["phones"] == 1 and jio["gens"] == {"4G": 1}, str(nw["operators"])[:300])
    check("network page: 98% of server calls worked, 300 ms average", jio["api_success"] == 0.98 and jio["avg_ms"] == 300 and jio["health"] == "good", str(jio))
    check("network page: AI checks per operator", jio["ai"]["checks"] == 1 and jio["ai"]["danger"] == 1 and jio["ai"]["avg_seconds"] == 3.4)
    check("network page: phone list shows both SIMs", nw["phones"][0]["sims"] == ["Jio", "Airtel"] and nw["phones"][0]["user"] == "Asha")
    check("viewer cannot open the network page", c.get("/api/portal/network", headers=hv).status_code == 403)

    # ---- logs
    lg = c.get("/api/portal/logs/ai", headers=ho).json()
    check("AI check log lists results and problems", {x["source"] for x in lg} >= {"voice_note", "live_call"}
          and any(not x["ok"] and x["error"] == "only_owner" for x in lg))
    check("AI log can show only problems", all(not x["ok"] for x in c.get("/api/portal/logs/ai?problems=true", headers=ho).json()))
    sl = c.get("/api/portal/logs/security?layer=2", headers=ho).json()
    check("security event log by layer", sl and all(e["layer"] == 2 for e in sl))
    check("server log readable by security roles", "lines" in c.get("/api/portal/logs/server", headers=ho).json())
    check("evidence activity log", any(x["what"] == "evidence saved" for x in c.get("/api/portal/logs/evidence", headers=hp).json()))
    check("logs only for the right roles", c.get("/api/portal/logs/ai", headers=hv).status_code == 403
          and c.get("/api/portal/logs/server", headers=hp).status_code == 403)

    # ---- daily reports
    with Session(engine) as s:
        s.add(Evidence(id="ev2", user_id=asha["id"], claimed_name="<img src=x onerror=alert(1)>", risk_score=60, level="suspicious"))
        s.commit()
    today = daily_report.today().isoformat()
    rl = c.get("/api/portal/reports", headers=ho).json()
    check("reports list has today (live)", rl["today"]["day"] == today and rl["today"]["headline"]["ai_checks"] >= 2)
    r = c.get(f"/api/portal/reports/{today}", headers=ho).json()
    check("today's report covers security, evidence, AI, fraud, network, team", {"security", "evidence", "ai", "fraud", "network", "team"} <= set(r))
    check("report counts AI checks, AI voices and problems", r["ai"]["checks"] >= 2 and r["ai"]["ai_voices"] >= 1 and r["ai"]["errors"].get("only_owner") == 1
          and r["ai"]["avg_seconds"] == 3.4)
    check("report counts security events and new evidence", r["security"]["events"] > 0 and r["evidence"]["new"] >= 2)
    rv = c.get(f"/api/portal/reports/{today}", headers=hv).json()
    check("viewer sees only the headline of a report", "headline" in rv and not ({"security", "evidence", "ai", "network"} & set(rv)))
    dl = c.get(f"/api/portal/reports/{today}/download", headers=ho)
    check("report downloads as a page with no scripts", dl.status_code == 200 and "VoiceGuard daily report" in dl.text and "<script" not in dl.text)
    check("text in the report is escaped", "<img src=x" not in dl.text and "&lt;img src=x" in dl.text)
    check("bad or future day refused", c.get("/api/portal/reports/yesterday", headers=ho).status_code == 400
          and c.get("/api/portal/reports/2999-01-01", headers=ho).status_code == 404)
    built = daily_report.run_due()
    check("yesterday's report is built once and stored", built == (daily_report.today() - P.dt.timedelta(days=1)).isoformat()
          and daily_report.run_due() is None and built in [x["day"] for x in c.get("/api/portal/reports", headers=ho).json()["days"]])
    check("admins get the daily summary", len(alerts_about("daily report")) >= 1)

    # ---- security & lockdown
    sv = c.get("/api/portal/security", headers=ho).json()
    check("security view has the seven layers", len(sv["layers"]) == 7)
    check("lockdown needs a reason", c.post("/api/portal/security/lockdown", json={"on": True}, headers=ho).status_code == 400)
    c.post("/api/portal/security/lockdown", json={"on": True, "reason": "test"}, headers=ho)
    check("lockdown stops the app", c.get("/api/demo/clips").status_code == 503)
    check("portal keeps working in lockdown", c.get("/api/portal/security", headers=ho).status_code == 200)
    c.post("/api/portal/security/lockdown", json={"on": False}, headers=ho)
    check("lockdown lifted", c.get("/api/demo/clips").status_code == 200)

    # ---- lock-out after wrong codes, unlock by the owner
    for _ in range(P.LOCK_AFTER):
        c.post("/api/portal/login/verify", json={"phone": "9876533003", "otp": "333333", "code": code("9876533003", wrong=True)},
               headers=net(5))
    check("admins alerted: account locked", len(alerts_about("account locked")) >= 1)
    used["9876533003"] = 0
    c.post("/api/portal/login/start", json={"phone": "9876533003"}, headers=net(6))
    r = c.post("/api/portal/login/verify", json={"phone": "9876533003", "otp": "333333", "code": code("9876533003")}, headers=net(6))
    check("locked account cannot sign in even with right codes", r.status_code == 400)
    check("locked account's open session stops", c.get("/api/portal/dashboard", headers=hv).status_code == 401)
    vid = next(x["id"] for x in c.get("/api/portal/team", headers=ho).json()["members"] if x["name"] == "Dev")
    c.post(f"/api/portal/team/{vid}/unlock", headers=ho)
    with Session(engine) as s:
        dev = s.get(P.TeamMember, vid)
        dev.totp_last_step = 0            # the test re-uses this time window's codes
        s.add(dev)
        s.commit()
    used["9876533003"] = 0
    hv = sign_in("9876533003", "333333", 6)
    check("unlocked member signs in again", c.get("/api/portal/me", headers=hv).status_code == 200)

    # ---- guessing from one address
    for _ in range(6):
        c.post("/api/portal/login/verify", json={"phone": "9876533009", "otp": "123456"}, headers=net(7))
    check("address guessing sign-ins is blocked", c.get("/portal", headers=net(7)).status_code == 403)

    # ---- sessions, idle time-out, sign-out
    ss = c.get("/api/portal/sessions", headers=ho).json()
    check("owner sees the team's sessions", len({x["member"] for x in ss}) >= 2)
    with Session(engine) as s:
        x = s.exec(select(P.PortalSession).where(P.PortalSession.revoked == False)).all()   # noqa: E712
        tok = hv["Authorization"][7:]
        mine = next(r for r in x if r.token_hash == P._h(tok))
        mine.last_used = now() - P.dt.timedelta(minutes=P.IDLE_MIN + 1)
        s.add(mine)
        s.commit()
    check("idle session ends", c.get("/api/portal/me", headers=hv).status_code == 401)
    c.post("/api/portal/logout", headers=hp)
    check("signed-out token is dead", c.get("/api/portal/me", headers=hp).status_code == 401)
    r = c.post(f"/api/portal/team/{vid}/reset-2fa", headers=ho).json()
    check("reset authenticator (lost phone)", r["two_step"] is False)

    # ---- audit
    au = c.get("/api/portal/audit", headers=ho).json()
    acts = {x["action"] for x in au}
    check("audit log records sign-ins, views, changes", {"login", "flagged number", "cleared number", "viewed evidence", "evidence case",
                                                          "created telecom key", "switched AI model", "lockdown on", "denied", "exported fraud numbers"} <= acts, str(acts))
    check("every alert went only to the admin", {uid for uid, _ in sent} == {admin["id"]})

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nALL {ok} PORTAL CHECKS PASSED")
