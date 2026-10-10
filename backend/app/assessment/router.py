from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.assessment import service
from app.assessment.schemas import AssessmentIn, AssessmentResultOut, SymptomOption
from app.assessment.symptoms import SYMPTOMS
from app.core.exceptions import NotFound
from app.database.models import Assessment

router = APIRouter(prefix="/assessment", tags=["Assessment Engine"])


@router.get("/symptoms", response_model=list[SymptomOption])
def symptom_options():
    return [SymptomOption(code=c, label=en, label_hi=hi) for c, (en, hi) in SYMPTOMS.items()]


@router.post("", response_model=AssessmentResultOut, status_code=status.HTTP_201_CREATED)
def create_assessment(body: AssessmentIn, db: DB, p: CurrentPrincipal):
    """Vitals → symptoms → history → (EPDS) → one of four care levels with findings and a facility."""
    mother = resolve_mother(db, p, body.mother_id)
    return service.run_assessment(db, mother, body, p.user, p.role)


@router.get("/history", response_model=list[AssessmentResultOut])
def history(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None), limit: int = Query(20, le=100)):
    mother = resolve_mother(db, p, mother_id)
    rows = db.scalars(select(Assessment).where(Assessment.mother_id == mother.id)
                      .order_by(Assessment.created_at.desc()).limit(limit)).all()
    return [service.to_out(a) for a in rows if a.result]


@router.get("/latest", response_model=AssessmentResultOut)
def latest(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    a = db.scalar(select(Assessment).where(Assessment.mother_id == mother.id).order_by(Assessment.created_at.desc()))
    if not a or not a.result:
        raise NotFound("No assessment yet", code="assessment_not_found")
    return service.to_out(a)


@router.get("/{assessment_id}", response_model=AssessmentResultOut)
def get_one(assessment_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    a = db.get(Assessment, assessment_id)
    if not a or not a.result:
        raise NotFound("Assessment not found")
    resolve_mother(db, p, a.mother_id)  # access check
    return service.to_out(a)
