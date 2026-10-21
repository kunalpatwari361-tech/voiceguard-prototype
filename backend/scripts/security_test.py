"""Attack every castle layer and check it notices, stops the attacker, and alerts the admin team.

Uses its own temporary database and small thresholds. Different attacker addresses are simulated with
X-Forwarded-For (VG_TRUST_PROXY=1). Run: .venv\\Scripts\\python scripts\\security_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ.update({
    "VG_WARMUP": "0", "VG_DISCOVERY": "0", "VG_DAILY_REPORT": "0", "VG_DB": str(TMP / "sec.db"), "VG_OTP_PROVIDER": "console",
    "TWILIO_WHATSAPP_FROM": "", "VG_FIREBASE_CREDENTIALS": str(TMP / "none.json"), "VG_TRUST_PROXY": "1",
    "VG_OTP_TEST_NUMBERS": "+919876522001:111111,+919876522002:222222,+919876522009:999999",
    "VG_ADMIN_PHONES": "+919876522009", "VG_APP_CERTS": "goodcert", "VG_APP_TRUST_MODE": "monitor",
    "VG_SEC_SCAN": "5", "VG_SEC_FLOOD": "40", "VG_SEC_BAD_TOKEN": "5", "VG_SEC_OTP_IP": "3", "VG_SEC_OTP_PHONE": "3",
    "VG_SEC_FORBIDDEN_USER": "3", "VG_SEC_FORBIDDEN_IP": "100", "VG_SEC_ATTACK_IP": "3", "VG_SEC_SCRAPE": "5",
})
from fastapi.testclient import TestClient  # noqa: E402

from app import security as sec  # noqa: E402
from app.hub import hub  # noqa: E402
from app.main import app  # noqa: E402

sec.BACKUP_DIR = TMP / "backups"
sent = []


async def fake_deliver(uid, msg):   # capture what the admin team would receive
    sent.append((uid, msg))
    return True
hub.deliver = fake_deliver


@app.get("/api/_test_boom")
def boom():
    raise RuntimeError("simulated bug")


ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        raise SystemExit(1)
    ok += 1


def ip(n):
    return {"X-Forwarded-For": f"10.0.0.{n}"}


def alerts_about(word):
    time.sleep(0.4)
    return [m for _, m in sent if word.lower() in (m["alert"]["title"] + m["alert"]["body"]).lower()]


with TestClient(app) as c:
    def login(phone, code, name, n):
        c.post("/api/auth/otp/start", json={"phone": phone}, headers=ip(n))
        r = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code, "name": name}, headers=ip(n)).json()
        return r["user"], {"Authorization": "Bearer " + r["token"]}

    admin, ha = login("9876522009", "999999", "Admin", 200)
    asha, hasha = login("9876522001", "111111", "Asha", 201)
    ravi, hravi = login("9876522002", "222222", "Ravi", 202)
    A = lambda n: {**ha, **ip(200)}  # noqa: E731

    # ---- internet tunnel (cloudflared on this computer): the real visitor is blocked, not everyone
    tun = {"CF-Connecting-IP": "203.0.113.77"}
    for k in range(5):
        c.get(f"/phpmyadmin/{k}", headers=tun)
    check("tunnel: the scanner's real address is blocked", c.get("/api/health", headers=tun).status_code == 403)
    check("tunnel: everyone else still gets in", c.get("/api/health").status_code == 200
          and c.get("/api/health", headers={"CF-Connecting-IP": "203.0.113.78"}).status_code == 200)
    r = c.post("/api/auth/otp/start", json={"phone": "9876522001"}, headers={"CF-Connecting-IP": "8.8.4.4"})
    check("demo numbers refused from the internet", r.status_code == 403 and "home Wi-Fi" in r.json()["detail"])
    check("demo numbers still work at home", c.post("/api/auth/otp/start", json={"phone": "9876522001"}, headers=ip(203)).status_code == 200)
    check("admins alerted: demo number from the internet", len(alerts_about("Demo number used from the internet")) >= 1)
    c.post("/api/auth/otp/verify", json={"phone": "9876522001", "code": "111111", "name": "Asha"}, headers=ip(203))

    # ---- Layer 1 Moat: scanner and flood
    for k in range(5):
        c.get(f"/wp-admin/{k}.php", headers=ip(1))
    check("L1 scanner is blocked", c.get("/api/health", headers=ip(1)).status_code == 403)
    check("L1 admin alerted about the scanner", len(alerts_about("Scanner")) >= 1)
    for _ in range(40):
        c.get("/api/health", headers=ip(2))
    check("L1 request flood is blocked", c.get("/api/health", headers=ip(2)).status_code == 403)

    # ---- Layer 2 Outer wall: fake tokens, OTP guessing
    for _ in range(5):
        c.get("/api/auth/me", headers={"Authorization": "Bearer fake-token", **ip(3)})
    check("L2 fake / stolen tokens -> address blocked", c.get("/api/health", headers=ip(3)).status_code == 403)
    c.post("/api/auth/otp/start", json={"phone": "9876500055"}, headers=ip(4))
    for _ in range(3):
        c.post("/api/auth/otp/verify", json={"phone": "9876500055", "code": "000000", "name": "x"}, headers=ip(4))
    check("L2 OTP guessing -> attacker blocked", c.get("/api/health", headers=ip(4)).status_code == 403)
    r = c.post("/api/auth/otp/start", json={"phone": "9876500055"}, headers=ip(5))
    check("L2 the targeted number's sign-in is paused (other addresses too)", r.status_code == 429, r.text[:80])

    # ---- Layer 3 Gatehouse: an account reaching for admin pages / other people's data
    for _ in range(3):
        c.get("/api/admin/console/status", headers={**hravi, **ip(6)})
    check("L3 admin probing raised an alert", len(alerts_about("Admin area probed")) >= 1)
    r = c.get("/api/auth/me", headers={**hravi, **ip(6)})
    check("L3 offending account is signed out / paused", r.status_code in (401, 429), str(r.status_code))

    # ---- Layer 4 Inner wall: injection, traversal, oversized
    r = c.get("/api/caller-identity/lookup/1' OR '1'='1", headers={**hasha, **ip(7)})
    check("L4 SQL injection blocked", r.status_code == 400 and "security" in r.text)
    r = c.post("/api/caller-identity/report-spam", json={"number": "9123400001", "reason": "<script>alert(1)</script>"}, headers={**hasha, **ip(7)})
    check("L4 script injection in a JSON body blocked", r.status_code == 400)
    c.get("/api/x/..%2F..%2Fetc%2Fpasswd", headers=ip(7))
    check("L4 repeat attacker blocked", c.get("/api/health", headers=ip(7)).status_code == 403)
    r = c.post("/api/caller-identity/report-spam", headers={**hasha, **ip(8), "Content-Length": str(200 * 1024 * 1024), "Content-Type": "application/json"}, content=b"{}")
    check("L4 oversized request rejected", r.status_code in (413, 400), str(r.status_code))
    check("normal requests from other people still work", c.get("/api/caller-identity/lookup/9123400001", headers={**hasha, **ip(9)}).status_code == 200)

    # ---- Layer 5 Keep: scraping, bugs, backups
    scraper, hs = login("9876522001", "111111", "Asha", 10)
    for k in range(5):
        c.get(f"/api/caller-identity/lookup/91234{k:05d}", headers={**hs, **ip(10)})
    r = c.get("/api/caller-identity/lookup/9123499999", headers={**hs, **ip(10)})
    check("L5 bulk scraping pauses the account", r.status_code == 429, str(r.status_code))
    r = c.get("/api/_test_boom", headers=ip(11))
    check("L5 server bug answered safely with an error id", r.status_code == 500 and r.json().get("error_id"))

    # ---- Layer 7 Royal guard: modified app copy
    r = c.get("/api/caller-identity/lookup/9123400002", headers={**hasha, **ip(12), "X-VG-App": "0.1;badcert;1"})
    check("L7 unknown app signature recorded (monitor mode lets it through)", r.status_code == 200 and len(alerts_about("unknown app")) >= 1)

    # ---- Layer 6 Vault: code changed while running -> lockdown, admin can still act
    fake = dict(sec._snapshot["files"])
    fake["routers/checks.py"] = "tampered"
    check("L6 tampering detected", sec.tamper_check(fake) is True)
    r = c.get("/api/caller-identity/lookup/9123400003", headers={**hasha, **ip(13)})
    check("L6 lockdown stops user traffic immediately (503)", r.status_code == 503 and r.json().get("lockdown"))
    check("L6 admin alerted (critical)", len(alerts_about("changed while running")) >= 1)
    check("admin console still works during lockdown", c.get("/api/admin/console/status", headers=A(0)).json()["lockdown"] is not None)
    c.post("/api/admin/console/lockdown", json={"on": False}, headers=A(0))
    check("admin ends the lockdown -> users served again", c.get("/api/caller-identity/lookup/9123400003", headers={**hasha, **ip(13)}).status_code == 200)

    # ---- Admin Console
    check("console page loads", c.get("/admin/console", headers=ip(14)).status_code == 200)
    check("non-admin refused by the console API", c.get("/api/admin/console/status", headers={**hasha, **ip(14)}).status_code == 403)
    st = c.get("/api/admin/console/status", headers=A(0)).json()
    states = {l["id"]: l["state"] for l in st["layers"]}
    check("status shows all 7 layers and which were breached", len(st["layers"]) == 7 and states[1] == "breached" and states[4] == "breached", str(states))
    inc = c.get("/api/admin/console/incidents", headers=A(0)).json()
    check("incidents listed with the action taken", len(inc) >= 8 and any("blocked" in i["action"] for i in inc), str(len(inc)))
    r = c.post(f"/api/admin/console/incidents/{inc[0]['id']}/resolve", headers=A(0)).json()
    check("admin can resolve an incident", r["status"] == "resolved")
    blocks = c.get("/api/admin/console/blocks", headers=A(0)).json()
    scan_block = [b for b in blocks if b["value"] == "10.0.0.1"][0]
    c.request("DELETE", f"/api/admin/console/blocks/{scan_block['id']}", headers=A(0))
    check("admin can unblock an address", c.get("/api/health", headers=ip(1)).status_code == 200)
    d = c.get("/api/admin/console/data", headers=A(0)).json()
    check("data page lists tables", d["tables"].get("user", 0) >= 3 and "sec_incident" in d["tables"])
    b = c.post("/api/admin/console/data/backup", headers=A(0)).json()
    check("backup made", (sec.BACKUP_DIR / b["file"]).exists())
    check("database integrity check", c.post("/api/admin/console/data/check", headers=A(0)).json()["result"] == "ok")
    check("logs readable", isinstance(c.get("/api/admin/console/logs?lines=50", headers=A(0)).json()["lines"], list))
    check("reports available", c.get("/api/admin/console/reports", headers=A(0)).json()["users"] >= 3)
    up = c.get("/api/admin/console/updates", headers=A(0)).json()
    check("updates show versions and integrity", "integrity" in up and up["ai_models"]["detector"])
    check("test alert reaches admins", c.post("/api/admin/console/test-alert", headers=A(0)).status_code == 200 and len(alerts_about("Test alert")) >= 1)
    # ---- Data and security sent by the app on the phones
    rep = {"install_id": "phone-asha-0001", "app_version": "0.1-prototype", "cert": "goodcert", "model": "samsung SM-F711B",
           "android": "15 (API 35)", "security": {"rooted": False, "debugger_attached": False, "debuggable": False, "emulator": False,
                                                 "adb_enabled": True, "screen_lock": True},
           "protection": {"call_screening": True, "default_dialer": True, "microphone": True, "notifications": True, "overlay": True},
           "settings": {"auto_block": True, "language": "hi"}, "counters": {"calls_screened": 4, "spam_warned": 1, "ai_checks": 2}, "crashes": []}
    r = c.post("/api/app/report", json=rep, headers={**hasha, **ip(20)}).json()
    check("phone report accepted (USB debugging noted, no alert)", r["ok"] and r["risks"] == ["usb_debugging"], str(r))
    dv = c.get("/api/admin/console/devices", headers=A(0)).json()
    check("console lists the phone, its app version and protection",
          dv["summary"]["phones"] == 1 and dv["devices"][0]["model"] == "samsung SM-F711B" and dv["devices"][0]["protection_off"] == [])
    bad = rep | {"cert": "evilcert", "security": rep["security"] | {"rooted": True}, "protection": rep["protection"] | {"call_screening": False},
                 "counters": {"calls_screened": 3}, "crashes": [{"at": int(time.time() * 1000), "exception": "java.lang.IllegalStateException",
                                                                  "message": "boom", "stack": "com.voiceguard.app.ui.X.y(X.kt:10)"}]}
    c.post("/api/app/report", json=bad, headers={**hasha, **ip(20)})
    check("rooted phone -> Layer 7 alert", len(alerts_about("phone is rooted")) >= 1)
    check("modified app copy -> Layer 7 alert", len(alerts_about("not the genuine")) >= 1)
    check("protection switched off on a phone -> alert", len(alerts_about("protection switched off")) >= 1)
    cr = c.get("/api/admin/console/crashes", headers=A(0)).json()
    check("app crash reported to the console", len(cr) == 1 and cr[0]["exception"].endswith("IllegalStateException") and len(alerts_about("App crash")) >= 1)
    act = c.get("/api/admin/console/app-activity", headers=A(0)).json()
    check("app activity counts are summed", act["totals"]["calls_screened"] == 7 and act["totals"]["ai_checks"] == 2, str(act["totals"]))
    check("non-admin cannot read phone data", c.get("/api/admin/console/devices", headers={**hasha, **ip(21)}).status_code == 403)
    check("every alert went only to the admin", {uid for uid, _ in sent} == {admin["id"]})

print(f"\nALL {ok} SECURITY CHECKS PASSED")
