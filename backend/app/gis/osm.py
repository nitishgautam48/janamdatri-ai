"""OpenStreetMap lookups so location features work anywhere in India, not only in the bundled gazetteer.

- ``reverse(lat, lon)``      GPS → village / town, district, state, pincode   (Nominatim)
- ``geocode_pincode(pin)``   pincode → coordinates, district, state           (Nominatim, then India Post + Nominatim)
- ``geocode_place(name)``    village / town name → coordinates                (Nominatim)
- ``load_area(db, lat, lon)`` real hospitals, PHC, CHC, sub centres and clinics around a point (Overpass),
                              saved to the ``facilities`` table so routing, referrals and SOS use them.

Every call has a short timeout and is cached; failures (no internet, service busy) return ``None`` / ``0``
so the rest of the app keeps working with the data it already has. Nominatim's usage policy is respected:
an identifying User-Agent and at most one request per second.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import get_json, set_json
from app.core.config import settings
from app.database.models import Facility

log = logging.getLogger("janamdatri.osm")

_lock = threading.Lock()
_last_nominatim = 0.0
HOSPITAL_RADIUS_M = 30000
CLINIC_RADIUS_M = 15000
AREA_CELL_DEG = 0.1          # one Overpass download covers a ~11 km cell (radius 30 km around it)
AREA_TTL = 7 * 24 * 3600
FAIL_TTL = 300               # after a failure, don't retry the same thing for 5 minutes


@dataclass
class Place:
    latitude: float
    longitude: float
    village: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    display_name: str | None = None


def enabled() -> bool:
    return bool(settings.osm_enabled)


def _client() -> httpx.Client:
    return httpx.Client(timeout=settings.osm_timeout_seconds, headers={"User-Agent": settings.osm_user_agent,
                                                                       "Accept-Language": "en"})


def _nominatim(path: str, params: dict) -> list | dict | None:
    global _last_nominatim
    with _lock:  # max 1 request / second (Nominatim policy)
        wait = 1.05 - (time.monotonic() - _last_nominatim)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim = time.monotonic()
    try:
        with _client() as c:
            r = c.get(settings.osm_nominatim_url.rstrip("/") + path, params={"format": "jsonv2", **params})
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Nominatim %s failed: %s", path, exc)
        return None


def _pin(value: str | None) -> str | None:
    m = re.search(r"\b(\d{6})\b", value or "")
    return m.group(1) if m else None


def _place_from(item: dict) -> Place:
    a = item.get("address") or {}
    village = (a.get("village") or a.get("hamlet") or a.get("town") or a.get("suburb") or a.get("neighbourhood")
               or a.get("city_district") or a.get("city") or a.get("municipality"))
    district = a.get("state_district") or a.get("district") or a.get("county") or a.get("city")
    if district:
        district = re.sub(r"\s+(District|Tahsil|Tehsil)$", "", district)
    return Place(float(item["lat"]), float(item["lon"]), village, district, a.get("state"), _pin(a.get("postcode")),
                 item.get("display_name"))


def reverse(lat: float, lon: float) -> Place | None:
    if not enabled():
        return None
    key = f"osm:rev:{lat:.4f}:{lon:.4f}"
    hit = get_json(key)
    if hit:
        return Place(**hit) if hit.get("latitude") is not None else None
    data = _nominatim("/reverse", {"lat": lat, "lon": lon, "zoom": 16, "addressdetails": 1})
    if not data or "lat" not in data:
        set_json(key, {"latitude": None}, ex=FAIL_TTL)
        return None
    place = _place_from(data)
    place.latitude, place.longitude = lat, lon  # keep her exact position
    set_json(key, place.__dict__, ex=30 * 24 * 3600)
    return place


def _search(params: dict) -> Place | None:
    data = _nominatim("/search", {"countrycodes": "in", "addressdetails": 1, "limit": 1, **params})
    return _place_from(data[0]) if isinstance(data, list) and data else None


def _india_post(pin: str) -> dict | None:
    """India Post directory: post offices, district and state for a pincode (no coordinates)."""
    try:
        with _client() as c:
            r = c.get(settings.osm_pincode_api.rstrip("/") + "/" + pin)
            r.raise_for_status()
            body = r.json()
        rec = body[0] if isinstance(body, list) and body else {}
        offices = rec.get("PostOffice") or []
        if rec.get("Status") != "Success" or not offices:
            return None
        return {"offices": [o.get("Name") for o in offices if o.get("Name")], "district": offices[0].get("District"),
                "state": offices[0].get("State"), "block": offices[0].get("Block")}
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.info("India Post lookup failed: %s", exc)
        return None


def geocode_pincode(pin: str) -> Place | None:
    if not enabled() or not re.fullmatch(r"\d{6}", pin or ""):
        return None
    key = f"osm:pin:{pin}"
    hit = get_json(key)
    if hit:
        return Place(**hit) if hit.get("latitude") is not None else None
    place = _search({"postalcode": pin})
    if place and place.pincode not in (None, pin):
        place = None
    if not place:  # Nominatim's pincode coverage in India is patchy: locate the pincode's post office instead
        post = _india_post(pin)
        if post:
            for office in post["offices"][:3]:
                place = _search({"q": f"{office}, {post['district']}, {post['state']}"})
                if place:
                    break
            if not place and post["district"]:
                place = _search({"q": f"{post['district']}, {post['state']}"})
            if place:
                place.district = post["district"] or place.district
                place.state = post["state"] or place.state
                place.village = place.village or (post["offices"][0] if post["offices"] else None)
    if not place:
        set_json(key, {"latitude": None}, ex=FAIL_TTL)
        return None
    place.pincode = pin
    set_json(key, place.__dict__, ex=90 * 24 * 3600)
    return place


def geocode_place(name: str, pincode: str | None = None) -> Place | None:
    name = (name or "").strip()
    if not enabled() or len(name) < 2:
        return None
    key = f"osm:place:{name.lower()}:{pincode or ''}"
    hit = get_json(key)
    if hit:
        return Place(**hit) if hit.get("latitude") is not None else None
    place = None
    if pincode:
        place = _search({"q": name, "postalcode": pincode}) or _search({"q": f"{name} {pincode}"})
    place = place or _search({"q": name})
    if not place:
        set_json(key, {"latitude": None}, ex=FAIL_TTL)
        return None
    set_json(key, place.__dict__, ex=90 * 24 * 3600)
    return place


# --- health facilities -------------------------------------------------------------
_TYPE_RULES = [
    ("DH", re.compile(r"\b(district|zila|jila|civil|sadar|general)\b.*\bhospital|\bmedical college|\baiims\b|"
                      r"\bdistrict women|\bdistrict hospital|जिला (अस्पताल|चिकित्सालय)|महिला चिकित्सालय", re.I)),
    ("CHC", re.compile(r"\bc\.?h\.?c\b|community health (centre|center)|सामुदायिक स्वास्थ्य", re.I)),
    ("PHC", re.compile(r"\bp\.?h\.?c\b|primary health (centre|center)|प्राथमिक स्वास्थ्य|\bupHC\b|urban primary", re.I)),
    ("SC", re.compile(r"\bsub[- ]?(centre|center|health)|\bh\.?s\.?c\b|health and wellness|wellness cent|"
                      r"ayushman arogya|उप ?(स्वास्थ्य )?केन्द्र|उप ?केंद्र", re.I)),
]


def classify(tags: dict) -> str | None:
    name = " ".join(filter(None, [tags.get("name"), tags.get("name:en"), tags.get("official_name")]))
    for ftype, rx in _TYPE_RULES:
        if rx.search(name):
            return ftype
    if tags.get("amenity") == "hospital" or tags.get("healthcare") == "hospital":
        return "HOSP"
    if tags.get("amenity") in ("clinic", "doctors") or tags.get("healthcare") in ("clinic", "centre", "doctor"):
        return "CLINIC"
    return None


def _overpass(lat: float, lon: float) -> list[dict] | None:
    query = (f"[out:json][timeout:25];("
             f'nwr(around:{HOSPITAL_RADIUS_M},{lat},{lon})["amenity"="hospital"];'
             f'nwr(around:{HOSPITAL_RADIUS_M},{lat},{lon})["healthcare"="hospital"];'
             f'nwr(around:{CLINIC_RADIUS_M},{lat},{lon})["amenity"~"^(clinic|doctors)$"];'
             f'nwr(around:{CLINIC_RADIUS_M},{lat},{lon})["healthcare"~"^(centre|clinic)$"];'
             f");out center tags 400;")
    for url in [u.strip() for u in settings.osm_overpass_urls.split(",") if u.strip()]:
        try:
            with _client() as c:
                r = c.post(url, data={"data": query}, timeout=max(settings.osm_timeout_seconds, 25))
                r.raise_for_status()
                return r.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as exc:
            log.info("Overpass %s failed: %s", url, exc)
    return None


def _facility_fields(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    name = (tags.get("name:en") or tags.get("name") or "").strip()
    if not name:
        return None
    ftype = classify(tags)
    if not ftype:
        return None
    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None
    hospital = ftype in ("DH", "CHC", "HOSP")
    emergency = tags.get("emergency")
    hours = tags.get("opening_hours")
    addr = ", ".join(x for x in [tags.get("addr:housename"), tags.get("addr:street"), tags.get("addr:village")
                                 or tags.get("addr:city"), tags.get("addr:district")] if x) or tags.get("addr:full")
    phone = (tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or "").split(";")[0].strip()
    return {
        "name": name[:160], "facility_type": ftype, "address": (addr or None) and addr[:255],
        "village": tags.get("addr:village") or tags.get("addr:city") or tags.get("addr:suburb"),
        "district": tags.get("addr:district"), "state": tags.get("addr:state"), "pincode": _pin(tags.get("addr:postcode")),
        "latitude": float(lat), "longitude": float(lon), "phone": phone[:20] or None,
        "is_24x7": hours == "24/7" or (hospital and emergency == "yes"),
        "has_emergency": emergency == "yes" or (hospital and emergency != "no"),
        "has_blood_bank": tags.get("healthcare:speciality", "").find("blood") >= 0,
        "services": [s for s in (tags.get("healthcare:speciality") or "").split(";") if s][:8],
        "opd_hours": (hours or None) and hours[:60],
    }


def load_area(db: Session, lat: float, lon: float, *, force: bool = False) -> int:
    """Download real health facilities around a point once per area and store them. Returns rows added/updated."""
    if not enabled():
        return 0
    cell = f"{round(lat / AREA_CELL_DEG) * AREA_CELL_DEG:.1f}:{round(lon / AREA_CELL_DEG) * AREA_CELL_DEG:.1f}"
    key = f"osm:area:{cell}"
    if not force and get_json(key):
        return 0
    elements = _overpass(lat, lon)
    if elements is None:
        set_json(key, {"failed": True}, ex=FAIL_TTL)
        return 0
    count = 0
    box = 0.45  # ~50 km: everything this download can contain
    existing = {f.external_id: f for f in db.scalars(select(Facility).where(
        Facility.source == "osm", Facility.latitude.between(lat - box, lat + box),
        Facility.longitude.between(lon - box, lon + box))).all() if f.external_id}
    for el in elements:
        fields = _facility_fields(el)
        if not fields:
            continue
        ext = f"osm:{el.get('type', 'node')}/{el.get('id')}"
        f = existing.get(ext)
        if f is None:
            f = Facility(source="osm", external_id=ext, active=True, **fields)
            db.add(f)
            existing[ext] = f
        else:
            for k, v in fields.items():
                setattr(f, k, v)
        count += 1
    db.commit()
    set_json(key, {"loaded": count}, ex=AREA_TTL)
    log.info("OpenStreetMap: %s health facilities stored around %.3f,%.3f", count, lat, lon)
    return count
