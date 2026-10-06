"""Voice Test / Voice CAPTCHA (feature 12).

Ask the caller to do something unusual - laugh, whisper, sing, answer in dialect. Voice-cloning
systems are trained on normal read speech, so these requests push them out of their comfort zone and
their artefacts grow (D-CAPTCHA research: detection 71% -> 91-100%).
"""
import datetime as dt
import difflib
import random
import uuid

CHALLENGES = [
    {"kind": "laugh", "en": "Ask the caller to laugh out loud for 3 seconds.",
     "hi": "कॉलर से 3 सेकंड ज़ोर से हँसने को कहें।"},
    {"kind": "whisper", "en": "Ask the caller to WHISPER: 'Papa, main theek hoon'.",
     "hi": "कॉलर से फुसफुसा कर बोलने को कहें: 'पापा, मैं ठीक हूँ'।", "expect": "papa main theek hoon"},
    {"kind": "sing", "en": "Ask the caller to sing one line of 'Happy Birthday'.",
     "hi": "कॉलर से 'हैप्पी बर्थडे' की एक लाइन गाने को कहें।"},
    {"kind": "phrase", "en": "Ask the caller to say: 'Aaj {weekday} hai aur main ghar par hoon'.",
     "hi": "कॉलर से बोलने को कहें: 'आज {weekday_hi} है और मैं घर पर हूँ'।", "expect": "aaj {weekday} hai aur main ghar par hoon"},
    {"kind": "family_question", "en": "Ask something only family knows: 'What did we eat last Sunday?'",
     "hi": "ऐसा सवाल पूछें जो सिर्फ़ परिवार जानता हो: 'पिछले रविवार हमने क्या खाया था?'"},
    {"kind": "dialect", "en": "Ask the caller to answer in your home dialect (e.g. Marathi/Bhojpuri).",
     "hi": "कॉलर से अपनी घरेलू बोली (जैसे मराठी/भोजपुरी) में जवाब देने को कहें।"},
]
WEEKDAYS = ["somvaar", "mangalvaar", "budhvaar", "guruvaar", "shukravaar", "shanivaar", "ravivaar"]
WEEKDAYS_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

_active: dict[str, dict] = {}


def new_challenge(kind: str | None = None) -> dict:
    options = [c for c in CHALLENGES if kind in (None, c["kind"])] or CHALLENGES
    c = dict(random.choice(options))
    wd = dt.date.today().weekday()
    for k in ("en", "hi", "expect"):
        if k in c:
            c[k] = c[k].format(weekday=WEEKDAYS[wd], weekday_hi=WEEKDAYS_HI[wd])
    c["id"] = uuid.uuid4().hex[:10]
    _active[c["id"]] = c
    return c


def get(challenge_id: str) -> dict | None:
    return _active.get(challenge_id)


def text_match(expected: str, heard: str) -> float:
    a = expected.lower().replace("ं", "").strip()
    b = heard.lower().strip()
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)
