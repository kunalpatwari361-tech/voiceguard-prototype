"""VoiceGuard HD Call (feature 11) with Live Call Check (6) and Reply Delay (13).

App-to-app call over the internet with 16 kHz wideband audio (2x a phone call's bandwidth), relayed
by this server. Reaching the person's *registered phone* is itself proof of identity, and because the
server sees HD audio it can run every voice check live and push results to the other side.
"""
import asyncio
import json
import logging
import time
import uuid

import numpy as np
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlmodel import Session

from ..ai.audio_io import pcm16_to_float
from ..ai.pipeline import analyze_voice
from ..ai.reply_delay import TurnTracker
from ..db import User, engine, get_session
from ..hub import hub
from ..auth import current_user, require_family, require_self, user_for_token

log = logging.getLogger("voiceguard.hd")
router = APIRouter(tags=["hd-call"])
FRAME = 320                       # 20 ms at 16 kHz
ANALYSE_AT = (6.0, 15.0, 30.0)    # seconds of speech after which a side is (re)analysed


class Call:
    def __init__(self, caller: str, callee: str):
        self.id = uuid.uuid4().hex[:10]
        self.caller, self.callee = caller, callee
        self.status = "ringing"
        self.audio: dict[str, WebSocket] = {}
        self.buf: dict[str, list[np.ndarray]] = {caller: [], callee: []}
        self.speech_s = {caller: 0.0, callee: 0.0}
        self.next_idx = {caller: 0, callee: 0}
        self.busy = {caller: False, callee: False}
        self.tracker = TurnTracker(frame_s=0.02)
        self.started = time.time()

    def other(self, uid: str) -> str:
        return self.callee if uid == self.caller else self.caller


calls: dict[str, Call] = {}


class Start(BaseModel):
    caller_id: str
    callee_id: str


@router.post("/api/hd/start")
async def start(body: Start, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.caller_id)
    caller = me
    callee = s.get(User, body.callee_id)
    require_family(me, callee)
    if not hub.online(callee.id):
        return {"status": "offline", "message": f"{callee.name}'s VoiceGuard app is not reachable.",
                "message_hi": f"{callee.name} का VoiceGuard ऐप अभी उपलब्ध नहीं है।"}
    c = Call(caller.id, callee.id)
    calls[c.id] = c
    await hub.send(callee.id, {"type": "hd_incoming", "call_id": c.id, "from": {"id": caller.id, "name": caller.name}})

    async def ring_timeout():
        await asyncio.sleep(40)
        if c.status == "ringing":
            c.status = "missed"
            await hub.send(c.caller, {"type": "hd_status", "call_id": c.id, "status": "missed"})
            await hub.send(c.callee, {"type": "hd_status", "call_id": c.id, "status": "missed"})
    asyncio.create_task(ring_timeout())
    return {"status": "ringing", "call_id": c.id}


async def on_control(user_id: str, msg: dict):
    """hd_answer / hd_hangup arriving on the main WebSocket."""
    c = calls.get(msg.get("call_id", ""))
    if not c:
        return
    if msg["type"] == "hd_answer" and c.status == "ringing":
        c.status = "accepted" if msg.get("accept") else "declined"
        await hub.send(c.caller, {"type": "hd_status", "call_id": c.id, "status": c.status})
    elif msg["type"] == "hd_hangup":
        c.status = "ended"
        await hub.send(c.other(user_id), {"type": "hd_status", "call_id": c.id, "status": "ended"})
        for ws in list(c.audio.values()):
            try:
                await ws.close()
            except Exception:
                pass
        calls.pop(c.id, None)


async def _analyse(c: Call, side: str):
    c.busy[side] = True
    try:
        y = np.concatenate(c.buf[side])[-16000 * 30:]
        with Session(engine) as s:
            u = s.get(User, side)
            vp = np.array(json.loads(u.voiceprint), dtype=np.float32) if u and u.voiceprint else None
            name = u.name if u else "caller"
        gaps = c.tracker.gaps.get(side)
        result = await asyncio.to_thread(analyze_voice, y, claimed_name=name, claimed_print=vp,
                                         hd_call="verified", reply_gaps=gaps)
        await hub.send(c.other(side), {"type": "hd_analysis", "call_id": c.id,
                                       "about": {"id": side, "name": name}, "result": result})
    except Exception:
        log.exception("HD analysis failed")
    finally:
        c.busy[side] = False


@router.websocket("/ws/hd/{call_id}/{user_id}")
async def audio_socket(ws: WebSocket, call_id: str, user_id: str, token: str | None = None):
    with Session(engine) as s:
        u = user_for_token(s, token)
    if not u or u.id != user_id:
        await ws.close(code=4401)   # not signed in as this user
        return
    c = calls.get(call_id)
    if not c or user_id not in (c.caller, c.callee):
        await ws.close(code=4404)
        return
    await ws.accept()
    c.audio[user_id] = ws
    other = c.other(user_id)
    pending = b""
    try:
        while True:
            data = await ws.receive_bytes()
            peer = c.audio.get(other)
            if peer:
                try:
                    await peer.send_bytes(data)
                except Exception:
                    c.audio.pop(other, None)
            pending += data
            n = len(pending) // (FRAME * 2)
            if not n:
                continue
            chunk, pending = pending[: n * FRAME * 2], pending[n * FRAME * 2:]
            y = pcm16_to_float(chunk)
            c.buf[user_id].append(y)
            for f in y.reshape(n, FRAME):
                db = float(20 * np.log10(np.sqrt(np.mean(f ** 2)) + 1e-9))
                c.tracker.push(user_id, other, db)
                if db > -45:
                    c.speech_s[user_id] += 0.02
            i = c.next_idx[user_id]
            if i < len(ANALYSE_AT) and c.speech_s[user_id] >= ANALYSE_AT[i] and not c.busy[user_id]:
                c.next_idx[user_id] = i + 1
                asyncio.create_task(_analyse(c, user_id))
            total = sum(len(b) for b in c.buf[user_id])
            while total > 16000 * 40 and len(c.buf[user_id]) > 1:
                total -= len(c.buf[user_id].pop(0))
    except WebSocketDisconnect:
        pass
    finally:
        c.audio.pop(user_id, None)
