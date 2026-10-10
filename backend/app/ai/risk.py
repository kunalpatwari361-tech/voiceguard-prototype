"""Final Risk Score (feature 16): fuse every check into one 0-100 number with reasons.

Audio checks can be fooled, so non-audio proof (Are You Really Calling?, HD call on the real
person's device) overrides them - pitch idea #7.
"""

WEIGHTS = {
    "deepfake": 0.28, "voiceprint": 0.20, "scam_words": 0.16, "fingerprints": 0.14,
    "voice_test": 0.10, "location": 0.10, "reply_delay": 0.06, "number": 0.06,
    "voice_changer": 0.14, "scam_voice": 0.22,
}
# A very sure AI-voice verdict is proof on its own: "nothing else found" (e.g. no scam words in a short clip)
# must not average it away - that turned a 100 % AI voice into "caution 53". On unseen voices
# (scripts/eval_pipeline.py) only ~2-3 % of real voices reach 0.9, while most AI voices do.
AI_SURE, AI_LIKELY = 0.9, 0.7
LABELS = {
    "deepfake": ("AI Voice Detector", "AI आवाज़ जाँच"),
    "voiceprint": ("Voice Print Match", "वॉइस प्रिंट मिलान"),
    "scam_words": ("Scam Words", "ठगी वाले शब्द"),
    "fingerprints": ("Voice fingerprints", "आवाज़ के निशान"),
    "voice_test": ("Voice Test", "वॉइस टेस्ट"),
    "location": ("Family Location", "परिवार की लोकेशन"),
    "reply_delay": ("Reply Delay", "जवाब में देरी"),
    "number": ("Number Info", "नंबर जानकारी"),
    "voice_changer": ("Voice changer", "वॉइस चेंजर"),
    "scam_voice": ("Scam Voice ID", "ठग की आवाज़ ID"),
    "really_calling": ("Are You Really Calling?", "क्या सच में आप कॉल कर रहे हैं?"),
    "hd_call": ("VoiceGuard HD Call", "VoiceGuard HD कॉल"),
}


def _level(score: float) -> str:
    return "danger" if score >= 0.65 else "caution" if score >= 0.35 else "safe"


ADVICE = {
    "danger": ("STOP. Do not send money or share OTP. Hang up and call the person back on their saved number.",
               "रुकिए! पैसे न भेजें, OTP न बताएं। फ़ोन काटें और उनके सेव नंबर पर वापस कॉल करें।"),
    "caution": ("Be careful. Verify with 'Are You Really Calling?' or a VoiceGuard HD call before acting.",
                "सावधान रहें। कुछ भी करने से पहले 'क्या सच में आप कॉल कर रहे हैं?' या HD कॉल से जाँचें।"),
    "safe": ("No strong scam signs found. Still never share OTP or PIN on a call.",
             "ठगी के बड़े संकेत नहीं मिले। फिर भी कॉल पर OTP या PIN कभी न बताएं।"),
}


