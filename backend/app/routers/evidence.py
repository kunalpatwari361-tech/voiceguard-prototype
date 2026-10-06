"""After the scam: Save Evidence (20), report for 1930 (21), Report to Chakshu (22),
Family Calls Cyber Cell (23)."""
import html
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..ai.number_info import normalize
from ..config import EVIDENCE_DIR
from ..db import Evidence, User, get_session
from ..auth import current_user, require_family, require_self
from .common import iso, push_alert

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

CHAKSHU_URL = "https://sancharsaathi.gov.in/sfc/"
CYBERCRIME_URL = "https://cybercrime.gov.in/"


def _owned(s: Session, me: User, evidence_id: str) -> Evidence:
    """Evidence is visible to its owner and their family only (browser links carry ?token=)."""
    e = s.get(Evidence, evidence_id)
    if not e:
        raise HTTPException(404)
    require_family(me, s.get(User, e.user_id))
    return e


def _view(e: Evidence) -> dict:
    return {"id": e.id, "user_id": e.user_id, "created_at": iso(e.created_at), "number": e.number,
            "claimed_name": e.claimed_name, "source": e.source, "risk_score": e.risk_score, "level": e.level,
            "transcript": e.transcript, "notes": e.notes, "has_audio": bool(e.audio_file),
            "report": json.loads(e.report_json or "{}")}


@router.post("")
async def save(user_id: str = Form(...), number: str | None = Form(None), claimed_name: str | None = Form(None),
               source: str = Form("call"), risk_score: int | None = Form(None), level: str | None = Form(None),
               transcript: str | None = Form(None), notes: str | None = Form(None), report_json: str = Form("{}"),
               file: UploadFile | None = File(None), me: User = Depends(current_user),
               s: Session = Depends(get_session)):
    require_self(me, user_id)
    e = Evidence(user_id=user_id, number=normalize(number) if number else None, claimed_name=claimed_name,
                 source=source, risk_score=risk_score, level=level, transcript=transcript, notes=notes,
                 report_json=report_json)
    if file is not None:
        path = EVIDENCE_DIR / f"{e.id}.wav"
        path.write_bytes(await file.read())
        e.audio_file = path.name
    s.add(e)
    s.commit()
    s.refresh(e)
    return _view(e)


