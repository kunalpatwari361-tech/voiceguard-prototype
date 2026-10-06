"""Firebase Cloud Messaging (FCM): reaches a family phone whose VoiceGuard app is closed.

The live WebSocket stays the fast path. Alerts, "Are you really calling?" questions and HD call ringing are
ALSO sent as a high-priority FCM data message; the app turns it into the same notification and ignores
whichever copy arrives second. Two files from the Firebase console switch it on (both stay on this laptop
and are git-ignored):
  data/firebase-service-account.json  private server key, used only to SEND (Project settings > Service accounts)
  data/google-services.json           the Android app's public Firebase settings, handed to the app after sign-in
Without them everything still works over the live link (status() == "off ...").
"""
import asyncio
import json
import logging
import threading
import time

import httpx
from sqlmodel import Session, select

from . import config
from .db import PushToken, engine

log = logging.getLogger("voiceguard.push")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# How long FCM keeps trying a phone that is switched off – a stale question or ring is useless.
TTL_S = {"alert": 6 * 3600, "verify_request": 60, "hd_incoming": 45}
MAX_DATA = 3500          # FCM allows 4 KB of data per message
MAX_PHONES = 5           # push tokens kept per account

_files: dict = {}


def _read(path) -> dict | None:
    """JSON file, re-read only when it changes (so dropping the files in switches push on without a restart)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _files.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.error("%s is not a valid JSON file", path)
        data = None
    _files[path] = (mtime, data)
    return data


def service_account() -> dict | None:
    sa = _read(config.FIREBASE_CREDENTIALS)
    return sa if sa and sa.get("type") == "service_account" and sa.get("private_key") else None


def android_config() -> dict | None:
    """Public settings for com.voiceguard.app (the same values every APK built with google-services.json carries)."""
    g = _read(config.FIREBASE_APP_CONFIG)
    if not g:
        return None
    p = g.get("project_info", {})
    for c in g.get("client", []):
        ci = c.get("client_info", {})
        if ci.get("android_client_info", {}).get("package_name") == config.ANDROID_PACKAGE:
            return {"project_id": p.get("project_id"), "sender_id": p.get("project_number"),
                    "app_id": ci.get("mobilesdk_app_id"), "api_key": (c.get("api_key") or [{}])[0].get("current_key"),
                    "storage_bucket": p.get("storage_bucket")}
    return None


def status() -> str:
    sa, app = service_account(), android_config()
    if sa and app:
        if sa.get("project_id") != app["project_id"]:
            return (f"misconfigured: server key is for Firebase project {sa.get('project_id')}, "
                    f"google-services.json for {app['project_id']}")
        return "on"
    missing = [n for n, ok in (("firebase-service-account.json", sa), ("google-services.json", app)) if not ok]
    return "off (missing data/" + ", data/".join(missing) + ")"


def enabled() -> bool:
    return status() == "on"


class _AccessToken:
    """OAuth token for the FCM API: a JWT signed with the service-account key, exchanged at Google (1 h)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.value, self.expires = None, 0.0

    def get(self) -> str:
        with self.lock:
            if self.value and time.time() < self.expires - 300:
                return self.value
            from google.auth import crypt, jwt   # only needed once push is switched on
            sa = service_account()
            uri = sa.get("token_uri", "https://oauth2.googleapis.com/token")
            t = int(time.time())
            assertion = jwt.encode(crypt.RSASigner.from_service_account_info(sa),
                                   {"iss": sa["client_email"], "scope": SCOPE, "aud": uri, "iat": t, "exp": t + 3600})
            r = httpx.post(uri, timeout=15, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                  "assertion": assertion.decode()})
            r.raise_for_status()
            j = r.json()
            self.value, self.expires = j["access_token"], time.time() + int(j.get("expires_in", 3600))
            return self.value


_token = _AccessToken()


def _fit(msg: dict) -> dict:
    if len(json.dumps(msg, ensure_ascii=False)) <= MAX_DATA or msg.get("type") != "alert":
        return msg
    a = dict(msg["alert"])
    keep = ("number", "victim_phone", "asker_phone", "asker_name", "from_name")
    a["payload"] = {k: v for k, v in (a.get("payload") or {}).items() if k in keep}
    a["body"] = (a.get("body") or "")[:600]
    return {"type": "alert", "alert": a}


def _send_one(token: str, msg: dict) -> str:
    """One FCM message. Returns "ok", "gone" (token no longer valid: forget it) or "error"."""
    sa = service_account()
    body = {"message": {"token": token,
                        "data": {"vg": json.dumps(msg, ensure_ascii=False, separators=(",", ":"))},
                        "android": {"priority": "HIGH", "ttl": f"{TTL_S.get(msg.get('type'), 3600)}s"}}}
    try:
        r = httpx.post(f"https://fcm.googleapis.com/v1/projects/{sa['project_id']}/messages:send", json=body,
                       headers={"Authorization": f"Bearer {_token.get()}"}, timeout=15)
    except Exception as e:   # network down, bad key, Google refused the key ...
        log.warning("FCM push failed before sending: %s", e)
        return "error"
    if r.status_code == 200:
        return "ok"
    text = r.text
    if r.status_code == 401:
        _token.value = None
    if r.status_code == 404 or "UNREGISTERED" in text or "SENDER_ID_MISMATCH" in text or \
            (r.status_code == 400 and "registration token" in text.lower()):
        return "gone"
    log.warning("FCM push failed %s: %s", r.status_code, text[:300])
    return "error"


def tokens(user_id: str) -> list[str]:
    with Session(engine) as s:
        return [p.token for p in s.exec(select(PushToken).where(PushToken.user_id == user_id)).all()]


def has_tokens(user_id: str) -> bool:
    return enabled() and bool(tokens(user_id))


def save_token(s: Session, user_id: str, token: str, auth_hash: str | None) -> None:
    from .db import now
    p = s.get(PushToken, token) or PushToken(token=token, user_id=user_id)
    p.user_id = user_id          # a phone that signs in with another number now belongs to that account
    p.auth_hash = auth_hash
    p.updated_at = now()
    s.add(p)
    s.commit()
    rows = s.exec(select(PushToken).where(PushToken.user_id == user_id).order_by(PushToken.updated_at.desc())).all()
    for old in rows[MAX_PHONES:]:
        s.delete(old)
    s.commit()


async def send(user_id: str, msg: dict) -> int:
    """Push to every phone the user is signed in on. Returns how many phones FCM accepted it for."""
    if msg.get("type") not in TTL_S or not enabled():
        return 0
    toks = tokens(user_id)
    if not toks:
        return 0
    msg = _fit(msg)
    results = await asyncio.gather(*(asyncio.to_thread(_send_one, t, msg) for t in toks))
    gone = [t for t, r in zip(toks, results) if r == "gone"]
    if gone:
        with Session(engine) as s:
            for t in gone:
                p = s.get(PushToken, t)
                if p:
                    s.delete(p)
            s.commit()
        log.info("dropped %d expired push token(s) of %s", len(gone), user_id)
    return sum(r == "ok" for r in results)


_tasks: set = set()


def send_soon(user_id: str, msg: dict) -> bool:
    """Fire-and-forget push, so the API reply is not slowed down. True if a push is on its way."""
    if msg.get("type") not in TTL_S or not has_tokens(user_id):
        return False
    task = asyncio.get_running_loop().create_task(send(user_id, msg))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
