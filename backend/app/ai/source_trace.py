"""Source Tracing (feature 5): which kind of generator most likely made this voice.

PROTOTYPE HEURISTIC. A production tracer is a classifier trained on labelled output from many TTS /
voice-conversion tools (ASVspoof-style attack IDs). Until that training data exists, this maps the
fingerprint profile to the generator families those fingerprints are typical of, and says so.
"""
import numpy as np

FAMILIES = {
    "neural_clone_tts": ("Neural voice-cloning TTS (ElevenLabs / XTTS style)",
                         "न्यूरल वॉइस-क्लोनिंग TTS"),
    "classic_tts": ("Classic text-to-speech (robotic / older engines)", "पुराना टेक्स्ट-टू-स्पीच"),
    "voice_conversion": ("Real-time voice changer (RVC / so-vits style)", "रियल-टाइम वॉइस चेंजर"),
    "ai_agent": ("Live AI call agent (speech-to-text + LLM + TTS)", "लाइव AI कॉल एजेंट"),
}


def trace(fake_prob: float | None, fp: dict, reply: dict | None = None) -> dict:
    if fake_prob is None or fake_prob < 0.5:
        return {"likely": None, "label": "No generator detected - sounds like a human voice.",
                "label_hi": "कोई AI जनरेटर नहीं मिला - इंसानी आवाज़ लगती है।", "candidates": [],
                "note": "prototype heuristic"}
    f = fp.get("features", {})
    st = fp.get("stats", {})
    g = lambda k: f.get(k, {}).get("score", 0.4)
    s = {
        "neural_clone_tts": 1.0 + g("breathing") + g("silence") + (0.5 if st.get("f0_std_semitones", 0) > 2 else 0),
        "classic_tts": 0.5 + 1.5 * g("pitch_shake") + g("rhythm") + (0.8 if st.get("f0_std_semitones", 3) < 1.5 else 0),
        "voice_conversion": 0.5 + 1.5 * g("mouth") + (1 - g("breathing")) + (1 - g("silence")) * 0.5,
        "ai_agent": 0.3 + (2.0 * reply["score"] if reply and reply.get("score") else 0),
    }
    keys = list(s)
    v = np.array([s[k] for k in keys]) * 2
    p = np.exp(v - v.max())
    p = p / p.sum()
    order = np.argsort(-p)
    best = keys[order[0]]
    return {
        "likely": best, "label": FAMILIES[best][0], "label_hi": FAMILIES[best][1],
        "candidates": [{"family": keys[i], "label": FAMILIES[keys[i]][0], "prob": round(float(p[i]), 3)} for i in order],
        "note": "prototype heuristic - needs labelled generator data for a trained tracer",
    }
