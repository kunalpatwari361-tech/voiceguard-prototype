"""Are You Really Calling? (feature 8) + Call-Back Alert to the impersonated person (18)."""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..ai.number_info import normalize
from ..auth import current_user, require_family, require_self
from ..db import User, get_session
from ..hub import hub
from .common import family_ids, public_user, push_alert

router = APIRouter(prefix="/api/verify", tags=["verify"])


class Ask(BaseModel):
    asker_id: str
    claimed_user_id: str
    number: str | None = None
    wait_s: int = 30


class Answer(BaseModel):
    request_id: str
    answer: str


@router.post("/answer")
def give_answer(body: Answer, me: User = Depends(current_user)):
    """The asked person's reply when it comes from a notification button rather than the live link."""
    if body.answer not in ("yes", "no"):
        raise HTTPException(400, "answer must be yes or no")
    if not hub.resolve(body.request_id, body.answer, by=me.id):
        raise HTTPException(404, "This question has expired or was not sent to you.")
    return {"ok": True}


@router.post("/ask")
async def ask(body: Ask, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, body.asker_id)
    asker = me
    claimed = s.get(User, body.claimed_user_id)
    require_family(me, claimed)
    number = normalize(body.number) if body.number else None
    online = hub.online(claimed.id)
    reachable = hub.reachable(claimed.id)   # online, or the app is closed but a push can wake it
    auto = None
    if online and claimed.call_state in ("idle", "ringing", "offhook"):
        in_call = claimed.call_state == "offhook"
        auto = {"in_call": in_call,
                "en": f"{claimed.name}'s phone is {'on a call' if in_call else 'NOT on any call'} right now.",
                "hi": f"{claimed.name} का फ़ोन अभी {'कॉल पर है' if in_call else 'किसी कॉल पर नहीं है'}।"}
    if number and number == claimed.phone:
        same_number = True
    else:
        same_number = False

    rid = uuid.uuid4().hex[:10]
    answer, source = "no_answer", "offline"
    if reachable:
        fut = hub.expect(rid, owner=claimed.id)
        await hub.deliver(claimed.id, {"type": "verify_request", "request_id": rid,
                                       "from": {"id": asker.id, "name": asker.name}, "number": number,
                                       "auto_in_call": auto["in_call"] if auto else None})
        if auto and not auto["in_call"]:
            # The phone itself proves it: no call in progress, so this caller cannot be them.
            answer, source = "no", "auto"
            asyncio.get_running_loop().call_later(120, lambda: hub.forget(rid))
        else:
            try:
                answer = await asyncio.wait_for(fut, timeout=body.wait_s)
                source = "person" if online else "person_push"
            except asyncio.TimeoutError:
                hub.forget(rid)
                answer, source = "no_answer", "timeout"

    if answer == "no":
        await push_alert(s, family_id=claimed.family_id, from_user_id=asker.id, to_user_id=claimed.id,
                         kind="impersonation",
                         title=f"Someone is pretending to be you to {asker.name}",
                         body=f"A caller{(' from ' + number) if number else ''} is using your name. "
                              f"Please call {asker.name} now on their saved number.",
                         payload={"asker_id": asker.id, "asker_name": asker.name, "asker_phone": asker.phone,
                                  "number": number})
        # Everyone else in the family hears about the fake call too.
        for uid in family_ids(s, asker.family_id):
            if uid not in (asker.id, claimed.id):
                await push_alert(s, family_id=asker.family_id, from_user_id=asker.id, to_user_id=uid, kind="scam_call",
                                 title=f"Fake call: someone is pretending to be {claimed.name} to {asker.name}",
                                 body=f"{claimed.name}'s own phone says it is not them. Call {asker.name} now.",
                                 payload={"number": number, "victim_phone": asker.phone, "from_name": asker.name})
    msg = {
        "yes": (f"{claimed.name} confirmed: it is really them.", f"{claimed.name} ने पुष्टि की: यह सच में वही हैं।"),
        "no": (f"{claimed.name} is NOT calling you. This call is fake.", f"{claimed.name} आपको कॉल नहीं कर रहे। यह कॉल नकली है।"),
        "no_answer": (f"{claimed.name} did not answer. Treat this call as suspicious and call back on the saved number.",
                      f"{claimed.name} ने जवाब नहीं दिया। सावधान रहें और सेव नंबर पर वापस कॉल करें।"),
    }[answer]
    return {"request_id": rid, "answer": answer, "source": source, "auto": auto, "same_number": same_number,
            "claimed": public_user(claimed), "message": msg[0], "message_hi": msg[1]}
