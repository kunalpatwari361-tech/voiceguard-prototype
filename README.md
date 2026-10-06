# VoiceGuard – prototype

Stops AI voice-clone scam calls ("Papa, I had an accident, send money"). Android app + AI server on a laptop.

```
 Android phone (Kotlin + Jetpack Compose)                  Laptop (Python 3.14 + FastAPI)
 ┌──────────────────────────────────────┐  USB adb reverse ┌──────────────────────────────────────────┐
 │ OTP sign-up → login token on every   │                  │ /api/auth     OTP sign-up, tokens        │
 │ request                              │                  │                                          │
 │ VoiceGuard Dialer (default Phone app)│                  │ /api/analyze  AI voice · fingerprints ·  │
 │  keypad · recents · contacts · SIMs  │ ◄──── HTTP ────► │               voice print · Whisper ·    │
 │ Call screen + live voice check       │                  │               scam words · risk score    │
 │ Caller ID card · spam/block screening│ ◄─ WebSocket ──► │ /ws           family link: "are you      │
 │ Family Circle · alerts · Panic Pause │                  │               calling?", alerts, location│
 │ HD call audio (16 kHz)               │ ◄─ WebSocket ──► │ /ws/hd        HD call relay + live AI    │
 │ Evidence · 1930 · Chakshu            │                  │ SQLite · models cached in K:\vgtools\hf  │
 └──────────────────────────────────────┘                  └──────────────────────────────────────────┘
```

## Screenshots (real app, Android 17 emulator)

| Home | Are you really calling? | Scam call | Voice Test |
|---|---|---|---|
| ![](docs/screenshots/03_home.png) | ![](docs/screenshots/05_are_you_calling.png) | ![](docs/screenshots/07_incoming_scam.png) | ![](docs/screenshots/10_voice_test.png) |

| Risk score | AI report | Evidence + Chakshu | Panic Pause |
|---|---|---|---|
| ![](docs/screenshots/12_risk_full.png) | ![](docs/screenshots/13_full_report.png) | ![](docs/screenshots/15_evidence_chakshu.png) | ![](docs/screenshots/16_panic_pause.png) |

| Dialer (Galaxy Z Flip3) |
|---|
| ![](docs/screenshots/17_dialer_keypad.png) |

## Tech stack

### Android app (`android/`)

| Area | Technology |
|---|---|
| Language & UI | Kotlin 2.0.21 · Jetpack Compose (BOM 2024.12.01, Material 3) · Navigation Compose 2.8.5 · Lifecycle 2.8.7 |
| Async & data | kotlinx.coroutines 1.9.0 · kotlinx.serialization-json 1.7.3 |
| Networking | OkHttp 4.12.0 – REST calls, the always-on WebSocket family link, and HD-call audio streaming |
| Phone calls (Telecom) | `RoleManager` (default Phone app + call-screening roles) · `InCallService` (own call screen, SIM picker, DTMF keypad, hold, mute, speaker) · `CallScreeningService` (spam warning, block list) · `TelecomManager.placeCall` with the chosen SIM (`PhoneAccountHandle`) |
| Phone data | `ContactsContract` (contacts + caller-ID lookup) · `CallLog` (Recents) · `TelephonyCallback` (call state for "Are you really calling?") |
| Audio | `AudioRecord` / `AudioTrack` (16 kHz PCM, `VOICE_COMMUNICATION` + echo canceller) · `MediaExtractor` + `MediaCodec` (decodes WhatsApp Opus, m4a, mp3) · `MediaPlayer` (demo calls) |
| Notifications & overlays | Call notification with Answer / Decline / Hang up / Speaker / Mute actions · full-screen alerts · `WindowManager` overlay (Truecaller-style caller-ID card) |
| Location & maps | `LocationManager` + `Geocoder` · osmdroid 6.1.20 (OpenStreetMap, no API key) |
| Background | Foreground service (`specialUse` + `location`) for the family link · `UsageStatsManager` (Panic Pause) · boot receiver |
| Build | Android Gradle Plugin 8.7.3 · Gradle 8.11.1 (wrapper) · JDK 17 (Temurin 17.0.20) · compileSdk/targetSdk 35 (Android 15) · minSdk 29 (Android 10) |
| Tested on | Samsung Galaxy Z Flip3 (SM-F711B, Android 15, dual SIM) · Android 17 emulator |

