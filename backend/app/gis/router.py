from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select

from app.api.deps import DB, AdminUser, OptionalPrincipal
from app.core.config import settings
from app.core.enums import CareLevel, Role
from app.core.exceptions import NotFound, ValidationFailed
from app.core.ratelimit import limit
from app.database.models import AssessmentResult, Facility, GISMapping, Mother
from app.gis import routing, service, tiles
from app.gis.schemas import FacilityIn, GISMappingIn

router = APIRouter(prefix="/gis", tags=["GIS Navigation"])


def _location(db, p, lat, lon, pincode, village):
    loc = service.resolve_location(db, lat=lat, lon=lon, pincode=pincode, village=village)
    mother = None
    if p and p.role == Role.MOTHER and p.user:
        mother = db.scalar(select(Mother).where(Mother.user_id == p.user.id))
    if loc is None and mother:
        loc = service.mother_location(db, mother)
    if loc is None:
        raise ValidationFailed("Share your location, or enter a pincode or village", code="location_required")
    return loc, mother


@router.get("/geocode")
def geocode(db: DB, pincode: str | None = Query(None, pattern=r"^\d{6}$"), village: str | None = None,
            lat: float | None = None, lon: float | None = None):
    loc = service.resolve_location(db, lat=lat, lon=lon, pincode=pincode, village=village)
    if not loc:
        raise NotFound("We could not find this place. Check the pincode or village name, or share your location.",
                       code="location_not_found")
    return loc.as_dict()


@router.get("/nearby")
def nearby(db: DB, p: OptionalPrincipal, lat: float | None = None, lon: float | None = None,
           pincode: str | None = Query(None, pattern=r"^\d{6}$"), village: str | None = None,
           type: list[str] | None = Query(None, description="PHC, CHC, DH, SC"),
           radius_km: float = Query(40, gt=0, le=200), limit: int = Query(10, ge=1, le=50)):
    """Nearby PHC / CHC / district hospital from GPS, pincode, village or the mother's passport."""
    loc, _ = _location(db, p, lat, lon, pincode, village)
    if type:  # a district hospital request also accepts other hospitals with emergency care
        type = sorted({t.upper() for t in type} | ({"HOSP"} if any(t.upper() == "DH" for t in type) else set()))
    items = service.nearby_facilities(db, loc, types=type, radius_km=radius_km, limit=limit)
    return {"location": loc.as_dict(), "count": len(items), "facilities": items}


@router.get("/route")
def road_route(from_lat: float = Query(..., ge=-90, le=90), from_lon: float = Query(..., ge=-180, le=180),
               to_lat: float = Query(..., ge=-90, le=90), to_lon: float = Query(..., ge=-180, le=180)):
    """Road route to a facility: distance, travel time and the line to draw on the map (Azure Maps or OSRM).
    ``available: false`` when no router is reachable: the app then shows the straight-line estimate."""
    r = routing.route((from_lat, from_lon), (to_lat, to_lon))
    if not r:
        straight = service.haversine_km(from_lat, from_lon, to_lat, to_lon)
        km, minutes = service.travel_estimate(straight)
        return {"available": False, "distance_km": km, "minutes": minutes, "path": [], "provider": routing.provider()}
    return {"available": True, **r}


@router.get("/map-config")
def map_config():
    """Map tiles and routing used by the app."""
    return {"tile_url": settings.map_tile_url, "tile_source": tiles.source(),
            "attribution": ("© MapTiler " if tiles.source() == "maptiler" else "") + settings.map_attribution,
            "fallback_tile_url": tiles.SOURCES["osm"],
            "routing": routing.provider(), "facilities": "azure maps + openstreetmap" if settings.azure_maps_key
            else "openstreetmap"}


@router.get("/tiles/{z}/{x}/{y}.png", dependencies=[Depends(limit("map-tiles", 4000, 3600))],
            responses={200: {"content": {"image/png": {}}}})
def tile(z: int, x: int, y: int):
    """A map picture, fetched once from OpenStreetMap (or a backup source) and then served from the server's copy."""
    if not tiles.valid(z, x, y):
        raise NotFound("No such map tile", code="tile_not_found")
    data = tiles.get(z, x, y)
    if data is None:
        return Response(status_code=503, headers={"Cache-Control": "no-store"})
    return Response(data, media_type=tiles.media_type(data), headers={"Cache-Control": "public, max-age=604800"})


@router.get("/facilities")
def facilities(db: DB, district: str | None = None, type: str | None = None,
               pincode: str | None = None, limit: int = Query(100, le=500)):
    q = select(Facility).where(Facility.active.is_(True))
    if district:
        q = q.where(Facility.district.ilike(district))
    if type:
        q = q.where(Facility.facility_type == type.upper())
    if pincode:
        q = q.where(Facility.pincode == pincode)
    rows = db.scalars(q.order_by(Facility.facility_type, Facility.name).limit(limit)).all()
    return {"count": len(rows), "facilities": [service.facility_dict(f) for f in rows]}


@router.get("/facilities/{facility_id}")
def facility(facility_id: uuid.UUID, db: DB):
    f = db.get(Facility, facility_id)
    if not f:
        raise NotFound("Facility not found")
    return service.facility_dict(f)


@router.get("/recommendation")
def recommendation(db: DB, p: OptionalPrincipal, care_level: str | None = Query(
        None, description="routine_monitoring | needs_attention | medical_review | immediate_care"),
        lat: float | None = None, lon: float | None = None,
        pincode: str | None = Query(None, pattern=r"^\d{6}$"), village: str | None = None):
    """Severity routing. Without ``care_level`` a signed-in mother's latest assessment is used."""
    loc, mother = _location(db, p, lat, lon, pincode, village)
    if care_level:
        try:
            level = CareLevel.from_key(care_level)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
    elif mother:
        latest = db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == mother.id)
                           .order_by(AssessmentResult.created_at.desc()))
        level = CareLevel(latest.level) if latest else CareLevel.ROUTINE_MONITORING
    else:
        level = CareLevel.ROUTINE_MONITORING
    rec = service.recommend_facility(db, loc, level)
    return {"care_level": level.key, "care_level_label": level.label, "location": loc.as_dict(),
            "routing": {"routine_monitoring": "PHC", "needs_attention": "PHC", "medical_review": "CHC",
                        "immediate_care": "DH"},
            "facility": rec}


# --- admin management --------------------------------------------------------
@router.post("/facilities", status_code=201)
def create_facility(body: FacilityIn, db: DB, _: AdminUser):
    f = Facility(**body.model_dump())
    db.add(f)
    db.commit()
    return service.facility_dict(f)


@router.put("/facilities/{facility_id}")
def update_facility(facility_id: uuid.UUID, body: FacilityIn, db: DB, _: AdminUser):
    f = db.get(Facility, facility_id)
    if not f:
        raise NotFound("Facility not found")
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    db.commit()
    return service.facility_dict(f)


@router.post("/mappings", status_code=201)
def create_mapping(body: GISMappingIn, db: DB, _: AdminUser):
    m = GISMapping(**body.model_dump())
    db.add(m)
    db.commit()
    return {"id": str(m.id), **body.model_dump()}
