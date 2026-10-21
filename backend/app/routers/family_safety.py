"""Family Safety API: /api/family-safety/* (see app/family_safety.py for the rules and docs/family-safety.md).

The acting member always comes from the sign-in token (current_user), never from the request body. Reading anyone's
location re-checks family membership and that member's consent on the server every time. 403 = not allowed (counted
by the castle security), 409 = sharing is not switched on (the phone stops sending), 404 = feature off.
"""
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as F
from sqlmodel import Session, select

from .. import family_safety as fs
from ..auth import current_user
from ..db import Family, User, get_session, now
from ..hub import hub
from .common import family_ids, iso

router = APIRouter(prefix="/api/family-safety", tags=["family-safety"])


def enabled():
    if not fs.ENABLED:
        raise HTTPException(404, "Family Safety is switched off on this server.")


def limit(name: str, key: str, count: int, per_s: int):
    if not fs.limiter.allow(name, key, count, per_s):
        fs.count("rate_limited_" + name, failure=True)
        raise HTTPException(429, "Too many requests - please wait a moment.")


def family_of(me: User, s: Session) -> Family:
    if not me.family_id:
        raise HTTPException(409, "You are not in a family circle yet.")
    f = s.get(Family, me.family_id)
    if not f:
        raise HTTPException(409, "Your family circle no longer exists.")
    return f


def same_family(s: Session, me: User, user_id: str) -> User:
    u = s.get(User, user_id)
    if not u or not me.family_id or u.family_id != me.family_id:
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "That person is not in your family circle.")
    return u


def owner_only(me: User, f: Family):
    if f.owner_id != me.id:
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "Only the family owner can do this.")


async def tell_family(s: Session, family_id: str, msg: dict, exclude: str | None = None):
    await hub.send_many(family_ids(s, family_id), msg, exclude=exclude)


Dep = [Depends(enabled)]


# ------------------------------------------------------------------ status
@router.get("/status")
def status(me: User = Depends(current_user), s: Session = Depends(get_session)):
    if not fs.ENABLED:
        return {"enabled": False}
    c, sh, state = fs.sharing_state(s, me)
    f = s.get(Family, me.family_id) if me.family_id else None
    return {"enabled": True, "consent_version": fs.CONSENT_VERSION, "sharing": state,
            "family": {"id": f.id, "name": f.name, "owner": f.owner_id == me.id} if f else None,
            "scope": {"location": c.scope_location, "battery": c.scope_battery, "alerts": c.scope_alerts} if c else None,
            "paused_until": iso(sh.paused_until) if sh and sh.paused else None,
            "battery_threshold": sh.battery_threshold if sh else 15,
            "intervals": {"moving_s": 60, "heartbeat_s": 300, "min_s": fs.MIN_INTERVAL_S}}


# ------------------------------------------------------------------ family group (the existing family circle)
class NewGroup(BaseModel):
    name: str = F(min_length=2, max_length=40)


@router.post("/group", dependencies=Dep)
async def create_group(body: NewGroup, me: User = Depends(current_user), s: Session = Depends(get_session)):
    if me.family_id and s.get(Family, me.family_id):
        raise HTTPException(409, "You are already in a family circle. Leave it first.")
    limit("group", me.id, 5, 3600)
    f = Family(name=body.name.strip(), owner_id=me.id)
    s.add(f)
    s.commit()
    s.refresh(f)
    me.family_id = f.id
    s.add(me)
    fs.audit(s, me.id, f.id, "group created")
    s.commit()
    return group(me, s)


