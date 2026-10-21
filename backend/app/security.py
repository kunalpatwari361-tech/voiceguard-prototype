"""Castle security: seven layers, a watchtower that alerts the admin team, and automatic containment.

  1 Moat         network perimeter    floods, scanners, blocked addresses            -> block the address
  2 Outer wall   sign-in              OTP guessing, fake / stolen tokens             -> block address or phone number
  3 Gatehouse    access control       reading other people's data, admin probing     -> sign the account out, pause it
  4 Inner wall   input shield         SQL / script injection, path tricks, huge uploads -> reject; repeat -> block
  5 Keep         data protection      bulk scraping, database damage; backups        -> pause the account; damage -> lockdown
  6 Vault        integrity            server code / AI weights / settings changed    -> lockdown while running
  7 Royal guard  app & device trust   modified or unknown app copies                 -> record (block in "enforce" mode)

Every breach is stored (SecEvent), serious ones become incidents (SecIncident) that are pushed to the admins' phones
(VoiceGuard app; WhatsApp for high / critical). Lockdown stops all user traffic until an admin clears it in the
Admin Console (/admin/console). Settings: VG_ADMIN_PHONES, VG_SEC_* thresholds, VG_APP_CERTS, VG_APP_TRUST_MODE.
"""
import asyncio
import collections
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import unquote_plus

from sqlmodel import Field, Session, SQLModel, select

from . import config
from .ai.number_info import normalize
from .db import AuthToken, User, engine, now

log = logging.getLogger("voiceguard.security")
APP_DIR = Path(__file__).resolve().parent
LOG_DIR = config.DATA_DIR / "logs"
BACKUP_DIR = config.DATA_DIR / "backups"
BASELINE = config.DATA_DIR / "security_baseline.json"


def _env_int(k: str, d: int) -> int:
    try:
        return int(os.getenv(k, d))
    except ValueError:
        return d


ADMIN_PHONES = {normalize(p) for p in os.getenv("VG_ADMIN_PHONES", "").split(",") if p.strip()}
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def real_ip(peer: str, headers) -> str:
    """Address of the person behind a request. Behind a trusted reverse proxy: X-Forwarded-For. Through the Cloudflare
    tunnel (cloudflared runs on this computer, so the request arrives from 127.0.0.1): CF-Connecting-IP. Only a
    program on this computer could fake that header, so blocking works on the real visitor, not on everyone."""
    if TRUST_PROXY and headers.get("x-forwarded-for"):
        return headers["x-forwarded-for"].split(",")[0].strip()
    if peer in LOOPBACK and headers.get("cf-connecting-ip"):
        return headers["cf-connecting-ip"].strip()[:64]
    return peer


def via_tunnel(peer: str, headers) -> bool:
    """Came through the public Cloudflare tunnel. Cloudflare always sets CF-Connecting-IP (a visitor cannot remove it),
    and uvicorn has usually already replaced the peer with the visitor's address. Someone on the home network faking
    the header only makes their own request look like it came from the internet."""
    return bool(headers.get("cf-connecting-ip"))


def from_internet(ip: str) -> bool:
    """True unless the address is this computer or the home / office network."""
    if ip in LOOPBACK:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return not (a.is_private or a.is_loopback or a.is_link_local)
