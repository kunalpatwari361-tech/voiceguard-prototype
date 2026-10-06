"""End-to-end API test with two simulated phones (Papa + Rahul). No real phones needed.

Covers OTP sign-up + login tokens (and that the server refuses requests without them), then every feature.
Run: .venv\\Scripts\\python scripts\\smoke_test.py
"""
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

os.environ["VG_WARMUP"] = "0"
import tempfile  # noqa: E402
os.environ["VG_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")  # never touch the real database
os.environ["VG_OTP_PROVIDER"] = "console"
os.environ["VG_OTP_TEST_NUMBERS"] = "+919876500001:111111,+919876500002:222222"
from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.main import app  # noqa: E402

ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if cond:
        ok += 1
    else:
        raise SystemExit(1)


sent_codes = {}
auth._console = lambda phone, code: sent_codes.__setitem__(phone, code)   # capture demo-mode codes


def signup(c, name, phone, role, code):
    r = c.post("/api/auth/otp/start", json={"phone": phone})
    assert r.status_code == 200, r.text
    r = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code, "name": name, "role": role}).json()
    return r["user"], {"Authorization": f"Bearer {r['token']}"}, r["token"]


with TestClient(app) as c:
    check("health", c.get("/api/health").json()["ok"])

    # ---------------- OTP sign-up and the guards around it
    check("no token -> 401", c.get("/api/scamlist").status_code == 401)
    check("old open sign-up removed", c.post("/api/users", json={"name": "X", "phone": "9876500009"}).status_code in (404, 405))
    r = c.post("/api/auth/otp/start", json={"phone": "12345"})
    check("invalid phone rejected", r.status_code == 400, r.json().get("detail"))
    r = c.post("/api/auth/otp/start", json={"phone": "9876500003"})
    check("demo OTP sent (console)", r.status_code == 200 and r.json()["provider"] == "console" and "+919876500003" in sent_codes)
    check("resend too fast -> 429", c.post("/api/auth/otp/start", json={"phone": "9876500003"}).status_code == 429)
    bad = c.post("/api/auth/otp/verify", json={"phone": "9876500003", "code": "000000", "name": "Mummy"})
    check("wrong code -> 400", bad.status_code == 400 and "left" in bad.json()["detail"], bad.json()["detail"])
    good = c.post("/api/auth/otp/verify", json={"phone": "9876500003", "code": sent_codes["+919876500003"], "name": "Mummy", "role": "parent"})
    check("right code -> account + token", good.status_code == 200 and good.json()["token"])
    reuse = c.post("/api/auth/otp/verify", json={"phone": "9876500003", "code": sent_codes["+919876500003"], "name": "Mummy"})
    check("code works only once", reuse.status_code == 400)
    mummy, hm, _ = good.json()["user"], {"Authorization": "Bearer " + good.json()["token"]}, None

    papa, hp, tok_p = signup(c, "Papa", "9876500001", "parent", "111111")
    rahul, hr, tok_r = signup(c, "Rahul", "9876500002", "child", "222222")
    check("test-number sign-up", papa["phone"] == "+919876500001" and rahul["phone"] == "+919876500002")
    check("token identifies user", c.get("/api/auth/me", headers=hp).json()["id"] == papa["id"])

    fam = c.post("/api/family", json={"user_id": papa["id"], "name": "Sharma family"}, headers=hp).json()
    check("cannot act as someone else -> 403",
          c.post("/api/family/join", json={"user_id": rahul["id"], "code": fam["invite_code"]}, headers=hp).status_code == 403)
    fam = c.post("/api/family/join", json={"user_id": rahul["id"], "code": fam["invite_code"]}, headers=hr).json()
    check("family circle", len(fam["members"]) == 2, fam["invite_code"])
    check("outsider cannot read family -> 403", c.get(f"/api/family/{fam['id']}", headers=hm).status_code == 403)

    # Rahul enrols a voice print from a real human clip
    real = Path(r"K:\vgtools\eval\real\asr_1.flac").read_bytes()
    r = c.post(f"/api/voiceprint/{rahul['id']}", files={"file": ("a.flac", real)}, data={"reset": "true"}, headers=hr)
    check("voice print enrol", r.status_code == 200, r.text[:80])

    # live link refuses phones without a valid token
    try:
        with c.websocket_connect(f"/ws/{rahul['id']}?token=wrong") as w:
            w.receive_json()
        refused = False
    except Exception:
        refused = True
    check("live link without token refused", refused)

    # Rahul's phone is online and idle -> "Are you really calling?" answers NO instantly
    with c.websocket_connect(f"/ws/{rahul['id']}?token={tok_r}") as rws, c.websocket_connect(f"/ws/{papa['id']}?token={tok_p}") as pws:
        rws.send_json({"type": "call_state", "state": "idle"})
        rws.send_json({"type": "location", "lat": 18.52, "lon": 73.85, "place": "Kothrud, Pune"})
        time.sleep(0.3)
        v = c.post("/api/verify/ask", json={"asker_id": papa["id"], "claimed_user_id": rahul["id"], "number": "+919000000101"}, headers=hp).json()
        check("are you really calling (auto NO)", v["answer"] == "no" and v["source"] == "auto", v["message"])
        msg = rws.receive_json()
        check("verify request pushed to Rahul", msg["type"] == "verify_request")
        alert = rws.receive_json()
        check("impersonation alert to Rahul", alert["type"] == "alert" and alert["alert"]["kind"] == "impersonation")

        # Rahul on a call -> asks him, he answers yes
        rws.send_json({"type": "call_state", "state": "offhook"})
        time.sleep(0.3)
        res = {}
        t = threading.Thread(target=lambda: res.update(c.post("/api/verify/ask", json={
            "asker_id": papa["id"], "claimed_user_id": rahul["id"], "wait_s": 10}, headers=hp).json()))
        t.start()
        req = rws.receive_json()
        rws.send_json({"type": "verify_answer", "request_id": req["request_id"], "answer": "yes"})
        t.join()
        check("are you really calling (person YES)", res["answer"] == "yes" and res["source"] == "person")

        # HD call signalling
        hd = c.post("/api/hd/start", json={"caller_id": papa["id"], "callee_id": rahul["id"]}, headers=hp).json()
        inc = rws.receive_json()
        check("HD call rings Rahul", inc["type"] == "hd_incoming" and inc["call_id"] == hd["call_id"])
        rws.send_json({"type": "hd_answer", "call_id": hd["call_id"], "accept": True})
        st = pws.receive_json()
        check("HD call accepted", st["type"] == "hd_status" and st["status"] == "accepted")
        rws.send_json({"type": "hd_hangup", "call_id": hd["call_id"]})
        check("HD call ended", pws.receive_json()["status"] == "ended")

    fam = c.get(f"/api/family/{fam['id']}", headers=hp).json()
    rl = [m for m in fam["members"] if m["id"] == rahul["id"]][0]
    check("family location", rl["location"]["place"] == "Kothrud, Pune")

    # Number info + community list
    ni = c.get("/api/numbers/+919000000101", headers=hp).json()
    check("number info (community reports)", ni["community_reports"] >= 2 and ni["spam_score"] > 0.6, f"score={ni['spam_score']}")
    check("international high-risk", c.get("/api/numbers/+923001234567", headers=hp).json()["spam_score"] >= 0.7)
    c.post("/api/blocked", json={"user_id": rahul["id"], "number": "9000000199"}, headers=hr)
    ni = c.get("/api/numbers/9000000199", headers=hp).json()
    check("family-shared block list", ni["spam_score"] >= 0.9)

    # Full analysis of the demo AI scam call, claiming to be Rahul
    clips = json.loads((ROOT / "demo_audio" / "clips.json").read_text(encoding="utf-8"))
    sc = clips[0]
    import numpy as np  # noqa: E402
    from app.ai.audio_io import load_audio, to_wav_bytes  # noqa: E402
    y = np.concatenate([load_audio((ROOT / "demo_audio" / t["phone"]).read_bytes()) for t in sc["turns"][:3]])
    t0 = time.time()
    a = c.post("/api/analyze", files={"file": ("a.wav", to_wav_bytes(y))}, data={
        "user_id": papa["id"], "claimed_user_id": rahul["id"], "number": sc["number"], "source": "demo_call",
        "reply_gaps": json.dumps([1.4, 1.35, 1.5])}, headers=hp).json()
    print("   ai_voice:", a["ai_voice"]["fake_prob"], "| fp:", a["fingerprints"]["score"], "| vp:", a["voice_print"],
          "| words:", a["scam_words"]["rules"]["categories"], "| transcript:", a["transcript"]["text"][:80])
    check("demo scam call -> DANGER", a["risk"]["level"] == "danger", f"{a['risk']['score']}/100 in {time.time() - t0:.0f}s")
    check("cannot check against a stranger's voice -> 403", c.post("/api/analyze", files={"file": ("a.wav", to_wav_bytes(y))},
          data={"claimed_user_id": papa["id"], "skip_asr": "true"}, headers=hm).status_code == 403)

    real_y = load_audio(real)
    b = c.post("/api/analyze", files={"file": ("r.wav", to_wav_bytes(real_y))}, data={
        "user_id": papa["id"], "claimed_user_id": rahul["id"], "source": "voice_note"}, headers=hp).json()
    print("   real voice:", b["risk"]["score"], b["risk"]["level"], "ai:", b["ai_voice"]["fake_prob"], "vp:", b["voice_print"])
    check("real Rahul voice -> not danger", b["risk"]["level"] != "danger", f"{b['risk']['score']}/100")

    # Voice test with the AI's attempt to laugh
    ch = c.get("/api/challenge/new", params={"kind": "laugh"}, headers=hp).json()
    lf = (ROOT / "demo_audio" / sc["challenge_responses"]["laugh"]["phone"]).read_bytes()
    vt = c.post(f"/api/challenge/{ch['id']}/verify", files={"file": ("l.wav", lf)}, data={"claimed_user_id": rahul["id"]}, headers=hp).json()
    check("voice test: AI laugh fails", vt["passed"] is False, "; ".join(x["en"] for x in vt["reasons"]))

    # Evidence + Chakshu + cyber cell
    ev = c.post("/api/evidence", data={"user_id": papa["id"], "number": sc["number"], "claimed_name": "Rahul",
                                       "risk_score": str(a["risk"]["score"]), "level": a["risk"]["level"],
                                       "transcript": a["transcript"]["text"], "report_json": json.dumps(a)},
                files={"file": ("e.wav", to_wav_bytes(y))}, headers=hp).json()
    chak = c.get(f"/api/evidence/{ev['id']}/chakshu", headers=hp).json()
    check("evidence + chakshu prefill", chak["fields"]["Suspected number"] == "+919000000101")
    check("printable report via ?token=", "VoiceGuard" in c.get(f"/api/evidence/{ev['id']}/report.html?token={tok_p}").text)
    check("evidence hidden from outsiders", c.get(f"/api/evidence/{ev['id']}", headers=hm).status_code == 403)
    check("family calls cyber cell", c.post("/api/evidence/cyber-cell", json={"user_id": papa["id"], "evidence_id": ev["id"]}, headers=hp).status_code == 200)

    # Future features
    check("bank API holds payment", c.post("/api/v1/enterprise/transaction-check", headers={"X-API-Key": "demo-bank-key"},
                                           json={"customer_phone": "9876500001", "amount": 50000}).json()["action"] == "hold")
    check("police join (simulated)", len(c.post("/api/future/police-join", json={"user_id": papa["id"]}, headers=hp).json()["steps"]) == 4)
    c.post("/api/v1/telecom/flag", headers={"X-API-Key": "demo-telecom-key"}, json={"number": "9000000155"})
    c.post("/api/v1/telecom/flag", headers={"X-API-Key": "demo-telecom-key"}, json={"number": "9000000155"})
    check("telecom lookup", c.get("/api/v1/telecom/lookup/9000000155").json()["label"] == "Suspected fraud")
    fed = c.get("/api/future/federated", headers=hp).json()
    check("federated learning", fed["final_accuracy"] > 0.8, f"acc={fed['final_accuracy']}")
    prot = c.post("/api/future/voice-shield/protect", data={"user_id": papa["id"]}, files={"file": ("v.flac", real)}, headers=hp).content
    det = c.post("/api/future/voice-shield/detect", data={"user_id": papa["id"]}, files={"file": ("v.wav", prot)}, headers=hp).json()
    det2 = c.post("/api/future/voice-shield/detect", data={"user_id": rahul["id"]}, files={"file": ("v.wav", prot)}, headers=hr).json()
    check("voice shield watermark", det["watermark_found"] and not det2["watermark_found"], f"z={det['z_score']} other={det2['z_score']}")
    alerts = c.get(f"/api/alerts/{rahul['id']}", headers=hr).json()
    check("alerts feed", len(alerts) >= 2, f"{len(alerts)} alerts")
    r = c.post("/api/alerts", json={"from_user_id": papa["id"], "kind": "scam_call", "title": "t"}, headers=hp).json()
    check("family alert reports recipients", r["sent_to"] == 1, f"sent_to={r['sent_to']} online={r['online']}")

    # logout revokes the token
    c.post("/api/auth/logout", headers=hm)
    check("logout revokes token", c.get("/api/auth/me", headers=hm).status_code == 401)

print(f"\nALL {ok} CHECKS PASSED")
