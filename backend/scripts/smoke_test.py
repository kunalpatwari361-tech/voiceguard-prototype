"""End-to-end API test with two simulated phones (Papa + Rahul). No real phones needed.

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
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if cond:
        ok += 1
    else:
        raise SystemExit(1)


with TestClient(app) as c:
    check("health", c.get("/api/health").json()["ok"])
    papa = c.post("/api/users", json={"name": "Papa", "phone": "9876500001", "role": "parent"}).json()
    rahul = c.post("/api/users", json={"name": "Rahul", "phone": "9876500002", "role": "child"}).json()
    fam = c.post("/api/family", json={"user_id": papa["id"], "name": "Sharma family"}).json()
    fam = c.post("/api/family/join", json={"user_id": rahul["id"], "code": fam["invite_code"]}).json()
    check("family circle", len(fam["members"]) == 2, fam["invite_code"])

    # Rahul enrols a voice print from a real human clip
    real = Path(r"K:\vgtools\eval\real\asr_1.flac").read_bytes()
    r = c.post(f"/api/voiceprint/{rahul['id']}", files={"file": ("a.flac", real)}, data={"reset": "true"})
    check("voice print enrol", r.status_code == 200, r.text[:80])

    # Rahul's phone is online and idle -> "Are you really calling?" answers NO instantly
    with c.websocket_connect(f"/ws/{rahul['id']}") as rws, c.websocket_connect(f"/ws/{papa['id']}") as pws:
        rws.send_json({"type": "call_state", "state": "idle"})
        rws.send_json({"type": "location", "lat": 18.52, "lon": 73.85, "place": "Kothrud, Pune"})
        time.sleep(0.3)
        v = c.post("/api/verify/ask", json={"asker_id": papa["id"], "claimed_user_id": rahul["id"], "number": "+919000000101"}).json()
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
            "asker_id": papa["id"], "claimed_user_id": rahul["id"], "wait_s": 10}).json()))
        t.start()
        req = rws.receive_json()
        rws.send_json({"type": "verify_answer", "request_id": req["request_id"], "answer": "yes"})
        t.join()
        check("are you really calling (person YES)", res["answer"] == "yes" and res["source"] == "person")

        # HD call signalling
        hd = c.post("/api/hd/start", json={"caller_id": papa["id"], "callee_id": rahul["id"]}).json()
        inc = rws.receive_json()
        check("HD call rings Rahul", inc["type"] == "hd_incoming" and inc["call_id"] == hd["call_id"])
        rws.send_json({"type": "hd_answer", "call_id": hd["call_id"], "accept": True})
        st = pws.receive_json()
        check("HD call accepted", st["type"] == "hd_status" and st["status"] == "accepted")
        rws.send_json({"type": "hd_hangup", "call_id": hd["call_id"]})
        check("HD call ended", pws.receive_json()["status"] == "ended")

    fam = c.get(f"/api/family/{fam['id']}").json()
    rl = [m for m in fam["members"] if m["id"] == rahul["id"]][0]
    check("family location", rl["location"]["place"] == "Kothrud, Pune")

    # Number info + community list
    ni = c.get("/api/numbers/+919000000101", params={"user_id": papa["id"]}).json()
    check("number info (community reports)", ni["community_reports"] >= 2 and ni["spam_score"] > 0.6, f"score={ni['spam_score']}")
    check("international high-risk", c.get("/api/numbers/+923001234567").json()["spam_score"] >= 0.7)
    c.post("/api/blocked", json={"user_id": rahul["id"], "number": "9000000199"})
    ni = c.get("/api/numbers/9000000199", params={"user_id": papa["id"]}).json()
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
        "reply_gaps": json.dumps([1.4, 1.35, 1.5])}).json()
    print(json.dumps({k: a.get(k) for k in ("risk",)}, ensure_ascii=False)[:600])
    print("   ai_voice:", a["ai_voice"]["fake_prob"], "| fp:", a["fingerprints"]["score"], "| vp:", a["voice_print"],
          "| words:", a["scam_words"]["rules"]["categories"], "| transcript:", a["transcript"]["text"][:80])
    check("demo scam call -> DANGER", a["risk"]["level"] == "danger", f"{a['risk']['score']}/100 in {time.time() - t0:.0f}s")

    real_y = load_audio(real)
    b = c.post("/api/analyze", files={"file": ("r.wav", to_wav_bytes(real_y))}, data={
        "user_id": papa["id"], "claimed_user_id": rahul["id"], "source": "voice_note"}).json()
    print("   real voice:", b["risk"]["score"], b["risk"]["level"], "ai:", b["ai_voice"]["fake_prob"], "vp:", b["voice_print"])
    check("real Rahul voice -> not danger", b["risk"]["level"] != "danger", f"{b['risk']['score']}/100")

    # Voice test with the AI's attempt to laugh
    ch = c.get("/api/challenge/new", params={"kind": "laugh"}).json()
    lf = (ROOT / "demo_audio" / sc["challenge_responses"]["laugh"]["phone"]).read_bytes()
    vt = c.post(f"/api/challenge/{ch['id']}/verify", files={"file": ("l.wav", lf)}, data={"claimed_user_id": rahul["id"]}).json()
    check("voice test: AI laugh fails", vt["passed"] is False, "; ".join(x["en"] for x in vt["reasons"]))

    # Evidence + Chakshu + cyber cell
    ev = c.post("/api/evidence", data={"user_id": papa["id"], "number": sc["number"], "claimed_name": "Rahul",
                                       "risk_score": str(a["risk"]["score"]), "level": a["risk"]["level"],
                                       "transcript": a["transcript"]["text"], "report_json": json.dumps(a)},
                files={"file": ("e.wav", to_wav_bytes(y))}).json()
    chak = c.get(f"/api/evidence/{ev['id']}/chakshu").json()
    check("evidence + chakshu prefill", chak["fields"]["Suspected number"] == "+919000000101")
    check("printable report", "VoiceGuard" in c.get(f"/api/evidence/{ev['id']}/report.html").text)
    check("family calls cyber cell", c.post("/api/evidence/cyber-cell", json={"user_id": papa["id"], "evidence_id": ev["id"]}).status_code == 200)

    # Future features
    check("bank API holds payment", c.post("/api/v1/enterprise/transaction-check", headers={"X-API-Key": "demo-bank-key"},
                                           json={"customer_phone": "9876500001", "amount": 50000}).json()["action"] == "hold")
    check("police join (simulated)", len(c.post("/api/future/police-join", json={"user_id": papa["id"]}).json()["steps"]) == 4)
    c.post("/api/v1/telecom/flag", headers={"X-API-Key": "demo-telecom-key"}, json={"number": "9000000155"})
    c.post("/api/v1/telecom/flag", headers={"X-API-Key": "demo-telecom-key"}, json={"number": "9000000155"})
    check("telecom lookup", c.get("/api/v1/telecom/lookup/9000000155").json()["label"] == "Suspected fraud")
    fed = c.get("/api/future/federated").json()
    check("federated learning", fed["final_accuracy"] > 0.8, f"acc={fed['final_accuracy']}")
    prot = c.post("/api/future/voice-shield/protect", data={"user_id": papa["id"]}, files={"file": ("v.flac", real)}).content
    det = c.post("/api/future/voice-shield/detect", data={"user_id": papa["id"]}, files={"file": ("v.wav", prot)}).json()
    det2 = c.post("/api/future/voice-shield/detect", data={"user_id": rahul["id"]}, files={"file": ("v.wav", prot)}).json()
    check("voice shield watermark", det["watermark_found"] and not det2["watermark_found"], f"z={det['z_score']} other={det2['z_score']}")
    alerts = c.get(f"/api/alerts/{rahul['id']}").json()
    check("alerts feed", len(alerts) >= 2, f"{len(alerts)} alerts")

print(f"\nALL {ok} CHECKS PASSED")