TRUST_PROXY = os.getenv("VG_TRUST_PROXY", "0") == "1"           # behind a reverse proxy: use X-Forwarded-For
APP_CERTS = {c.strip().lower() for c in os.getenv("VG_APP_CERTS", "").split(",") if c.strip()}
APP_TRUST_MODE = os.getenv("VG_APP_TRUST_MODE", "monitor")       # monitor (alert only) / enforce (block)
TAMPER_LOCKDOWN = os.getenv("VG_SEC_TAMPER_LOCKDOWN", "1") == "1"
LIMITS = {   # (count, seconds)
    "flood": (_env_int("VG_SEC_FLOOD", 600), 60),
    "scan": (_env_int("VG_SEC_SCAN", 30), 300),
    "bad_token": (_env_int("VG_SEC_BAD_TOKEN", 40), 600),
    "otp_ip": (_env_int("VG_SEC_OTP_IP", 10), 900),
    "otp_phone": (_env_int("VG_SEC_OTP_PHONE", 8), 900),
    "forbidden_ip": (_env_int("VG_SEC_FORBIDDEN_IP", 25), 300),
    "forbidden_user": (_env_int("VG_SEC_FORBIDDEN_USER", 8), 600),
    "attack_ip": (_env_int("VG_SEC_ATTACK_IP", 5), 600),
    "scrape": (_env_int("VG_SEC_SCRAPE", 300), 600),
    "errors": (_env_int("VG_SEC_ERRORS", 20), 300),
}
MAX_BODY = _env_int("VG_SEC_MAX_BODY_MB", 30) * 1024 * 1024
BAN_MIN = _env_int("VG_SEC_BAN_MINUTES", 60)

LAYERS = [
    {"id": 1, "key": "perimeter", "name": "Moat", "what": "Network perimeter", "guards": "Floods, scanners, blocked addresses"},
    {"id": 2, "key": "auth", "name": "Outer wall", "what": "Sign-in", "guards": "OTP guessing, fake or stolen login tokens"},
    {"id": 3, "key": "access", "name": "Gatehouse", "what": "Access control", "guards": "Reading other people's data, admin probing"},
    {"id": 4, "key": "input", "name": "Inner wall", "what": "Input shield", "guards": "SQL / script injection, path tricks, oversized uploads"},
    {"id": 5, "key": "data", "name": "Keep", "what": "Data protection", "guards": "Bulk scraping, database damage; backups"},
    {"id": 6, "key": "integrity", "name": "Vault", "what": "Code, AI & config integrity", "guards": "Changed server code, AI weights or settings"},
    {"id": 7, "key": "app", "name": "Royal guard", "what": "App & device trust", "guards": "Modified or unknown app copies"},
]
SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ------------------------------------------------------------------ tables (new)
class SecEvent(SQLModel, table=True):
    __tablename__ = "sec_event"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    layer: int = Field(index=True)
    kind: str
    severity: str = "low"
    ip: str | None = None
    user_id: str | None = None
    path: str | None = None
    detail: str = ""


class SecIncident(SQLModel, table=True):
    __tablename__ = "sec_incident"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    last_at: dt.datetime = Field(default_factory=now)
    layer: int = Field(index=True)
    kind: str
    severity: str
    title: str
    detail: str = ""
    ip: str | None = None
    user_id: str | None = None
    action: str = ""                  # what was done automatically
    count: int = 1
    status: str = Field(default="open", index=True)   # open / acknowledged / resolved
    handled_by: str | None = None
    handled_at: dt.datetime | None = None


class SecBlock(SQLModel, table=True):
    __tablename__ = "sec_block"
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)     # ip / phone / token / user
    value: str = Field(index=True)
    reason: str = ""
    until: dt.datetime | None = None  # None = until removed
    created_at: dt.datetime = Field(default_factory=now)
    by: str = "auto"


class SecState(SQLModel, table=True):
    __tablename__ = "sec_state"
    key: str = Field(primary_key=True)
    value: str = ""


# ------------------------------------------------------------------ server log file (shown in the console)
def _setup_log_file():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(LOG_DIR / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)


_setup_log_file()


def tail_log(lines: int = 200, q: str = "") -> list[str]:
    p = LOG_DIR / "server.log"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        rows = collections.deque(f, maxlen=5000)
    rows = [r.rstrip("\n") for r in rows if not q or q.lower() in r.lower()]
    return rows[-max(1, min(lines, 2000)):]