### Server (`backend/`)

| Area | Technology |
|---|---|
| Runtime | Python 3.14 |
| Web | FastAPI 0.142 · Uvicorn 0.54 (+ websockets 17.2) · Starlette 1.7 · Pydantic 2.13 · python-multipart · python-dotenv |
| Storage | SQLModel 0.0.47 on SQLAlchemy 2.0 + SQLite (users, family, alerts, evidence, scam list) |
| Sign-up & security | Phone-number OTP (Twilio Verify for real SMS, or demo mode) → login token; codes and tokens stored only as HMAC-SHA256 hashes; every route checks the token and that you act only for yourself / your family (httpx 0.28 for Twilio) |
| AI runtime | PyTorch 2.14.1 (CPU) · Hugging Face Transformers 5.18 · huggingface_hub 1.33 |
| Audio & DSP | NumPy 2.5 (FFT band-pass filters – no SciPy at runtime) · soundfile 0.14 / libsndfile 1.2.2 (WAV, FLAC, OGG-Opus, MP3 + real Opus/MP3 encoding) · soxr 1.1 (resampling) · Praat via parselmouth 0.4.7 |
| Optional LLM | Anthropic Python SDK 1.11 → `claude-opus-5-5` (only when `ANTHROPIC_API_KEY` is set) |
| Exact pins | `backend/requirements.txt` (server) · `backend/requirements-train.txt` (training extras) |

### Training & evaluation (`backend/training/`, `backend/scripts/`)

| Area | Technology |
|---|---|
| Classifier | scikit-learn 1.9 (standard scaler + logistic regression on frozen WavLM features) |
| Real speech | Google FLEURS (Hindi, English, Marathi, Tamil, Telugu, Punjabi) · LibriSpeech sample (read with pyarrow 25) |
| AI speech | Meta MMS-TTS (Hindi, English) · Microsoft SpeechT5 HiFi-GAN vocoder (re-synthesised real speech) · Windows SAPI voices |
| Channel augmentation | G.711 phone line, AMR-like low bitrate, packet loss, real Opus (WhatsApp) and phone+Opus |
| Federated learning demo | FedAvg simulation in NumPy |

### Developer tools

Git + GitHub (private repo) · GitHub CLI 2.102 · Android platform-tools (adb) · Windows `.bat` helpers
(`start_server`, `build_app`, `install_app`, `connect_phones`, `add_adb_to_path`, `retrain_detector`, `sim_second_phone`).

## AI models