@router.get("/user/{user_id}")
def list_for_user(user_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    require_self(me, user_id)
    u = me
    ids = [u.id]
    if u.family_id:  # family helpers can see a parent's evidence to help report it
        ids = [m.id for m in s.exec(select(User).where(User.family_id == u.family_id)).all()]
    rows = s.exec(select(Evidence).where(Evidence.user_id.in_(ids)).order_by(Evidence.created_at.desc())).all()
    return [_view(e) for e in rows]


@router.get("/{evidence_id}")
def get_one(evidence_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    e = _owned(s, me, evidence_id)
    if not e:
        raise HTTPException(404)
    return _view(e)


@router.get("/{evidence_id}/audio")
def audio(evidence_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    e = _owned(s, me, evidence_id)
    if not e or not e.audio_file:
        raise HTTPException(404)
    return FileResponse(EVIDENCE_DIR / e.audio_file, media_type="audio/wav", filename=f"voiceguard-{e.id}.wav")


def complaint_text(e: Evidence, victim: User | None) -> str:
    r = json.loads(e.report_json or "{}")
    reasons = "; ".join(x.get("en", "") for x in r.get("risk", {}).get("reasons", [])[:5] if x.get("en"))
    return (f"On {e.created_at:%d %b %Y at %H:%M} UTC, {victim.name if victim else 'the victim'} "
            f"({victim.phone if victim else ''}) received a suspected fraud call from {e.number or 'an unknown number'}"
            f"{' in which the caller pretended to be ' + e.claimed_name if e.claimed_name else ''}. "
            f"VoiceGuard risk score: {e.risk_score if e.risk_score is not None else 'n/a'}/100 ({e.level or 'n/a'}). "
            f"Signals: {reasons or 'see attached report'}. "
            f"{('Caller said: ' + e.transcript[:400]) if e.transcript else ''}").strip()


@router.get("/{evidence_id}/chakshu")
def chakshu(evidence_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Pre-filled answers for the Chakshu form on Sanchar Saathi (no public API, so the user pastes them)."""
    e = _owned(s, me, evidence_id)
    if not e:
        raise HTTPException(404)
    victim = s.get(User, e.user_id)
    return {
        "url": CHAKSHU_URL,
        "cybercrime_url": CYBERCRIME_URL,
        "fields": {
            "Medium": "Call",
            "Category": "Impersonation of family member (AI voice clone)" if e.claimed_name else "Suspected fraud call",
            "Suspected number": e.number or "",
            "Date": f"{e.created_at:%d-%m-%Y}",
            "Time": f"{e.created_at:%H:%M} UTC",
            "Complainant mobile": victim.phone if victim else "",
            "Description": complaint_text(e, victim),
        },
        "complaint_text": complaint_text(e, victim),
    }


@router.get("/{evidence_id}/report.html", response_class=HTMLResponse)
def report_html(evidence_id: str, me: User = Depends(current_user), s: Session = Depends(get_session)):
    e = _owned(s, me, evidence_id)
    if not e:
        raise HTTPException(404)
    victim = s.get(User, e.user_id)
    r = json.loads(e.report_json or "{}")
    rows = "".join(f"<tr><td>{html.escape(x.get('label', ''))}</td><td>{'' if x.get('score') is None else round(x['score'] * 100)}"
                   f"</td><td>{html.escape(x.get('en', ''))}</td></tr>" for x in r.get("risk", {}).get("reasons", []))
    esc = html.escape
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>VoiceGuard evidence {esc(e.id)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#111}}
h1{{font-size:22px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px;text-align:left;font-size:14px}}
.big{{font-size:28px;font-weight:700;color:{'#b91c1c' if e.level == 'danger' else '#b45309' if e.level == 'caution' else '#15803d'}}}
.box{{background:#f5f5f5;padding:12px;border-radius:8px;white-space:pre-wrap}}</style></head><body>
<h1>VoiceGuard - Suspected Scam Call Evidence</h1>
<p>Evidence ID <b>{esc(e.id)}</b> &middot; saved {e.created_at:%d %b %Y %H:%M} UTC</p>
<table><tr><th>Victim</th><td>{esc(victim.name if victim else '')} ({esc(victim.phone if victim else '')})</td></tr>
<tr><th>Caller number</th><td>{esc(e.number or 'unknown')}</td></tr>
<tr><th>Pretended to be</th><td>{esc(e.claimed_name or '-')}</td></tr>
<tr><th>Source</th><td>{esc(e.source)}</td></tr>
<tr><th>Risk score</th><td class=big>{e.risk_score if e.risk_score is not None else '-'}/100 {esc((e.level or '').upper())}</td></tr></table>
<h2>Signals</h2><table><tr><th>Check</th><th>Score</th><th>Finding</th></tr>{rows}</table>
<h2>What the caller said</h2><div class=box>{esc(e.transcript or 'No transcript')}</div>
<h2>Complaint text</h2><div class=box>{esc(complaint_text(e, victim))}</div>
<p>Report: call <b>1930</b> (National Cyber Crime Helpline) &middot; <a href="{CYBERCRIME_URL}">cybercrime.gov.in</a>
&middot; <a href="{CHAKSHU_URL}">Chakshu (Sanchar Saathi)</a></p>
<p style="color:#666;font-size:12px">Generated by the VoiceGuard prototype. AI scores are indicators, not proof.</p>
</body></html>"""


class CyberCell(BaseModel):
    user_id: str
    evidence_id: str | None = None
    message: str = ""


@router.post("/cyber-cell")
async def family_calls_cyber_cell(body: CyberCell, me: User = Depends(current_user), s: Session = Depends(get_session)):
    """Ask the family to report on the victim's behalf (elders often can't navigate 1930 alone)."""
    require_self(me, body.user_id)
    u = me
    return await push_alert(s, family_id=u.family_id, from_user_id=u.id, kind="cyber_cell",
                            title=f"{u.name} needs help reporting a scam call",
                            body=body.message or "Please call 1930 / file at cybercrime.gov.in for them. Evidence is saved.",
                            payload={"evidence_id": body.evidence_id, "victim_name": u.name, "victim_phone": u.phone})
