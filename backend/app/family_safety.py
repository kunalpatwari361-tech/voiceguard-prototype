"""Family Safety & Live Location Sharing (isolated module: new fs_* tables, /api/family-safety/*).

Built on what VoiceGuard already has: the family circle (Family, User.family_id) is the group, the existing phone
sign-in token identifies every request, the WebSocket hub + FCM deliver realtime updates and alerts, and alerts land
in the existing Alert table so older app versions still show them.

Privacy rules:
- a member shares only after explicit consent (version, scope, grant and revoke times are kept);
- consent belongs to one family circle; leaving / being removed revokes it, and every read re-checks it;
- only the LATEST location is kept (no movement history), deleted on revoke, leave or after the retention time;
- nobody - not the family owner, not the VoiceGuard team - can switch another member's sharing on;
- coordinates are never written to logs or audit entries.

Switch the whole feature off with VG_FAMILY_SAFETY=0 (caller ID and fraud detection are not affected).
"""
import collections
import datetime as dt
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import threading
import time
import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, select

from . import config
from .db import Alert, Family, User, dumps, engine, now

log = logging.getLogger("voiceguard.family_safety")

ENABLED = os.getenv("VG_FAMILY_SAFETY", "1") == "1"
CONSENT_VERSION = "2026-10-21"
LIVE_S = 6 * 60                      # newer than this: "live"
STALE_S = 60 * 60                    # older than LIVE_S: "stale"; older than this: "offline"
MAX_FIX_AGE_S = 15 * 60              # older fixes are refused as stale
MAX_FUTURE_S = 120                   # clock skew allowed for a fix "from the future"
MIN_INTERVAL_S = 5                   # at most one update every 5 s per member
MAX_ACCURACY_M = 2000                # worse than this is not a location
GEOFENCE_MAX_ACCURACY_M = 250        # zones and arrivals ignore vaguer fixes
LOCATION_RETENTION_H = int(os.getenv("VG_FAMILY_LOCATION_RETENTION_H", "168"))
EVENT_RETENTION_D = int(os.getenv("VG_FAMILY_EVENT_RETENTION_D", "90"))
EVENT_LOCATION_D = 30                # coordinates inside safety events are removed after this
INVITE_MAX_H = 168
BATTERY_REARM = 5                    # % above the threshold before a new low-battery alert is possible
BATTERY_COOLDOWN_H = 6
ZONE_COOLDOWN_S = 10 * 60
SOS_DUPLICATE_S = 120
WATCH_S = 10 * 60                    # the map asks for realtime updates for 10 minutes at a time
SOS_BACKOFF_S = (0, 20, 40, 80, 160, 320)
OTHER_BACKOFF_S = (0, 30, 120)

CONSENT_TEXT = {
    "what": "Your phone's location (latitude, longitude and how accurate it is) and, only if you tick it, your "
            "battery level. Only the latest position is kept - VoiceGuard does not store where you have been.",
    "why": "So your family circle can see that you are safe, get your SOS with your location, and be told when you "
           "arrive somewhere or enter / leave a safe zone you agreed to.",
    "who": "Only the members of your family circle who are signed in to VoiceGuard. Not other users, not the "
           "VoiceGuard team, not advertisers. The family owner cannot turn your sharing on or see it while paused.",
    "when": "While sharing is on, your phone sends its position about once a minute when you move (at least every "
            "5 minutes), also when the app is in the background. A notification is always shown while it runs. "
            "Pause or stop at any time in the app or from that notification. After the phone restarts, sharing "
            "waits until you open VoiceGuard again.",
    "keep": "Your latest position is deleted when you pause for good, stop, leave the family, or after "
            f"{LOCATION_RETENTION_H // 24} days without updates. Safety alerts are kept {EVENT_RETENTION_D} days "
            f"(their location {EVENT_LOCATION_D} days).",
}


