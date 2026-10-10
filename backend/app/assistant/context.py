"""Builds the mother's health context used to personalise AI answers."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import CareLevel
from app.core.pregnancy import gestational_week
from app.database.models import AssessmentResult, Mother, Report, ReportFinding


def mother_profile(db: Session, mother: Mother | None) -> dict:
    if not mother:
        return {}
    week = gestational_week(mother.pregnancy.lmp) if mother.pregnancy else None
    findings = db.scalars(select(ReportFinding).where(ReportFinding.mother_id == mother.id, ReportFinding.valid.is_(True),
                                                      ReportFinding.kind.in_(["lab_value", "vital"]))
                          .order_by(ReportFinding.created_at.desc()).limit(10)).all()
    latest: dict[str, ReportFinding] = {}
    for f in findings:
        latest.setdefault(f.code, f)
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == mother.id)
                       .order_by(AssessmentResult.created_at.desc()))
    report = db.scalar(select(Report).where(Report.mother_id == mother.id, Report.status == "analyzed")
                       .order_by(Report.created_at.desc()))
    return {
        "name": mother.name, "week": week, "language": mother.language,
        "conditions": list(mother.medical_history.conditions or []) if mother.medical_history else [],
        "latest": {code: {"label": f.label, "value": f.value_text, "num": f.value_num, "flag": f.flag,
                          "report_type": f.report.report_type if f.report else None,
                          "date": f.observed_on} for code, f in latest.items()},
        "latest_findings": [f"{f.label} {f.value_text} ({f.flag})" for f in latest.values() if f.flag],
        "latest_assessment": CareLevel(result.level).label if result else None,
        "latest_report": report,
        "has_asha": mother.assigned_asha_id is not None,
    }
