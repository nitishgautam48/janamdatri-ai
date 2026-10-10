"""Automatic mother → ASHA assignment.

Scores every available, verified ASHA by village, sub-centre, pincode, sector and GIS
distance, with a penalty for full caseloads. The best match becomes the assigned ASHA
and the next best (preferring the same sub-centre) the backup.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.models import AshaWorker, Mother
from app.gis.service import degree_box, haversine_km, mother_location

MAX_DISTANCE_KM = 25


@dataclass
class Candidate:
    asha: AshaWorker
    score: float
    reasons: list[str]
    distance_km: float | None


def _norm(v: str | None) -> str:
    return (v or "").split(",")[0].strip().lower()


def _caseload(db: Session, asha_id) -> int:
    return db.scalar(select(func.count()).select_from(Mother).where(Mother.assigned_asha_id == asha_id)) or 0


def rank_candidates(db: Session, mother: Mother) -> list[Candidate]:
    loc = mother_location(db, mother)
    sub_centre = mother.sub_centre_code or (loc.sub_centre_code if loc else None)
    sector = mother.sector_code or (loc.sector_code if loc else None)
    pincode = mother.pincode or (loc.pincode if loc else None)
    village = _norm(mother.village or (loc.village if loc else None))
    filters = [AshaWorker.pincode == pincode] if pincode else []
    if sub_centre:
        filters.append(AshaWorker.sub_centre_code == sub_centre)
    if sector:
        filters.append(AshaWorker.sector_code == sector)
    q = select(AshaWorker).where(AshaWorker.verified.is_(True), AshaWorker.available.is_(True))
    if loc:  # nearby by box, plus code matches anywhere
        dlat, dlon = degree_box(loc.latitude, MAX_DISTANCE_KM)
        filters.append(AshaWorker.latitude.between(loc.latitude - dlat, loc.latitude + dlat)
                       & AshaWorker.longitude.between(loc.longitude - dlon, loc.longitude + dlon))
    if not filters:
        return []
    q = q.where(or_(*filters))
    out: list[Candidate] = []
    for a in db.scalars(q).all():
        score, reasons = 0.0, []
        served = {_norm(v) for v in (a.villages_served or [])} | {_norm(a.village)}
        if village and village in served:
            score += 50
            reasons.append("village")
        if sub_centre and a.sub_centre_code == sub_centre:
            score += 25
            reasons.append("sub-centre")
        if pincode and a.pincode == pincode:
            score += 20
            reasons.append(f"pincode {pincode}")
        if sector and a.sector_code == sector:
            score += 10
            reasons.append("sector")
        dist = None
        if loc and a.latitude is not None and a.longitude is not None:
            dist = haversine_km(loc.latitude, loc.longitude, a.latitude, a.longitude)
            if dist > MAX_DISTANCE_KM and not reasons:
                continue
            score += max(0.0, 20 - dist)  # closer is better
            if dist <= 5:
                reasons.append("GIS distance")
        if _caseload(db, a.id) >= a.max_caseload:
            score -= 40
        if score > 0:
            out.append(Candidate(a, score, reasons, dist))
    out.sort(key=lambda c: -c.score)
    return out


def reason_text(reasons: list[str]) -> str:
    if not reasons:
        return "Matched by nearest available ASHA"
    if len(reasons) == 1:
        return f"Matched by {reasons[0]}"
    return "Matched by " + ", ".join(reasons[:-1]) + " and " + reasons[-1]


def assign_asha(db: Session, mother: Mother, *, force: bool = False) -> Candidate | None:
    if mother.assigned_asha_id and not force:
        return None
    ranked = rank_candidates(db, mother)
    if not ranked:
        return None
    primary = ranked[0]
    mother.assigned_asha_id = primary.asha.id
    mother.assignment_reason = reason_text(primary.reasons)
    backups = [c for c in ranked[1:] if c.asha.sub_centre_code == primary.asha.sub_centre_code] or ranked[1:]
    mother.backup_asha_id = backups[0].asha.id if backups else None
    if not mother.sub_centre_code:
        mother.sub_centre_code = primary.asha.sub_centre_code
    if not mother.sector_code:
        mother.sector_code = primary.asha.sector_code
    return primary
