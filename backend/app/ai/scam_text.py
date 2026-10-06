"""Scam Words Alert (feature 14): find scam tactics in what the caller says.

Rule engine first (Hindi, Hinglish, English, Devanagari) so it works offline with no key.
If ANTHROPIC_API_KEY is set, Claude also reads the transcript for tactics the rules miss.
"""
import json
import logging
import re

from .. import config

log = logging.getLogger("voiceguard.scam_text")

# category -> (weight, phrases). Phrases are matched on lower-cased text with word boundaries.
RULES: dict[str, tuple[float, list[str]]] = {
    "money_request": (0.45, [
        "send money", "transfer", "upi", "gpay", "google pay", "phonepe", "phone pe", "paytm",
        "account number", "ifsc", "rupees", "rupaye", "rupay", "paise bhejo", "paisa bhejo",
        "paise bhej", "paise chahiye", "payment", "qr code", "scan karo", "lakh", "thousand",
        "पैसे", "पैसा", "रुपये", "रुपए", "रुपे", "रूपये", "भेज दो", "भेजो", "भेज दे", "ट्रांसफर", "पेमेंट", "हज़ार",
        "लाख", "यूपीआई", "यू पी आई", "जमा कर", "deposit",
    ]),
    "urgency": (0.25, [
        "urgent", "immediately", "right now", "jaldi", "abhi", "turant", "fauran", "within 10 minutes",
        "last chance", "hurry", "जल्दी", "तुरंत", "अभी", "फ़ौरन", "फौरन",
    ]),
    "emergency_story": (0.35, [
        "accident", "hospital", "operation", "injured", "arrested", "arrest", "police station",
        "jail", "kidnap", "in trouble", "musibat", "fas gaya", "phas gaya", "pakad liya",
        "एक्सीडेंट", "दुर्घटना", "अस्पताल", "हॉस्पिटल", "गिरफ्तार", "पुलिस", "जेल", "मुसीबत", "फँस", "फंस",
    ]),
    "secrecy": (0.35, [
        "don't tell", "do not tell", "dont tell", "keep it secret", "kisi ko mat batana",
        "kisi ko mat bolna", "mummy ko mat", "papa ko mat", "ghar pe mat batana",
        "किसी को मत", "मत बताना", "किसी को न बताएं",
    ]),
    "credentials": (0.5, [
        "otp", "one time password", "pin", "cvv", "password", "card number", "expiry",
        "ओटीपी", "पिन", "पासवर्ड",
    ]),
    "authority_threat": (0.4, [
        "cbi", "customs", "narcotics", "drugs", "parcel", "courier", "fedex", "digital arrest",
        "income tax", "enforcement directorate", "money laundering", "court", "warrant", "trai",
        "your number will be blocked", "sim will be blocked", "aadhaar", "aadhar",
        "कस्टम", "पार्सल", "डिजिटल अरेस्ट", "वारंट", "आधार", "सीबीआई",
    ]),
    "new_number": (0.3, [
        "new number", "naya number", "phone kho gaya", "phone toot gaya", "phone tut gaya",
        "this is my new number", "mera number", "dusre number", "friend's phone", "dost ka phone",
        "नया नंबर", "फ़ोन खो गया", "फोन खो गया", "दोस्त का फ़ोन", "दोस्त का फोन",
    ]),
    "prize_lottery": (0.35, [
        "lottery", "prize", "you have won", "kbc", "lucky draw", "inaam", "cashback", "refund",
        "लॉटरी", "इनाम", "जीत",
    ]),
    "remote_access": (0.45, [
        "anydesk", "teamviewer", "quick support", "screen share", "download this app", "apk",
        "install karo", "link pe click", "click the link",
    ]),
}