# ------------------------------------------------------------------ counters, blocks, lockdown
class Window:
    """Sliding-window counters in memory."""

    def __init__(self):
        self._d: dict[tuple, collections.deque] = {}
        self._lock = threading.Lock()

    def hit(self, name: str, key: str) -> int:
        limit, per = LIMITS[name]
        t = time.time()
        with self._lock:
            q = self._d.setdefault((name, key), collections.deque())
            q.append(t)
            while q and t - q[0] > per:
                q.popleft()
            return len(q)

    def reset(self, name: str, key: str):
        with self._lock:
            self._d.pop((name, key), None)


win = Window()
_blocks: dict[tuple[str, str], dt.datetime | None] = {}
_blocks_at = [0.0]
_state: dict[str, str] = {}
_loop: asyncio.AbstractEventLoop | None = None
_wa_last: dict[str, float] = {}


def _refresh_blocks(force: bool = False):
    if not force and time.time() - _blocks_at[0] < 20:
        return
    _blocks_at[0] = time.time()
    with Session(engine) as s:
        rows = s.exec(select(SecBlock)).all()
        t = now()
        _blocks.clear()
        for b in rows:
            if b.until is None or b.until > t:
                _blocks[(b.kind, b.value)] = b.until
        _state.clear()
        _state.update({x.key: x.value for x in s.exec(select(SecState)).all()})


def is_blocked(kind: str, value: str | None) -> bool:
    if not value:
        return False
    _refresh_blocks()
    if (kind, value) not in _blocks:
        return False
    until = _blocks[(kind, value)]
    return until is None or until > now()


def block(kind: str, value: str, minutes: int | None, reason: str, by: str = "auto") -> SecBlock:
    with Session(engine) as s:
        b = SecBlock(kind=kind, value=value, reason=reason[:200], by=by,
                     until=None if minutes is None else now() + dt.timedelta(minutes=minutes))
        s.add(b)
        s.commit()
        s.refresh(b)
    _refresh_blocks(force=True)
    log.warning("security block %s %s for %s min: %s", kind, value if kind != "token" else value[:8] + "…", minutes, reason)
    return b


def unblock(block_id: int):
    with Session(engine) as s:
        b = s.get(SecBlock, block_id)
        if b:
            s.delete(b)
            s.commit()
    _refresh_blocks(force=True)


def revoke_sessions(user_id: str) -> int:
    with Session(engine) as s:
        rows = s.exec(select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.revoked == False)).all()  # noqa: E712
        for r in rows:
            r.revoked = True
            s.add(r)
        s.commit()
        return len(rows)


def lockdown() -> dict | None:
    _refresh_blocks()
    if _state.get("lockdown") == "1":
        return {"reason": _state.get("lockdown_reason", ""), "at": _state.get("lockdown_at", "")}
    return None


def set_lockdown(on: bool, reason: str = "", by: str = "auto"):
    with Session(engine) as s:
        for k, v in (("lockdown", "1" if on else "0"), ("lockdown_reason", reason[:300]),
                     ("lockdown_at", now().isoformat() if on else ""), ("lockdown_by", by)):
            row = s.get(SecState, k) or SecState(key=k)
            row.value = v
            s.add(row)
        s.commit()
    _refresh_blocks(force=True)
    log.warning("security LOCKDOWN %s by %s: %s", "ON" if on else "off", by, reason)


# ------------------------------------------------------------------ recording breaches + alerting admins
def admin_users(s: Session) -> list[User]:
    return [u for u in s.exec(select(User)).all() if normalize(u.phone) in ADMIN_PHONES] if ADMIN_PHONES else []


def _schedule(coro):
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        if _loop and _loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, _loop)
        else:
            coro.close()