def fuse(signals: dict) -> dict:
    s: dict[str, float] = {}
    detail: dict[str, tuple[str, str]] = {}

    if signals.get("deepfake") is not None:
        s["deepfake"] = float(signals["deepfake"])
        detail["deepfake"] = (f"AI-voice probability {s['deepfake'] * 100:.0f}%.",
                              f"AI आवाज़ की संभावना {s['deepfake'] * 100:.0f}%।")
    vp = signals.get("voiceprint")
    if vp:
        s["voiceprint"] = {"match": 0.05, "uncertain": 0.5, "different": 0.9}[vp["verdict"]]
        who = vp.get("name", "the claimed person")
        detail["voiceprint"] = ({"match": f"Voice matches {who}'s saved voice print.",
                                 "uncertain": f"Voice only partly matches {who}.",
                                 "different": f"Voice does NOT match {who}'s voice print."}[vp["verdict"]],
                                {"match": f"आवाज़ {who} के वॉइस प्रिंट से मिलती है।",
                                 "uncertain": f"आवाज़ {who} से थोड़ी ही मिलती है।",
                                 "different": f"आवाज़ {who} के वॉइस प्रिंट से नहीं मिलती।"}[vp["verdict"]])
    for key in ("scam_words", "fingerprints", "reply_delay", "number"):
        val = signals.get(key)
        if val is not None:
            s[key] = float(val)
    if "number" in s:
        detail["number"] = (f"Caller number risk {s['number'] * 100:.0f}% (reports, country, pattern).",
                            f"कॉलर नंबर जोखिम {s['number'] * 100:.0f}% (रिपोर्ट, देश, पैटर्न)।")
    if "fingerprints" in s:
        detail["fingerprints"] = (f"Voice fingerprints look {s['fingerprints'] * 100:.0f}% machine-like.",
                                  f"आवाज़ के निशान {s['fingerprints'] * 100:.0f}% मशीन जैसे हैं।")
    if "reply_delay" in s:
        detail["reply_delay"] = ("Reply timing checked across the conversation.", "बातचीत में जवाब का समय जाँचा गया।")
    if signals.get("scam_words_tips"):
        tips = signals["scam_words_tips"]
        detail["scam_words"] = (" ".join(t["en"] for t in tips[:3]), " ".join(t["hi"] for t in tips[:3]))
    vt = signals.get("voice_test")
    if vt is not None:
        s["voice_test"] = 0.1 if vt.get("passed") else 0.85
        detail["voice_test"] = (("Caller passed the Voice Test.", "कॉलर ने वॉइस टेस्ट पास किया।") if vt.get("passed")
                                else ("Caller failed the Voice Test.", "कॉलर वॉइस टेस्ट में फेल हुआ।"))
    vc = signals.get("voice_changer")
    if vc is not None:
        s["voice_changer"] = float(vc)
        detail["voice_changer"] = (f"Voice-changer effect found ({vc * 100:.0f}%): a real person is disguising their voice.",
                                   f"वॉइस-चेंजर का असर मिला ({vc * 100:.0f}%): कोई असली इंसान अपनी आवाज़ छुपा रहा है।")
    sv = signals.get("scam_voice")
    if sv:
        s["scam_voice"] = 0.95 if sv.get("strong") else 0.8
        nums = len(sv.get("numbers") or [])
        detail["scam_voice"] = (f"Same voice as {sv.get('calls', 0)} earlier reported scam call(s) from {nums} number(s) "
                                f"- Voice ID {sv['id']} (similarity {sv.get('similarity', 0):.2f}).",
                                f"यही आवाज़ पहले {sv.get('calls', 0)} रिपोर्ट हुई ठगी कॉल में थी ({nums} नंबर) "
                                f"- Voice ID {sv['id']}।")
    loc = signals.get("location")
    if loc is not None:
        s["location"] = 0.8 if loc.get("mismatch") else 0.2
        detail["location"] = (loc.get("en", ""), loc.get("hi", ""))

    tot = sum(WEIGHTS[k] for k in s)
    score = sum(WEIGHTS[k] * v for k, v in s.items()) / tot if tot else 0.3
    strong = [k for k, v in s.items() if v >= 0.7]
    if len(strong) >= 2:
        score = max(score, 0.75)
    if s.get("scam_words", 0) >= 0.85 and (s.get("deepfake", 0) >= 0.5 or s.get("voiceprint", 0) >= 0.9):
        score = max(score, 0.9)
    if sv and sv.get("strong"):   # a voice the community already confirmed as a scammer's
        score = max(score, 0.85)
    ai = s.get("deepfake", 0.0)
    if ai >= AI_SURE:
        score = max(score, 0.70)
    elif ai >= AI_LIKELY:
        score = max(score, 0.45)

    overrides = []
    rc = signals.get("really_calling")
    if rc == "no":
        score = max(score, 0.96)
        overrides.append(("really_calling", "Their own phone says they are NOT calling you.",
                          "उनके अपने फ़ोन ने बताया कि वे आपको कॉल नहीं कर रहे।"))
    elif rc == "yes":
        score = min(score, 0.2)
        overrides.append(("really_calling", "They confirmed on their own phone that they are calling.",
                          "उन्होंने अपने फ़ोन पर पुष्टि की कि वे ही कॉल कर रहे हैं।"))
    elif rc == "no_answer":
        score = max(score, min(1.0, score + 0.1))
    hd = signals.get("hd_call")
    if hd == "verified" and rc != "no":
        score = min(score, 0.15)
        overrides.append(("hd_call", "Verified over a VoiceGuard HD call on their registered phone.",
                          "उनके रजिस्टर्ड फ़ोन पर VoiceGuard HD कॉल से पुष्टि हुई।"))
    elif hd == "refused":
        score = min(1.0, score + 0.2)
        overrides.append(("hd_call", "Caller refused to move to a VoiceGuard HD call.",
                          "कॉलर ने VoiceGuard HD कॉल पर आने से मना किया।"))

    level = _level(score)
    reasons = [{"key": k, "label": LABELS[k][0], "label_hi": LABELS[k][1], "score": round(v, 3),
                "en": detail.get(k, ("", ""))[0], "hi": detail.get(k, ("", ""))[1]}
               for k, v in sorted(s.items(), key=lambda kv: -WEIGHTS[kv[0]] * kv[1])]
    for k, en, hi in overrides:
        reasons.insert(0, {"key": k, "label": LABELS[k][0], "label_hi": LABELS[k][1], "score": None, "en": en, "hi": hi})
    return {"score": round(score * 100), "level": level, "reasons": reasons,
            "advice": ADVICE[level][0], "advice_hi": ADVICE[level][1], "checks_used": len(s) + len(overrides)}