TIPS = {
    "money_request": ("Caller is asking for money.", "कॉलर पैसे माँग रहा है।"),
    "urgency": ("Caller is rushing you.", "कॉलर जल्दबाज़ी करवा रहा है।"),
    "emergency_story": ("Emergency story (accident / police / hospital).", "इमरजेंसी की कहानी (एक्सीडेंट / पुलिस / अस्पताल)।"),
    "secrecy": ("Asks you to keep it secret from family.", "परिवार से छुपाने को कह रहा है।"),
    "credentials": ("Asks for OTP / PIN / password.", "OTP / PIN / पासवर्ड माँग रहा है।"),
    "authority_threat": ("Threat using police, customs or CBI.", "पुलिस / कस्टम / CBI का डर दिखा रहा है।"),
    "new_number": ("Says this is a new number.", "कह रहा है कि यह नया नंबर है।"),
    "prize_lottery": ("Prize, lottery or refund bait.", "इनाम / लॉटरी / रिफंड का लालच।"),
    "remote_access": ("Wants you to install an app or share screen.", "ऐप डाउनलोड या स्क्रीन शेयर करवाना चाहता है।"),
}


def _norm(t: str) -> str:
    """Whisper spells Hindi loosely: drop the nukta (ज़ -> ज) and treat chandrabindu like anusvara."""
    return (t or "").lower().replace("़", "").replace("ँ", "ं")


def _compile(phrase: str) -> re.Pattern:
    phrase = _norm(phrase)
    if re.search(r"[a-z]", phrase):
        return re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])")
    return re.compile(re.escape(phrase))  # Devanagari: plain substring


_PATTERNS = {cat: [(p, _compile(p)) for p in phrases] for cat, (_, phrases) in RULES.items()}


def rule_scan(text: str) -> dict:
    t = _norm(text)
    hits = []
    cats = {}
    for cat, pats in _PATTERNS.items():
        found = [p for p, rx in pats if rx.search(t)]
        if found:
            cats[cat] = found
            hits.extend({"category": cat, "phrase": f} for f in found)
    # independent-evidence combination: 1 - prod(1 - w)
    keep = 1.0
    for cat in cats:
        keep *= 1 - RULES[cat][0]
    score = 1 - keep
    # the classic "family emergency" combo is a near-certain scam pattern
    if "money_request" in cats and ("emergency_story" in cats or "secrecy" in cats or "new_number" in cats):
        score = max(score, 0.85)
    return {
        "score": round(score, 3),
        "categories": list(cats),
        "hits": hits,
        "tips": [{"category": c, "en": TIPS[c][0], "hi": TIPS[c][1]} for c in cats],
    }


_SCHEMA = {
    "type": "object",
    "properties": {
        "scam_probability": {"type": "number"},
        "tactics": {"type": "array", "items": {"type": "string"}},
        "summary_en": {"type": "string"},
        "summary_hi": {"type": "string"},
    },
    "required": ["scam_probability", "tactics", "summary_en", "summary_hi"],
    "additionalProperties": False,
}

_PROMPT = """You protect elderly people in India from phone scams, especially voice-cloning scams where a
caller pretends to be a family member. Read this call transcript (may be Hindi, Hinglish or English)
and judge how likely it is a scam. Tactics to look for: money requests, urgency, emergency stories,
secrecy, OTP/PIN requests, police/customs/CBI threats ("digital arrest"), "new number" claims,
prize bait, remote-access apps. Give scam_probability between 0 and 1, the tactics you found
(short names), and a one-sentence warning in simple English and in simple Hindi (Devanagari).

Claimed caller: {claimed}
Transcript:
<transcript>
{text}
</transcript>"""


def claude_scan(text: str, claimed: str | None = None) -> dict | None:
    if not config.ANTHROPIC_API_KEY or not text.strip():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=25.0, max_retries=1)
        resp = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": _PROMPT.format(claimed=claimed or "unknown", text=text[:6000])}],
        )
        if resp.stop_reason == "refusal":
            return None
        raw = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(raw)
        data["scam_probability"] = max(0.0, min(1.0, float(data["scam_probability"])))
        data["model"] = resp.model
        return data
    except Exception as e:  # network down, no credit, etc. -> rules still work
        log.warning("Claude scan skipped: %s", e)
        return None


def analyze(text: str, claimed: str | None = None) -> dict:
    rules = rule_scan(text)
    llm = claude_scan(text, claimed)
    score = rules["score"]
    if llm:
        score = max(score, 0.6 * llm["scam_probability"] + 0.4 * score)
    return {"score": round(score, 3), "rules": rules, "claude": llm}
