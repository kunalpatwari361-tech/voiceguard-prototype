"""Generate the demo scam-call clips the app plays in "Demo scam call" mode.

Uses Meta MMS-TTS (VITS) for Hindi and English - a real neural TTS, i.e. genuinely AI-generated speech.
Each scenario has several turns (so Reply Delay can be measured live) plus answers to Voice Test
challenges. Every clip is written twice: HD (16 kHz) and phone-quality (8 kHz G.711 channel).

Run once:  .venv\\Scripts\\python scripts\\make_demo_audio.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402,F401  (sets HF_HOME)
from app.ai.audio_io import resample  # noqa: E402
from app.ai.phone_channel import phone_channel  # noqa: E402

OUT = ROOT / "demo_audio"

SCENARIOS = [
    {
        "id": "hi_accident", "lang": "hin", "title": "Son in accident (Hindi)", "title_hi": "बेटे का एक्सीडेंट (हिंदी)",
        "number": "+919000000101", "relation": "son",
        "turns": [
            "हेलो पापा, मैं बोल रहा हूँ। मेरी आवाज़ सुनाई दे रही है?",
            "पापा मेरा एक्सीडेंट हो गया है, मैं अस्पताल में हूँ। बहुत दर्द हो रहा है।",
            "डॉक्टर बोल रहे हैं अभी पचास हज़ार रुपये जमा करने हैं। जल्दी से यूपीआई पर भेज दो।",
            "और मम्मी को मत बताना, वो परेशान हो जाएंगी। जल्दी करो पापा।",
        ],
        "challenge": {"laugh": "हा हा हा हा हा", "whisper": "पापा मैं ठीक हूँ", "sing": "हैप्पी बर्थडे टू यू",
                      "phrase": "आज मंगलवार है और मैं घर पर हूँ", "family_question": "पता नहीं पापा, अभी याद नहीं आ रहा, पहले पैसे भेज दो",
                      "dialect": "हाँ पापा, मैं ठीक हूँ, बस पैसे भेज दो"},
    },
    {
        "id": "en_police", "lang": "eng", "title": "Arrested by police (English)", "title_hi": "पुलिस ने पकड़ा (अंग्रेज़ी)",
        "number": "+919000000102", "relation": "son",
        "turns": [
            "Hello papa, it is me. Can you hear me properly?",
            "Papa, I am in big trouble. The police have arrested me after a small fight.",
            "The officer says if we pay forty thousand rupees right now, they will let me go. Please send it on this number.",
            "Please do not tell anyone at home. Just send the money quickly, papa.",
        ],
        "challenge": {"laugh": "ha ha ha ha ha", "whisper": "papa I am fine", "sing": "happy birthday to you",
                      "phrase": "today is tuesday and I am at home", "family_question": "I do not remember papa, please just send the money first",
                      "dialect": "yes papa, I am fine, just send the money"},
    },
    {
        "id": "hi_newnumber", "lang": "hin", "title": "New number, urgent money (Hindi)", "title_hi": "नया नंबर, तुरंत पैसे (हिंदी)",
        "number": "+919000000103", "relation": "daughter",
        "turns": [
            "हेलो मम्मी, मेरा फ़ोन खो गया है। यह मेरा नया नंबर है, सेव कर लो।",
            "मम्मी मुझे अभी तुरंत बीस हज़ार रुपये चाहिए, कॉलेज की फीस भरनी है।",
            "आपके फ़ोन पर एक ओटीपी आएगा, वो मुझे बता देना। बहुत ज़रूरी है।",
        ],
        "challenge": {"laugh": "हा हा हा हा", "whisper": "मम्मी मैं ठीक हूँ", "sing": "हैप्पी बर्थडे टू यू",
                      "phrase": "आज मंगलवार है और मैं घर पर हूँ", "family_question": "अभी याद नहीं है, पहले ओटीपी बता दो",
                      "dialect": "हाँ मम्मी, सब ठीक है"},
    },
    {
        "id": "en_digital_arrest", "lang": "eng", "title": "Fake CBI 'digital arrest' (English)", "title_hi": "नकली CBI 'डिजिटल अरेस्ट' (अंग्रेज़ी)",
        "number": "+919000000104", "relation": None,
        "turns": [
            "This is inspector Verma calling from the CBI cyber department.",
            "A courier parcel in your name was caught at customs with drugs and fake passports.",
            "You are now under digital arrest. Do not disconnect this call and do not tell your family.",
            "To clear your name, transfer your savings to the RBI safe account. I will send the account number.",
        ],
        "challenge": {"laugh": "ha ha ha", "whisper": "I am an officer", "sing": "I cannot sing on duty",
                      "phrase": "today is tuesday and I am at home", "family_question": "That is not relevant, follow my instructions",
                      "dialect": "Speak in English only"},
    },
]


def load_tts(lang: str):
    from transformers import AutoTokenizer, VitsModel
    mid = f"facebook/mms-tts-{lang}"
    return AutoTokenizer.from_pretrained(mid), VitsModel.from_pretrained(mid).eval()


@torch.inference_mode()
def speak(tok, model, text: str, seed: int) -> np.ndarray:
    torch.manual_seed(seed)
    inputs = tok(text, return_tensors="pt")
    wav = model(**inputs).waveform[0].numpy().astype(np.float32)
    wav = resample(wav, model.config.sampling_rate, 16000)
    wav = wav / (np.abs(wav).max() + 1e-9) * 0.8
    pad = np.zeros(int(0.25 * 16000), np.float32)
    return np.concatenate([pad, wav, pad])


def write(name: str, y: np.ndarray) -> dict:
    sf.write(OUT / f"{name}_hd.wav", y, 16000, subtype="PCM_16")
    sf.write(OUT / f"{name}_phone.wav", phone_channel(y, codec="g711", packet_loss=0.01, seed=3), 16000, subtype="PCM_16")
    return {"hd": f"{name}_hd.wav", "phone": f"{name}_phone.wav", "seconds": round(len(y) / 16000, 2)}


def main():
    OUT.mkdir(exist_ok=True)
    tts = {lang: load_tts(lang) for lang in ("hin", "eng")}
    meta = []
    for sc in SCENARIOS:
        tok, model = tts[sc["lang"]]
        turns = []
        for i, text in enumerate(sc["turns"]):
            turns.append({"text": text, **write(f"{sc['id']}_t{i + 1}", speak(tok, model, text, seed=i))})
        ch = {k: {"text": t, **write(f"{sc['id']}_c_{k}", speak(tok, model, t, seed=99))} for k, t in sc["challenge"].items()}
        meta.append({"id": sc["id"], "title": sc["title"], "title_hi": sc["title_hi"], "number": sc["number"],
                     "relation": sc["relation"], "language": "hi" if sc["lang"] == "hin" else "en",
                     "generator": f"facebook/mms-tts-{sc['lang']} (VITS neural TTS)", "turns": turns,
                     "challenge_responses": ch})
        print("made", sc["id"])
    (OUT / "clips.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