@router.get("/group", dependencies=Dep)
def group(me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    out = []
    for m in fs.members(s, f.id):
        _, _, state = fs.sharing_state(s, m)
        out.append({"user_id": m.id, "name": m.name, "owner": m.id == f.owner_id, "is_me": m.id == me.id, "sharing": state})
    return {"id": f.id, "name": f.name, "owner_id": f.owner_id, "i_am_owner": f.owner_id == me.id, "members": out}


class NewInvite(BaseModel):
    expires_hours: int = F(48, ge=1, le=fs.INVITE_MAX_H)
    max_uses: int = F(1, ge=1, le=10)


@router.post("/group/invites", dependencies=Dep)
def create_invite(body: NewInvite, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    limit("invite", me.id, 10, 3600)
    token = fs.new_invite_token()
    inv = fs.FsInvite(family_id=f.id, token_hash=fs.token_hash(token), created_by=me.id,
                      expires_at=now() + dt.timedelta(hours=body.expires_hours), max_uses=body.max_uses)
    s.add(inv)
    fs.audit(s, me.id, f.id, "invite created", detail=f"{body.expires_hours} h, {body.max_uses} use(s)")
    s.commit()
    s.refresh(inv)
    return {"id": inv.id, "token": token, "expires_at": iso(inv.expires_at), "max_uses": inv.max_uses,
            "note": "Share this code privately. It works only until it expires and only after the person signs in."}


@router.get("/group/invites", dependencies=Dep)
def list_invites(me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    rows = s.exec(select(fs.FsInvite).where(fs.FsInvite.family_id == f.id).order_by(fs.FsInvite.created_at.desc()).limit(50)).all()
    t = now()
    return [{"id": i.id, "created_by": i.created_by, "created_at": iso(i.created_at), "expires_at": iso(i.expires_at),
             "uses": i.uses, "max_uses": i.max_uses,
             "state": "revoked" if i.revoked_at else "expired" if fs.aware(i.expires_at) <= t else "used" if i.uses >= i.max_uses else "active"}
            for i in rows]


@router.delete("/group/invites/{invite_id}", dependencies=Dep)
def revoke_invite(invite_id: int, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    inv = s.get(fs.FsInvite, invite_id)
    if not inv or inv.family_id != f.id:
        raise HTTPException(404, "Invitation not found")
    if inv.created_by != me.id and f.owner_id != me.id:
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "Only the person who made it or the owner can cancel it.")
    inv.revoked_at = now()
    s.add(inv)
    fs.audit(s, me.id, f.id, "invite revoked", str(invite_id))
    s.commit()
    return {"ok": True}


class InviteToken(BaseModel):
    token: str = F(min_length=10, max_length=80)


def _valid_invite(s: Session, token: str) -> fs.FsInvite:
    inv = s.exec(select(fs.FsInvite).where(fs.FsInvite.token_hash == fs.token_hash(token.strip()))).first()
    if not inv or inv.revoked_at or fs.aware(inv.expires_at) <= now() or inv.uses >= inv.max_uses or not s.get(Family, inv.family_id):
        fs.count("invite_invalid", failure=True)
        raise HTTPException(404, "This invitation is not valid (wrong, expired, used or cancelled).")
    return inv


@router.post("/invites/preview", dependencies=Dep)
def preview_invite(body: InviteToken, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Shows which circle the code is for, so the person can decide - joining needs a separate accept."""
    limit("invite_try", me.id, 20, 600)
    inv = _valid_invite(s, body.token)
    f = s.get(Family, inv.family_id)
    by = s.get(User, inv.created_by)
    return {"family_name": f.name, "invited_by": by.name if by else None, "members": len(fs.members(s, f.id)),
            "expires_at": iso(inv.expires_at), "already_member": me.family_id == f.id}


@router.post("/invites/accept", dependencies=Dep)
async def accept_invite(body: InviteToken, me: User = Depends(current_user), s: Session = Depends(get_session)):
    limit("invite_try", me.id, 20, 600)
    inv = _valid_invite(s, body.token)
    if me.family_id == inv.family_id:
        return group(me, s)
    if me.family_id and s.get(Family, me.family_id):
        raise HTTPException(409, "You are in another family circle. Leave it first.")
    inv.uses += 1
    me.family_id = inv.family_id
    s.add(inv)
    s.add(me)
    fs.audit(s, me.id, inv.family_id, "joined with invite", str(inv.id))
    s.commit()
    await tell_family(s, inv.family_id, {"type": "family_updated"}, exclude=me.id)
    return group(me, s)


@router.post("/group/members/{user_id}/remove", dependencies=Dep)
async def remove_member(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    owner_only(me, f)
    if user_id == me.id:
        raise HTTPException(400, "Use Leave to leave your own circle.")
    u = same_family(s, me, user_id)
    fs.revoke(s, u, "removed from family circle", actor=me.id)
    fs.unwatch(f.id, u.id)
    u.family_id = None
    s.add(u)
    fs.audit(s, me.id, f.id, "member removed", u.id)
    s.commit()
    await hub.send(u.id, {"type": "family_safety_removed", "family_name": f.name})
    await tell_family(s, f.id, {"type": "family_safety_member", "member": {"user_id": u.id, "status": "removed", "location": None}})
    return group(me, s)


@router.post("/group/leave", dependencies=Dep)
async def leave_group(me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    fs.revoke(s, me, "left family circle")
    fs.unwatch(f.id, me.id)
    me.family_id = None
    s.add(me)
    if f.owner_id == me.id:   # hand the circle to the longest-standing member, if any
        rest = [m for m in fs.members(s, f.id) if m.id != me.id]
        if rest:
            f.owner_id = sorted(rest, key=lambda m: m.created_at)[0].id
            s.add(f)
    fs.audit(s, me.id, f.id, "left group")
    s.commit()
    await tell_family(s, f.id, {"type": "family_safety_member", "member": {"user_id": me.id, "status": "left", "location": None}})
    return {"ok": True}


# ------------------------------------------------------------------ consent and sharing
@router.get("/consent", dependencies=Dep)
def consent_info(me: User = Depends(current_user), s: Session = Depends(get_session)):
    rows = s.exec(select(fs.FsConsent).where(fs.FsConsent.user_id == me.id).order_by(fs.FsConsent.granted_at.desc()).limit(20)).all()
    return {"version": fs.CONSENT_VERSION, "text": fs.CONSENT_TEXT,
            "history": [{"version": c.version, "family_id": c.family_id, "location": c.scope_location, "battery": c.scope_battery,
                         "alerts": c.scope_alerts, "granted_at": iso(c.granted_at), "revoked_at": iso(c.revoked_at),
                         "revoked_reason": c.revoked_reason} for c in rows]}


class Grant(BaseModel):
    version: str
    accept: bool
    share_location: bool = True
    share_battery: bool = False
    zone_alerts: bool = True
    battery_threshold: int = F(15, ge=5, le=50)


@router.post("/consent", dependencies=Dep)
async def grant_consent(body: Grant, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Only the member themself can turn their own sharing on (no one can do it for them)."""
    f = family_of(me, s)
    if not body.accept or not body.share_location:
        raise HTTPException(400, "Sharing needs your explicit yes to sharing your location.")
    if body.version != fs.CONSENT_VERSION:
        raise HTTPException(409, "The explanation changed - please read it again.")
    limit("consent", me.id, 20, 3600)
    for old in s.exec(select(fs.FsConsent).where(fs.FsConsent.user_id == me.id, fs.FsConsent.revoked_at == None)).all():   # noqa: E711
        old.revoked_at, old.revoked_reason = now(), "replaced by new consent"
        s.add(old)
    c = fs.FsConsent(user_id=me.id, family_id=f.id, version=fs.CONSENT_VERSION, scope_location=True,
                     scope_battery=body.share_battery, scope_alerts=body.zone_alerts)
    s.add(c)
    s.commit()
    s.refresh(c)
    sh = s.get(fs.FsSharing, me.id) or fs.FsSharing(user_id=me.id, consent_id=c.id)
    sh.consent_id, sh.paused, sh.paused_until, sh.battery_threshold, sh.updated_at = c.id, False, None, body.battery_threshold, now()
    s.add(sh)
    fs.audit(s, me.id, f.id, "consent granted", detail=f"v{c.version} battery={c.scope_battery} alerts={c.scope_alerts}")
    s.commit()
    await fs.broadcast_member(s, me)
    return status(me, s)


class Pause(BaseModel):
    minutes: int | None = F(None, ge=5, le=7 * 24 * 60)   # None = until resumed


@router.post("/sharing/pause", dependencies=Dep)
async def pause(body: Pause, me: User = Depends(current_user), s: Session = Depends(get_session)):
    c, sh, state = fs.sharing_state(s, me)
    if state == "off" or not sh:
        raise HTTPException(409, "Sharing is not on.")
    sh.paused, sh.paused_until, sh.updated_at = True, (now() + dt.timedelta(minutes=body.minutes)) if body.minutes else None, now()
    s.add(sh)
    fs.audit(s, me.id, me.family_id, "sharing paused", detail=f"{body.minutes or 'until resumed'} min")
    s.commit()
    await fs.broadcast_member(s, me)
    return status(me, s)


@router.post("/sharing/resume", dependencies=Dep)
async def resume(me: User = Depends(current_user), s: Session = Depends(get_session)):
    c, sh, state = fs.sharing_state(s, me)
    if state == "off" or not sh:
        raise HTTPException(409, "Sharing is not on - turn it on first.")
    sh.paused, sh.paused_until, sh.updated_at = False, None, now()
    s.add(sh)
    fs.audit(s, me.id, me.family_id, "sharing resumed")
    s.commit()
    await fs.broadcast_member(s, me)
    return status(me, s)


class Settings(BaseModel):
    battery_threshold: int = F(ge=5, le=50)


@router.post("/sharing/settings", dependencies=Dep)
def sharing_settings(body: Settings, me: User = Depends(current_user), s: Session = Depends(get_session)):
    _, sh, state = fs.sharing_state(s, me)
    if not sh:
        raise HTTPException(409, "Sharing is not on.")
    sh.battery_threshold = body.battery_threshold
    s.add(sh)
    s.commit()
    return status(me, s)


@router.post("/consent/revoke", dependencies=Dep)
async def revoke_consent(me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Stop: ends consent, deletes the latest location at once, other members lose access immediately."""
    was = fs.revoke(s, me, "stopped by member")
    if me.family_id:
        await fs.broadcast_member(s, me)
    return {"ok": True, "was_sharing": was}


# ------------------------------------------------------------------ location updates
class Fix(BaseModel):
    lat: float
    lon: float
    accuracy: float = F(ge=0, le=fs.MAX_ACCURACY_M)
    recorded_at: int                              # phone time of the fix, epoch milliseconds (UTC)
    battery: int | None = F(None, ge=0, le=100)   # sent only when battery sharing is on; None = unknown
    charging: bool | None = None


@router.post("/location", dependencies=Dep)
async def post_location(body: Fix, me: User = Depends(current_user), s: Session = Depends(get_session)):
    c, sh, state = fs.sharing_state(s, me)
    if state != "sharing" or not c:
        fs.count("location_rejected_not_sharing", failure=True)
        raise HTTPException(409, {"code": "sharing_" + state, "message": "Location sharing is not on - nothing was stored."})
    if not fs.valid_point(body.lat, body.lon):
        fs.count("location_rejected_invalid", failure=True)
        raise HTTPException(422, "Invalid coordinates.")
    t = now()
    try:
        rec = dt.datetime.fromtimestamp(body.recorded_at / 1000, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        fs.count("location_rejected_invalid", failure=True)
        raise HTTPException(422, "Invalid time.")
    if rec > t + dt.timedelta(seconds=fs.MAX_FUTURE_S):
        fs.count("location_rejected_future", failure=True)
        raise HTTPException(422, "The phone's clock is ahead - this fix was not stored.")
    if (t - rec).total_seconds() > fs.MAX_FIX_AGE_S:
        fs.count("location_rejected_stale", failure=True)
        return {"accepted": False, "reason": "stale", "events": []}
    last = s.get(fs.FsLocation, me.id)
    if last and last.family_id == me.family_id and fs.aware(last.recorded_at) >= rec:
        fs.count("location_duplicate")
        return {"accepted": False, "reason": "duplicate", "events": []}   # replay / out-of-order fix
    if not fs.limiter.allow("location", me.id, 1, fs.MIN_INTERVAL_S):
        fs.count("location_rate_limited", failure=True)
        return {"accepted": False, "reason": "too_soon", "events": []}
    loc = last or fs.FsLocation(user_id=me.id, family_id=me.family_id, lat=body.lat, lon=body.lon, accuracy=body.accuracy, recorded_at=rec)
    loc.family_id, loc.lat, loc.lon, loc.accuracy, loc.recorded_at, loc.received_at = me.family_id, body.lat, body.lon, body.accuracy, rec, t
    battery_shared = c.scope_battery and body.battery is not None
    loc.battery = body.battery if battery_shared else None
    loc.charging = body.charging if battery_shared else None
    s.add(loc)
    events = fs.location_events(s, me, loc, c)
    bev = fs.battery_event(s, me, body.battery, body.charging, sh.battery_threshold) if c.scope_battery else None
    s.commit()
    fs.count("location_accepted")
    sent = []
    for ev, recipients, vars_ in events:
        s.add(ev)
        s.commit()
        sent.append(ev.kind)
        await fs.notify(s, ev, recipients, vars_)
    if bev:
        s.add(bev)
        s.commit()
        sent.append("battery_low")
        others = [m.id for m in fs.members(s, me.family_id) if m.id != me.id]
        await fs.notify(s, bev, others, {"level": body.battery})
    await fs.broadcast_member(s, me)
    return {"accepted": True, "events": sent}


# ------------------------------------------------------------------ live family map
@router.get("/map", dependencies=Dep)
def family_map(me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    return {"family": f.name, "members": [fs.map_entry(s, me, m) for m in fs.members(s, f.id)],
            "live_s": fs.LIVE_S, "stale_s": fs.STALE_S, "time": iso(now())}


@router.post("/map/watch", dependencies=Dep)
def map_watch(me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Realtime: while the map is open the server pushes each member's update over the app's live link."""
    f = family_of(me, s)
    return {"seconds": fs.watch(f.id, me.id)}


@router.post("/map/unwatch", dependencies=Dep)
def map_unwatch(me: User = Depends(current_user)):
    if me.family_id:
        fs.unwatch(me.family_id, me.id)
    return {"ok": True}


# ------------------------------------------------------------------ SOS and other safety events
class Sos(BaseModel):
    client_key: str = F(min_length=8, max_length=64)   # the phone makes one per SOS press (idempotency)
    message: str = F("", max_length=200)
    lat: float | None = None
    lon: float | None = None
    accuracy: float | None = F(None, ge=0, le=fs.MAX_ACCURACY_M)
    recorded_at: int | None = None


def event_view(s: Session, me: User, ev: fs.FsEvent) -> dict:
    by = s.get(User, ev.user_id)
    out = {"id": ev.id, "kind": ev.kind, "status": ev.status, "from": {"user_id": ev.user_id, "name": by.name if by else "?"},
           "detail": ev.detail, "created_at": iso(ev.created_at), "updated_at": iso(ev.updated_at), "closed_by": ev.closed_by,
           "location": ({"lat": ev.lat, "lon": ev.lon, "accuracy": ev.accuracy, "at": iso(ev.location_at)} if ev.lat is not None else None)}
    ds = s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == ev.id)).all()
    if ev.user_id == me.id:   # the sender sees who it reached
        names = {u.id: u.name for u in s.exec(select(User).where(User.id.in_([d.recipient_id for d in ds]))).all()} if ds else {}
        out["deliveries"] = [{"to": names.get(d.recipient_id, "?"), "status": d.status, "attempts": d.attempts,
                              "delivered_at": iso(d.delivered_at)} for d in ds]
    else:
        mine = next((d for d in ds if d.recipient_id == me.id), None)
        out["my_delivery"] = mine.status if mine else None
    return out


def visible_event(s: Session, me: User, event_id: str) -> fs.FsEvent:
    ev = s.get(fs.FsEvent, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    is_recipient = s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == ev.id, fs.FsDelivery.recipient_id == me.id)).first()
    if ev.user_id != me.id and not (is_recipient and me.family_id == ev.family_id):
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "This safety event is not for you.")
    return ev


@router.post("/sos", dependencies=Dep)
async def sos(body: Sos, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    dup = s.exec(select(fs.FsEvent).where(fs.FsEvent.user_id == me.id, fs.FsEvent.client_key == body.client_key)).first()
    if dup:   # the same press sent again (retry after a timeout, double tap): no second alert
        fs.count("sos_duplicate")
        return event_view(s, me, dup) | {"duplicate": True}
    recent = s.exec(select(fs.FsEvent).where(fs.FsEvent.user_id == me.id, fs.FsEvent.kind == "sos", fs.FsEvent.status == "active",
                                             fs.FsEvent.created_at > now() - dt.timedelta(seconds=fs.SOS_DUPLICATE_S))).first()
    if recent:
        fs.count("sos_duplicate")
        return event_view(s, me, recent) | {"duplicate": True}
    limit("sos", me.id, 5, 600)
    ev = fs.FsEvent(family_id=f.id, user_id=me.id, kind="sos", client_key=body.client_key, detail=body.message.strip())
    if body.lat is not None and body.lon is not None and fs.valid_point(body.lat, body.lon):
        # the location sent with the SOS press itself (pressing SOS is the member's own choice to share it)
        ev.lat, ev.lon, ev.accuracy = body.lat, body.lon, body.accuracy
        ev.location_at = dt.datetime.fromtimestamp(body.recorded_at / 1000, dt.timezone.utc) if body.recorded_at else now()
    elif fs.sharing_state(s, me)[2] == "sharing" and (loc := s.get(fs.FsLocation, me.id)) and fs.status_of(loc)[0] != "offline":
        ev.lat, ev.lon, ev.accuracy, ev.location_at = loc.lat, loc.lon, loc.accuracy, loc.recorded_at
    s.add(ev)
    fs.audit(s, me.id, f.id, "sos sent", ev.id)
    s.commit()
    s.refresh(ev)
    fs.count("sos_created")
    recipients = [m.id for m in fs.members(s, f.id) if m.id != me.id]
    await fs.notify(s, ev, recipients, {})
    return event_view(s, me, ev) | {"duplicate": False, "recipients": len(recipients),
                                    "note": "Your family circle has been alerted. VoiceGuard does not contact police or 112."}


@router.get("/events", dependencies=Dep)
def events(active: bool = False, me: User = Depends(current_user), s: Session = Depends(get_session)):
    mine = s.exec(select(fs.FsEvent).where(fs.FsEvent.user_id == me.id).order_by(fs.FsEvent.created_at.desc()).limit(50)).all()
    got = [d.event_id for d in s.exec(select(fs.FsDelivery).where(fs.FsDelivery.recipient_id == me.id)
                                      .order_by(fs.FsDelivery.id.desc()).limit(100)).all()]
    theirs = s.exec(select(fs.FsEvent).where(fs.FsEvent.id.in_(got), fs.FsEvent.family_id == me.family_id)).all() if got else []
    rows = sorted({e.id: e for e in [*mine, *theirs]}.values(), key=lambda e: e.created_at, reverse=True)
    if active:
        rows = [e for e in rows if e.status == "active"]
    return [event_view(s, me, e) for e in rows[:60]]


@router.get("/events/{event_id}", dependencies=Dep)
def event_detail(event_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    return event_view(s, me, visible_event(s, me, event_id))


@router.post("/events/{event_id}/ack", dependencies=Dep)
def event_ack(event_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    ev = visible_event(s, me, event_id)
    if ev.user_id == me.id:
        return {"ok": True}
    fs.acknowledge(s, ev, me)
    return {"ok": True}


class Close(BaseModel):
    note: str = F("", max_length=200)


async def _close(s: Session, me: User, ev: fs.FsEvent, status_: str, note: str):
    if ev.kind != "sos":
        raise HTTPException(400, "Only SOS alerts can be cancelled or resolved.")
    if ev.status != "active":
        return
    ev.status, ev.closed_by, ev.updated_at = status_, me.name, now()
    if note:
        ev.detail = (ev.detail + f" [{status_} by {me.name}: {note.strip()}]").strip()[:500]
    s.add(ev)
    for d in s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == ev.id, fs.FsDelivery.status.in_(["pending", "sent"]))).all():
        d.next_attempt_at = None   # stop retrying
        s.add(d)
    fs.audit(s, me.id, ev.family_id, "sos " + status_, ev.id)
    s.commit()
    rec = [d.recipient_id for d in s.exec(select(fs.FsDelivery).where(fs.FsDelivery.event_id == ev.id)).all()] + [ev.user_id]
    msg = {"type": "family_safety_event", "event": {"id": ev.id, "kind": ev.kind, "status": ev.status, "closed_by": me.name}}
    for uid in dict.fromkeys(rec):
        if uid != me.id:
            await hub.send(uid, msg)


@router.post("/events/{event_id}/cancel", dependencies=Dep)
async def event_cancel(event_id: str, body: Close, me: User = Depends(current_user), s: Session = Depends(get_session)):
    ev = visible_event(s, me, event_id)
    if ev.user_id != me.id:
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "Only the person who sent the SOS can cancel it (others can mark it resolved).")
    await _close(s, me, ev, "cancelled", body.note)
    return event_view(s, me, ev)


@router.post("/events/{event_id}/resolve", dependencies=Dep)
async def event_resolve(event_id: str, body: Close, me: User = Depends(current_user), s: Session = Depends(get_session)):
    ev = visible_event(s, me, event_id)
    await _close(s, me, ev, "resolved", body.note)
    return event_view(s, me, ev)


# ------------------------------------------------------------------ arrival notifications
class NewArrival(BaseModel):
    name: str = F(min_length=1, max_length=40)
    lat: float
    lon: float
    radius_m: int = F(150, ge=50, le=2000)
    recipients: list[str] = F(min_length=1, max_length=20)
    expires_hours: int = F(12, ge=1, le=48)


@router.post("/arrivals", dependencies=Dep)
def create_arrival(body: NewArrival, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    if fs.sharing_state(s, me)[2] == "off":
        raise HTTPException(409, "Turn on location sharing first - arrival alerts use it.")
    if not fs.valid_point(body.lat, body.lon):
        raise HTTPException(422, "Invalid destination.")
    for r in body.recipients:
        same_family(s, me, r)
    limit("arrival", me.id, 20, 3600)
    a = fs.FsArrival(family_id=f.id, user_id=me.id, name=body.name.strip(), lat=body.lat, lon=body.lon, radius_m=body.radius_m,
                     recipients=json.dumps([r for r in dict.fromkeys(body.recipients) if r != me.id]),
                     expires_at=now() + dt.timedelta(hours=body.expires_hours))
    s.add(a)
    fs.audit(s, me.id, f.id, "arrival set", detail=f"{len(fs.ids(a.recipients))} recipient(s)")
    s.commit()
    s.refresh(a)
    return arrival_view(a)


def arrival_view(a: fs.FsArrival) -> dict:
    return {"id": a.id, "name": a.name, "lat": a.lat, "lon": a.lon, "radius_m": a.radius_m, "recipients": fs.ids(a.recipients),
            "status": a.status, "expires_at": iso(a.expires_at), "arrived_at": iso(a.arrived_at)}


@router.get("/arrivals", dependencies=Dep)
def list_arrivals(me: User = Depends(current_user), s: Session = Depends(get_session)):
    return [arrival_view(a) for a in s.exec(select(fs.FsArrival).where(fs.FsArrival.user_id == me.id)
                                            .order_by(fs.FsArrival.created_at.desc()).limit(30)).all()]


@router.delete("/arrivals/{arrival_id}", dependencies=Dep)
def cancel_arrival(arrival_id: int, me: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.get(fs.FsArrival, arrival_id)
    if not a or a.user_id != me.id:
        raise HTTPException(404, "Arrival alert not found")
    a.status = "cancelled" if a.status == "active" else a.status
    s.add(a)
    s.commit()
    return arrival_view(a)


# ------------------------------------------------------------------ safe zones
class NewZone(BaseModel):
    name: str = F(min_length=1, max_length=40)
    lat: float
    lon: float
    radius_m: int = F(200, ge=50, le=5000)
    notify_entry: bool = True
    notify_exit: bool = True
    subjects: list[str] = F(min_length=1, max_length=20)     # whose entry / exit is reported
    recipients: list[str] = F(min_length=1, max_length=20)


class EditZone(BaseModel):
    name: str | None = F(None, min_length=1, max_length=40)
    radius_m: int | None = F(None, ge=50, le=5000)
    enabled: bool | None = None
    notify_entry: bool | None = None
    notify_exit: bool | None = None


def zone_view(s: Session, me: User, z: fs.FsZone) -> dict:
    return {"id": z.id, "name": z.name, "lat": z.lat, "lon": z.lon, "radius_m": z.radius_m, "enabled": z.enabled,
            "notify_entry": z.notify_entry, "notify_exit": z.notify_exit, "subjects": fs.ids(z.subjects),
            "recipients": fs.ids(z.recipients), "owner_id": z.owner_id, "mine": z.owner_id == me.id,
            "watches_me": me.id in fs.ids(z.subjects)}


@router.post("/zones", dependencies=Dep)
def create_zone(body: NewZone, me: User = Depends(current_user), s: Session = Depends(get_session)):
    f = family_of(me, s)
    if not fs.valid_point(body.lat, body.lon):
        raise HTTPException(422, "Invalid zone centre.")
    for uid in [*body.subjects, *body.recipients]:
        same_family(s, me, uid)
    limit("zone", me.id, 30, 3600)
    z = fs.FsZone(family_id=f.id, owner_id=me.id, name=body.name.strip(), lat=body.lat, lon=body.lon, radius_m=body.radius_m,
                  notify_entry=body.notify_entry, notify_exit=body.notify_exit,
                  subjects=json.dumps(list(dict.fromkeys(body.subjects))), recipients=json.dumps(list(dict.fromkeys(body.recipients))))
    s.add(z)
    fs.audit(s, me.id, f.id, "zone created", detail=f"{len(body.subjects)} member(s) watched")
    s.commit()
    s.refresh(z)
    return zone_view(s, me, z)


@router.get("/zones", dependencies=Dep)
def list_zones(me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Zones I made, zones that report about me (so nobody is watched without seeing it) and zones I get alerts from."""
    f = family_of(me, s)
    rows = s.exec(select(fs.FsZone).where(fs.FsZone.family_id == f.id)).all()
    return [zone_view(s, me, z) for z in rows if z.owner_id == me.id or me.id in fs.ids(z.subjects) or me.id in fs.ids(z.recipients)]


def own_zone(s: Session, me: User, zone_id: int) -> fs.FsZone:
    z = s.get(fs.FsZone, zone_id)
    if not z or z.family_id != me.family_id:
        raise HTTPException(404, "Zone not found")
    if z.owner_id != me.id:
        fs.count("authz_denied", failure=True)
        raise HTTPException(403, "Only the person who made this zone can change it.")
    return z


@router.patch("/zones/{zone_id}", dependencies=Dep)
@router.post("/zones/{zone_id}/edit", dependencies=Dep)   # same, for clients without PATCH
def edit_zone(zone_id: int, body: EditZone, me: User = Depends(current_user), s: Session = Depends(get_session)):
    z = own_zone(s, me, zone_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(z, k, v.strip() if isinstance(v, str) else v)
    z.updated_at = now()
    s.add(z)
    fs.audit(s, me.id, z.family_id, "zone changed", str(z.id))
    s.commit()
    return zone_view(s, me, z)


@router.delete("/zones/{zone_id}", dependencies=Dep)
def delete_zone(zone_id: int, me: User = Depends(current_user), s: Session = Depends(get_session)):
    z = own_zone(s, me, zone_id)
    for st in s.exec(select(fs.FsZoneState).where(fs.FsZoneState.zone_id == z.id)).all():
        s.delete(st)
    s.delete(z)
    fs.audit(s, me.id, me.family_id, "zone deleted", str(zone_id))
    s.commit()
    return {"ok": True}


@router.post("/zones/{zone_id}/leave", dependencies=Dep)
def leave_zone(zone_id: int, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """A watched member can take themself out of someone else's zone at any time."""
    z = s.get(fs.FsZone, zone_id)
    if not z or z.family_id != me.family_id or me.id not in fs.ids(z.subjects):
        raise HTTPException(404, "Zone not found")
    z.subjects = json.dumps([u for u in fs.ids(z.subjects) if u != me.id])
    s.add(z)
    for st in s.exec(select(fs.FsZoneState).where(fs.FsZoneState.zone_id == z.id, fs.FsZoneState.user_id == me.id)).all():
        s.delete(st)
    fs.audit(s, me.id, z.family_id, "left zone", str(z.id))
    s.commit()
    return {"ok": True}
