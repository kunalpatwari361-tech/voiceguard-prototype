"""Tests for the Caller Identity & Verification module (unit + API + security). Uses its own temporary database.

Run: .venv\\Scripts\\python scripts\\caller_identity_test.py
Regression of everything else: .venv\\Scripts\\python scripts\\smoke_test.py
"""
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
    "VG_WARMUP": "0", "VG_DISCOVERY": "0", "VG_DAILY_REPORT": "0", "VG_DB": str(TMP / "cid.db"), "VG_OTP_PROVIDER": "console",
    "TWILIO_WHATSAPP_FROM": "", "VG_FIREBASE_CREDENTIALS": str(TMP / "none.json"),
    "VG_OTP_TEST_NUMBERS": "+919876511001:111111,+919876511002:222222,+919876511003:333333,+919876511004:444444",
    "VG_CALLER_ID_ADMIN_PHONES": "+919876511004",
    "VG_TELECOM_ID_URL": "",
})
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app import caller_identity as cid  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        raise SystemExit(1)
    ok += 1


# ---------------------------------------------------------------- unit: input checks
check("normalises Indian numbers", cid.valid_number("098765 11001") == "+919876511001" and cid.valid_number("9876511001") == "+919876511001")
check("malformed numbers rejected", all(cid.valid_number(x) is None for x in ("", "abc", "12", "+12345678901234567", "98765-ABCDE")))
check("names in Indian scripts accepted", cid.clean_name("राम कुमार") == "राम कुमार" and cid.clean_name("ராஜா") == "ராஜா")
check("markup / symbols / junk names rejected",
      all(cid.clean_name(x) is None for x in ("<script>", "a", "x" * 61, "12345", "Ram;DROP TABLE", "Ram\x00")))
check("no telecom provider -> not available", cid.telecom_lookup("+919876511001")["available"] is False)

