"""Central settings. Values can be overridden with environment variables or backend/.env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Keep the ~3 GB of Hugging Face models off the C: drive.
_hf = os.getenv("VG_HF_HOME") or (r"K:\vgtools\hf" if Path(r"K:\vgtools").exists() else None)
if _hf:
    os.environ.setdefault("HF_HOME", _hf)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidence"
DB_PATH = Path(os.getenv("VG_DB", str(DATA_DIR / "voiceguard.db")))
DATA_DIR.mkdir(exist_ok=True)
EVIDENCE_DIR.mkdir(exist_ok=True)

SAMPLE_RATE = 16000

# "voiceguard" = our phone-trained detector (training/train_detector.py); or any HF audio-classification id.
DEEPFAKE_MODEL = os.getenv("VG_DEEPFAKE_MODEL", "voiceguard")
SPEAKER_MODEL = os.getenv("VG_SPEAKER_MODEL", "microsoft/wavlm-base-plus-sv")
ASR_MODEL = os.getenv("VG_ASR_MODEL", "openai/whisper-small")

# Optional: deeper scam-conversation understanding with Claude. Without a key the
# rule-based Hindi/English phrase engine is used on its own.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("VG_CLAUDE_MODEL", "claude-opus-5-5")

# Demo API key that a partner bank uses for the Enterprise API (feature 32).
ENTERPRISE_API_KEY = os.getenv("VG_ENTERPRISE_KEY", "demo-bank-key")

# Skip loading heavy models at startup (useful for quick UI work).
WARMUP_MODELS = os.getenv("VG_WARMUP", "1") == "1"

# ---------------------------------------------------------------- phone-number OTP sign-up
# Real SMS through Twilio Verify when these are set (https://www.twilio.com/docs/verify).
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_VERIFY_SID = os.getenv("TWILIO_VERIFY_SID", "").strip()
# "twilio" = real SMS; "console" = demo mode, the code is printed in the server window and data/dev_otp.log.
OTP_PROVIDER = os.getenv("VG_OTP_PROVIDER", "twilio" if TWILIO_VERIFY_SID else "console").strip().lower()
# Optional fixed codes for demo/test numbers, no SMS sent: "+919876500002:246810,+919876500003:135790"
OTP_TEST_NUMBERS = {
    pair.split(":")[0].strip(): pair.split(":")[1].strip()
    for pair in os.getenv("VG_OTP_TEST_NUMBERS", "").split(",") if ":" in pair
}
OTP_TTL_S = 300            # a code is valid for 5 minutes
OTP_MAX_ATTEMPTS = 5       # wrong guesses allowed per code
OTP_RESEND_S = 30          # wait before asking for a new code
OTP_MAX_PER_HOUR = 5       # codes per phone number per hour
TOKEN_TTL_DAYS = 180       # how long a phone stays signed in

# ---------------------------------------------------------------- push notifications (Firebase Cloud Messaging)
# Wakes a family phone whose app is closed. Both files come from the Firebase console and stay on this laptop.
FIREBASE_CREDENTIALS = Path(os.getenv("VG_FIREBASE_CREDENTIALS", str(DATA_DIR / "firebase-service-account.json")))
FIREBASE_APP_CONFIG = Path(os.getenv("VG_FIREBASE_APP_CONFIG", str(DATA_DIR / "google-services.json")))
ANDROID_PACKAGE = "com.voiceguard.app"

# Secret used to hash OTP codes and login tokens (created once, kept in data/secret.key).
_secret_file = DATA_DIR / "secret.key"
if os.getenv("VG_SECRET"):
    SECRET = os.getenv("VG_SECRET").encode()
else:
    if not _secret_file.exists():
        import secrets as _s
        _secret_file.write_text(_s.token_hex(32))
    SECRET = _secret_file.read_text().strip().encode()
