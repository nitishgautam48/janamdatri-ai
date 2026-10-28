"""Hospitals and health centres from Azure Maps (``AZURE_MAPS_KEY``), alongside OpenStreetMap.

Azure Maps' point-of-interest search adds facilities that are missing from OpenStreetMap and often has better
names and phone numbers. Results are stored as facilities (``source="azure"``), once per ~11 km area per week;
a place already known (same name within 300 m) is not added twice.
"""
from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import get_json, set_json
from app.core.config import settings
from app.database.models import Facility
from app.gis import osm

log = logging.getLogger(__name__)

AREA_CELL_DEG, AREA_TTL, FAIL_TTL = 0.1, 7 * 24 * 3600, 300
SEARCHES = [("hospital", 30000), ("health centre", 20000), ("clinic", 15000)]


def enabled() -> bool:
    return bool(settings.azure_maps_key)


def _search(query: str, lat: float, lon: float, radius: int) -> list[dict]:
    r = httpx.get("https://atlas.microsoft.com/search/poi/category/json", timeout=10, params={
        "api-version": "1.0", "query": query, "lat": lat, "lon": lon, "radius": radius, "limit": 100,
        "countrySet": "IN", "view": "IN", "language": "en-GB", "subscription-key": settings.azure_maps_key})
    r.raise_for_status()
    return r.json().get("results", [])


def _fields(item: dict) -> dict | None:
    poi, addr, pos = item.get("poi") or {}, item.get("address") or {}, item.get("position") or {}
    name = (poi.get("name") or "").strip()
    if not name or pos.get("lat") is None:
        return None
    cats = " ".join(poi.get("categories") or []).lower()
    if any(w in cats for w in ("pharmacy", "dentist", "veterinar", "optician")):
        return None
    amenity = "hospital" if "hospital" in cats else "clinic"
    ftype = osm.classify({"name": name, "amenity": amenity})
    if not ftype:
        return None
    hospital = ftype in ("DH", "CHC", "HOSP")
    pin = re.sub(r"\D", "", addr.get("postalCode") or "")
    return {
        "name": name[:160], "facility_type": ftype, "address": (addr.get("freeformAddress") or "")[:255] or None,
        "village": addr.get("municipalitySubdivision") or addr.get("municipality"),
        "district": addr.get("countrySecondarySubdivision"), "state": addr.get("countrySubdivision"),
        "pincode": pin if len(pin) == 6 else None, "latitude": float(pos["lat"]), "longitude": float(pos["lon"]),
        "phone": (poi.get("phone") or "").replace(" ", "")[:20] or None,
        "is_24x7": hospital, "has_emergency": hospital, "has_blood_bank": False, "services": [], "opd_hours": None,
    }


def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())[:12]


def load_area(db: Session, lat: float, lon: float) -> int:
    """Store Azure Maps facilities around a point (once per area per week). Returns the number of new ones."""
    if not enabled():
        return 0
    from app.gis.service import haversine_km

    cell = f"{round(lat / AREA_CELL_DEG) * AREA_CELL_DEG:.1f}:{round(lon / AREA_CELL_DEG) * AREA_CELL_DEG:.1f}"
    key = f"azmaps:area:{cell}"
    if get_json(key):
        return 0
    try:
        items = list({i.get("id"): i for q, rad in SEARCHES for i in _search(q, lat, lon, rad)}.values())
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Azure Maps search failed: %s", exc)
        set_json(key, {"failed": True}, ex=FAIL_TTL)
        return 0
    box = 0.45
    known = db.scalars(select(Facility).where(Facility.latitude.between(lat - box, lat + box),
                                              Facility.longitude.between(lon - box, lon + box))).all()
    by_ext = {f.external_id: f for f in known if f.external_id}
    count = 0
    for item in items:
        fields = _fields(item)
        if not fields:
            continue
        ext = f"azure:{item.get('id')}"[:64]
        f = by_ext.get(ext)
        if f is None:
            same = next((k for k in known if _norm(k.name) == _norm(fields["name"]) and
                         haversine_km(k.latitude, k.longitude, fields["latitude"], fields["longitude"]) < 0.3), None)
            if same:
                if not same.phone and fields["phone"]:
                    same.phone = fields["phone"]
                continue
            f = Facility(source="azure", external_id=ext, active=True, **fields)
            db.add(f)
            known.append(f)
            by_ext[ext] = f
            count += 1
        else:
            for k, v in fields.items():
                setattr(f, k, v)
    db.commit()
    set_json(key, {"loaded": count}, ex=AREA_TTL)
    log.info("Azure Maps: %s health facilities stored around %.3f,%.3f", count, lat, lon)
    return count
