"""ASHA dashboard queries: assigned mothers, ANC due, follow-ups, emergencies, referrals."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import mothers_for_asha_query
from app.core.enums import CareLevel, EmergencyStatus, ReferralStatus
from app.core.pregnancy import gestational_week
from app.core.security import mask_phone
from app.database.base import utcnow
from app.database.models import (
    AshaWorker,
    AssessmentResult,
    EmergencyEvent,
    Message,
    Mother,
    Referral,
    Report,
    Visit,
)
from app.messaging.service import presence


def _latest_result(db: Session, mother_id) -> AssessmentResult | None:
    return db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == mother_id)
                     .order_by(AssessmentResult.created_at.desc()))


def _latest_visit(db: Session, mother_id) -> Visit | None:
    return db.scalar(select(Visit).where(Visit.mother_id == mother_id).order_by(Visit.visit_date.desc()))


def _short_finding(r: AssessmentResult | None) -> str | None:
    if not r:
        return None
    abnormal = [n["text"] for n in r.what_we_noticed if n.get("level") != "routine_monitoring"]
    if not abnormal:
        return None
    t = abnormal[0]
    if t.lower().startswith(("hemoglobin", "hb ")):
        return "low Hb"
    return t[:40]


def mother_row(db: Session, m: Mother, asha: AshaWorker | None = None) -> dict:
    week = gestational_week(m.pregnancy.lmp) if m.pregnancy else None
    r = _latest_result(db, m.id)
    v = _latest_visit(db, m.id)
    lvl = CareLevel(r.level) if r else None
    finding = _short_finding(r)
    short = {CareLevel.ROUTINE_MONITORING: "Routine", CareLevel.NEEDS_ATTENTION: "Needs attention",
             CareLevel.MEDICAL_REVIEW: "Medical review", CareLevel.IMMEDIATE_CARE: "Immediate care"}
    subtitle = (f"{short[lvl]}" + (f" · {finding}" if finding else "")) if lvl is not None else "No health check yet"
    if lvl == CareLevel.ROUTINE_MONITORING and m.pregnancy:
        subtitle = f"Routine · ANC {min((m.pregnancy.anc_visits_completed or 0) + 1, 4)}"
    return {
        "mother_id": str(m.id), "name": m.name, "age": m.age, "village": m.village, "week": week,
        "title": f"{m.name or 'Mother'}" + (f" · wk {week}" if week is not None else ""), "subtitle": subtitle,
        "care_level": lvl.key if lvl is not None else None, "last_assessment_at": r.created_at if r else None,
        "last_visit_at": v.visit_date if v else None, "next_visit_date": v.next_visit_date if v else None,
        "next_anc_date": m.pregnancy.next_anc_date if m.pregnancy else None,
        "is_backup": bool(asha and m.backup_asha_id == asha.id and m.assigned_asha_id != asha.id),
        "phone": mask_phone(m.user.phone) if m.user else None, "status": m.status,
    }


def classify(db: Session, m: Mother, today: date) -> set[str]:
    tags = set()
    preg = m.pregnancy
    week = gestational_week(preg.lmp) if preg else None
    if preg and preg.next_anc_date and preg.next_anc_date <= today + timedelta(days=7):
        tags.add("anc_due")
    elif preg and week is not None and week > 12 and (preg.anc_visits_completed or 0) == 0:
        tags.add("anc_due")
    r = _latest_result(db, m.id)
    v = _latest_visit(db, m.id)
    if r and r.level >= CareLevel.NEEDS_ATTENTION and (not v or v.visit_date < r.created_at):
        tags.add("follow_up")
    if v and v.next_visit_date and v.next_visit_date <= today:
        tags.add("follow_up")
        tags.add("visit_today")
    if r and r.level >= CareLevel.MEDICAL_REVIEW and (not v or v.visit_date < r.created_at):
        tags.add("visit_today")
        tags.add("high_risk")
    return tags


def dashboard(db: Session, asha: AshaWorker) -> dict:
    today = date.today()
    mothers = db.scalars(mothers_for_asha_query(asha)).all()
    tagged = [(m, classify(db, m, today)) for m in mothers]
    ids = [m.id for m in mothers]
    emergencies = [e for e in db.scalars(select(EmergencyEvent).where(
        EmergencyEvent.status.in_([EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED])
    ).order_by(EmergencyEvent.created_at.desc()).limit(100)).all()
        if (e.mother_id in ids) or any(s.get("asha_id") == str(asha.id) for s in e.escalation_chain or [])]
    referrals = db.scalar(select(func.count()).select_from(Referral).where(
        Referral.mother_id.in_(ids), Referral.status.in_([ReferralStatus.GENERATED, ReferralStatus.SENT]))) if ids else 0
    unread = db.scalar(select(func.count()).select_from(Message).where(
        Message.asha_id == asha.id, Message.sender_role == "mother", Message.read_at.is_(None))) or 0
    new_reports = db.scalar(select(func.count()).select_from(Report).where(
        Report.mother_id.in_(ids), Report.created_at >= utcnow() - timedelta(days=7))) if ids else 0
    visits_today = [mother_row(db, m, asha) for m, t in tagged if "visit_today" in t]
    visits_today.sort(key=lambda r: -(CareLevel.from_key(r["care_level"]) if r["care_level"] else -1))

    def ev_row(e: EmergencyEvent) -> dict:
        m = db.get(Mother, e.mother_id) if e.mother_id else None
        week = gestational_week(m.pregnancy.lmp) if m and m.pregnancy else None
        mins = int((utcnow() - (e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=utcnow().tzinfo))).total_seconds() // 60)
        return {"emergency_id": str(e.id), "status": e.status, "mother_id": str(m.id) if m else None,
                "title": f"{(m.name or 'Mother').split(' ')[0] if m else 'Unregistered caller'}" + (f", wk {week}" if week else "")
                         + (f" · {e.emergency_type}" if e.emergency_type else "") + f" · {mins} min ago",
                "location": {"latitude": e.latitude, "longitude": e.longitude, "text": e.location_text}}

    return {
        "greeting": f"नमस्ते, {asha.name.split(' ')[0]} didi",
        "asha": {"name": asha.name, "sub_centre": asha.sub_centre_name or asha.sub_centre_code, "verified": asha.verified,
                 "available": asha.available, "presence": presence(asha)},
        "counts": {"assigned_mothers": len(mothers), "todays_visits": len(visits_today),
                   "anc_due": sum(1 for _, t in tagged if "anc_due" in t),
                   "follow_ups": sum(1 for _, t in tagged if "follow_up" in t), "referrals": referrals or 0,
                   "emergencies": len(emergencies), "unread_messages": unread, "new_reports": new_reports or 0},
        "emergency_cases": [ev_row(e) for e in emergencies],
        "todays_visits": visits_today,
        "actions": ["register_mother", "visit_notes", "referral", "message"],
    }


FILTERS = {"anc_due", "follow_up", "high_risk", "visit_today"}


def list_mothers(db: Session, asha: AshaWorker, filter_: str | None, q: str | None) -> list[dict]:
    today = date.today()
    rows = []
    for m in db.scalars(mothers_for_asha_query(asha).order_by(Mother.name)).all():
        if q and q.lower() not in (m.name or "").lower() and q.lower() not in (m.village or "").lower():
            continue
        if filter_ and filter_ not in classify(db, m, today):
            continue
        rows.append(mother_row(db, m, asha))
    return rows
