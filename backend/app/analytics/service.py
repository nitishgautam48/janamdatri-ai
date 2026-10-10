"""Programme analytics (population level). Patient-facing results never show numbers; these are for
administrators and ASHA supervisors."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import CARE_LEVEL_KEYS
from app.core.pregnancy import gestational_week, trimester
from app.database.base import utcnow
from app.database.models import (
    AnalyticsSnapshot,
    AshaWorker,
    AssessmentResult,
    EmergencyEvent,
    Mother,
    Referral,
    ReportFinding,
    User,
    Visit,
)


def _mother_ids(db: Session, district: str | None = None, asha_id=None) -> list:
    q = select(Mother.id)
    if district:
        q = q.where(Mother.district.ilike(district))
    if asha_id:
        q = q.where((Mother.assigned_asha_id == asha_id) | (Mother.backup_asha_id == asha_id))
    return list(db.scalars(q).all())


def anemia_band(hb: float) -> str:
    return "severe" if hb < 7 else "moderate" if hb < 10 else "mild" if hb < 11 else "normal"


def latest_hb(db: Session, ids: list) -> dict:
    out: dict = {}
    if not ids:
        return out
    rows = db.execute(select(ReportFinding.mother_id, ReportFinding.value_num, ReportFinding.created_at)
                      .where(ReportFinding.code == "hemoglobin", ReportFinding.valid.is_(True),
                             ReportFinding.mother_id.in_(ids))).all()
    rows += db.execute(select(Visit.mother_id, Visit.hemoglobin, Visit.visit_date)
                       .where(Visit.hemoglobin.is_not(None), Visit.mother_id.in_(ids))).all()
    for mid, hb, _at in sorted(rows, key=lambda r: r[2]):
        if hb is not None:
            out[mid] = hb
    return out


def overview(db: Session, district: str | None = None, asha_id=None) -> dict:
    ids = _mother_ids(db, district, asha_id)
    mothers = db.scalars(select(Mother).where(Mother.id.in_(ids))).all() if ids else []
    tri = Counter()
    for m in mothers:
        w = gestational_week(m.pregnancy.lmp) if m.pregnancy else None
        tri[f"trimester_{trimester(w)}" if w is not None else "unknown"] += 1
    bands = Counter(anemia_band(v) for v in latest_hb(db, ids).values())
    since_30 = (utcnow() - timedelta(days=30)).replace(tzinfo=None)
    return {
        "generated_at": utcnow(), "scope": {"district": district, "asha_id": str(asha_id) if asha_id else None},
        "total_mothers": len(mothers),
        "by_status": dict(Counter(m.status for m in mothers)),
        "by_trimester": dict(tri),
        "registered_last_30_days": sum(1 for m in mothers if m.created_at.replace(tzinfo=None) >= since_30),
        "anemia": {"with_hb_recorded": sum(bands.values()), **{b: bands.get(b, 0) for b in ("normal", "mild", "moderate", "severe")}},
        "anc_compliance": anc_compliance(db, mothers),
        "assessment_categories": assessment_categories(db, ids),
        "referrals": referral_stats(db, ids),
        "emergencies": emergency_stats(db, ids),
        "asha_activity": asha_activity(db, asha_id=asha_id) if not district else asha_activity(db, district=district),
    }


def anc_compliance(db: Session, mothers: list[Mother]) -> dict:
    expected_by_week = lambda w: 0 if w < 12 else 1 if w < 26 else 2 if w < 34 else 3 if w < 36 else 4  # noqa: E731
    eligible = on_track = 0
    overdue = 0
    for m in mothers:
        p = m.pregnancy
        if not p or not p.lmp or m.status != "pregnant":
            continue
        w = gestational_week(p.lmp)
        if w is None:
            continue
        eligible += 1
        if (p.anc_visits_completed or 0) >= expected_by_week(w):
            on_track += 1
        if p.next_anc_date and p.next_anc_date < date.today():
            overdue += 1
    return {"eligible": eligible, "on_track": on_track, "behind": eligible - on_track, "overdue_appointments": overdue,
            "on_track_ratio": round(on_track / eligible, 3) if eligible else None}


def assessment_categories(db: Session, ids: list, days: int = 90) -> dict:
    counts = {k: 0 for k in CARE_LEVEL_KEYS.values()}
    if ids:
        since = utcnow() - timedelta(days=days)
        for cat, n in db.execute(select(AssessmentResult.category, func.count()).where(
                AssessmentResult.mother_id.in_(ids), AssessmentResult.created_at >= since)
                .group_by(AssessmentResult.category)).all():
            counts[cat] = n
    return {"window_days": days, "counts": counts, "total": sum(counts.values())}


def referral_stats(db: Session, ids: list) -> dict:
    if not ids:
        return {"total": 0, "by_status": {}, "by_care_level": {}, "by_facility_type": {}}
    rows = db.scalars(select(Referral).where(Referral.mother_id.in_(ids))).all()
    return {"total": len(rows), "by_status": dict(Counter(r.status for r in rows)),
            "by_care_level": dict(Counter(r.care_level for r in rows)),
            "by_facility_type": dict(Counter((r.facility_snapshot or {}).get("type", "unknown") for r in rows))}


def emergency_stats(db: Session, ids: list, days: int = 90) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(EmergencyEvent).where(EmergencyEvent.created_at >= since)).all()
    if ids is not None:
        rows = [e for e in rows if e.mother_id is None or e.mother_id in set(ids)]
    ack = [e for e in rows if e.acknowledged_at]
    mins = [(e.acknowledged_at.replace(tzinfo=None) - e.created_at.replace(tzinfo=None)).total_seconds() / 60 for e in ack]
    return {"window_days": days, "total": len(rows), "by_status": dict(Counter(e.status for e in rows)),
            "by_type": dict(Counter(e.emergency_type or "sos" for e in rows)),
            "anonymous": sum(1 for e in rows if e.mother_id is None),
            "median_minutes_to_acknowledge": round(sorted(mins)[len(mins) // 2], 1) if mins else None,
            "escalated_beyond_asha": sum(1 for e in rows if e.escalation_level >= 2)}


def asha_activity(db: Session, district: str | None = None, asha_id=None, days: int = 30) -> dict:
    q = select(AshaWorker)
    if district:
        q = q.where(AshaWorker.district.ilike(district))
    if asha_id:
        q = q.where(AshaWorker.id == asha_id)
    workers = db.scalars(q).all()
    since = utcnow() - timedelta(days=days)
    visits = Counter(dict(db.execute(select(Visit.asha_id, func.count()).where(Visit.visit_date >= since)
                                     .group_by(Visit.asha_id)).all()))
    caseload = Counter(dict(db.execute(select(Mother.assigned_asha_id, func.count()).group_by(Mother.assigned_asha_id)).all()))
    return {"window_days": days, "total_ashas": len(workers), "verified": sum(1 for w in workers if w.verified),
            "available_now": sum(1 for w in workers if w.available),
            "total_visits": sum(visits.get(w.id, 0) for w in workers),
            "workers": [{"asha_id": str(w.id), "name": w.name, "sub_centre": w.sub_centre_code,
                         "assigned_mothers": caseload.get(w.id, 0), "visits": visits.get(w.id, 0),
                         "last_active_at": w.last_active_at} for w in workers][:200]}


def anemia_trends(db: Session, months: int = 6, district: str | None = None, asha_id=None) -> dict:
    ids = set(_mother_ids(db, district, asha_id))
    start = (date.today().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    buckets: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    rows = db.execute(select(ReportFinding.mother_id, ReportFinding.value_num, ReportFinding.observed_on, ReportFinding.created_at)
                      .where(ReportFinding.code == "hemoglobin", ReportFinding.valid.is_(True))).all()
    rows = [(m, v, d or c.date()) for m, v, d, c in rows]
    rows += [(m, v, d.date()) for m, v, d in db.execute(select(Visit.mother_id, Visit.hemoglobin, Visit.visit_date)
                                                         .where(Visit.hemoglobin.is_not(None))).all()]
    for mid, hb, d in rows:
        if mid in ids and hb is not None and d >= start:
            buckets[f"{d:%Y-%m}"][anemia_band(hb)].add(mid)
    series = []
    for i in range(months):
        mo = (start + timedelta(days=32 * i)).replace(day=1)
        key = f"{mo:%Y-%m}"
        b = buckets.get(key, {})
        series.append({"month": key, **{band: len(b.get(band, set())) for band in ("normal", "mild", "moderate", "severe")}})
    return {"months": months, "series": series}


def take_snapshot(db: Session) -> AnalyticsSnapshot:
    today = date.today()
    data = overview(db)
    data.pop("generated_at", None)
    data["asha_activity"] = {k: v for k, v in data["asha_activity"].items() if k != "workers"}
    snap = db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.snapshot_date == today,
                                                     AnalyticsSnapshot.scope == "global"))
    if snap:
        snap.metrics = data
    else:
        snap = AnalyticsSnapshot(snapshot_date=today, scope="global", scope_key="all", metrics=data)
        db.add(snap)
    db.commit()
    return snap


def counts_by_role(db: Session) -> dict:
    return dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