async def _notify(inc: dict):
    from . import messaging
    from .hub import hub
    with Session(engine) as s:
        admins = [(u.id, u.phone) for u in admin_users(s)]
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(inc["severity"], "⚪")
    layer = next((f"Layer {l['id']} {l['name']}" for l in LAYERS if l["id"] == inc["layer"]), "Security")
    title = f"{icon} VoiceGuard security: {inc['title']}"
    body = f"{layer} · {inc['severity'].upper()}\n{inc['detail']}\nAction taken: {inc['action'] or 'recorded'}\nOpen /admin/console"
    for uid, phone in admins:
        await hub.deliver(uid, {"type": "alert", "alert": {
            "id": f"sec-{inc['id']}-{inc['count']}", "kind": "security", "title": title, "body": body,
            "from_user_id": None, "payload": {"incident_id": inc["id"]}, "created_at": now().isoformat()}})
        key = f"{phone}:{inc['layer']}:{inc['kind']}"
        if SEVERITY[inc["severity"]] >= 2 and messaging.enabled() and time.time() - _wa_last.get(key, 0) > 600:
            _wa_last[key] = time.time()
            await asyncio.to_thread(messaging.send, phone, f"{title}\n{body}", "VoiceGuard admin", "")


def record(layer: int, kind: str, severity: str, detail: str, *, ip: str | None = None, user_id: str | None = None,
           path: str | None = None, action: str = "", title: str | None = None) -> int | None:
    """Store a breach. medium+ becomes (or adds to) an open incident and alerts the admin team."""
    with Session(engine) as s:
        s.add(SecEvent(layer=layer, kind=kind, severity=severity, ip=ip, user_id=user_id, path=(path or "")[:200],
                       detail=detail[:300]))
        s.commit()
        if SEVERITY[severity] < 1:
            return None
        since = now() - dt.timedelta(minutes=30)
        inc = s.exec(select(SecIncident).where(SecIncident.layer == layer, SecIncident.kind == kind,
                                               SecIncident.ip == ip, SecIncident.status == "open",
                                               SecIncident.last_at > since)).first()
        if inc:
            inc.count += 1
            inc.last_at = now()
            if SEVERITY[severity] > SEVERITY[inc.severity]:
                inc.severity = severity
            if action:
                inc.action = action
        else:
            inc = SecIncident(layer=layer, kind=kind, severity=severity, title=title or kind.replace("_", " "),
                              detail=detail[:500], ip=ip, user_id=user_id, action=action)
        s.add(inc)
        s.commit()
        s.refresh(inc)
        data = {"id": inc.id, "layer": layer, "kind": kind, "severity": inc.severity, "title": inc.title,
                "detail": inc.detail, "action": inc.action, "count": inc.count}
    log.warning("security L%s %s %s: %s (%s)", layer, severity, kind, detail[:120], action or "recorded")
    if inc.count == 1 or SEVERITY[severity] >= 2:   # first time, or escalated: tell the admins
        _schedule(_notify(data))
    return data["id"]


# ------------------------------------------------------------------ layer 4: input shield
_ATTACKS = [(re.compile(p, re.I), name) for p, name in [
    (r"('|%27)\s*(or|and)\s+['\"\w]+\s*=\s*['\"\w]+", "SQL injection"), (r"\bunion\b\s+(all\s+)?\bselect\b", "SQL injection"),
    (r";\s*(drop|delete|truncate|alter|insert|update)\s+\w", "SQL injection"), (r"\bsleep\s*\(\s*\d+\s*\)", "SQL injection"),
    (r"information_schema|xp_cmdshell|sqlite_master", "SQL injection"),
    (r"<\s*script\b", "script injection"), (r"javascript\s*:", "script injection"), (r"<\s*iframe\b", "script injection"),
    (r"\bon(error|load|mouseover|focus)\s*=", "script injection"),
    (r"\.\./|\.\.\\|%2e%2e", "path traversal"), (r"/etc/passwd|\\windows\\system32", "path traversal"),
    (r"\$\{jndi:", "JNDI injection"), (r";\s*(rm|wget|curl|bash|sh|powershell|nc)\s", "command injection"),
]]