with TestClient(app) as c:
    def login(phone, code, name):
        c.post("/api/auth/otp/start", json={"phone": phone})
        r = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code, "name": name}).json()
        return r["user"], {"Authorization": "Bearer " + r["token"]}

    asha, ha = login("9876511001", "111111", "Asha")
    ravi, hr = login("9876511002", "222222", "Ravi")
    meena, hm = login("9876511003", "333333", "Meena")
    admin, hx = login("9876511004", "444444", "Admin")

    # ------------------------------------------------------------ lookups and sources
    check("sign-in required", c.get("/api/caller-identity/lookup/9876511001").status_code == 401)
    r = c.get("/api/caller-identity/lookup/9876511001", headers=hr).json()
    check("VoiceGuard user: number confirmed, name NOT presented as verified",
          r["identity"]["source"] == "voiceguard_user" and r["identity"]["status"] == "number_confirmed"
          and "not verified" in r["identity"]["detail"], r["identity"]["status"])
    check("identity and spam risk are separate fields", "spam" in r and r["spam"] is not None and "score" in r["spam"])
    check("telecom source reported as unavailable", r["telecom"]["available"] is False and "authorised" in r["telecom"]["reason"])
    check("notice: identity is not proof of who is speaking", "not proof" in r["notice"])
    r = c.get("/api/caller-identity/lookup/9123400001", headers=hr).json()
    check("unknown number -> unknown identity (not blocked)", r["identity"] is None)
    check("malformed number -> 400", c.get("/api/caller-identity/lookup/12ab", headers=hr).status_code == 400)

    # ------------------------------------------------------------ community names: unverified, conflicts
    unknown = "9123400002"
    r = c.post("/api/caller-identity/report-name", json={"number": unknown, "name": "Sharma Electricals"}, headers=ha).json()
    check("name correction accepted for moderation", r["ok"] and r["status"] == "pending")
    check("one user's suggestion is not shown yet", c.get(f"/api/caller-identity/lookup/{unknown}", headers=hm).json()["identity"] is None)
    c.post("/api/caller-identity/report-name", json={"number": unknown, "name": "sharma electricals"}, headers=hr)
    r = c.get(f"/api/caller-identity/lookup/{unknown}", headers=hm).json()["identity"]
    check("two users agree -> shown as COMMUNITY, unverified, low confidence",
          r and r["source"] == "community" and r["status"] == "unverified" and r["confidence"] == "low", str(r))
    c.post("/api/caller-identity/report-name", json={"number": unknown, "name": "Bank KYC Desk"}, headers=hm)
    c.post("/api/caller-identity/report-name", json={"number": unknown, "name": "Bank KYC Desk"}, headers=hx)
    r = c.get(f"/api/caller-identity/lookup/{unknown}", headers=ha).json()["identity"]
    check("conflicting community names flagged", r["status"] == "conflicting" and "different names" in r["detail"])
    check("bad name rejected (400)", c.post("/api/caller-identity/report-name", json={"number": unknown, "name": "<b>x</b>"}, headers=ha).status_code == 400)
    r = c.post("/api/caller-identity/report-spam", json={"number": unknown, "reason": "Asked for OTP"}, headers=ha).json()
    check("spam report goes to the moderation queue", r["ok"] and r["status"] == "pending")
    mine = c.get("/api/caller-identity/my-submissions", headers=ha).json()
    check("user sees only their own submissions", len(mine) == 2 and all(m["number"] == "+919123400002" for m in mine))

    # ------------------------------------------------------------ admin permissions (server-side)
    check("ordinary user cannot use admin API -> 403", c.get("/api/admin/caller-identity/submissions", headers=ha).status_code == 403)
    check("unauthenticated admin request -> 401", c.get("/api/admin/caller-identity/summary").status_code == 401)
    check("ordinary user cannot export -> 403", c.get("/api/admin/caller-identity/export?what=submissions", headers=hr).status_code == 403)
    check("admin dashboard page loads (no data in it)", c.get("/admin/caller-identity").status_code == 200)
    q = c.get("/api/admin/caller-identity/submissions?status=pending", headers=hx).json()
    check("admin sees the review queue", len(q) == 5, str(len(q)))
    spam = [x for x in q if x["kind"] == "spam"][0]
    kyc = [x for x in q if x["name"] == "Bank KYC Desk"][0]
    c.post(f"/api/admin/caller-identity/submissions/{kyc['id']}/review", json={"action": "reject", "note": "scam label attempt"}, headers=hx)
    c.post(f"/api/admin/caller-identity/submissions/{spam['id']}/review", json={"action": "flag", "legal_hold": True}, headers=hx)
    r = c.get(f"/api/caller-identity/lookup/{unknown}", headers=ha).json()["identity"]
    check("rejecting the conflicting name resolves the conflict", r["status"] == "unverified" and r["name"].lower() == "sharma electricals")
    check("invalid review action -> 400",
          c.post(f"/api/admin/caller-identity/submissions/{spam['id']}/review", json={"action": "delete"}, headers=hx).status_code == 400)

    # ------------------------------------------------------------ verified businesses
    b = c.post("/api/admin/caller-identity/businesses", json={"number": "1800110022", "name": "Example Bank Helpline", "category": "bank"}, headers=hx).json()
    check("new business starts unverified", b["verified"] is False and
          c.get("/api/caller-identity/lookup/1800110022", headers=ha).json()["identity"] is None)
    check("verification needs a note of how it was checked",
          c.post(f"/api/admin/caller-identity/businesses/{b['id']}/verify", json={"note": "ok"}, headers=hx).status_code == 400)
    c.post(f"/api/admin/caller-identity/businesses/{b['id']}/verify", json={"note": "Checked bank letter; called back the number on its website"}, headers=hx)
    r = c.get("/api/caller-identity/lookup/1800110022", headers=ha).json()["identity"]
    check("verified business shown as VERIFIED, high confidence", r["source"] == "verified_business" and r["status"] == "verified" and r["confidence"] == "high")
    check("ordinary user cannot verify a business -> 403",
          c.post(f"/api/admin/caller-identity/businesses/{b['id']}/verify", json={"note": "I say so, trust me!!"}, headers=ha).status_code == 403)

    # ------------------------------------------------------------ admin tools
    check("admin search finds records", len(c.get("/api/admin/caller-identity/records?q=Sharma", headers=hx).json()["submissions"]) >= 2)
    conf = c.get("/api/admin/caller-identity/conflicts", headers=hx).json()
    check("conflicts list available", isinstance(conf, list))
    csv = c.get("/api/admin/caller-identity/export?what=submissions", headers=hx).text
    check("export works and contains no account ids", "number" in csv.splitlines()[0] and "user_id" not in csv.splitlines()[0])
    ev = c.get("/api/admin/caller-identity/events?kind=denied", headers=hx).json()
    check("unauthorised admin attempts are logged", len(ev) >= 3)
    au = c.get("/api/admin/caller-identity/audit", headers=hx).json()
    check("audit trail records admin actions", {"submission.reject", "business.verify", "export"} <= {a["action"] for a in au})

    # ------------------------------------------------------------ deletion and retention
    r = c.post("/api/caller-identity/my-submissions/delete", headers=ha).json()
    check("deletion: own submissions deleted, legal-hold item kept anonymised", r["deleted"] == 1 and r["kept_anonymised"] == 1, str(r))
    check("after deletion the user has no submissions", c.get("/api/caller-identity/my-submissions", headers=ha).json() == [])
    check("deletion request visible to admin", len(c.get("/api/admin/caller-identity/deletions", headers=hx).json()) == 1)
    with Session(engine) as s:
        s.add(cid.CidSubmission(kind="name", number="+919123400099", name="Old Name", user_id=ravi["id"],
                                created_at=cid.now() - dt.timedelta(days=cid.RETENTION_DAYS + 5)))
        s.commit()
    r = c.post("/api/admin/caller-identity/retention/purge", headers=hx).json()
    check("retention removes expired submissions", r["submissions_deleted"] >= 1, str(r))

    # ------------------------------------------------------------ rate limiting
    statuses = [c.post("/api/caller-identity/report-spam", json={"number": f"91234{i:05d}", "reason": "x"}, headers=hr).status_code for i in range(25)]
    check("submissions are rate limited (429)", 429 in statuses and statuses.count(200) <= 20)

    # ------------------------------------------------------------ the telecom provider timing out is handled safely
    cid.TELECOM_URL, cid.TELECOM_TIMEOUT_S = "http://10.255.255.1:9/lookup", 1.0
    t0 = time.time()
    r = c.get("/api/caller-identity/lookup/9876511001", headers=hm).json()
    cid.TELECOM_URL = ""
    check("provider timeout -> fails safely to other sources, quickly",
          r["telecom"]["available"] is False and r["identity"]["source"] == "voiceguard_user" and time.time() - t0 < 5,
          f"{time.time() - t0:.1f}s {r['telecom'].get('reason')}")

print(f"\nALL {ok} CALLER IDENTITY CHECKS PASSED")
