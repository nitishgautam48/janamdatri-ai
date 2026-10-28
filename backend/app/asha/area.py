"""An ASHA's working area from her phone's location, and the map of her mothers.

Setting the area (``POST /asha/me/location``) stores her position, fills village / pincode / district from the
gazetteer or OpenStreetMap, and connects the mothers near her who have no ASHA yet (the usual matching rules:
village, sub-centre, pincode, then distance within ``MAX_DISTANCE_KM``).
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.asha.assignment import MAX_DISTANCE_KM, assign_asha
from app.asha.service import mother_row, mothers_for_asha_query
from app.database.models import AshaWorker, Mother
from app.gis.service import degree_box, haversine_km, mother_location, nearby_facilities, resolve_location

UNASSIGNED_SCAN = 300  # mothers without an ASHA looked at per update (nearest first by box, then by pincode)


def area_of(asha: AshaWorker) -> dict:
    place = ", ".join(x for i, x in enumerate([asha.village, asha.district, asha.state])
                      if x and x not in [asha.village, asha.district, asha.state][:i])
    return {"has_location": asha.latitude is not None and asha.longitude is not None, "place": place or None,
            "village": asha.village, "pincode": asha.pincode, "district": asha.district,
            "latitude": asha.latitude, "longitude": asha.longitude, "villages_served": asha.villages_served or []}


def set_area(db: Session, asha: AshaWorker, lat: float, lon: float) -> dict:
    loc = resolve_location(db, lat=lat, lon=lon)
    moved = asha.latitude is None or haversine_km(asha.latitude, asha.longitude, lat, lon) > MAX_DISTANCE_KM
    asha.latitude, asha.longitude = lat, lon
    if loc:
        # A new place replaces the old names; nearby, a missing name keeps the old one.
        for field in ("village", "pincode", "district", "state"):
            value = getattr(loc, field)
            if value or moved:
                setattr(asha, field, value)
        if moved:
            asha.villages_served = []
        if loc.village and loc.village.lower() not in {v.lower() for v in asha.villages_served or []}:
            asha.villages_served = [*(asha.villages_served or []), loc.village]
    db.flush()

    dlat, dlon = degree_box(lat, MAX_DISTANCE_KM)
    near = or_(Mother.latitude.between(lat - dlat, lat + dlat) & Mother.longitude.between(lon - dlon, lon + dlon),
               Mother.latitude.is_(None))
    connected = []
    for m in db.scalars(select(Mother).where(Mother.assigned_asha_id.is_(None), near).limit(UNASSIGNED_SCAN)).all():
        ml = mother_location(db, m)
        if not ml or haversine_km(lat, lon, ml.latitude, ml.longitude) > MAX_DISTANCE_KM:
            continue
        assign_asha(db, m)
        if m.assigned_asha_id == asha.id:
            connected.append(m.name or "Mother")
    db.commit()
    return {"area": area_of(asha), "newly_connected": len(connected), "names": connected[:10]}


def map_data(db: Session, asha: AshaWorker) -> dict:
    """Her mothers with a map position (exact from the passport, or approximate from village / pincode)."""
    mothers = []
    for m in db.scalars(mothers_for_asha_query(asha)).all():
        loc = mother_location(db, m)
        if not loc:
            continue
        row = mother_row(db, m, asha)
        row.update(latitude=loc.latitude, longitude=loc.longitude, approximate=loc.source != "passport",
                   distance_km=round(haversine_km(asha.latitude, asha.longitude, loc.latitude, loc.longitude), 1)
                   if asha.latitude is not None else None)
        mothers.append(row)
    facilities = []
    if asha.latitude is not None:
        loc = resolve_location(db, lat=asha.latitude, lon=asha.longitude)
        if loc:
            facilities = [{"name": f["name"], "type": f["type"], "latitude": f["latitude"], "longitude": f["longitude"]}
                          for f in nearby_facilities(db, loc, types=["PHC", "CHC", "DH"], radius_km=30, limit=6)
                          if f.get("latitude") is not None]
    return {"area": area_of(asha), "mothers": mothers, "facilities": facilities}