def find_attack(text: str) -> str | None:
    if not text:
        return None
    for rx, name in _ATTACKS:
        if rx.search(text):
            return name
    return None


# ------------------------------------------------------------------ layer 6: integrity (code, AI weights, settings)
def file_hashes() -> dict[str, str]:
    out = {}
    for p in sorted(APP_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".html", ".js", ".css", ".npz"):
            out[str(p.relative_to(APP_DIR)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    env = config.BASE_DIR / ".env"
    if env.exists():
        out["(settings) .env"] = hashlib.sha256(env.read_bytes()).hexdigest()
    return out


def diff(a: dict, b: dict) -> dict:
    return {"changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
            "added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys())}


def baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}


def approve_baseline(by: str) -> dict:
    h = file_hashes()
    BASELINE.write_text(json.dumps({"approved_at": now().isoformat(), "approved_by": by, "files": h}, indent=0), encoding="utf-8")
    _snapshot["files"] = h
    return {"files": len(h)}


def integrity_status() -> dict:
    b = baseline()
    d = diff(b.get("files", {}), file_hashes())
    return {"approved_at": b.get("approved_at"), "approved_by": b.get("approved_by"), **d,
            "clean": not any(d.values())}


_snapshot: dict = {}


def db_check() -> str:
    try:
        con = sqlite3.connect(config.DB_PATH)
        r = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
        return r
    except Exception as e:
        return f"error: {type(e).__name__}"


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"voiceguard-{now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(config.DB_PATH)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    for old in sorted(BACKUP_DIR.glob("voiceguard-*.db"))[:-10]:   # keep the last 10
        old.unlink(missing_ok=True)
    return dst


INTEGRITY_LOCK = threading.Lock()


def planned_change(by: str, change) -> dict:
    """A change the team makes on purpose (switching the AI model from the team portal): done while the watchtower
    waits and approved at once, so it is never mistaken for tampering."""
    with INTEGRITY_LOCK:
        change()
        return approve_baseline(by)


def tamper_check(current: dict | None = None) -> bool:
    """Vault: True (and lockdown) if server code, AI weights or settings changed since the server started."""
    cur = current if current is not None else file_hashes()
    d = diff(_snapshot.get("files", {}), cur)
    if not any(d.values()):
        return False
    _snapshot["files"] = cur
    files = ", ".join((d["changed"] + d["added"] + d["removed"])[:6])
    if TAMPER_LOCKDOWN:
        set_lockdown(True, f"Server files changed while running: {files}")
    record(6, "files_changed_live", "critical", f"Changed while the server was running: {files}",
           action="lockdown" if TAMPER_LOCKDOWN else "recorded", title="Server code or AI files changed while running")
    return True


def _monitor():
    """Watchtower thread: live tamper check every minute, database check every 30 minutes."""
    last_db = time.time()
    while True:
        time.sleep(60)
        try:
            with INTEGRITY_LOCK:
                tamper_check()
            if time.time() - last_db > 1800:
                last_db = time.time()
                r = db_check()
                if r != "ok":
                    set_lockdown(True, f"Database check failed: {r}")
                    record(5, "db_damaged", "critical", f"Database integrity check: {r}", action="lockdown",
                           title="Database damaged")
        except Exception:
            log.exception("watchtower check failed")


def start(loop: asyncio.AbstractEventLoop | None = None):
    """Called once at server start."""
    global _loop
    _loop = loop
    cur = file_hashes()
    _snapshot["files"] = cur
    b = baseline()
    if not b:
        approve_baseline("first start")
    else:
        d = diff(b.get("files", {}), cur)
        if any(d.values()):
            record(6, "changed_since_approved", "medium",
                   f"{len(d['changed']) + len(d['added']) + len(d['removed'])} file(s) differ from the approved version - "
                   "check them in the Admin Console (Updates) and approve if expected.",
                   action="recorded", title="Server files differ from the approved version")
    threading.Thread(target=_monitor, daemon=True, name="watchtower").start()


# ------------------------------------------------------------------ the gate (ASGI middleware, outermost)
EXEMPT_LOCKDOWN = ("/api/health", "/admin/", "/api/admin/", "/api/auth/", "/portal", "/api/portal/")
PARTNER_API = "/api/v1/"   # telecom / bank servers (API keys), not phones: no app identity to check
SCRAPE_PATHS = ("/api/numbers/", "/api/caller-identity/lookup/")


def _raw_token(headers: dict, query: str) -> str | None:
    a = headers.get("authorization", "")
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    m = re.search(r"(?:^|&)token=([^&]+)", query)
    return unquote_plus(m.group(1)) if m else None


def _hash(raw: str | None) -> str | None:
    return hashlib.sha256(raw.encode()).hexdigest() if raw else None


def _uid(raw: str | None) -> str | None:
    """The account behind a token, through the existing sign-in check (looked up only when a rule fires)."""
    if not raw:
        return None
    from .auth import user_for_token
    with Session(engine) as s:
        u = user_for_token(s, raw)
        return u.id if u else None


async def _send_json(send, status: int, data: dict):
    body = json.dumps(data).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class SecurityGate:
    def __init__(self, app):
        self.app = app

    @staticmethod
    def _ip(scope, headers) -> str:
        return real_ip((scope.get("client") or ("?", 0))[0], headers)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        ip, path = self._ip(scope, headers), scope.get("path", "")
        # Layer 1: blocked addresses never get in (web sockets too)
        if is_blocked("ip", ip):
            if scope["type"] == "websocket":
                await receive()
                return await send({"type": "websocket.close", "code": 4403})
            return await _send_json(send, 403, {"detail": "Access blocked by VoiceGuard security."})
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)
        # lockdown: everything except health, admin and sign-in stops
        ld = lockdown()
        if ld and not path.startswith(EXEMPT_LOCKDOWN) and path != "/":
            return await _send_json(send, 503, {"detail": "VoiceGuard is in security lockdown. The admin team has been alerted.",
                                                "lockdown": True})
        raw = _raw_token(headers, scope.get("query_string", b"").decode("latin-1"))
        th = _hash(raw)
        if is_blocked("token", th):
            return await _send_json(send, 429, {"detail": "Your access is paused by VoiceGuard security. Sign in again later."})
        # Layer 4: size and attack patterns in the address
        try:
            length = int(headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            record(4, "oversized_request", "medium", f"{length // 1_048_576} MB request to {path}", ip=ip, path=path, action="rejected")
            return await _send_json(send, 413, {"detail": "Request too large."})
        attack = find_attack(unquote_plus(scope.get("raw_path", b"").decode("latin-1") + "?" + scope.get("query_string", b"").decode("latin-1")))
        # read small JSON / form bodies once so they can be checked, then hand them on unchanged
        body = b""
        ctype = headers.get("content-type", "")
        if not attack and length and length <= 65_536 and ("json" in ctype or "x-www-form-urlencoded" in ctype):
            more = True
            while more:
                m = await receive()
                body += m.get("body", b"")
                more = m.get("more_body", False)
            attack = find_attack(unquote_plus(body.decode("utf-8", "replace")))
            if path.startswith(("/api/auth/otp/", "/api/portal/login/")) and "json" in ctype:
                try:
                    phone = json.loads(body or b"{}").get("phone", "")
                except (ValueError, AttributeError):
                    phone = ""
                if phone and phone_blocked(phone):   # Layer 2: a number under OTP attack is paused
                    return await _send_json(send, 429, {"detail": "Sign-in for this number is paused for security. Try again later."})
                if phone and from_internet(ip) and demo_number(phone):
                    # fixed demo codes (VG_OTP_TEST_NUMBERS) are known to many people: never from the internet
                    record(2, "demo_number_from_internet", "medium", f"Sign-in with demo number {normalize(phone)[:6]}… from the internet refused",
                           ip=ip, path=path, action="refused", title="Demo number used from the internet")
                    return await _send_json(send, 403, {"detail": "Demo numbers work only on the home Wi-Fi. Sign in with your real number."})
            sent = [False]

            async def replay():
                if not sent[0]:
                    sent[0] = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            downstream = replay
        else:
            downstream = receive
        if attack:
            n = win.hit("attack_ip", ip)
            act = "request rejected"
            if n >= LIMITS["attack_ip"][0]:
                block("ip", ip, BAN_MIN, f"repeated {attack} attempts")
                act = f"address blocked for {BAN_MIN} min"
            record(4, "attack_" + attack.split()[0].lower(), "high" if n >= LIMITS["attack_ip"][0] else "medium",
                   f"{attack} attempt on {path}", ip=ip, path=path, action=act, title=f"{attack} attempt")
            return await _send_json(send, 400, {"detail": "Request blocked by VoiceGuard security."})
        # Layer 7: app identity (X-VG-App: version;signing-cert-sha256;debuggable)
        app_hdr = headers.get("x-vg-app")
        if path.startswith("/api/") and not path.startswith(EXEMPT_LOCKDOWN + (PARTNER_API,)):
            cert = (app_hdr or "").split(";")[1].strip().lower() if app_hdr and ";" in app_hdr else None
            unknown = APP_CERTS and cert is not None and cert not in APP_CERTS
            missing = APP_TRUST_MODE == "enforce" and app_hdr is None
            if unknown or missing:
                if win.hit("attack_ip", "app:" + ip) == 1 or APP_TRUST_MODE == "enforce":   # monitor: once per 10 min
                    record(7, "unknown_app" if unknown else "no_app_identity", "high" if unknown else "medium",
                           f"{'Unknown app signature ' + (cert or '')[:16] if unknown else 'Request without app identity'} on {path}",
                           ip=ip, path=path, action="blocked" if APP_TRUST_MODE == "enforce" else "recorded",
                           title="Modified or unknown app" if unknown else "Request not from the VoiceGuard app")
                if APP_TRUST_MODE == "enforce":
                    return await _send_json(send, 403, {"detail": "Please use the official VoiceGuard app."})
        # Layer 1: flood
        if win.hit("flood", ip) == LIMITS["flood"][0]:
            block("ip", ip, 30, "request flood")
            record(1, "flood", "high", f"More than {LIMITS['flood'][0]} requests in a minute", ip=ip, path=path,
                   action="address blocked for 30 min", title="Request flood")
        status = [0]
        resp = bytearray()   # the reply of a failed OTP check is kept, to tell a wrong code from an expired one

        async def send_wrap(msg):
            if msg["type"] == "http.response.start":
                status[0] = msg["status"]
            elif msg["type"] == "http.response.body" and status[0] == 400 and path == "/api/auth/otp/verify" and len(resp) < 2048:
                resp.extend(msg.get("body", b""))
            await send(msg)
        try:
            await self.app(scope, downstream, send_wrap)
        except Exception as e:
            err = uuid.uuid4().hex[:8]
            log.error("unhandled error %s on %s: %s\n%s", err, path, e, traceback.format_exc())
            if win.hit("errors", "all") >= LIMITS["errors"][0]:
                record(5, "error_burst", "medium", f"Many server errors in 5 minutes (latest {err} on {path})", path=path,
                       title="Burst of server errors (possible bug or attack)")
            else:
                record(5, "bug", "low", f"Error {err} on {path}: {type(e).__name__}", ip=ip, path=path)
            if status[0] == 0:
                return await _send_json(send, 500, {"detail": "Internal error - the team has been notified.", "error_id": err})
            raise
        self._observe(ip, path, status[0], raw, body, ctype, bytes(resp))

    def _observe(self, ip: str, path: str, code: int, raw: str | None, body: bytes, ctype: str, resp: bytes = b""):
        th = _hash(raw)
        if code == 404 and win.hit("scan", ip) == LIMITS["scan"][0]:
            block("ip", ip, BAN_MIN, "scanning for pages")
            record(1, "scanner", "high", f"{LIMITS['scan'][0]} unknown pages requested in 5 minutes", ip=ip, path=path,
                   action=f"address blocked for {BAN_MIN} min", title="Scanner probing the server")
        elif code == 401 and win.hit("bad_token", ip) == LIMITS["bad_token"][0]:
            block("ip", ip, BAN_MIN, "many invalid sign-in tokens")
            record(2, "bad_tokens", "high", f"{LIMITS['bad_token'][0]} requests with invalid tokens in 10 minutes", ip=ip,
                   path=path, action=f"address blocked for {BAN_MIN} min", title="Fake or stolen login tokens")
        elif code == 400 and path == "/api/auth/otp/verify" and b"Wrong code" in resp:   # not "expired"
            phone = None
            if "json" in ctype:
                try:
                    phone = normalize(json.loads(body or b"{}").get("phone", ""))
                except Exception:
                    phone = None
            if win.hit("otp_ip", ip) == LIMITS["otp_ip"][0]:
                block("ip", ip, BAN_MIN, "OTP guessing")
                record(2, "otp_guessing", "high", f"{LIMITS['otp_ip'][0]} wrong OTP codes from one address", ip=ip, path=path,
                       action=f"address blocked for {BAN_MIN} min", title="OTP code guessing")
            if phone and win.hit("otp_phone", phone) == LIMITS["otp_phone"][0]:
                block("phone", phone, BAN_MIN, "OTP guessing on this number")
                record(2, "otp_guessing_number", "high", f"{LIMITS['otp_phone'][0]} wrong codes for {phone}", ip=ip, path=path,
                       action=f"sign-in for this number paused {BAN_MIN} min", title="Someone is guessing a number's OTP")
        elif code == 403:
            uid = _uid(raw)
            if path.startswith("/api/admin/") and uid:
                record(3, "admin_probe", "medium", f"Non-admin account tried {path}", ip=ip, user_id=uid, path=path,
                       title="Admin area probed by a non-admin account")
            if uid and win.hit("forbidden_user", uid) == LIMITS["forbidden_user"][0]:
                n = revoke_sessions(uid)
                block("token", th, BAN_MIN, "repeated forbidden requests")
                record(3, "forbidden_user", "high", f"{LIMITS['forbidden_user'][0]} forbidden requests by one account",
                       ip=ip, user_id=uid, path=path, action=f"signed out on {n} device(s), paused {BAN_MIN} min",
                       title="Account trying to reach other people's data")
            if win.hit("forbidden_ip", ip) == LIMITS["forbidden_ip"][0]:
                block("ip", ip, BAN_MIN, "repeated forbidden requests")
                record(3, "forbidden_ip", "high", f"{LIMITS['forbidden_ip'][0]} forbidden requests from one address",
                       ip=ip, path=path, action=f"address blocked for {BAN_MIN} min", title="Access-control probing")
        elif code == 200 and th and path.startswith(SCRAPE_PATHS) and win.hit("scrape", th) == LIMITS["scrape"][0]:
            uid = _uid(raw)
            block("token", th, 30, "bulk number lookups")
            record(5, "scraping", "high", f"{LIMITS['scrape'][0]} number lookups in 10 minutes by one account",
                   ip=ip, user_id=uid, path=path, action="account paused for 30 min", title="Bulk data scraping")


def phone_blocked(phone: str) -> bool:
    return is_blocked("phone", normalize(phone or ""))


def demo_number(phone: str) -> bool:
    from .auth import TEST_NUMBERS
    return normalize(phone or "") in TEST_NUMBERS