# ------------------------------------------------------------------ tables (new, additive)
class FsConsent(SQLModel, table=True):
    """Consent history: one row per grant; revoked_at is set when it ends (kept as proof, no coordinates)."""
    __tablename__ = "fs_consent"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    version: str
    scope_location: bool = True
    scope_battery: bool = False
    scope_alerts: bool = True            # family safe-zone alerts about me
    granted_at: dt.datetime = Field(default_factory=now)
    revoked_at: dt.datetime | None = None
    revoked_reason: str = ""


class FsSharing(SQLModel, table=True):
    """The member's current sharing switch (pause / threshold)."""
    __tablename__ = "fs_sharing"
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    consent_id: int = Field(foreign_key="fs_consent.id")
    paused: bool = False
    paused_until: dt.datetime | None = None
    battery_threshold: int = 15
    updated_at: dt.datetime = Field(default_factory=now)


class FsLocation(SQLModel, table=True):
    """Latest shared position only - one row per member, replaced on every update."""
    __tablename__ = "fs_location"
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    lat: float
    lon: float
    accuracy: float
    recorded_at: dt.datetime
    received_at: dt.datetime = Field(default_factory=now, index=True)
    battery: int | None = None
    charging: bool | None = None


class FsInvite(SQLModel, table=True):
    __tablename__ = "fs_invite"
    id: int | None = Field(default=None, primary_key=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: dt.datetime = Field(default_factory=now)
    expires_at: dt.datetime
    max_uses: int = 1
    uses: int = 0
    revoked_at: dt.datetime | None = None


class FsZone(SQLModel, table=True):
    __tablename__ = "fs_zone"
    id: int | None = Field(default=None, primary_key=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    owner_id: str = Field(foreign_key="user.id")
    name: str
    lat: float
    lon: float
    radius_m: int
    enabled: bool = True
    notify_entry: bool = True
    notify_exit: bool = True
    subjects: str = "[]"                 # members whose entry / exit is reported (they can see the zone)
    recipients: str = "[]"               # members who get the alerts
    created_at: dt.datetime = Field(default_factory=now)
    updated_at: dt.datetime = Field(default_factory=now)


class FsZoneState(SQLModel, table=True):
    __tablename__ = "fs_zone_state"
    __table_args__ = (UniqueConstraint("zone_id", "user_id"),)
    id: int | None = Field(default=None, primary_key=True)
    zone_id: int = Field(foreign_key="fs_zone.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    inside: bool | None = None           # None = not known yet (first fix sets it without an alert)
    changed_at: dt.datetime = Field(default_factory=now)
    last_event_at: dt.datetime | None = None
    last_enter_at: dt.datetime | None = None # the cool-down stops repeats of the same alert, not the opposite one
    last_exit_at: dt.datetime | None = None


class FsArrival(SQLModel, table=True):
    __tablename__ = "fs_arrival"
    id: int | None = Field(default=None, primary_key=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)   # the member travelling (who set it)
    name: str
    lat: float
    lon: float
    radius_m: int = 150
    recipients: str = "[]"
    created_at: dt.datetime = Field(default_factory=now)
    expires_at: dt.datetime
    status: str = "active"               # active / arrived / cancelled / expired
    arrived_at: dt.datetime | None = None


class FsBattery(SQLModel, table=True):
    __tablename__ = "fs_battery"
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    level: int | None = None
    charging: bool | None = None
    below: bool = False
    last_alert_at: dt.datetime | None = None
    updated_at: dt.datetime = Field(default_factory=now)


class FsEvent(SQLModel, table=True):
    """A safety event: SOS, arrival, safe-zone entry / exit, low battery."""
    __tablename__ = "fs_event"
    __table_args__ = (UniqueConstraint("user_id", "client_key"),)
    id: str = Field(default_factory=lambda: "SE-" + uuid.uuid4().hex[:12].upper(), primary_key=True)
    family_id: str = Field(foreign_key="family.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    kind: str                            # sos / arrival / zone_enter / zone_exit / battery_low
    status: str = "active"               # sos: active / cancelled / resolved; others: info
    client_key: str | None = None        # idempotency key from the phone (SOS)
    lat: float | None = None
    lon: float | None = None
    accuracy: float | None = None
    location_at: dt.datetime | None = None
    detail: str = ""
    created_at: dt.datetime = Field(default_factory=now, index=True)
    updated_at: dt.datetime = Field(default_factory=now)
    closed_by: str | None = None


class FsDelivery(SQLModel, table=True):
    """One row per event and recipient: pending -> sent (handed to the live link / push) -> delivered (the phone
    confirmed) ; failed = could not be handed over after all retries; unconfirmed = sent, never confirmed."""
    __tablename__ = "fs_delivery"
    __table_args__ = (UniqueConstraint("event_id", "recipient_id"),)
    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(foreign_key="fs_event.id", index=True)
    recipient_id: str = Field(foreign_key="user.id", index=True)
    alert_id: int | None = None
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    last_attempt_at: dt.datetime | None = None
    next_attempt_at: dt.datetime | None = Field(default=None, index=True)
    delivered_at: dt.datetime | None = None
    error: str = ""


class FsAudit(SQLModel, table=True):
    """Who changed what (membership, consent, invites, zones, events) - never coordinates or tokens."""
    __tablename__ = "fs_audit"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=now, index=True)
    actor_id: str = ""
    family_id: str = ""
    action: str
    target: str = ""
    detail: str = ""


TABLES = [FsConsent, FsSharing, FsLocation, FsInvite, FsZone, FsZoneState, FsArrival, FsBattery, FsEvent, FsDelivery, FsAudit]


# ------------------------------------------------------------------ small helpers
def audit(s: Session, actor: str, family: str | None, action: str, target: str = "", detail: str = ""):
    s.add(FsAudit(actor_id=actor or "", family_id=family or "", action=action, target=str(target)[:80], detail=detail[:200]))


def aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def valid_point(lat, lon) -> bool:
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool) \
        and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def token_hash(token: str) -> str:
    return hmac.new(config.SECRET, ("fs-invite|" + token).encode(), hashlib.sha256).hexdigest()


def new_invite_token() -> str:
    return "vgfam_" + secrets.token_urlsafe(24)


def ids(raw: str) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [x for x in v if isinstance(x, str)]
    except ValueError:
        return []


class Limiter:
    """Sliding-window limits per member (abuse prevention); in memory."""

    def __init__(self):
        self._d: dict[tuple, collections.deque] = {}
        self._lock = threading.Lock()

    def allow(self, name: str, key: str, count: int, per_s: int) -> bool:
        t = time.time()
        with self._lock:
            q = self._d.setdefault((name, key), collections.deque())
            while q and t - q[0] > per_s:
                q.popleft()
            if len(q) >= count:
                return False
            q.append(t)
            return True


limiter = Limiter()

# operational counters for the Team Portal (since the server started)
stats: collections.Counter = collections.Counter()
recent_failures: collections.deque = collections.deque(maxlen=2000)   # (time, kind)
worker_state = {"started": None, "heartbeat": None, "errors": 0}


def count(kind: str, failure: bool = False):
    stats[kind] += 1
    if failure:
        recent_failures.append((time.time(), kind))


# ------------------------------------------------------------------ consent & visibility
def active_consent(s: Session, u: User) -> FsConsent | None:
    """The member's consent for their CURRENT family circle. A consent for a circle they are no longer in (left via
    any screen, removed) is revoked here, so access ends at once."""
    rows = s.exec(select(FsConsent).where(FsConsent.user_id == u.id, FsConsent.revoked_at == None)).all()   # noqa: E711
    current = None
    for c in rows:
        if c.family_id == u.family_id and c.version == CONSENT_VERSION:
            current = c
        else:
            c.revoked_at, c.revoked_reason = now(), ("left family circle" if c.family_id != u.family_id else "new consent text")
            s.add(c)
            _forget_location(s, u.id)
    if len(rows) != (1 if current else 0):
        s.commit()
    return current


def sharing_state(s: Session, u: User) -> tuple[FsConsent | None, FsSharing | None, str]:
    """(consent, sharing row, state) with state = sharing / paused / off."""
    c = active_consent(s, u)
    if not c:
        return None, None, "off"
    sh = s.get(FsSharing, u.id)
    if not sh or sh.consent_id != c.id:
        return c, None, "off"
    if sh.paused:
        until = aware(sh.paused_until)
        if until and until <= now():
            sh.paused, sh.paused_until = False, None
            s.add(sh)
            s.commit()
        else:
            return c, sh, "paused"
    return c, sh, "sharing"


def location_visible(s: Session, viewer: User, subject: User) -> bool:
    """Server-side check for every read: same circle, subject shares (consented, not paused)."""
    if not viewer.family_id or viewer.family_id != subject.family_id:
        return False
    c, _, state = sharing_state(s, subject)
    return state == "sharing" and c is not None and c.scope_location


def members(s: Session, family_id: str) -> list[User]:
    return list(s.exec(select(User).where(User.family_id == family_id)).all())


def status_of(loc: FsLocation | None) -> tuple[str, int | None]:
    if not loc:
        return "no_location", None
    age = int((now() - aware(loc.received_at)).total_seconds())
    return ("live" if age <= LIVE_S else "stale" if age <= STALE_S else "offline"), age


def map_entry(s: Session, viewer: User, m: User) -> dict:
    c, sh, state = sharing_state(s, m)
    e = {"user_id": m.id, "name": m.name, "is_me": m.id == viewer.id, "sharing": state}
    if state != "sharing" or not (c and c.scope_location):
        return e | {"status": state if state != "sharing" else "off", "location": None}
    loc = s.get(FsLocation, m.id)
    st, age = status_of(loc)
    e |= {"status": st, "age_s": age, "location": None}
    if loc and loc.family_id == m.family_id:
        e["location"] = {"lat": loc.lat, "lon": loc.lon, "accuracy": loc.accuracy, "recorded_at": loc.recorded_at.isoformat(),
                         "received_at": aware(loc.received_at).isoformat()}
        if c.scope_battery and loc.battery is not None:
            e["battery"] = {"level": loc.battery, "charging": loc.charging}
    return e


def _forget_location(s: Session, user_id: str):
    row = s.get(FsLocation, user_id)
    if row:
        s.delete(row)
    for st in s.exec(select(FsZoneState).where(FsZoneState.user_id == user_id)).all():
        s.delete(st)


def revoke(s: Session, u: User, reason: str, actor: str | None = None) -> bool:
    """Ends consent and sharing, deletes the latest location. Returns True if something was active."""
    rows = s.exec(select(FsConsent).where(FsConsent.user_id == u.id, FsConsent.revoked_at == None)).all()   # noqa: E711
    for c in rows:
        c.revoked_at, c.revoked_reason = now(), reason[:60]
        s.add(c)
    sh = s.get(FsSharing, u.id)
    if sh:
        s.delete(sh)
    _forget_location(s, u.id)
    for a in s.exec(select(FsArrival).where(FsArrival.user_id == u.id, FsArrival.status == "active")).all():
        a.status = "cancelled"
        s.add(a)
    audit(s, actor or u.id, u.family_id, "consent revoked", u.id, reason)
    s.commit()
    return bool(rows)


# ------------------------------------------------------------------ realtime map (WebSocket hub)
_watchers: dict[str, dict[str, float]] = {}   # family id -> {user id: watching until (epoch s)}


def watch(family_id: str, user_id: str) -> int:
    _watchers.setdefault(family_id, {})[user_id] = time.time() + WATCH_S
    return WATCH_S


def unwatch(family_id: str, user_id: str):
    _watchers.get(family_id, {}).pop(user_id, None)


def watchers(family_id: str) -> list[str]:
    w = _watchers.get(family_id, {})
    t = time.time()
    for k in [k for k, until in w.items() if until <= t]:
        del w[k]
    return list(w)


async def broadcast_member(s: Session, subject: User):
    """Realtime: tell the family members who have the map open about this member's new state."""
    from .hub import hub
    for wid in watchers(subject.family_id or ""):
        if wid == subject.id:
            continue
        viewer = s.get(User, wid)
        if not viewer or viewer.family_id != subject.family_id:
            unwatch(subject.family_id, wid)
            continue
        if await hub.send(wid, {"type": "family_safety_member", "member": map_entry(s, viewer, subject)}):
            count("realtime_sent")
        else:
            count("realtime_failed", failure=True)


# ------------------------------------------------------------------ notifications with delivery tracking
TITLES = {"sos": "🆘 SOS from {name}", "arrival": "📍 {name} arrived: {place}", "zone_enter": "🟢 {name} entered {place}",
          "zone_exit": "🟠 {name} left {place}", "battery_low": "🔋 {name}'s phone battery is low ({level}%)"}


def _body(ev: FsEvent, name: str) -> str:
    where = ""
    if ev.lat is not None and ev.location_at:
        mins = int((now() - aware(ev.location_at)).total_seconds() // 60)
        where = f"Location from {mins} min ago (± {int(ev.accuracy or 0)} m)." if mins else f"Location now (± {int(ev.accuracy or 0)} m)."
    if ev.kind == "sos":
        return (f"{name} pressed SOS in VoiceGuard. {where or 'No location available.'} {ev.detail}".strip()
                + " VoiceGuard has NOT contacted police or 112 - call them yourself if needed.")
    return (ev.detail + (" " + where if where and ev.kind != "battery_low" else "")).strip()


async def notify(s: Session, ev: FsEvent, recipients: list[str], title_vars: dict):
    """Creates one delivery row per recipient (current family members only) and tries the first delivery now."""
    sender = s.get(User, ev.user_id)
    valid = [r for r in dict.fromkeys(recipients) if r != ev.user_id and (u := s.get(User, r)) and u.family_id == ev.family_id]
    title = TITLES[ev.kind].format(name=sender.name if sender else "Family member", **title_vars)
    payload = {"safety_event_id": ev.id, "safety_kind": ev.kind, "status": ev.status, "from_name": sender.name if sender else ""}
    if ev.lat is not None:
        payload["location"] = {"lat": ev.lat, "lon": ev.lon, "accuracy": ev.accuracy,
                               "age_min": int((now() - aware(ev.location_at)).total_seconds() // 60) if ev.location_at else None,
                               "map": f"https://maps.google.com/?q={ev.lat:.6f},{ev.lon:.6f}"}
    for rid in valid:
        if s.exec(select(FsDelivery).where(FsDelivery.event_id == ev.id, FsDelivery.recipient_id == rid)).first():
            continue
        a = Alert(family_id=ev.family_id, from_user_id=ev.user_id, to_user_id=rid, kind="family_" + ev.kind, title=title,
                  body=_body(ev, sender.name if sender else ""), payload_json=dumps(payload))
        s.add(a)
        s.commit()
        s.refresh(a)
        s.add(FsDelivery(event_id=ev.id, recipient_id=rid, alert_id=a.id, next_attempt_at=now()))
    s.commit()
    await process_deliveries(s, event_id=ev.id)
    return valid


async def process_deliveries(s: Session, event_id: str | None = None):
    """Sends due deliveries (first try or retry with back-off). SOS keeps retrying until the phone confirms."""
    from .hub import hub
    from .routers.common import alert_dict
    t = now()
    stmt = select(FsDelivery).where(FsDelivery.status.in_(["pending", "sent"]), FsDelivery.next_attempt_at != None,   # noqa: E711
                                    FsDelivery.next_attempt_at <= t)
    if event_id:
        stmt = stmt.where(FsDelivery.event_id == event_id)
    for d in s.exec(stmt.limit(200)).all():
        ev = s.get(FsEvent, d.event_id)
        a = s.get(Alert, d.alert_id) if d.alert_id else None
        if not ev or not a or (ev.kind == "sos" and ev.status != "active" and d.status == "sent"):
            d.next_attempt_at = None
            s.add(d)
            continue
        backoff = SOS_BACKOFF_S if ev.kind == "sos" else OTHER_BACKOFF_S
        try:
            ok = await hub.deliver(d.recipient_id, {"type": "alert", "alert": alert_dict(a)})
            d.error = "" if ok else "phone not reachable (offline, no push)"
        except Exception as e:   # one failing phone must not stop the others
            ok, d.error = False, type(e).__name__
            count("delivery_error", failure=True)
        d.attempts += 1
        d.last_attempt_at = t
        if ok and d.status == "pending":
            d.status = "sent"
            count("delivery_sent")
        done_trying = d.attempts >= len(backoff)
        need_more = (ev.kind == "sos" and ev.status == "active") or d.status == "pending"
        if done_trying or not need_more:
            d.next_attempt_at = None
            if d.status == "pending":
                d.status = "failed"
                count("delivery_failed", failure=True)
            elif ev.kind == "sos" and done_trying:
                d.status = "unconfirmed"
                count("delivery_unconfirmed", failure=True)
        else:
            d.next_attempt_at = t + dt.timedelta(seconds=backoff[d.attempts])
        s.add(d)
    s.commit()


def acknowledge(s: Session, ev: FsEvent, u: User) -> bool:
    d = s.exec(select(FsDelivery).where(FsDelivery.event_id == ev.id, FsDelivery.recipient_id == u.id)).first()
    if not d:
        return False
    if d.status != "delivered":
        d.status, d.delivered_at, d.next_attempt_at = "delivered", now(), None
        s.add(d)
        s.commit()
        count("delivery_confirmed")
    return True


# ------------------------------------------------------------------ geofences (zones and arrivals) and battery
def _inside(dist: float, accuracy: float, radius: float, was_inside: bool | None) -> bool | None:
    """Hysteresis: enter only when clearly inside, leave only when clearly outside a margin band.
    Returns None when the fix cannot decide (stays as it was)."""
    margin = max(30.0, radius * 0.2)
    half = accuracy / 2
    if dist + half <= radius:
        return True
    if dist - half >= radius + margin:
        return False
    if was_inside is None:
        return dist <= radius   # first fix: best guess, never alerts
    return was_inside


def location_events(s: Session, u: User, loc: FsLocation, consent: FsConsent) -> list[tuple[FsEvent, list[str], dict]]:
    """Zone entry / exit and arrivals triggered by this fix (alerts sent by the caller)."""
    out = []
    if loc.accuracy > GEOFENCE_MAX_ACCURACY_M:
        count("geofence_skipped_inaccurate")
        return out
    t = now()
    if consent.scope_alerts:
        for z in s.exec(select(FsZone).where(FsZone.family_id == u.family_id, FsZone.enabled == True)).all():   # noqa: E712
            if u.id not in ids(z.subjects):
                continue
            st = s.exec(select(FsZoneState).where(FsZoneState.zone_id == z.id, FsZoneState.user_id == u.id)).first() \
                or FsZoneState(zone_id=z.id, user_id=u.id)
            was = st.inside
            inside = _inside(distance_m(loc.lat, loc.lon, z.lat, z.lon), loc.accuracy, z.radius_m, was)
            if inside is not None and inside != was:
                st.inside, st.changed_at = inside, t
                kind = "zone_enter" if inside else "zone_exit"
                last_same = aware(st.last_enter_at if inside else st.last_exit_at)
                cool = last_same is not None and (t - last_same).total_seconds() < ZONE_COOLDOWN_S
                if was is not None and not cool and ((inside and z.notify_entry) or (not inside and z.notify_exit)):
                    st.last_event_at = t
                    if inside:
                        st.last_enter_at = t
                    else:
                        st.last_exit_at = t
                    ev = FsEvent(family_id=u.family_id, user_id=u.id, kind=kind, status="info", lat=loc.lat, lon=loc.lon,
                                 accuracy=loc.accuracy, location_at=loc.recorded_at,
                                 detail=f"{'Entered' if inside else 'Left'} the safe zone '{z.name}'.")
                    out.append((ev, ids(z.recipients), {"place": z.name}))
            s.add(st)
    for a in s.exec(select(FsArrival).where(FsArrival.user_id == u.id, FsArrival.status == "active")).all():
        if aware(a.expires_at) <= t:
            a.status = "expired"
        elif _inside(distance_m(loc.lat, loc.lon, a.lat, a.lon), loc.accuracy, a.radius_m, False):
            a.status, a.arrived_at = "arrived", t
            ev = FsEvent(family_id=u.family_id, user_id=u.id, kind="arrival", status="info", lat=loc.lat, lon=loc.lon,
                         accuracy=loc.accuracy, location_at=loc.recorded_at, detail=f"Arrived at {a.name}.")
            out.append((ev, ids(a.recipients), {"place": a.name}))
        s.add(a)
    return out


def battery_event(s: Session, u: User, level: int | None, charging: bool | None, threshold: int) -> FsEvent | None:
    """Threshold crossing with re-arm and cool-down. Unknown level = no change (never assumed full)."""
    if level is None:
        return None
    b = s.get(FsBattery, u.id) or FsBattery(user_id=u.id)
    t = now()
    ev = None
    if charging or level >= threshold + BATTERY_REARM:
        b.below = False
    elif level <= threshold and not b.below:
        b.below = True
        if not b.last_alert_at or (t - aware(b.last_alert_at)).total_seconds() >= BATTERY_COOLDOWN_H * 3600:
            b.last_alert_at = t
            ev = FsEvent(family_id=u.family_id, user_id=u.id, kind="battery_low", status="info",
                         detail=f"Battery at {level}% and not charging - location sharing may stop soon.")
    b.level, b.charging, b.updated_at = level, charging, t
    s.add(b)
    return ev


# ------------------------------------------------------------------ background worker: retries, expiry, retention
def cleanup(s: Session) -> dict:
    t = now()
    gone = {"locations": 0, "events": 0, "invites": 0, "event_locations": 0}
    for row in s.exec(select(FsLocation).where(FsLocation.received_at < t - dt.timedelta(hours=LOCATION_RETENTION_H))).all():
        s.delete(row)
        gone["locations"] += 1
    old = t - dt.timedelta(days=EVENT_RETENTION_D)
    for ev in s.exec(select(FsEvent).where(FsEvent.created_at < old)).all():
        for d in s.exec(select(FsDelivery).where(FsDelivery.event_id == ev.id)).all():
            s.delete(d)
        s.delete(ev)
        gone["events"] += 1
    for ev in s.exec(select(FsEvent).where(FsEvent.created_at < t - dt.timedelta(days=EVENT_LOCATION_D), FsEvent.lat != None)).all():   # noqa: E711
        ev.lat = ev.lon = ev.accuracy = None
        s.add(ev)
        gone["event_locations"] += 1
    for inv in s.exec(select(FsInvite).where(FsInvite.expires_at < t - dt.timedelta(days=30))).all():
        s.delete(inv)
        gone["invites"] += 1
    for a in s.exec(select(FsArrival).where(FsArrival.status == "active", FsArrival.expires_at < t)).all():
        a.status = "expired"
        s.add(a)
    s.commit()
    return gone


async def worker():
    import asyncio
    worker_state["started"] = time.time()
    last_cleanup = 0.0
    while True:
        try:
            with Session(engine) as s:
                await process_deliveries(s)
                if time.time() - last_cleanup > 3600:
                    last_cleanup = time.time()
                    gone = cleanup(s)
                    if any(gone.values()):
                        log.info("family safety retention: %s", gone)
            worker_state["heartbeat"] = time.time()
        except Exception:
            worker_state["errors"] += 1
            log.exception("family safety worker")
        await asyncio.sleep(10)


def start():
    import asyncio
    if ENABLED:
        asyncio.get_running_loop().create_task(worker())
