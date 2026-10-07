"""WhatsApp family alerts sent by the server, so the phone never has to open WhatsApp.

WhatsApp does not let one app send from the user's own WhatsApp account. The server therefore sends from a
WhatsApp *business* number through Twilio (the same Twilio account as the SMS codes):
  testing:    Twilio WhatsApp Sandbox, From = whatsapp:+14155238886. Each family phone first sends
              "join <your sandbox code>" to that number once (Twilio console → Messaging → Try it out →
              Send a WhatsApp message). Free-form alerts then work for 24 h after their last message to it;
              the sandbox membership lasts 3 days.
  production: your own WhatsApp sender + an approved template (TWILIO_WHATSAPP_CONTENT_SID, variables
              {{1}} = person, {{2}} = details).
"""
import json
import logging
import time

import httpx

from . import config

log = logging.getLogger("voiceguard.whatsapp")

ERRORS = {
    63015: "has not joined the VoiceGuard WhatsApp sandbox yet (they send \"join <code>\" to +1 415 523 8886 once)",
    63016: "has not messaged the WhatsApp number in the last 24 hours (sandbox rule) – ask them to send \"hi\" to it",
    63003: "is not a valid WhatsApp number",
    63007: "– the WhatsApp sender is not set up in Twilio (check TWILIO_WHATSAPP_FROM)",
    21608: "– Twilio trial accounts can only message verified numbers",
}


def enabled() -> bool:
    return bool(config.TWILIO_WHATSAPP_FROM and config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN)


def status() -> str:
    if not enabled():
        return "off (set TWILIO_WHATSAPP_FROM in backend/.env)"
    sandbox = config.TWILIO_WHATSAPP_FROM.endswith("14155238886")
    return "on (Twilio sandbox)" if sandbox else "on"


def _api(path: str) -> str:
    return f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/{path}"


def send(to: str, body: str, person: str = "", details: str = "") -> dict:
    """One WhatsApp message; waits a few seconds for Twilio's verdict so the app can say who got it."""
    auth = (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    data = {"From": config.TWILIO_WHATSAPP_FROM, "To": "whatsapp:" + to}
    if config.TWILIO_WHATSAPP_CONTENT_SID:
        data |= {"ContentSid": config.TWILIO_WHATSAPP_CONTENT_SID,
                 "ContentVariables": json.dumps({"1": person or "Your family member", "2": details or body[:900]})}
    else:
        data["Body"] = body[:1500]
    try:
        r = httpx.post(_api("Messages.json"), data=data, auth=auth, timeout=15)
    except httpx.HTTPError as e:
        return {"ok": False, "status": "error", "error": f"WhatsApp service unreachable ({e.__class__.__name__})"}
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        code = j.get("code")
        log.warning("WhatsApp to %s refused: %s %s", to, code, j.get("message"))
        return {"ok": False, "status": "failed", "code": code, "error": ERRORS.get(code, j.get("message", "refused"))}
    sid, st, code = j.get("sid"), j.get("status"), j.get("error_code")
    end = time.time() + 8          # most refusals (not joined, 24-h window) arrive a moment after "queued"
    while st in ("accepted", "queued", "sending") and time.time() < end:
        time.sleep(1.5)
        try:
            k = httpx.get(_api(f"Messages/{sid}.json"), auth=auth, timeout=10).json()
            st, code = k.get("status"), k.get("error_code")
        except (httpx.HTTPError, ValueError):
            break
    if st in ("failed", "undelivered"):
        return {"ok": False, "status": st, "code": code, "error": ERRORS.get(code, f"not delivered (Twilio error {code})")}
    return {"ok": True, "status": st}
