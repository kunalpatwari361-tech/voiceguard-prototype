"""Source Tracing (feature 5) + the combined Reverse Engineering verdict (feature 4).

trace()   - WHICH kind of tool made the voice. Uses the trained 5-class Source Tracer (training/train_tracer.py:
            human / AI TTS or clone / robotic TTS / AI voice changer / voice-changer app) when its weights exist,
            otherwise the old fingerprint rules (prototype heuristic).
combine() - one answer to "what made this voice?" from every model at once: the AI-voice detector, the Source
            Tracer, the voice fingerprints, the Voice Print, the reply timing and the Scam Voice ID.
"""
import numpy as np

FAMILIES = {
    "human": ("Human voice", "इंसानी आवाज़"),
    "ai_speech": ("AI voice clone / text-to-speech (ElevenLabs, XTTS style)", "AI वॉइस क्लोन / टेक्स्ट-टू-स्पीच"),
    "classic_tts": ("Robotic text-to-speech (older engines)", "पुराना रोबोटिक टेक्स्ट-टू-स्पीच"),
    "neural_vc": ("AI voice changer: a real person re-voiced by AI (RVC style)",
                  "AI वॉइस चेंजर: असली इंसान की आवाज़ AI से बदली गई"),
    "dsp_changer": ("Voice-changer app: pitch / gender / formant effect on a real person",
                    "वॉइस-चेंजर ऐप: असली इंसान की आवाज़ की पिच / लिंग बदला गया"),
    "ai_agent": ("Live AI call agent (speech-to-text + chatbot + TTS)", "लाइव AI कॉल एजेंट"),
    "unclear": ("Not sure - signals disagree", "पक्का नहीं - संकेत अलग-अलग हैं"),
}
MEANING = {
    "human": ("Sounds like a real person speaking normally.", "असली इंसान सामान्य तरह से बोल रहा लगता है।"),
    "ai_speech": ("A computer generated this voice - possibly cloned from someone's videos or voice notes.",
                  "यह आवाज़ कंप्यूटर ने बनाई है - शायद किसी के वीडियो या वॉइस नोट से क्लोन की गई।"),
    "classic_tts": ("A computer read out a script.", "कंप्यूटर ने लिखी हुई बात पढ़ी है।"),
    "neural_vc": ("A real person is talking, but AI turns their voice into someone else's.",
                  "असली इंसान बोल रहा है, पर AI उसकी आवाज़ को किसी और की आवाज़ में बदल रहा है।"),
    "dsp_changer": ("A real person is hiding their real voice with a voice-changer app. Your family would never do this.",
                    "असली इंसान वॉइस-चेंजर ऐप से अपनी आवाज़ छुपा रहा है। आपके परिवार वाले ऐसा कभी नहीं करेंगे।"),
    "ai_agent": ("A bot is talking to you live: it listens, writes an answer and speaks it with an AI voice.",
                 "एक बॉट आपसे लाइव बात कर रहा है: सुनता है, जवाब लिखता है और AI आवाज़ में बोलता है।"),
    "unclear": ("The checks do not agree. Verify with 'Are you really calling?' or an HD call.",
                "जाँचें एक-दूसरे से मेल नहीं खातीं। 'क्या सच में आप कॉल कर रहे हैं?' या HD कॉल से पुष्टि करें।"),
}
SYNTH = ("ai_speech", "classic_tts", "neural_vc")
# Voice-changer thresholds from the held-out test (training/train_tracer.py, vg_tracer*_report.json):
#   phone / WhatsApp audio: >= 0.5 catches 87% of voice-changer clips, flags 0.24% of real voices;
#                           >= 0.6 (counted in the risk score) catches 83%, flags 0.18%
#   loudspeaker + room (Live Call Check): >= 0.9 catches 37%, flags 0.3% - lower thresholds flag 2-4% of real
#                           voices, so in the room it is only shown when very sure and never raises the risk
CHANGER_SHOW = 0.5
CHANGER_RISK = 0.6
CHANGER_SHOW_ROOM = 0.9


