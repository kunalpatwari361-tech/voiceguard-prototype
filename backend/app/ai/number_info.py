"""Number Info (feature 15): what can we tell from the number alone."""
import re

# Country codes frequently seen in Indian cyber-fraud advisories (job / investment / "digital arrest" calls).
HIGH_RISK_CC = {"92": "Pakistan", "880": "Bangladesh", "855": "Cambodia", "856": "Laos", "95": "Myanmar",
                "84": "Vietnam", "63": "Philippines", "234": "Nigeria", "62": "Indonesia", "977": "Nepal"}
KNOWN_CC = {"1": "USA/Canada", "44": "UK", "971": "UAE", "966": "Saudi Arabia", "65": "Singapore",
            "61": "Australia", "49": "Germany", "86": "China", **HIGH_RISK_CC}


def normalize(number: str) -> str:
    n = re.sub(r"[^\d+]", "", number or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    if n.startswith("0") and len(n) == 11:
        n = "+91" + n[1:]
    if len(n) == 10 and n[0] in "6789":
        n = "+91" + n
    return n


def info(number: str, community_reports: int = 0, blocked_by_family: bool = False,
         in_family: str | None = None) -> dict:
    n = normalize(number)
    flags: list[dict] = []
    kind = "unknown"
    country = None
    score = 0.1
    if in_family:
        return {"number": n, "kind": "family", "country": "India", "family_member": in_family,
                "flags": [{"en": f"Saved family member: {in_family}", "hi": f"परिवार का सदस्य: {in_family}", "level": "safe"}],
                "spam_score": 0.0, "community_reports": community_reports}
    if n.startswith("+91"):
        country = "India"
        local = n[3:]
        if local.startswith("140"):
            kind = "telemarketing"
            flags.append({"en": "140-series: registered telemarketing number.", "hi": "140 सीरीज़: टेलीमार्केटिंग नंबर।", "level": "warn"})
            score = 0.4
        elif local.startswith("160"):
            kind = "bank_service"
            flags.append({"en": "160-series: official bank / financial service line.", "hi": "160 सीरीज़: बैंक/वित्तीय सेवा।", "level": "safe"})
            score = 0.05
        elif len(local) == 10 and local[0] in "6789":
            kind = "mobile"
        elif len(local) != 10:
            flags.append({"en": "Number length is wrong for India - may be spoofed.", "hi": "भारत के लिए नंबर की लंबाई गलत है - नकली हो सकता है।", "level": "danger"})
            score = 0.6
        else:
            kind = "landline"
    elif n.startswith("+"):
        digits = n[1:]
        for cc in sorted(KNOWN_CC, key=len, reverse=True):
            if digits.startswith(cc):
                country = KNOWN_CC[cc]
                break
        kind = "international"
        score = 0.45
        flags.append({"en": f"International call ({country or 'unknown country'}).", "hi": f"विदेशी कॉल ({country or 'अज्ञात देश'})।", "level": "warn"})
        if any(digits.startswith(cc) for cc in HIGH_RISK_CC):
            score = 0.75
            flags.append({"en": "Country often used by scam call centres.", "hi": "इस देश से अक्सर ठगी के कॉल आते हैं।", "level": "danger"})
    elif len(n) in (5, 6):
        kind = "short_code"
        score = 0.2
    if community_reports:
        score = max(score, min(0.98, 0.55 + 0.1 * community_reports))
        flags.append({"en": f"Reported as scam by {community_reports} VoiceGuard user(s).",
                      "hi": f"{community_reports} VoiceGuard यूज़र ने इसे ठगी बताया है।", "level": "danger"})
    if blocked_by_family:
        score = max(score, 0.9)
        flags.append({"en": "Your family has blocked this number.", "hi": "आपके परिवार ने यह नंबर ब्लॉक किया है।", "level": "danger"})
    if kind == "mobile" and not community_reports:
        flags.append({"en": "Unknown mobile number - not in your family circle.", "hi": "अनजान मोबाइल नंबर - परिवार में नहीं।", "level": "info"})
    return {"number": n, "kind": kind, "country": country, "flags": flags,
            "spam_score": round(score, 3), "community_reports": community_reports}
