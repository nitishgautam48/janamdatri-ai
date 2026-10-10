"""Maternal Health Passport: create/update, computed pregnancy dates, completion and the full view."""
from __future__ import annotations

import secrets
import string
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.asha.assignment import assign_asha
from app.core.enums import Role
from app.core.pregnancy import (
    TRIMESTER_LABELS,
    anc_target_date,
    edd_from_lmp,
    gestational_days,
    gestational_week,
    lmp_from_edd,
    next_anc,
    trimester,
)
from app.core.security import mask_phone, normalize_phone
from app.database.models import (
    AssessmentResult,
    EmergencyContact,
    Facility,
    HealthPassport,
    MedicalHistory,
    Mother,
    PregnancyProfile,
    Report,
    ReportFinding,
    User,
)
from app.gis.service import facility_dict, mother_location, nearby_facilities, resolve_location
from app.passport.schemas import STEPS, Completion, CompletionStep, PassportIn

STEP_TITLES = {
    "about_you": ("About You", "आपके बारे में"),
    "pregnancy_details": ("Pregnancy Details", "गर्भावस्था की जानकारी"),
    "medical_history": ("Medical History", "पहले की बीमारियाँ"),
    "reports": ("Upload Reports", "रिपोर्ट"),
    "emergency_contacts": ("Emergency contacts & facility", "आपातकालीन संपर्क"),
}


def _passport_number() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "JMD-" + "".join(secrets.choice(alphabet) for _ in range(8))


def get_or_create_passport(db: Session, mother: Mother, actor: User | None) -> tuple[HealthPassport, bool]:
    if mother.passport:
        return mother.passport, False
    p = HealthPassport(mother_id=mother.id, passport_number=_passport_number(),
                       created_by_user_id=actor.id if actor else None, skipped_steps=[])
    db.add(p)
    mother.passport = p
    return p, True


def apply_passport(db: Session, mother: Mother, body: PassportIn, actor: User | None) -> HealthPassport:
    passport, _ = get_or_create_passport(db, mother, actor)
    location_changed = False

    if body.about:
        data = body.about.model_dump(exclude_unset=True)
        for key in ("village", "pincode", "latitude", "longitude"):
            if key in data and data[key] != getattr(mother, key):
                location_changed = True
        for k, v in data.items():
            setattr(mother, k, v)
        if mother.user and data.get("name"):
            mother.user.full_name = data["name"]
        if mother.user and data.get("language"):
            mother.user.language = data["language"]

    if body.pregnancy:
        preg = mother.pregnancy or PregnancyProfile(mother_id=mother.id)
        if mother.pregnancy is None:
            db.add(preg)
            mother.pregnancy = preg
        data = body.pregnancy.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(preg, k, v)
        if preg.lmp and not data.get("edd"):
            preg.edd = edd_from_lmp(preg.lmp)
            preg.edd_source = preg.edd_source if data.get("edd_source") else "lmp"
        elif preg.edd and not preg.lmp:
            preg.lmp = lmp_from_edd(preg.edd)
            preg.edd_source = data.get("edd_source") or "manual"
        if preg.first_pregnancy is True:
            preg.gravida = preg.gravida or 1
            preg.parity = 0 if preg.parity is None else preg.parity
        if not data.get("next_anc_date"):
            week = gestational_week(preg.lmp)
            item = next_anc(week, preg.anc_visits_completed or 0)
            preg.next_anc_date = anc_target_date(preg.lmp, item)

    if body.medical_history:
        mh = mother.medical_history or MedicalHistory(mother_id=mother.id)
        if mother.medical_history is None:
            db.add(mh)
            mother.medical_history = mh
        for k, v in body.medical_history.model_dump(exclude_unset=True).items():
            setattr(mh, k, v if v is not None else ([] if k in ("conditions", "complication_details", "allergies",
                                                                  "current_medications") else v))

    if body.emergency_contacts is not None:
        mother.emergency_contacts.clear()
        db.flush()
        for i, c in enumerate(body.emergency_contacts[:5]):
            mother.emergency_contacts.append(EmergencyContact(
                name=c.name, relation=c.relation, phone=normalize_phone(c.phone),
                is_primary=c.is_primary or i == 0))

    for field in ("blood_group", "height_cm", "pre_pregnancy_weight_kg"):
        if getattr(body, field) is not None:
            setattr(passport, field, getattr(body, field))
    if body.skip_steps:
        passport.skipped_steps = sorted(set(passport.skipped_steps or []) | set(body.skip_steps))
    if body.preferred_facility_id and db.get(Facility, body.preferred_facility_id):
        mother.preferred_facility_id = body.preferred_facility_id

    # Fill coordinates and health-system codes from the gazetteer.
    if location_changed or (mother.latitude is None and (mother.pincode or mother.village)):
        gps = bool(body.about and body.about.latitude is not None and body.about.longitude is not None)
        loc = resolve_location(db, lat=mother.latitude if gps else None, lon=mother.longitude if gps else None,
                               pincode=mother.pincode, village=mother.village)
        if loc:
            mother.latitude, mother.longitude = loc.latitude, loc.longitude
            mother.district = mother.district or loc.district
            mother.state = mother.state or loc.state
            mother.sub_centre_code = loc.sub_centre_code or mother.sub_centre_code
            mother.sector_code = loc.sector_code or mother.sector_code
            mother.pincode = mother.pincode or loc.pincode
    db.flush()

    if mother.latitude is not None and not mother.preferred_facility_id:
        loc = mother_location(db, mother)
        phc = nearby_facilities(db, loc, types=["PHC"], limit=1) if loc else []
        if phc:
            mother.preferred_facility_id = uuid.UUID(phc[0]["id"])

    # Auto-assign an ASHA as soon as we know where she lives (or re-match if she moved).
    if mother.village or mother.pincode or mother.latitude is not None:
        assign_asha(db, mother, force=location_changed and mother.registered_by_asha_id is None)
    return passport