def _heuristic(fake_prob: float, fp: dict, reply: dict | None) -> dict[str, float]:
    """Old rule-based guess (used only until the trained tracer exists)."""
    f = fp.get("features", {})
    st = fp.get("stats", {})
    g = lambda k: f.get(k, {}).get("score", 0.4)
    s = {
        "ai_speech": 1.0 + g("breathing") + g("silence") + (0.5 if st.get("f0_std_semitones", 0) > 2 else 0),
        "classic_tts": 0.5 + 1.5 * g("pitch_shake") + g("rhythm") + (0.8 if st.get("f0_std_semitones", 3) < 1.5 else 0),
        "neural_vc": 0.5 + 1.5 * g("mouth") + (1 - g("breathing")) + (1 - g("silence")) * 0.5,
    }
    v = np.array(list(s.values())) * 2
    p = np.exp(v - v.max())
    p = p / p.sum() * fake_prob
    return {"human": round(1 - fake_prob, 4)} | {k: round(float(x), 4) for k, x in zip(s, p)} | {"dsp_changer": 0.0}


def trace(fake_prob: float | None, fp: dict, reply: dict | None = None, probs: dict | None = None,
          model: str | None = None, room: bool = False) -> dict:
    trained = probs is not None
    if not trained:
        if fake_prob is None or fake_prob < 0.5:
            return {"likely": None, "label": "No generator detected - sounds like a human voice.",
                    "label_hi": "कोई AI जनरेटर नहीं मिला - इंसानी आवाज़ लगती है।", "candidates": [],
                    "probs": None, "model": "rules", "note": "prototype heuristic"}
        probs = _heuristic(fake_prob, fp, reply)
    order = sorted(probs, key=probs.get, reverse=True)
    best = order[0]
    changer = probs.get("dsp_changer", 0) >= (CHANGER_SHOW_ROOM if room else CHANGER_SHOW) and best == "dsp_changer"
    if (fake_prob or 0) < 0.5 and not changer:
        best = "human"     # the AI-voice detector decides human vs AI (see combine)
    elif (fake_prob or 0) >= 0.5 and best in ("human", "dsp_changer"):
        best = max(SYNTH, key=lambda k: probs.get(k, 0))
    if best == "ai_speech" and reply and (reply.get("score") or 0) >= 0.6:
        best = "ai_agent"
    label = FAMILIES[best]
    if best == "human":
        label = ("No generator detected - sounds like a human voice.", "कोई AI जनरेटर नहीं मिला - इंसानी आवाज़ लगती है।")
    return {
        "likely": None if best == "human" else best, "label": label[0], "label_hi": label[1],
        "candidates": [{"family": k, "label": FAMILIES[k][0], "label_hi": FAMILIES[k][1], "prob": probs[k]} for k in order],
        "probs": probs, "model": model or "rules",
        "note": ("trained Source Tracer (5 voice types; 'AI voice changer' is learned from neural-vocoder re-voiced speech)"
                 if trained else "prototype heuristic - needs labelled generator data for a trained tracer"),
    }


