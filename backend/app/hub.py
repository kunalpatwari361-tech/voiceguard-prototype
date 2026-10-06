"""Live connection hub: one WebSocket per phone for pushes (alerts, verify requests, HD call ringing)."""
import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger("voiceguard.hub")


class Hub:
    def __init__(self):
        self.conns: dict[str, set[WebSocket]] = {}
        self.waiters: dict[str, asyncio.Future] = {}   # request_id -> future (verify / location)

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self.conns.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: str, ws: WebSocket):
        self.conns.get(user_id, set()).discard(ws)

    def online(self, user_id: str) -> bool:
        return bool(self.conns.get(user_id))

    async def send(self, user_id: str, msg: dict) -> bool:
        sent = False
        for ws in list(self.conns.get(user_id, ())):
            try:
                await ws.send_json(msg)
                sent = True
            except Exception:
                self.disconnect(user_id, ws)
        return sent

    async def send_many(self, user_ids, msg: dict, exclude: str | None = None):
        for uid in user_ids:
            if uid != exclude:
                await self.send(uid, msg)

    def expect(self, request_id: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self.waiters[request_id] = fut
        return fut

    def resolve(self, request_id: str, value):
        fut = self.waiters.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(value)


hub = Hub()
