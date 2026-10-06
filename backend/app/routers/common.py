import datetime as dt
import json

from fastapi import HTTPException
from sqlmodel import Session, select

from ..db import Alert, User, dumps, now
from ..hub import hub


def iso(t: dt.datetime | None) -> str | None:
    if not t:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.isoformat(timespec="seconds") + "Z"


def age_min(t: dt.datetime | None) -> int | None:
    return int((now() - t).total_seconds() // 60) if t else None


def user_or_404(s: Session, user_id: str) -> User:
    u = s.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    return u


def public_user(u: User) -> dict:
    return {
        "id": u.id, "name": u.name, "phone": u.phone, "role": u.role, "family_id": u.family_id,
        "online": hub.online(u.id), "last_seen": iso(u.last_seen),
        "call_state": u.call_state, "call_state_age_min": age_min(u.call_state_at),
        "location": ({"lat": u.lat, "lon": u.lon, "accuracy": u.loc_accuracy, "place": u.place,
                      "at": iso(u.loc_at), "age_min": age_min(u.loc_at)} if u.lat is not None else None),
        "voiceprint_enrolled": u.voiceprint is not None, "voiceprint_samples": u.voiceprint_samples,
    }


def family_ids(s: Session, family_id: str | None) -> list[str]:
    if not family_id:
        return []
    return [u.id for u in s.exec(select(User).where(User.family_id == family_id)).all()]


def alert_dict(a: Alert) -> dict:
    return {"id": a.id, "family_id": a.family_id, "from_user_id": a.from_user_id, "to_user_id": a.to_user_id,
            "kind": a.kind, "title": a.title, "body": a.body, "payload": json.loads(a.payload_json or "{}"),
            "created_at": iso(a.created_at)}


async def push_alert(s: Session, *, family_id: str | None, from_user_id: str | None, kind: str, title: str,
                     body: str = "", payload: dict | None = None, to_user_id: str | None = None) -> dict:
    a = Alert(family_id=family_id, from_user_id=from_user_id, to_user_id=to_user_id, kind=kind,
              title=title, body=body, payload_json=dumps(payload or {}))
    s.add(a)
    s.commit()
    s.refresh(a)
    msg = {"type": "alert", "alert": alert_dict(a)}
    targets = [to_user_id] if to_user_id else [u for u in family_ids(s, family_id) if u != from_user_id]
    for uid in targets:
        await hub.send(uid, msg)
    # Tell the sender who actually got it ("Sent to 2 family members, 1 online now").
    return alert_dict(a) | {"sent_to": len(targets), "online": sum(1 for u in targets if hub.online(u))}
