"""SQLite storage (SQLModel)."""
import datetime as dt
import json
import secrets
import uuid

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def now() -> dt.datetime:
    """Timezone-aware UTC (SQLModel stores and returns aware UTC datetimes)."""
    return dt.datetime.now(dt.timezone.utc)


def short_id() -> str:
    return uuid.uuid4().hex[:8]


class User(SQLModel, table=True):
    id: str = Field(default_factory=short_id, primary_key=True)
    name: str
    phone: str = Field(index=True)
    role: str = "member"                     # parent / child / member
    family_id: str | None = Field(default=None, index=True)
    created_at: dt.datetime = Field(default_factory=now)
    last_seen: dt.datetime | None = None
    call_state: str | None = None            # idle / ringing / offhook (reported by the phone)
    call_state_at: dt.datetime | None = None
    lat: float | None = None
    lon: float | None = None
    loc_accuracy: float | None = None
    place: str | None = None
    loc_at: dt.datetime | None = None
    voiceprint: str | None = None            # JSON list of floats (512-d WavLM x-vector)
    voiceprint_samples: int = 0
    voiceprint_at: dt.datetime | None = None


class Family(SQLModel, table=True):
    id: str = Field(default_factory=short_id, primary_key=True)
    name: str
    invite_code: str = Field(default_factory=lambda: f"{secrets.randbelow(900000) + 100000}", index=True)
    owner_id: str
    created_at: dt.datetime = Field(default_factory=now)


class ScamReport(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    number: str = Field(index=True)
    reporter_id: str | None = None
    reason: str = ""
    source: str = "community"                # community / telecom / seed
    created_at: dt.datetime = Field(default_factory=now)


class BlockedNumber(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    number: str = Field(index=True)
    created_at: dt.datetime = Field(default_factory=now)


class Evidence(SQLModel, table=True):
    id: str = Field(default_factory=short_id, primary_key=True)
    user_id: str = Field(index=True)
    created_at: dt.datetime = Field(default_factory=now)
    number: str | None = None
    claimed_name: str | None = None
    source: str = "call"
    risk_score: int | None = None
    level: str | None = None
    transcript: str | None = None
    notes: str | None = None
    report_json: str = "{}"
    audio_file: str | None = None


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    family_id: str | None = Field(default=None, index=True)
    from_user_id: str | None = None
    to_user_id: str | None = None             # None = whole family
    kind: str                                 # scam_call / impersonation / panic / callback / cyber_cell / police_join
    title: str
    body: str = ""
    payload_json: str = "{}"
    created_at: dt.datetime = Field(default_factory=now)


class RiskEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    created_at: dt.datetime = Field(default_factory=now)
    score: int
    level: str
    number: str | None = None
    source: str = "call"


class OtpRequest(SQLModel, table=True):
    """Pending phone-number verification. Only a keyed hash of the code is stored."""
    phone: str = Field(primary_key=True)
    code_hash: str | None = None
    provider: str = "console"
    expires_at: dt.datetime | None = None
    attempts: int = 0
    last_sent_at: dt.datetime | None = None
    window_start: dt.datetime | None = None
    send_count: int = 0


class AuthToken(SQLModel, table=True):
    """Login token given to a phone after OTP verification. Only a keyed hash is stored."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    token_hash: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=now)
    expires_at: dt.datetime
    revoked: bool = False


def init_db():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        if not s.exec(select(ScamReport).where(ScamReport.source == "seed")).first():
            # Clearly fake demo numbers (TRAI test ranges are not public, so use an obviously invalid 9000000xxx block).
            seed = [("+919000000101", "Fake 'son in accident' call, asked for UPI transfer"),
                    ("+919000000101", "Same number, cloned voice of grandson"),
                    ("+919000000102", "Digital arrest - fake CBI officer"),
                    ("+919000000103", "KBC lottery prize scam"),
                    ("+923000000104", "International call asking for OTP"),
                    ("+918555000105", "Courier parcel with drugs - customs threat")]
            for n, r in seed:
                s.add(ScamReport(number=n, reason=r, source="seed"))
            s.commit()


def get_session():
    with Session(engine) as s:
        yield s


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
