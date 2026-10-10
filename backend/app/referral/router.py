from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.core.audit import audit
from app.core.enums import ReferralStatus
from app.core.exceptions import Forbidden, NotFound
from app.database.models import Referral
from app.referral import service

router = APIRouter(prefix="/referral", tags=["Referral Summary"])


class ReferralIn(BaseModel):
    mother_id: uuid.UUID | None = None
    assessment_id: uuid.UUID | None = Field(None, description="Defaults to the latest assessment")
    care_level: str | None = Field(None, description="Override: routine_monitoring | needs_attention | medical_review | immediate_care")
    facility_id: uuid.UUID | None = Field(None, description="Override the auto-matched facility")
    reason: str | None = Field(None, max_length=1000)
    send_to_mother: bool = True


class StatusIn(BaseModel):
    status: ReferralStatus


def _get(db, p, referral_id) -> Referral:
    r = db.get(Referral, referral_id)
    if not r:
        raise NotFound("Referral not found")
    resolve_mother(db, p, r.mother_id)
    return r


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate(body: ReferralIn, db: DB, p: CurrentPrincipal):
    mother = resolve_mother(db, p, body.mother_id)
    r = service.generate(db, p, mother, assessment_id=body.assessment_id, care_level=body.care_level,
                         facility_id=body.facility_id, reason=body.reason, send_to_mother=body.send_to_mother)
    audit(db, p.id, "referral.generate", "referral", r.id, mother_id=str(mother.id))
    db.commit()
    return service.referral_dict(r)


@router.get("/history")
def history(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    rows = db.scalars(select(Referral).where(Referral.mother_id == mother.id).order_by(Referral.created_at.desc())).all()
    return {"count": len(rows), "referrals": [service.referral_dict(r) for r in rows]}


@router.get("/shared/{token}")
def shared(token: str, db: DB, format: str = Query("json", pattern="^(json|html|pdf)$")):
    """Public, read-only, expiring link for 'Show Doctor' / Share."""
    r = service.by_token(db, token)
    if format == "html":
        return HTMLResponse(service.render_html(r))
    if format == "pdf":
        return Response(service.render_pdf(r), media_type="application/pdf")
    return service.referral_dict(r, include_share=False)


@router.get("/{referral_id}")
def get_one(referral_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    return service.referral_dict(_get(db, p, referral_id))


@router.get("/{referral_id}/pdf")
def pdf(referral_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    r = _get(db, p, referral_id)
    return Response(service.render_pdf(r), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{r.referral_number}.pdf"'})


@router.get("/{referral_id}/print", response_class=HTMLResponse)
def print_view(referral_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    return HTMLResponse(service.render_html(_get(db, p, referral_id)))


@router.patch("/{referral_id}/status")
def set_status(referral_id: uuid.UUID, body: StatusIn, db: DB, p: CurrentPrincipal):
    r = _get(db, p, referral_id)
    if p.role == "mother" and body.status not in (ReferralStatus.ACKNOWLEDGED, ReferralStatus.COMPLETED):
        raise Forbidden("Mothers can mark a referral acknowledged or completed")
    r.status = body.status.value
    db.commit()
    return service.referral_dict(r)