| Feature | Model / method |
|---|---|
| AI Voice Detector (3) | **VoiceGuard detector** – frozen Microsoft WavLM-Base-Plus layers 2–6 (mean + std pooling) + logistic head, *trained by us on clean, phone-line and WhatsApp-Opus audio* (`backend/training/`, weights `app/ai/weights/vg_detector.npz`, 93 KB) |
| Voice Print Match (10) | Microsoft `wavlm-base-plus-sv` x-vectors (cosine ≥ 0.86 = same speaker); the detector reuses the same network, so no extra memory |
| Scam Words (14), Voice Note text, Voice Test | OpenAI `whisper-small` (Hindi + English) + Hindi/Hinglish/English rule engine (tolerant of Whisper's spelling) |
| Optional deeper scam-talk check | Claude (`claude-opus-5-5`) – only if `ANTHROPIC_API_KEY` is set in `backend/.env` |
| Reverse Engineering Engine (4) | Signal processing: jitter/shimmer/HNR, breathing, pause rhythm, silence noise floor, formant jumps (phone-aware weights) |
| Source Tracing (5) | Rule-based mapping of fingerprints to generator families (*prototype heuristic*) |
| Reply Delay (13) | Turn-gap timing from voice activity (works at any audio quality) |
| Phone-quality training (#2) | 8 kHz, 300–3400 Hz, G.711 μ-law, AMR-like low bitrate, packet loss, real Opus |
| Demo scam voices | Meta `mms-tts-hin` / `mms-tts-eng` (real neural TTS, CC-BY-NC – non-commercial) |
| Smart Learning (31) | FedAvg federated learning simulation (NumPy) |

Models download automatically on first run (~3 GB, cached in `K:\vgtools\hf` when that folder exists).

### Why we trained our own detector

`backend/scripts/eval_detectors.py` tested public Hugging Face deepfake detectors on human speech vs. AI speech
(AUC: 0.5 = coin toss, 1.0 = perfect):

| Detector | Clean AUC | Phone-line AUC | WhatsApp-Opus AUC |
|---|---|---|---|
| MelodyMachine/Deepfake-audio-detection-V2 | 0.27 | 0.45 | – |
| mo-thecreator/Deepfake-audio-detection | 0.61 | 0.58 | – |
| garystafford/wav2vec2-deepfake-voice-detector | 0.65 | 0.61 | – |
| Our fingerprint rules alone | 0.97 | 0.43 | – |
| **VoiceGuard detector** (held-out sentences/speakers) | **0.998** | **0.995** | **0.994** |

VoiceGuard detector accuracy on the held-out set: 97% clean, 97% phone line, 96% WhatsApp Opus, 95% phone + Opus
(1,431 clips; details in `app/ai/weights/vg_detector_report.json`). It caught every demo AI voice. Known gap: false
alarms on languages it has not seen (e.g. Spanish) – the extra Indian-language data in `retrain_detector.bat` targets this.

Off-the-shelf detectors don't transfer, and phone lines destroy clean-audio clues – exactly pitch ideas #1 and #2.

## Run it

**First time on a new PC** (needs Python 3.14, JDK 17 and the Android SDK – or Android Studio):
```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
Then build the APK with `build_app.bat` (or open `android/` in Android Studio and press Run).

1. **Start the server** – double-click `start_server.bat` (first start downloads/loads models, ~30 s after the first time).
2. **Phones** – enable *Developer options → USB debugging* on both phones, plug them into the laptop, accept the prompt.
3. **Install** – double-click `install_app.bat` (installs the APK and links each phone to the server over USB).
   Re-run `connect_phones.bat` whenever you re-plug a phone.
4. **Set up** – on Papa's phone: name, number, role *Parent* → **Send OTP** → type the 6-digit code →
   *Create family circle* → note the 6-digit invite code.
   On Rahul's phone: role *Son/Daughter* → *Join* with the code. Tap *Allow* on every permission row
   (Call screening, Usage access, Display over other apps, Full-screen alerts).
5. On Rahul's phone: *My Voice Print* → read 3 sentences.

Rebuild the app after code changes with `build_app.bat`.

**Only one phone (or the emulator)?** Run `sim_second_phone.bat` and type the family invite code – the laptop then
acts as "Rahul's phone" (online, not on a call, voice print saved, receives alerts).

**Improve the AI-voice detector:** `retrain_detector.bat` continues training with the extra Indian-language data
(Hindi, Marathi, Tamil, Telugu, Punjabi). It resumes where it stopped; restart the server afterwards.

## Sign-up and security (OTP)

Every account is created by **verifying the phone number with a one-time code**; there is no other way in.

1. App → `POST /api/auth/otp/start {phone}` – the server sends a 6-digit code.
2. App → `POST /api/auth/otp/verify {phone, code, name, role}` – right code → account created (or the existing one
   for that number) + a **login token**. The app stores it and sends `Authorization: Bearer <token>` on every request;
   WebSockets and browser links (printable evidence report) use `?token=`.
3. Without a valid token the server answers **401**; acting for another user answers **403**; family data, evidence
   and voice prints are visible only inside your family circle. `POST /api/auth/logout` revokes the token.

| Rule | Value |
|---|---|
| Code length / lifetime | 6 digits, 5 minutes, single use |
| Wrong guesses | 5 per code, then ask for a new code |
| Resend | after 30 s, at most 5 codes per number per hour |
| Login token | 180 days, random 256-bit, stored only as an HMAC-SHA256 hash (`data/secret.key`) |

**How the code is delivered** (set in `backend/.env`, see `.env.example`):
- **Demo mode** (default): no SMS – the code is printed in the server window and `backend\data\dev_otp.log`.
- **Real SMS**: fill `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SID` (Twilio Verify). A Twilio trial
  can text the numbers you verify in the Twilio console.
- **Test numbers**: `VG_OTP_TEST_NUMBERS=+919876500002:246810` – fixed codes, no SMS (for judges / `sim_second_phone.bat`).

Partner APIs (bank `/api/v1/enterprise/*`, telecom `/api/v1/telecom/flag`) use their own API keys instead.

## Troubleshooting

**`adb : The term 'adb' is not recognized…`** – Windows doesn't know where `adb.exe` is.
- The `.bat` scripts don't need it on PATH: they search PATH, `ANDROID_HOME`, `ANDROID_SDK_ROOT`,
  Android Studio's `%LOCALAPPDATA%\Android\Sdk` and `K:\vgtools\android-sdk` (`tools\find_adb.bat`).
- To type `adb` yourself, double-click `add_adb_to_path.bat`, then **open a new terminal** (terminals only read
  PATH when they start; restart VS Code / the Claude app if the terminal lives inside it).
- Quick fix for the terminal you already have open (PowerShell):
  `$env:Path += ";K:\vgtools\android-sdk\platform-tools"`

**Phone not listed by `adb devices`** – enable *Developer options → USB debugging*, use a data USB cable, and tap
*Allow* on the phone. Some brands need their USB driver (Samsung: "Samsung Android USB Driver").

## 3-minute demo script

1. **Papa** → *Demo scam call* → pretends to be **Rahul** → *Start*. The number already shows community scam reports (Number Info).
2. *Answer*. The cloned "Rahul" says he had an accident. Talk back – the app measures the reply delay.
3. Tap **Are You Really Calling?** → Rahul's phone is not on a call → **FAKE CALL** in red within a second,
   and Rahul's phone shows "Someone is pretending to be you to Papa".
4. **Voice Test** → "ask him to laugh" → *Check caller's answer* → the AI fails.
5. **Check voice now** → Final Risk Score (AI voice, voice print mismatch, scam words, reply delay) → DANGER.
6. *Full report* → **Save evidence** → *Evidence & report* → **Chakshu** pre-filled answers / **Call 1930** / **Ask family to report**.
7. Open Google Pay / PhonePe right after → **Panic Pause** screen.
8. **Verify with HD call** → Rahul accepts → real HD audio between the phones, live AI check of Rahul's real voice
   (voice print **match**, low risk).
9. *Future lab* → Bank API holds the ₹50,000 payment; police join; telecom flag; voice shield; federated learning.

## All 32 features → where they live

| # | Feature | Status | Where |
|---|---|---|---|
| 1 | VoiceGuard Dialer | Real: default Phone app – keypad (T9 search), real call log, phone contacts, caller ID card, in-call keypad/hold/mute/speaker | `ui/DialerScreen.kt`, `data/Contacts.kt`, `ui/InCallActivity.kt`, `telecom/CallerIdOverlay.kt` |
| 2 | Family Circle | Real | `routers/people.py`, `ui/FamilyScreens.kt` |
| 3 | AI Voice Detector | Real (trained model) | `ai/models.py`, `training/train_detector.py` |
| 4 | Reverse Engineering Engine | Real | `ai/fingerprints.py` |
| 5 | Source Tracing | Heuristic prototype | `ai/source_trace.py` |
| 6 | Live Call Check | Real time: HD/demo calls fully; normal calls via loudspeaker every 6 s (auto for unknown callers)* | `ui/LiveCall.kt`, `routers/hdcall.py`, `ui/CheckScreen.kt` |
| 7 | Voice Note Check | Real (share WhatsApp audio to app) | `audio/Decoder.kt`, `ui/CheckScreen.kt` |
| 8 | Are You Really Calling? | Real | `routers/verify.py`, `ui/CallTools.kt`, `ui/HdScreens.kt` |
| 9 | Family Location Check | Real (GPS + map) | `service/Loc.kt`, `ui/FamilyScreens.kt` |
| 10 | Voice Print Match | Real | `ai/models.py`, `ui/FamilyScreens.kt` |
| 11 | VoiceGuard HD Call | Real (16 kHz app-to-app) | `audio/HdAudio.kt`, `routers/hdcall.py` |
| 12 | Voice Test (Voice CAPTCHA) | Real | `ai/challenge.py`, `routers/checks.py` |
| 13 | Reply Delay Check | Real | `ai/reply_delay.py` |
| 14 | Scam Words Alert | Real (Whisper + rules, optional Claude) | `ai/scam_text.py` |
| 15 | Number Info | Real (prefix rules + community data) | `ai/number_info.py` |
| 16 | Final Risk Score | Real | `ai/risk.py` |
| 17 | Family Alert | Automatic: DANGER result, reported/high-risk caller, fake "Are you calling?", Panic – with a Call button | `routers/common.py`, `service/GuardService.kt` |
| 18 | Call-Back Alert | Real | `service/GuardService.kt (CallWatch)`, `routers/verify.py` |
| 19 | Panic Pause | Real (usage access + overlay) | `service/GuardService.kt`, `ui/PanicPauseActivity.kt` |
| 20 | Save Evidence | Real | `routers/evidence.py` |
| 21 | One-Tap Call 1930 | Real | everywhere (`dial()`) |
| 22 | Report to Chakshu | Pre-filled form + link (no public API) | `routers/evidence.py` |
| 23 | Family Calls Cyber Cell | Real | `routers/evidence.py` |
| 24 | Spam Warning | Real (call screening role) | `telecom/ScreeningService.kt` |
| 25 | Block Numbers | Real, shared by family | `telecom/ScreeningService.kt`, `routers/checks.py` |
| 26 | Community Scam List | Real | `routers/checks.py` |
| 27 | Auto Check Every Call | Partial (number on every call; voice on demo/HD) | Settings toggle |
| 28 | Police Join the Call | Simulated | `routers/future.py` |
| 29 | Telecom Partnership | Mock operator API | `routers/future.py` |
| 30 | Voice Shield | Prototype (audio watermark) | `ai/voice_shield.py` |
| 31 | Smart Learning (federated) | Simulation (FedAvg) | `app/training/federated.py` |
| 32 | Bank and Enterprise APIs | Real REST API (demo key) | `routers/future.py` |

\* Android 10+ does not let normal apps record phone-call audio. The app tries speaker-mode recording and tells
you if Android blocks it; HD calls, voice notes and the non-audio checks (#8, #9, #15, #19) cover that gap –
pitch idea #7.

## Folders

```
VoiceGuard/
  backend/           FastAPI server, AI engine, training scripts
    app/ai/          detector, fingerprints, voice print, ASR, scam words, risk fusion
    app/routers/     REST + WebSocket endpoints
    training/        phone_augment.py, build_dataset.py, train_detector.py, finetune_phone.py
    scripts/         make_demo_audio.py, eval_detectors.py, smoke_test.py
    demo_audio/      generated AI scam-call clips (HD + phone quality)
  android/           Android Studio / Gradle project
K:\vgtools\          JDK, Android SDK, Gradle, model cache, datasets (outside the project)
```
