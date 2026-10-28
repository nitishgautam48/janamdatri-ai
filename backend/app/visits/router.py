"""Home Visit Engine: ASHA records BP, weight, Hb, symptoms, notes and the next visit."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, File, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.ai.llm import get_llm
from app.ai.whisper import get_whisper
from app.api.deps import DB, CurrentAsha, CurrentPrincipal, resolve_mother
from app.assessment import service as assessment_service
from app.assessment.schemas import AssessmentIn, HistoryAnswers, Vitals
from app.assessment.symptoms import SYMPTOMS
from app.core.audit import audit
from app.core.enums import CareLevel, Role
from app.core.exceptions import NotFound, ValidationFailed
from app.core.pregnancy import anc_target_date, gestational_week, next_anc
from app.core.storage import AUDIO_TYPES, absolute_path, save_upload
from app.database.base import utcnow
from app.database.models import Visit
from app.visits import understand as notes

router = APIRouter(prefix="/visit", tags=["Home Visits"])
NEXT_VISIT_DAYS = {CareLevel.IMMEDIATE_CARE: 1, CareLevel.MEDICAL_REVIEW: 3, CareLevel.NEEDS_ATTENTION: 7,
                   CareLevel.ROUTINE_MONITORING: 30}


class VisitIn(BaseModel):
    mother_id: uuid.UUID
    visit_date: datetime | None = None
    visit_type: str = Field("home", pattern="^(home|anc|postnatal|followup)$")
    bp: str | None = Field(None, examples=["118/76"])
    bp_systolic: int | None = Field(None, ge=60, le=260)
    bp_diastolic: int | None = Field(None, ge=30, le=180)
    weight_kg: float | None = Field(None, ge=25, le=200)
    hemoglobin: float | None = Field(None, ge=2, le=20)
    symptoms: list[str] = []
    checklist: dict = Field(default_factory=dict, examples=[{"ifa_tablets_given": 30, "diet_counselling": True,
                                                             "danger_signs_explained": True, "birth_preparedness_plan": False}])
    notes: str | None = Field(None, max_length=4000)
    next_visit_date: date | None = None
    anc_completed: bool = Field(False, description="Mark this as a completed ANC contact")
    run_assessment: bool = True
    taking_ifa: bool | None = None
    baby_moving_normally: bool | None = None

    @field_validator("symptoms")
    @classmethod
    def known(cls, v):
        bad = [s for s in v if s not in SYMPTOMS]
        if bad:
            raise ValueError(f"Unknown symptoms {bad}")
        return v

    def parsed_bp(self) -> tuple[int | None, int | None]:
        if self.bp:
            m = re.match(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s*$", self.bp)
            if not m:
                raise ValidationFailed("BP should look like 118/76", code="invalid_bp")
            return int(m.group(1)), int(m.group(2))
        return self.bp_systolic, self.bp_diastolic


def visit_dict(v: Visit) -> dict:
    return {"id": str(v.id), "mother_id": str(v.mother_id), "asha_id": str(v.asha_id), "visit_date": v.visit_date,
            "visit_type": v.visit_type, "bp": f"{v.bp_systolic}/{v.bp_diastolic}" if v.bp_systolic else None,
            "weight_kg": v.weight_kg, "hemoglobin": v.hemoglobin, "symptoms": v.symptoms, "checklist": v.checklist,
            "notes": v.notes, "voice_note": bool(v.voice_note_path), "next_visit_date": v.next_visit_date,
            "assessment_id": str(v.assessment_id) if v.assessment_id else None}


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create(body: VisitIn, db: DB, p: CurrentPrincipal, asha: CurrentAsha):
    mother = resolve_mother(db, p, body.mother_id)
    sbp, dbp = body.parsed_bp()
    v = Visit(mother_id=mother.id, asha_id=asha.id, visit_date=body.visit_date or utcnow(), visit_type=body.visit_type,
              bp_systolic=sbp, bp_diastolic=dbp, weight_kg=body.weight_kg, hemoglobin=body.hemoglobin,
              symptoms=body.symptoms, checklist=body.checklist, notes=body.notes)
    result = None
    if body.run_assessment and (any(x is not None for x in (sbp, body.hemoglobin, body.weight_kg)) or body.symptoms):
        result = assessment_service.run_assessment(db, mother, AssessmentIn(
            mother_id=mother.id, vitals=Vitals(bp_systolic=sbp, bp_diastolic=dbp, weight_kg=body.weight_kg,
                                               hemoglobin=body.hemoglobin),
            symptoms=body.symptoms, history=HistoryAnswers(taking_ifa=body.taking_ifa,
                                                           baby_moving_normally=body.baby_moving_normally)),
            p.user, Role.ASHA)
        v.assessment_id = result.assessment_id
    level = CareLevel(result.result.rank) if result else CareLevel.ROUTINE_MONITORING
    v.next_visit_date = body.next_visit_date or (date.today() + timedelta(days=NEXT_VISIT_DAYS[level]))
    preg = mother.pregnancy
    if preg and (body.anc_completed or body.visit_type == "anc"):
        preg.anc_visits_completed = (preg.anc_visits_completed or 0) + 1
        item = next_anc(gestational_week(preg.lmp), preg.anc_visits_completed)
        preg.next_anc_date = anc_target_date(preg.lmp, item, date.today() + timedelta(days=14))
    asha.last_active_at = utcnow()
    db.add(v)
    audit(db, p.id, "visit.create", "mother", mother.id)
    db.commit()
    out = visit_dict(v)
    out["assessment"] = result.model_dump() if result else None
    return out


class NotesIn(BaseModel):
    text: str = Field(min_length=2, max_length=4000)
    language: str | None = None


@router.post("/understand")
def understand_notes(body: NotesIn, asha: CurrentAsha):
    """Spoken or typed visit notes → visit-form fields (BP, Hb, weight, symptoms, checklist, next visit…), for the
    ASHA to check before saving. Nothing is saved here."""
    found = notes.understand(body.text, get_llm("visit"))
    used_model = found.pop("_model", False)
    hi = body.language == "hi"
    return {"fields": found, "summary": notes.summary(found, hi) or None,
            "generated_by": "rules+ai" if used_model else "rules"}


@router.post("/{visit_id}/voice-note")
async def voice_note(visit_id: uuid.UUID, db: DB, p: CurrentPrincipal, asha: CurrentAsha, file: UploadFile = File(...)):
    v = db.get(Visit, visit_id)
    if not v or v.asha_id != asha.id:
        raise NotFound("Visit not found")
    rel, _, _ = await save_upload(file, f"visits/{v.mother_id}", AUDIO_TYPES)
    v.voice_note_path = rel
    t = get_whisper().transcribe(absolute_path(rel), context="visit")
    if t.text:
        v.notes = ((v.notes + "\n") if v.notes else "") + f"[Voice note] {t.text}"
    db.commit()
    return visit_dict(v)


@router.get("/history")
def history(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None), limit: int = Query(50, le=200)):
    if p.role == Role.ASHA and not mother_id:
        from app.api.deps import get_current_asha

        asha = get_current_asha(db, p)
        rows = db.scalars(select(Visit).where(Visit.asha_id == asha.id).order_by(Visit.visit_date.desc()).limit(limit)).all()
    else:
        mother = resolve_mother(db, p, mother_id)
        rows = db.scalars(select(Visit).where(Visit.mother_id == mother.id).order_by(Visit.visit_date.desc()).limit(limit)).all()
    return {"count": len(rows), "visits": [visit_dict(v) for v in rows]}
