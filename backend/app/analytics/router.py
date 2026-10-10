from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.analytics import service
from app.api.deps import DB, AdminUser, Principal, get_current_asha, require_roles
from app.core.enums import Role
from app.database.models import AnalyticsSnapshot, Mother

router = APIRouter(prefix="/analytics", tags=["Analytics"])
Viewer = Depends(require_roles(Role.ADMIN, Role.ASHA))


def _scope(db, p: Principal, district: str | None):
    """Admins see everything (optionally by district); ASHA workers see only their own mothers."""
    if p.role == Role.ASHA:
        return None, get_current_asha(db, p).id
    return district, None


@router.get("/overview")
def overview(db: DB, p: Principal = Viewer, district: str | None = None):
    d, asha_id = _scope(db, p, district)
    out = service.overview(db, d, asha_id)
    if p.role == Role.ADMIN:
        out["users_by_role"] = service.counts_by_role(db)
    return out


@router.get("/anemia-trends")
def anemia_trends(db: DB, p: Principal = Viewer, months: int = Query(6, ge=1, le=24), district: str | None = None):
    d, asha_id = _scope(db, p, district)
    return service.anemia_trends(db, months, d, asha_id)


@router.get("/anc-compliance")
def anc_compliance(db: DB, p: Principal = Viewer, district: str | None = None):
    d, asha_id = _scope(db, p, district)
    ids = service._mother_ids(db, d, asha_id)
    mothers = db.scalars(select(Mother).where(Mother.id.in_(ids))).all() if ids else []
    return service.anc_compliance(db, mothers)


@router.get("/assessments")
def assessments(db: DB, p: Principal = Viewer, days: int = Query(90, ge=1, le=730), district: str | None = None):
    d, asha_id = _scope(db, p, district)
    return service.assessment_categories(db, service._mother_ids(db, d, asha_id), days)


@router.get("/referrals")
def referrals(db: DB, p: Principal = Viewer, district: str | None = None):
    d, asha_id = _scope(db, p, district)
    return service.referral_stats(db, service._mother_ids(db, d, asha_id))


@router.get("/emergencies")
def emergencies(db: DB, p: Principal = Viewer, days: int = Query(90, ge=1, le=730), district: str | None = None):
    d, asha_id = _scope(db, p, district)
    ids = service._mother_ids(db, d, asha_id) if (d or asha_id) else None
    return service.emergency_stats(db, ids, days)


@router.get("/asha-activity")
def asha_activity(db: DB, p: Principal = Viewer, days: int = Query(30, ge=1, le=365), district: str | None = None):
    d, asha_id = _scope(db, p, district)
    return service.asha_activity(db, d, asha_id, days)


@router.post("/snapshot")
def snapshot(db: DB, _: AdminUser):
    s = service.take_snapshot(db)
    return {"snapshot_date": s.snapshot_date, "metrics": s.metrics}


@router.get("/snapshots")
def snapshots(db: DB, _: AdminUser, days: int = Query(30, ge=1, le=365)):
    rows = db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.snapshot_date >= date.today() - timedelta(days=days))
                      .order_by(AnalyticsSnapshot.snapshot_date)).all()
    return {"snapshots": [{"date": s.snapshot_date, "scope": s.scope, "metrics": s.metrics} for s in rows]}
