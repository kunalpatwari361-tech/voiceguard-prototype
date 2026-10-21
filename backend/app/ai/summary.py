"""AI Call Summary: every check summed up in a few plain sentences, in English and Hindi.

Built from the results themselves, so it works offline with no key. When Claude read the transcript
(ANTHROPIC_API_KEY set), its summary of what the caller said is used for that line.
"""
from .scam_text import TIPS

LEVEL = {"danger": ("DANGER", "खतरा"), "caution": ("CAUTION", "सावधान"), "safe": ("SAFE", "सुरक्षित")}


def make(result: dict, claimed_name: str | None = None, number_info: dict | None = None) -> dict:
    points: list[dict] = []

    def add(en: str, hi: str, kind: str):
        points.append({"en": en, "hi": hi, "kind": kind})

    if claimed_name:
        add(f"The caller claimed to be {claimed_name}.", f"कॉलर ने खुद को {claimed_name} बताया।", "claim")

    ni = number_info or {}
    if ni.get("community_reports"):
        n = ni["community_reports"]
        add(f"This number was reported as scam {n} time(s).", f"इस नंबर की {n} बार ठगी की शिकायत हुई है।", "number")
    elif ni.get("name_source") == "voiceguard":
        add(f"The number belongs to {ni['name']}, a verified VoiceGuard user.",
            f"यह नंबर {ni['name']} का है, जो सत्यापित VoiceGuard यूज़र हैं।", "number")
    elif ni.get("kind") == "international":
        add(f"International call from {ni.get('country') or 'an unknown country'}.",
            f"{ni.get('country') or 'अज्ञात देश'} से विदेशी कॉल।", "number")

    rev = result.get("reverse_engineering") or {}
    ai = (result.get("ai_voice") or {}).get("fake_prob")
    if rev.get("label") and ai is not None:
        add(f"Voice: {rev['label']} ({ai * 100:.0f}% AI-made).",
            f"आवाज़: {rev.get('label_hi') or rev['label']} ({ai * 100:.0f}% AI से बनी)।", "voice")

    vp = result.get("voice_print") or {}
    who = vp.get("name") or claimed_name or "the claimed person"
    if vp.get("verdict") == "different":
        add(f"The voice does NOT match {who}'s saved voice.", f"आवाज़ {who} की सेव आवाज़ से नहीं मिलती।", "voice")
    elif vp.get("verdict") == "match":
        add(f"The voice matches {who}'s saved voice.", f"आवाज़ {who} की सेव आवाज़ से मिलती है।", "voice")

    sv = result.get("voice_id") or {}
    if sv.get("id") and not sv.get("ignored"):
        add(f"Same voice as {sv.get('calls', 0)} reported scam call(s) (Voice ID {sv['id']}).",
            f"यही आवाज़ {sv.get('calls', 0)} रिपोर्ट हुई ठगी कॉल में थी (Voice ID {sv['id']})।", "voice")

    sw = result.get("scam_words") or {}
    llm = sw.get("claude") or {}
    cats = (sw.get("rules") or {}).get("categories") or []
    if llm.get("summary_en"):
        add(llm["summary_en"], llm.get("summary_hi") or llm["summary_en"], "words")
    elif cats:
        add("What was said: " + " ".join(TIPS[c][0] for c in cats if c in TIPS),
            "क्या कहा गया: " + " ".join(TIPS[c][1] for c in cats if c in TIPS), "words")
    elif (result.get("transcript") or {}).get("text"):
        add("No scam phrases were heard.", "कोई ठगी वाले शब्द नहीं सुने गए।", "words")

    risk = result.get("risk") or {}
    if risk.get("level") in LEVEL:
        en, hi = LEVEL[risk["level"]]
        add(f"Result: {en} ({risk.get('score')}/100). {risk.get('advice', '')}".strip(),
            f"नतीजा: {hi} ({risk.get('score')}/100)। {risk.get('advice_hi', '')}".strip(), "result")

    return {"en": " ".join(p["en"] for p in points), "hi": " ".join(p["hi"] for p in points), "points": points,
            "by": "claude + checks" if llm.get("summary_en") else "checks"}