def completion(db: Session, mother: Mother) -> Completion:
    passport = mother.passport
    skipped = set(passport.skipped_steps or []) if passport else set()
    report_count = db.scalar(select(func.count()).select_from(Report).where(Report.mother_id == mother.id)) or 0
    preg = mother.pregnancy
    done = {
        "about_you": bool(mother.name and mother.age and (mother.village or mother.pincode)),
        "pregnancy_details": bool(preg and (preg.lmp or preg.edd)),
        "medical_history": mother.medical_history is not None,
        "reports": report_count > 0,
        "emergency_contacts": len(mother.emergency_contacts) > 0,
    }
    steps = [CompletionStep(key=k, title=STEP_TITLES[k][0], title_hi=STEP_TITLES[k][1],
                            done=done[k] or k in skipped, skipped=(k in skipped and not done[k])) for k in STEPS]
    # Skipped steps don't nag (next_step moves on) but don't count as filled either.
    completed = sum(1 for s in steps if s.done and not s.skipped)
    return Completion(profile_strength=completed * 100 // len(STEPS), completed_steps=completed,
                      total_steps=len(STEPS), next_step=next((s.key for s in steps if not s.done), None), steps=steps)


def pregnancy_view(preg: PregnancyProfile | None) -> dict | None:
    if not preg:
        return None
    week = gestational_week(preg.lmp)
    tri = trimester(week)
    days = gestational_days(preg.lmp)
    return {
        "lmp": preg.lmp, "edd": preg.edd, "edd_source": preg.edd_source,
        "current_week": week, "current_day": (days % 7) if days is not None else None,
        "weeks_to_go": max(40 - week, 0) if week is not None else None,
        "days_to_edd": (preg.edd - date.today()).days if preg.edd else None,
        "trimester": tri, "trimester_label": TRIMESTER_LABELS[tri][0] if tri else None,
        "trimester_label_hi": TRIMESTER_LABELS[tri][1] if tri else None,
        "first_pregnancy": preg.first_pregnancy, "gravida": preg.gravida, "parity": preg.parity,
        "multiple_pregnancy": preg.multiple_pregnancy, "anc_visits_completed": preg.anc_visits_completed,
        "next_anc_date": preg.next_anc_date,
    }


def asha_brief(asha) -> dict | None:
    if not asha:
        return None
    return {"id": str(asha.id), "name": asha.name, "sub_centre": asha.sub_centre_name or asha.sub_centre_code,
            "verified": asha.verified, "available": asha.available}


def passport_view(db: Session, mother: Mother, viewer_role: str) -> dict:
    passport = mother.passport
    own = viewer_role == Role.MOTHER
    reports = db.scalars(select(Report).where(Report.mother_id == mother.id)
                         .order_by(Report.created_at.desc()).limit(10)).all()
    results = db.scalars(select(AssessmentResult).where(AssessmentResult.mother_id == mother.id)
                         .order_by(AssessmentResult.created_at.desc()).limit(10)).all()
    flagged = db.scalars(select(ReportFinding).where(ReportFinding.mother_id == mother.id,
                                                     ReportFinding.flag.in_(["low", "high", "critical"]))
                         .order_by(ReportFinding.created_at.desc()).limit(5)).all()
    facility = db.get(Facility, mother.preferred_facility_id) if mother.preferred_facility_id else None
    mh = mother.medical_history
    return {
        "mother_id": str(mother.id),
        "passport_number": passport.passport_number if passport else None,
        "about": {
            "name": mother.name, "age": mother.age, "language": mother.language, "village": mother.village,
            "district": mother.district, "state": mother.state, "pincode": mother.pincode, "address": mother.address,
            "location": {"latitude": mother.latitude, "longitude": mother.longitude}
            if mother.latitude is not None else None,
            "rch_id": mother.rch_id, "status": mother.status,
            "phone": mask_phone(mother.user.phone) if mother.user else None,
        },
        "pregnancy": pregnancy_view(mother.pregnancy),
        "medical_history": {
            "conditions": mh.conditions, "past_complications": mh.past_complications,
            "complication_details": mh.complication_details, "previous_c_section": mh.previous_c_section,
            "allergies": mh.allergies, "current_medications": mh.current_medications, "notes": mh.notes,
        } if mh else None,
        "vitals_baseline": {"blood_group": passport.blood_group, "height_cm": passport.height_cm,
                            "pre_pregnancy_weight_kg": passport.pre_pregnancy_weight_kg} if passport else None,
        "reports": {
            "count": db.scalar(select(func.count()).select_from(Report).where(Report.mother_id == mother.id)) or 0,
            "recent": [{"id": str(r.id), "type": r.report_type, "status": r.status, "uploaded_at": r.created_at,
                        "report_date": r.report_date} for r in reports],
            "flags": [f"{f.label} {f.value_text or ''} ({f.flag})".replace("  ", " ") for f in flagged],
        },
        "assessment_history": [{"id": str(r.assessment_id), "date": r.created_at, "category": r.category,
                                "label": _label(r.category)} for r in results],
        "emergency_contacts": [{"id": str(c.id), "name": c.name, "relation": c.relation,
                                "phone": c.phone if own else mask_phone(c.phone), "is_primary": c.is_primary}
                               for c in mother.emergency_contacts],
        "assigned_asha": asha_brief(mother.assigned_asha),
        "backup_asha": asha_brief(mother.backup_asha),
        "preferred_facility": facility_dict(facility) if facility else None,
        "completion": completion(db, mother).model_dump(),
    }


def _label(category: str) -> str:
    from app.core.enums import CareLevel

    return CareLevel.from_key(category).label