def combine(fake_prob: float, st: dict, fp: dict, vp: dict | None, reply: dict | None,
            scam_voice: dict | None, room: bool) -> dict:
    """Reverse Engineering verdict: every model votes, the answer says what made the voice and why."""
    p = st.get("probs") or {}
    trained = st.get("model") not in (None, "rules")
    p_human = p.get("human", 1 - fake_prob)
    p_changer = p.get("dsp_changer", 0.0)
    synth = {k: p.get(k, 0.0) for k in SYNTH}
    ev = []

    def add(en, hi):
        ev.append({"en": en, "hi": hi})

    # Who decides what: the AI-voice detector (the best-tested model) decides human vs AI-made; the Source Tracer
    # names the kind of AI and is the only model that hears voice-changer apps (the detector is not trained on them).
    # On new speakers the tracer alone mistakes ~1 in 4 real voices for AI, so it never overrules the detector.
    show = CHANGER_SHOW_ROOM if room else CHANGER_SHOW
    if trained and p_changer >= show and p_changer == max(p.values()) and fake_prob < 0.5:
        kind, conf = "dsp_changer", p_changer
    elif fake_prob >= 0.5:
        kind = max(synth, key=synth.get) if trained and sum(synth.values()) > 0.05 else "ai_speech"
        conf = fake_prob
    elif fake_prob < 0.35:
        kind, conf = "human", 1 - fake_prob
    else:
        kind, conf = "unclear", 0.5
    if kind == "ai_speech" and reply and (reply.get("score") or 0) >= 0.6:
        kind = "ai_agent"

    add(f"AI-voice detector: {fake_prob * 100:.0f}% AI-made.", f"AI आवाज़ जाँच: {fake_prob * 100:.0f}% AI से बनी।")
    if trained:
        if kind in SYNTH or kind in ("ai_agent", "dsp_changer"):
            k = "ai_speech" if kind == "ai_agent" else kind
            add(f"Source Tracer: {p.get(k, 0) * 100:.0f}% {FAMILIES[k][0].split(':')[0].lower()}.",
                f"स्रोत पहचान: {p.get(k, 0) * 100:.0f}% {FAMILIES[k][1].split(':')[0]}।")
        if kind != "dsp_changer":
            if p_changer >= 0.25 and not room:
                add(f"Some voice-changer effect too ({p_changer * 100:.0f}%).",
                    f"वॉइस-चेंजर का असर भी ({p_changer * 100:.0f}%)।")
            elif kind == "human":
                add("Source Tracer: no voice-changer effect found.", "स्रोत पहचान: कोई वॉइस-चेंजर असर नहीं मिला।")
    if not room:
        machine = [f["label"] for f in fp.get("features", {}).values() if f.get("score", 0) >= 0.6]
        if machine:
            add("Machine-like fingerprints: " + ", ".join(machine) + ".",
                "मशीन जैसे निशान: " + ", ".join(machine) + "।")
    if vp:
        if vp["verdict"] == "different":
            add(f"Voice print: NOT {vp.get('name')}'s real voice (similarity {vp['similarity']:.2f}).",
                f"वॉइस प्रिंट: यह {vp.get('name')} की असली आवाज़ नहीं है ({vp['similarity']:.2f})।")
        elif vp["verdict"] == "match":
            add(f"Voice print: sounds like {vp.get('name')} (similarity {vp['similarity']:.2f}).",
                f"वॉइस प्रिंट: {vp.get('name')} जैसी आवाज़ ({vp['similarity']:.2f})।")
    if reply and (reply.get("score") or 0) >= 0.6:
        add("Replies come after a fixed, machine-like delay.", "जवाब हर बार एक जैसी मशीनी देरी से आते हैं।")
    if scam_voice:
        add(f"Same voice as {scam_voice.get('calls', 0)} earlier reported scam call(s) - Voice ID {scam_voice['id']}.",
            f"यही आवाज़ पहले {scam_voice.get('calls', 0)} रिपोर्ट हुई ठगी कॉल में थी - Voice ID {scam_voice['id']}।")
    if room:
        add("Heard through the loudspeaker: room sound makes this less certain.",
            "स्पीकर से सुनी आवाज़: कमरे की आवाज़ से यह कम पक्का है।")
    return {"kind": kind, "label": FAMILIES[kind][0], "label_hi": FAMILIES[kind][1],
            "meaning": MEANING[kind][0], "meaning_hi": MEANING[kind][1],
            "confidence": round(float(conf), 3), "evidence": ev,
            "models": [m for m in ("AI-voice detector", "Source Tracer" if trained else "tracer rules",
                                   "voice fingerprints", "voice print" if vp else None,
                                   "Scam Voice ID" if scam_voice else None) if m]}
