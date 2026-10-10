"""GIS: location resolution, distance, nearby facilities and severity routing."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import CARE_LEVEL_FACILITY, CareLevel, FacilityType
from app.core.exceptions import ValidationFailed
from app.database.models import Facility, GISMapping, Mother
from app.gis import osm

ROAD_FACTOR = 1.3  # straight-line → typical rural road distance
FACILITY_LABELS = {"SC": "Sub Centre", "PHC": "Primary Health Centre", "CHC": "Community Health Centre",
                   "DH": "District Hospital", "HOSP": "Hospital", "CLINIC": "Clinic"}
ROUTING_LABELS = {"PHC": "Routine care", "CHC": "Medical review", "DH": "Emergency", "SC": "Sub centre",
                  "HOSP": "Hospital", "CLINIC": "Clinic"}
# If the routed level is not reachable nearby, step up (never down). Government facilities first; other
# hospitals (HOSP) and clinics found on OpenStreetMap are used when no government one is within reach.
ESCALATION = {"SC": ["SC", "PHC", "CHC", "DH", "HOSP", "CLINIC"], "PHC": ["PHC", "CHC", "DH", "HOSP", "CLINIC"],
              "CHC": ["CHC", "DH", "HOSP"], "DH": ["DH", "HOSP"]}
GAZETTEER_MATCH_KM = 8  # a bundled locality closer than this is trusted for village / codes


@dataclass
class Location:
    latitude: float
    longitude: float
    source: str  # gps | village | pincode | manual | passport
    pincode: str | None = None
    village: str | None = None
    district: str | None = None
    state: str | None = None
    sub_centre_code: str | None = None
    sector_code: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def degree_box(lat: float, radius_km: float) -> tuple[float, float]:
    """Half-widths in degrees of a box that contains a circle of ``radius_km`` around ``lat``."""
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return dlat, dlon


def travel_estimate(straight_km: float) -> tuple[float, int]:
    road = round(straight_km * ROAD_FACTOR, 1)
    minutes = int(round(road / settings.rural_avg_speed_kmph * 60 + 3))
    return road, minutes


def _nearest_mapping(db: Session, lat: float, lon: float) -> GISMapping | None:
    # Bounding box pre-filter (~30 km) then exact distance.
    dlat, dlon = degree_box(lat, 30)
    rows = db.scalars(select(GISMapping).where(
        GISMapping.latitude.between(lat - dlat, lat + dlat), GISMapping.longitude.between(lon - dlon, lon + dlon))).all()
    return min(rows, key=lambda m: haversine_km(lat, lon, m.latitude, m.longitude), default=None)


def resolve_location(db: Session, *, lat: float | None = None, lon: float | None = None,
                     pincode: str | None = None, village: str | None = None, manual: bool = False) -> Location | None:
    """GPS → village+pincode → pincode → village. Returns None if nothing matches."""
    if lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValidationFailed("Invalid coordinates", code="invalid_coordinates")
        source = "manual" if manual else "gps"
        m = _nearest_mapping(db, lat, lon)
        if m and haversine_km(lat, lon, m.latitude, m.longitude) <= GAZETTEER_MATCH_KM:
            return Location(lat, lon, source, pincode or m.pincode, village or m.village, m.district, m.state,
                            m.sub_centre_code, m.sector_code)
        place = osm.reverse(lat, lon)
        if place:
            return Location(lat, lon, source, pincode or place.pincode, village or place.village, place.district,
                            place.state, m.sub_centre_code if m else None, m.sector_code if m else None)
        return Location(lat, lon, source, pincode or (m.pincode if m else None), village or (m.village if m else None),
                        m.district if m else None, m.state if m else None,
                        m.sub_centre_code if m else None, m.sector_code if m else None)
    q = select(GISMapping)
    if village and pincode:
        m = db.scalar(q.where(GISMapping.pincode == pincode, func.lower(GISMapping.village) == _village_key(village)))
        if m:
            return _from_mapping(m, "village")
    if pincode:
        rows = db.scalars(q.where(GISMapping.pincode == pincode)).all()
        if rows:
            loc = _from_mapping(rows[0], "pincode")
            loc.latitude = sum(r.latitude for r in rows) / len(rows)
            loc.longitude = sum(r.longitude for r in rows) / len(rows)
            loc.village = village or (rows[0].village if len(rows) == 1 else None)
            return loc
    if village:
        m = db.scalar(q.where(func.lower(GISMapping.village) == _village_key(village)))
        if m:
            return _from_mapping(m, "village")
    # Not in the bundled gazetteer: look it up on OpenStreetMap / India Post.
    place = None
    if village:
        place = osm.geocode_place(village, pincode)
        if place and pincode and place.pincode and place.pincode != pincode:
            place = None
    if not place and pincode:
        place = osm.geocode_pincode(pincode)
    if place:
        return Location(place.latitude, place.longitude, "village" if village and place.village else "pincode",
                        pincode or place.pincode, village or place.village, place.district, place.state)
    return None


def _village_key(village: str) -> str:
    return village.split(",")[0].strip().lower()


def _from_mapping(m: GISMapping, source: str) -> Location:
    return Location(m.latitude, m.longitude, source, m.pincode, m.village, m.district, m.state,
                    m.sub_centre_code, m.sector_code)


def mother_location(db: Session, mother: Mother) -> Location | None:
    if mother.latitude is not None and mother.longitude is not None:
        return Location(mother.latitude, mother.longitude, "passport", mother.pincode, mother.village,
                        mother.district, mother.state, mother.sub_centre_code, mother.sector_code)
    return resolve_location(db, pincode=mother.pincode, village=mother.village)


def facility_dict(f: Facility, loc: Location | None = None) -> dict:
    d = {
        "id": str(f.id), "name": f.name, "type": f.facility_type, "type_label": FACILITY_LABELS.get(f.facility_type),
        "routing_label": ROUTING_LABELS.get(f.facility_type), "address": f.address, "district": f.district,
        "pincode": f.pincode, "latitude": f.latitude, "longitude": f.longitude, "phone": f.phone,
        "is_24x7": f.is_24x7, "has_emergency": f.has_emergency, "has_blood_bank": f.has_blood_bank,
        "services": f.services, "opd_hours": f.opd_hours, "source": getattr(f, "source", None) or "registry",
    }
    if loc:
        straight = haversine_km(loc.latitude, loc.longitude, f.latitude, f.longitude)
        road, minutes = travel_estimate(straight)
        d.update(distance_km=road, travel_minutes=minutes,
                 directions_url=f"https://www.google.com/maps/dir/?api=1&origin={loc.latitude},{loc.longitude}"
                                f"&destination={f.latitude},{f.longitude}&travelmode=driving")
        meta = [ROUTING_LABELS.get(f.facility_type, ""), f"{road} km", f"{minutes} min"]
        if f.is_24x7:
            meta.append("24×7")
        d["display_meta"] = " · ".join(m for m in meta if m)
    return d


def nearby_facilities(db: Session, loc: Location, *, types: list[str] | None = None, radius_km: float = 50,
                      limit: int = 10, emergency_only: bool = False) -> list[dict]:
    rows = _nearby_rows(db, loc, types, radius_km, emergency_only)
    if len(rows) < min(limit, 3) and radius_km <= 100 and osm.load_area(db, loc.latitude, loc.longitude):
        rows = _nearby_rows(db, loc, types, radius_km, emergency_only)  # real facilities downloaded for this area
    return [facility_dict(f, loc) for _, f in rows[:limit]]


def _nearby_rows(db: Session, loc: Location, types: list[str] | None, radius_km: float,
                 emergency_only: bool) -> list[tuple[float, Facility]]:
    dlat, dlon = degree_box(loc.latitude, radius_km)
    q = select(Facility).where(Facility.active.is_(True),
                               Facility.latitude.between(loc.latitude - dlat, loc.latitude + dlat),
                               Facility.longitude.between(loc.longitude - dlon, loc.longitude + dlon))
    if types:
        q = q.where(Facility.facility_type.in_(types))
    if emergency_only:
        q = q.where(Facility.has_emergency.is_(True))
    rows = []
    for f in db.scalars(q).all():
        dist = haversine_km(loc.latitude, loc.longitude, f.latitude, f.longitude)
        if dist <= radius_km:
            rows.append((dist, f))
    rows.sort(key=lambda r: r[0])
    return rows


def recommend_facility(db: Session, loc: Location, level: CareLevel, radius_km: float = 60) -> dict | None:
    """Severity routing: routine → PHC, medical review → CHC, emergency → district hospital."""
    routed = CARE_LEVEL_FACILITY[level].value
    for ftype in ESCALATION[routed]:
        found = nearby_facilities(db, loc, types=[ftype], radius_km=radius_km, limit=1,
                                  emergency_only=level == CareLevel.IMMEDIATE_CARE)
        if found:
            rec = found[0]
            rec["routed_for"] = level.key
            rec["routed_type"] = routed
            rec["stepped_up"] = ftype != routed
            return rec
    # Nothing at or above the routed level nearby: nearest emergency-capable facility, any distance.
    found = nearby_facilities(db, loc, radius_km=500, limit=1, emergency_only=True)
    if found:
        found[0].update(routed_for=level.key, routed_type=routed, stepped_up=True)
        return found[0]
    return None


def facility_type_for(level: CareLevel) -> FacilityType:
    return CARE_LEVEL_FACILITY[level]
