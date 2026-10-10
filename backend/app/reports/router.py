from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.core.audit import audit
from app.core.config import settings
from app.core.enums import ReportStatus, ReportType
from app.core.exceptions import Forbidden, NotFound
from app.core.storage import DOCUMENT_TYPES, absolute_path, delete_file, save_upload
from app.database.models import Report, ReportFinding
from app.reports import service

router = APIRouter(prefix="/reports", tags=["Reports & OCR"])


class AnalyzeIn(BaseModel):
    report_id: uuid.UUID
    client_text: str | None = Field(None, max_length=20000,
                                    description="Text read on the device, used when server OCR finds nothing")
    language: str | None = None


def _get_report(db, p, report_id: uuid.UUID) -> Report:
    r = db.get(Report, report_id)
    if not r:
        raise NotFound("Report not found")
    resolve_mother(db, p, r.mother_id)
    return r


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload(db: DB, p: CurrentPrincipal, file: UploadFile = File(...),
                 report_type: ReportType = Form(ReportType.OTHER), title: str | None = Form(None),
                 report_date: date | None = Form(None), mother_id: uuid.UUID | None = Form(None),
                 analyze: bool = Form(True), client_text: str | None = Form(None)):
    """Upload a photo or PDF (CBC, prescription, ultrasound, blood/lab report). Analysed straight away
    unless ``analyze=false`` or Celery is enabled (then it is queued)."""
    mother = resolve_mother(db, p, mother_id)
    rel, size, ctype = await save_upload(file, f"reports/{mother.id}", DOCUMENT_TYPES)
    r = Report(mother_id=mother.id, uploaded_by_user_id=p.id, report_type=report_type.value, title=title,
               file_path=rel, original_filename=(file.filename or "")[:255], content_type=ctype, size_bytes=size,
               report_date=report_date, status=ReportStatus.UPLOADED)
    db.add(r)
    db.flush()
    if mother.passport and "reports" in (mother.passport.skipped_steps or []):
        mother.passport.skipped_steps = [s for s in mother.passport.skipped_steps if s != "reports"]
    audit(db, p.id, "report.upload", "report", r.id, mother_id=str(mother.id))
    db.commit()
    if analyze:
        if settings.celery_enabled:
            from app.workers.tasks import analyze_report_task

            r.status = ReportStatus.PROCESSING
            db.commit()
            analyze_report_task.delay(str(r.id), client_text, mother.language)
        else:
            service.analyze_report(db, r, client_text, mother.language)
            db.commit()
    db.refresh(r)
    return service.report_dict(r)


@router.post("/analyze")
def analyze(body: AnalyzeIn, db: DB, p: CurrentPrincipal):
    r = _get_report(db, p, body.report_id)
    mother = resolve_mother(db, p, r.mother_id)
    service.analyze_report(db, r, body.client_text, body.language or mother.language)
    db.commit()
    db.refresh(r)
    return service.report_dict(r, include_text=True)


@router.get("/history")
def history(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None),
            report_type: ReportType | None = None, limit: int = Query(50, le=200)):
    mother = resolve_mother(db, p, mother_id)
    q = select(Report).where(Report.mother_id == mother.id)
    if report_type:
        q = q.where(Report.report_type == report_type.value)
    rows = db.scalars(q.order_by(Report.created_at.desc()).limit(limit)).all()
    return {"count": len(rows), "reports": [service.report_dict(r) for r in rows]}


@router.get("/trends")
def trends(db: DB, p: CurrentPrincipal, code: str = Query("hemoglobin"), mother_id: uuid.UUID | None = Query(None)):
    """Values over time for charts, e.g. the haemoglobin trend with its normal line."""
    mother = resolve_mother(db, p, mother_id)
    rows = db.scalars(select(ReportFinding).where(ReportFinding.mother_id == mother.id, ReportFinding.code == code,
                                                  ReportFinding.valid.is_(True))).all()
    points = sorted(({"date": f.observed_on or f.created_at.date(), "value": f.value_num, "flag": f.flag,
                      "report_id": str(f.report_id)} for f in rows if f.value_num is not None), key=lambda x: x["date"])
    normal = {"hemoglobin": {"value": 11.0, "label": "11.0 normal", "unit": "g/dL"}}.get(code)
    return {"code": code, "points": points, "normal_line": normal}


@router.get("/medications")
def medications(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    return {"medications": service.medication_schedule(db, mother.id)}


@router.get("/{report_id}")
def get_report(report_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    return service.report_dict(_get_report(db, p, report_id), include_text=True)


@router.get("/{report_id}/file")
def get_file(report_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    r = _get_report(db, p, report_id)
    path = absolute_path(r.file_path)
    if not path.exists():
        raise NotFound("File missing")
    return FileResponse(path, media_type=r.content_type, filename=r.original_filename or path.name)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    r = _get_report(db, p, report_id)
    if p.role not in ("mother", "admin") and r.uploaded_by_user_id != p.id:
        raise Forbidden("Only the mother or the uploader can delete a report")
    delete_file(r.file_path)
    db.delete(r)
    audit(db, p.id, "report.delete", "report", report_id)
    db.commit()
