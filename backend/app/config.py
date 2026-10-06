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
