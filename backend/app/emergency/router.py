from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentPrincipal, OptionalPrincipal, asha_can_access, get_current_asha
from app.core.enums import EmergencyStatus, Role
from app.core.exceptions import Forbidden, NotFound, ValidationFailed
from app.core.ratelimit import limit
from app.database.base import utcnow
from app.database.models import EmergencyEvent, Mother
from app.emergency import service
from app.gis.service import resolve_location

router = APIRouter(prefix="/emergency", tags=["Emergency"])


class AlertIn(BaseModel):
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    village: str | None = None
    emergency_type: str | None = Field(None, max_length=40, examples=["bleeding", "convulsions", "labour", "sos"])
    description: str | None = Field(None, max_length=500)
    caller_phone: str | None = None
    notify_asha: bool = True


class LocationIn(BaseModel):
    event_id: uuid.UUID
    public_token: str | None = Field(None, description="Needed when not logged in (returned by /emergency/alert)")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


def _mother(db, p) -> Mother | None:
    if p and p.role == Role.MOTHER and p.user:
        return db.scalar(select(Mother).where(Mother.user_id == p.user.id))
    return None


def _event_for(db, p, event_id: uuid.UUID, token: str | None) -> EmergencyEvent:
    ev = db.get(EmergencyEvent, event_id)
    if not ev:
        raise NotFound("Emergency not found")
    if token and token == ev.public_token:
        return ev
    if p and p.role == Role.ADMIN:
        return ev
    if p and p.user and ev.user_id == p.user.id:
        return ev
    if p and p.role == Role.ASHA and ev.mother_id:
        mother = db.get(Mother, ev.mother_id)
        if mother and asha_can_access(get_current_asha(db, p), mother):
            return ev
    if p and p.role == Role.ASHA:
        asha = get_current_asha(db, p)
        if any(s.get("asha_id") == str(asha.id) for s in ev.escalation_chain or []):
            return ev
    raise Forbidden("Not allowed to view this emergency")


@router.get("/numbers")
def numbers():
    return {"numbers": service.NUMBERS}


@router.post("/alert", status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit("sos", 10, 600))])
def alert(body: AlertIn, db: DB, p: OptionalPrincipal):
    """SOS. Works without login. Notifies the assigned (or local) ASHA and family, finds the nearest
    emergency facility and starts the escalation chain."""
    mother = _mother(db, p)
    ev = service.raise_alert(db, mother=mother, user_id=p.id if p else None, lat=body.latitude, lon=body.longitude,
                             pincode=body.pincode, village=body.village, emergency_type=body.emergency_type,
                             description=body.description, caller_phone=body.caller_phone, notify_asha=body.notify_asha)
    db.commit()
    return service.event_dict(ev, include_token=True)


@router.post("/location")
def location(body: LocationIn, db: DB, p: OptionalPrincipal):
    """Live location sharing for an active alert."""
    ev = _event_for(db, p, body.event_id, body.public_token)
    if ev.status in (EmergencyStatus.RESOLVED, EmergencyStatus.CANCELLED):
        raise ValidationFailed("This emergency is closed")
    service.update_location(db, ev, body.latitude, body.longitude)
    db.commit()
    return service.event_dict(ev)


@router.get("/facilities")
def facilities(db: DB, p: OptionalPrincipal, lat: float | None = None, lon: float | None = None,
               pincode: str | None = Query(None, pattern=r"^\d{6}$"), village: str | None = None,
               limit_: int = Query(5, alias="limit", ge=1, le=20)):
    """Nearest 24×7 emergency facilities (no login needed)."""
    loc = resolve_location(db, lat=lat, lon=lon, pincode=pincode, village=village)
    if loc is None and (m := _mother(db, p)):
        from app.gis.service import mother_location

        loc = mother_location(db, m)
    if loc is None:
        raise ValidationFailed("Share your location, or enter a pincode or village", code="location_required")
    return {"location": loc.as_dict(), "facilities": service.nearest_emergency_facilities(db, loc, limit_),
            "numbers": service.NUMBERS}


@router.get("/active")
def active(db: DB, p: CurrentPrincipal):
    """Active emergencies for the ASHA's mothers (or all, for admins)."""
    q = select(EmergencyEvent).where(EmergencyEvent.status.in_([EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED]))
    rows = db.scalars(q.order_by(EmergencyEvent.created_at.desc()).limit(200)).all()
    if p.role == Role.ASHA:
        asha = get_current_asha(db, p)
        rows = [e for e in rows if any(s.get("asha_id") == str(asha.id) for s in e.escalation_chain or [])]
    elif p.role != Role.ADMIN:
        rows = [e for e in rows if p.user and e.user_id == p.user.id]
    return {"emergencies": [service.event_dict(e) for e in rows]}


@router.get("/{event_id}")
def get_event(event_id: uuid.UUID, db: DB, p: OptionalPrincipal, token: str | None = Query(None)):
    return service.event_dict(_event_for(db, p, event_id, token))


@router.post("/{event_id}/acknowledge")
def acknowledge(event_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    if p.role not in (Role.ASHA, Role.ADMIN):
        raise Forbidden("Only health workers can acknowledge an emergency")
    ev = _event_for(db, p, event_id, None)
    asha = get_current_asha(db, p) if p.role == Role.ASHA else None
    service.acknowledge(db, ev, p.id, asha)
    db.commit()
    return service.event_dict(ev)


@router.post("/{event_id}/resolve")
def resolve(event_id: uuid.UUID, db: DB, p: OptionalPrincipal, token: str | None = Query(None),
            cancelled: bool = Query(False, description="True if raised by mistake")):
    ev = _event_for(db, p, event_id, token)
    ev.status = EmergencyStatus.CANCELLED if cancelled else EmergencyStatus.RESOLVED
    ev.resolved_at = utcnow()
    db.commit()
    return service.event_dict(ev)


@router.post("/escalate-due")
def escalate_due(db: DB, _: AdminUser):
    """Run one escalation pass (Celery beat does this every minute when enabled)."""
    return {"escalated": service.escalate_due(db)}
