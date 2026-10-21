"""VoiceGuard prototype server.

Run:  .venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   (start_server.bat)
Phones on the same Wi-Fi find it by themselves (app/discovery.py); over USB:  adb reverse tcp:8000 tcp:8000
"""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .ai import models
from .db import LocationOptOut, Session, User, engine, init_db, now
from .hub import hub
from .auth import user_for_token
from . import daily_report, discovery, live_location, portal, push, security
from .routers import admin_console, app_reports, auth_routes, caller_identity, checks, evidence, future, hdcall, people, push_routes, verify
from .routers import portal as portal_routes
from .routers.common import iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voiceguard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    portal.ensure_owners()                       # team portal owners from VG_PORTAL_OWNERS / VG_ADMIN_PHONES
    security.start(asyncio.get_running_loop())   # castle security: watchtower + integrity baseline
    daily_report.start()                         # team portal: yesterday's report built just after midnight (IST)
    discovery.start()
    if config.WARMUP_MODELS:
        threading.Thread(target=models.warmup, daemon=True).start()
    yield


app = FastAPI(title="VoiceGuard API", version="0.1.0", lifespan=lifespan,
              description="Prototype backend for the VoiceGuard anti voice-clone scam app.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(security.SecurityGate)   # outermost: the seven security layers see every request first
for r in (auth_routes.router, push_routes.router, people.router, checks.router, verify.router, hdcall.router,
          evidence.router, future.router, caller_identity.router, admin_console.router, app_reports.router,
          portal_routes.router):   # caller_identity: isolated add-on
    app.include_router(r)

demo_dir = config.BASE_DIR / "demo_audio"
demo_dir.mkdir(exist_ok=True)
app.mount("/demo", StaticFiles(directory=demo_dir), name="demo")


@app.get("/api/health")
def health():
    from . import messaging
    return {"ok": True, "models": models.status(), "push": push.status(), "whatsapp": messaging.status(), "time": iso(now())}


@app.get("/api/demo/clips")
def demo_clips():
    import json
    meta = demo_dir / "clips.json"
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else []


@app.websocket("/ws/{user_id}")
async def live(ws: WebSocket, user_id: str, token: str | None = None):
    """One persistent connection per phone: presence, call state, location, verify answers, HD signalling.
    The phone must present the login token it got from OTP sign-up (?token=...)."""
    with Session(engine) as s:
        u = user_for_token(s, token)
    if not u or u.id != user_id:
        await ws.close(code=4401)
        return
    await hub.connect(user_id, ws)
    log.info("phone online: %s", user_id)
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "ping":
                await ws.send_json({"type": "pong"})
                continue
            with Session(engine) as s:
                u = s.get(User, user_id)
                u.last_seen = now()
                if t == "call_state":
                    u.call_state = msg.get("state")
                    u.call_state_at = now()
                elif t == "location":
                    if s.get(LocationOptOut, user_id):   # sharing is off: store nothing
                        continue
                    u.lat, u.lon = msg.get("lat"), msg.get("lon")
                    u.loc_accuracy = msg.get("accuracy")
                    u.place = msg.get("place") or u.place
                    u.loc_at = now()
                s.add(u)
                s.commit()
            if t == "location":
                # Live Location: pass every fix straight to the family members watching the live map
                live = {"lat": msg.get("lat"), "lon": msg.get("lon"), "accuracy": msg.get("accuracy"),
                        "place": msg.get("place"), "battery": msg.get("battery"), "at": iso(now())}
                for w in live_location.watchers(user_id):
                    await hub.send(w, {"type": "family_location", "user_id": user_id, "location": live})
            if t == "location" and msg.get("request_id"):
                hub.resolve(msg["request_id"], True, by=user_id)
            elif t == "verify_answer" and msg.get("answer") in ("yes", "no"):
                hub.resolve(msg.get("request_id", ""), msg["answer"], by=user_id)
            elif t in ("hd_answer", "hd_hangup"):
                await hdcall.on_control(user_id, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error for %s", user_id)
    finally:
        hub.disconnect(user_id, ws)
        log.info("phone offline: %s", user_id)
