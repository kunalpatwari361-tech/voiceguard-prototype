"""Pretend to be a second family phone (e.g. Rahul) when you only have one real device.

It signs up with an OTP (like the app), joins the family with the invite code, saves a voice print from a real recording,
then stays online: reports "not on a call" + a location, answers "Are you really calling?" requests
(auto, or as told by --answer) and prints every alert it receives.

Usage:
  .venv\\Scripts\\python scripts\\sim_family_phone.py --code 465011 --name Rahul --phone 9876500002
"""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
import websockets


def _code_from_dev_log(phone: str) -> str | None:
    """Demo mode prints codes to backend/data/dev_otp.log on this same laptop."""
    log = Path(__file__).resolve().parents[1] / "data" / "dev_otp.log"
    if not log.exists():
        return None
    for line in reversed(log.read_text(encoding="utf-8").splitlines()):
        if f"OTP for {phone}:" in line:
            return line.rsplit(":", 1)[1].strip()
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--code", required=True, help="family invite code shown on the real phone")
    ap.add_argument("--name", default="Rahul")
    ap.add_argument("--phone", default="9876500002")
    ap.add_argument("--role", default="child")
    ap.add_argument("--voice", default=r"K:\vgtools\eval\real\asr_1.flac", help="real recording for the voice print")
    ap.add_argument("--place", default="Kothrud, Pune")
    ap.add_argument("--lat", type=float, default=18.5074)
    ap.add_argument("--lon", type=float, default=73.8077)
    ap.add_argument("--state", default="idle", choices=["idle", "offhook"])
    ap.add_argument("--answer", default=None, choices=[None, "yes", "no"], help="reply to verify requests")
    ap.add_argument("--otp", default=None, help="OTP code (otherwise read from data/dev_otp.log in demo mode, or asked)")
    a = ap.parse_args()

    async with httpx.AsyncClient(base_url=a.server, timeout=120) as c:
        r = await c.post("/api/auth/otp/start", json={"phone": a.phone})
        if r.status_code != 200:
            raise SystemExit(f"OTP request failed: {r.text}")
        phone = r.json()["phone"]
        code = a.otp or _code_from_dev_log(phone) or input(f"Enter the OTP sent to {phone}: ").strip()
        r = await c.post("/api/auth/otp/verify", json={"phone": phone, "code": code, "name": a.name, "role": a.role})
        if r.status_code != 200:
            raise SystemExit(f"OTP verification failed: {r.text}")
        token = r.json()["token"]
        u = r.json()["user"]
        c.headers["Authorization"] = f"Bearer {token}"
        f = (await c.post("/api/family/join", json={"user_id": u["id"], "code": a.code})).json()
        print(f"{a.name} ({u['id']}) joined {f['name']}: {[m['name'] for m in f['members']]}")
        vp = Path(a.voice)
        if vp.exists():
            r = await c.post(f"/api/voiceprint/{u['id']}", files={"file": (vp.name, vp.read_bytes())}, data={"reset": "true"})
            print("voice print:", r.json().get("message", r.text))

    ws_url = a.server.replace("http", "ws") + f"/ws/{u['id']}?token={token}"
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps({"type": "call_state", "state": a.state}))
                await ws.send(json.dumps({"type": "location", "lat": a.lat, "lon": a.lon, "accuracy": 15, "place": a.place}))
                print(f"{a.name}'s phone is online ({a.state}, {a.place}). Ctrl+C to stop.")
                async for raw in ws:
                    m = json.loads(raw)
                    t = m.get("type")
                    if t == "verify_request":
                        print(f"<- {m['from']['name']} asks: are you calling? (caller {m.get('number')})")
                        if a.answer:
                            await ws.send(json.dumps({"type": "verify_answer", "request_id": m["request_id"], "answer": a.answer}))
                            print(f"-> answered {a.answer}")
                    elif t == "location_request":
                        await ws.send(json.dumps({"type": "location", "lat": a.lat, "lon": a.lon, "accuracy": 15,
                                                  "place": a.place, "request_id": m["request_id"]}))
                        print("-> sent location")
                    elif t == "alert":
                        al = m["alert"]
                        print(f"<- ALERT [{al['kind']}] {al['title']} - {al['body']}")
                    elif t == "hd_incoming":
                        print(f"<- HD call from {m['from']['name']} (the simulator has no audio; declining)")
                        await ws.send(json.dumps({"type": "hd_answer", "call_id": m["call_id"], "accept": False}))
        except (OSError, websockets.ConnectionClosed) as e:
            print("reconnecting…", e)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
