"""Runs the assessment engine for a mother, persists the result and informs her ASHA."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.risk_model import get_risk_model
from app.assessment.engine import EngineInput, evaluate
from app.assessment.schemas import AssessmentIn, AssessmentResultOut, CareLevelOut, Finding
from app.core.enums import CARE_LEVEL_KEYS, CareLevel, NotificationType, Role
from app.core.pregnancy import gestational_week
from app.database.base import utcnow
from app.database.models import AshaWorker, Assessment, AssessmentResult, Mother, ReportFinding, User, Visit
from app.gis.service import mother_location, recommend_facility
from app.notifications.service import notify
from app.notifications.sms import send_sms

REPORT_LOOKBACK_DAYS = 60


def _latest_finding(db: Session, mother_id, code: str) -> ReportFinding | None:
    since = utcnow() - timedelta(days=REPORT_LOOKBACK_DAYS)
    return db.scalar(select(ReportFinding).where(
        ReportFinding.mother_id == mother_id, ReportFinding.code == code, ReportFinding.valid.is_(True),
        ReportFinding.created_at >= since).order_by(ReportFinding.created_at.desc()))


def hb_history(db: Session, mother_id) -> list[tuple[date, float]]:
    points: list[tuple[date, float]] = []
    for f in db.scalars(select(ReportFinding).where(ReportFinding.mother_id == mother_id, ReportFinding.code == "hemoglobin",
                                                     ReportFinding.valid.is_(True))).all():
        if f.value_num is not None:
            points.append(((f.observed_on or f.created_at.date()), f.value_num))
    for v in db.scalars(select(Visit).where(Visit.mother_id == mother_id, Visit.hemoglobin.is_not(None))).all():
        points.append((v.visit_date.date(), v.hemoglobin))
    for a in db.scalars(select(Assessment).where(Assessment.mother_id == mother_id)).all():
        hb = (a.inputs or {}).get("hemoglobin")
        if hb is not None and (a.inputs or {}).get("hemoglobin_source") == "entered":
            points.append((a.created_at.date(), float(hb)))
    points.sort(key=lambda p: p[0])
    return points[-6:]


def build_input(db: Session, mother: Mother, body: AssessmentIn) -> EngineInput:
    v = body.vitals
    preg, mh = mother.pregnancy, mother.medical_history
    hb, hb_src = v.hemoglobin, "entered" if v.hemoglobin is not None else None
    sbp, dbp = v.bp_systolic, v.bp_diastolic
    sugar_f, sugar_r, ogtt = v.blood_sugar_fasting, v.blood_sugar_random, v.blood_sugar_ogtt_2h
    if body.use_latest_reports:
        if hb is None and (f := _latest_finding(db, mother.id, "hemoglobin")):
            hb, hb_src = f.value_num, f"report:{f.report_id}"
        if sbp is None and (f := _latest_finding(db, mother.id, "bp")):
            sbp, dbp = int(f.value_num), int(f.value_num2) if f.value_num2 else None
        if sugar_f is None and (f := _latest_finding(db, mother.id, "blood_sugar_fasting")):
            sugar_f = f.value_num
        if sugar_r is None and (f := _latest_finding(db, mother.id, "blood_sugar_random")):
            sugar_r = f.value_num
        if ogtt is None and (f := _latest_finding(db, mother.id, "blood_sugar_ogtt")):
            ogtt = f.value_num
    last_visit = db.scalar(select(Visit).where(Visit.mother_id == mother.id, Visit.weight_kg.is_not(None))
                           .order_by(Visit.visit_date.desc()))
    return EngineInput(
        week=gestational_week(preg.lmp) if preg else None, age=mother.age,
        bp_systolic=sbp, bp_diastolic=dbp, weight_kg=v.weight_kg, hemoglobin=hb, hemoglobin_source=hb_src,
        sugar_fasting=sugar_f, sugar_random=sugar_r, sugar_ogtt_2h=ogtt, temperature_c=v.temperature_c,
        pulse=v.pulse, urine_protein=v.urine_protein, fetal_heart_rate=v.fetal_heart_rate, symptoms=body.symptoms,
        taking_ifa=body.history.taking_ifa, bleeding_this_week=body.history.bleeding_this_week,
        baby_moving_normally=body.history.baby_moving_normally, epds=body.epds,
        conditions=list(mh.conditions or []) if mh else [], past_complications=mh.past_complications if mh else None,
        previous_c_section=bool(mh and mh.previous_c_section), multiple_pregnancy=bool(preg and preg.multiple_pregnancy),
        first_pregnancy=preg.first_pregnancy if preg else None, hb_history=hb_history(db, mother.id),
        previous_weight=(last_visit.visit_date.date(), last_visit.weight_kg) if last_visit else None,
        next_anc_date=preg.next_anc_date if preg else None,
        anc_visits_completed=preg.anc_visits_completed if preg else 0,
    )


def _inform(db: Session, mother: Mother, level: CareLevel, assessment: Assessment, facility: dict | None) -> bool:
    if level < CareLevel.NEEDS_ATTENTION:
        return False
    asha = db.get(AshaWorker, mother.assigned_asha_id) if mother.assigned_asha_id else None
    name = mother.name or "A mother"
    week = assessment.pregnancy_week
    title = {CareLevel.IMMEDIATE_CARE: "Emergency: immediate care recommended",
             CareLevel.MEDICAL_REVIEW: "Medical review recommended",
             CareLevel.NEEDS_ATTENTION: "Follow-up needed this week"}[level]
    body = f"{name}{f', wk {week}' if week else ''}: {level.label}."
    if facility:
        body += f" Suggested: {facility['name']}."
    data = {"mother_id": str(mother.id), "assessment_id": str(assessment.id), "care_level": level.key}
    priority = {CareLevel.IMMEDIATE_CARE: "urgent", CareLevel.MEDICAL_REVIEW: "high"}.get(level, "normal")
    informed = False
    for a in filter(None, [asha, db.get(AshaWorker, mother.backup_asha_id) if level == CareLevel.IMMEDIATE_CARE
                           and mother.backup_asha_id else None]):
        if a.user_id:
            notify(db, a.user_id, NotificationType.EMERGENCY if level == CareLevel.IMMEDIATE_CARE else NotificationType.SYSTEM,
                   title, body, data=data, priority=priority, sms=level == CareLevel.IMMEDIATE_CARE)
        elif level == CareLevel.IMMEDIATE_CARE:
            send_sms(a.phone, f"Janamdatri: {title}. {body}")
        informed = True
    if level == CareLevel.IMMEDIATE_CARE:
        for c in mother.emergency_contacts[:1]:
            where = facility["name"] if facility else "nearest hospital"
            send_sms(c.phone, f"Janamdatri: {name} needs to go to hospital now ({where}). Call 108 for an ambulance.")
    return informed


def run_assessment(db: Session, mother: Mother, body: AssessmentIn, actor: User | None, actor_role: str) -> AssessmentResultOut:
    x = build_input(db, mother, body)
    loc = mother_location(db, mother)
    # Run once to know the level, then route to a facility, then phrase recommendations with its name.
    first = evaluate(x, model=get_risk_model())
    facility = recommend_facility(db, loc, first.level) if loc else None
    result = evaluate(x, model=get_risk_model(), facility_name=facility["name"] if facility else None)
    assessment = Assessment(
        mother_id=mother.id, performed_by_user_id=actor.id if actor else None,
        source="asha" if actor_role == Role.ASHA else "self", pregnancy_week=x.week,
        inputs={"bp_systolic": x.bp_systolic, "bp_diastolic": x.bp_diastolic, "weight_kg": x.weight_kg,
                "hemoglobin": x.hemoglobin, "hemoglobin_source": x.hemoglobin_source,
                "blood_sugar_fasting": x.sugar_fasting, "blood_sugar_random": x.sugar_random,
                "blood_sugar_ogtt_2h": x.sugar_ogtt_2h, "temperature_c": x.temperature_c, "pulse": x.pulse,
                "urine_protein": x.urine_protein, "fetal_heart_rate": x.fetal_heart_rate, "symptoms": x.symptoms,
                "history": body.history.model_dump(), "epds_given": bool(x.epds)},
    )
    db.add(assessment)
    db.flush()
    db.add(AssessmentResult(
        assessment_id=assessment.id, mother_id=mother.id, category=result.level.key, level=int(result.level),
        signals=[s.as_dict() for s in result.signals], what_we_noticed=result.noticed,
        possible_concerns=result.concerns, recommendations=result.recommendations,
        referral_suggestion=result.referral_suggestion, facility_type=facility["type"] if facility else None,
        facility=facility, follow_up=result.follow_up,
    ))
    informed = _inform(db, mother, result.level, assessment, facility)
    db.commit()
    db.refresh(assessment)
    return to_out(assessment, informed)


def to_out(a: Assessment, asha_informed: bool | None = None) -> AssessmentResultOut:
    r = a.result
    lvl = CareLevel(r.level)
    inputs = dict(a.inputs or {})
    inputs.pop("epds_given", None)
    return AssessmentResultOut(
        assessment_id=a.id, mother_id=a.mother_id, date=a.created_at, pregnancy_week=a.pregnancy_week,
        result=CareLevelOut(key=lvl.key, label=lvl.label, label_hi=lvl.label_hi, rank=int(lvl)),
        ladder=list(CARE_LEVEL_KEYS.values()),
        what_we_noticed=[Finding(**n) for n in r.what_we_noticed],
        possible_concerns=[Finding(**n) for n in r.possible_concerns],
        recommendations=[Finding(**n) for n in r.recommendations],
        referral_suggestion=r.referral_suggestion, follow_up=r.follow_up, nearest_facility=r.facility,
        asha_informed=asha_informed if asha_informed is not None else lvl >= CareLevel.NEEDS_ATTENTION,
        inputs_used=inputs,
    )
