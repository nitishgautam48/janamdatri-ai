"""Emergency alerts and the escalation chain.

Mother → Assigned ASHA → Backup ASHA → ANM → Nearest facility → Emergency services (108)

On alert the assigned ASHA and the family contact are notified at once and 108 is offered
as a one-tap call. Unacknowledged alerts move one step up the chain every
``ESCALATION_STEP_MINUTES`` (Celery beat, or ``escalate_due`` from a cron).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asha.assignment import rank_candidates
from app.core.config import settings
from app.core.enums import CareLevel, EmergencyStatus, NotificationType
from app.core.security import normalize_phone
from app.database.base import utcnow
from app.database.models import AshaWorker, EmergencyEvent, Mother
from app.gis.service import Location, mother_location, nearby_facilities, recommend_facility, resolve_location
from app.notifications.service import notify
from app.notifications.sms import send_sms

NUMBERS = [
    {"number": "108", "label": "Ambulance", "label_hi": "एम्बुलेंस", "primary": True},
    {"number": "102", "label": "Pregnancy transport (JSSK)", "label_hi": "गर्भवती महिलाओं के लिए वाहन"},
    {"number": "14416", "label": "Tele-MANAS mental health support", "label_hi": "टेली-मानस मानसिक स्वास्थ्य सहायता"},
    {"number": "112", "label": "National emergency", "label_hi": "राष्ट्रीय आपातकालीन नंबर"},
    {"number": "181", "label": "Women helpline", "label_hi": "महिला हेल्पलाइन"},
]
CHAIN = ["mother", "assigned_asha", "backup_asha", "anm", "nearest_facility", "emergency_services"]


def _maps(lat, lon) -> str | None:
    return f"https://www.google.com/maps?q={lat},{lon}" if lat is not None and lon is not None else None


def _now_iso() -> str:
    return utcnow().isoformat()


def _anonymous_asha(db: Session, loc: Location | None) -> tuple[AshaWorker | None, AshaWorker | None]:
    """For alerts without an account: the ASHA serving that location."""
    if not loc:
        return None, None
    probe = Mother(village=loc.village, pincode=loc.pincode, latitude=loc.latitude, longitude=loc.longitude,
                   sub_centre_code=loc.sub_centre_code, sector_code=loc.sector_code)
    ranked = rank_candidates(db, probe)
    return (ranked[0].asha if ranked else None), (ranked[1].asha if len(ranked) > 1 else None)


def _notify_asha(db: Session, asha: AshaWorker, ev: EmergencyEvent, who: str, level_note: str = "") -> None:
    where = ev.location_text or (f"{ev.latitude:.4f},{ev.longitude:.4f}" if ev.latitude is not None else "location not shared")
    text = f"{who} needs help ({ev.emergency_type or 'SOS'}). Location: {where}. {_maps(ev.latitude, ev.longitude) or ''}"
    if asha.user_id:
        notify(db, asha.user_id, NotificationType.EMERGENCY, f"EMERGENCY{level_note}", text.strip(),
               data={"emergency_id": str(ev.id), "mother_id": str(ev.mother_id) if ev.mother_id else None,
                     "maps_url": _maps(ev.latitude, ev.longitude)}, priority="urgent", sms=True)
    else:
        send_sms(asha.phone, f"EMERGENCY{level_note}: {text.strip()}")


def _step(chain: list[dict], key: str) -> dict:
    return next(s for s in chain if s["key"] == key)


def raise_alert(db: Session, *, mother: Mother | None, user_id=None, lat=None, lon=None, pincode=None, village=None,
                emergency_type: str | None = None, description: str | None = None, caller_phone: str | None = None,
                notify_asha: bool = True) -> EmergencyEvent:
    loc = resolve_location(db, lat=lat, lon=lon, pincode=pincode, village=village)
    if loc is None and mother:
        loc = mother_location(db, mother)
    facility = recommend_facility(db, loc, CareLevel.IMMEDIATE_CARE) if loc else None
    if mother:
        asha = db.get(AshaWorker, mother.assigned_asha_id) if mother.assigned_asha_id else None
        backup = db.get(AshaWorker, mother.backup_asha_id) if mother.backup_asha_id else None
    else:
        asha, backup = _anonymous_asha(db, loc)
    anm_source = asha or backup
    ev = EmergencyEvent(
        mother_id=mother.id if mother else None, user_id=user_id, public_token=secrets.token_urlsafe(24),
        emergency_type=emergency_type, description=description,
        caller_phone=normalize_phone(caller_phone) if caller_phone else None,
        latitude=loc.latitude if loc else lat, longitude=loc.longitude if loc else lon,
        location_text=", ".join(x for x in [loc.village, loc.district] if x) if loc else None,
        pincode=loc.pincode if loc else pincode, status=EmergencyStatus.ACTIVE, escalation_level=1,
        nearest_facility=facility, last_escalated_at=utcnow(),
    )
    chain = [
        {"key": "mother", "label": "Mother", "name": mother.name if mother else "Caller", "status": "raised", "at": _now_iso()},
        {"key": "assigned_asha", "label": "Assigned ASHA", "name": asha.name if asha else None,
         "status": "pending" if asha else "unavailable", "asha_id": str(asha.id) if asha else None},
        {"key": "backup_asha", "label": "Backup ASHA", "name": backup.name if backup else None,
         "status": "ready" if backup else "unavailable", "asha_id": str(backup.id) if backup else None},
        {"key": "anm", "label": "ANM", "name": anm_source.anm_name if anm_source else None,
         "status": "ready" if anm_source and anm_source.anm_phone else "unavailable"},
        {"key": "nearest_facility", "label": "Nearest facility", "name": facility["name"] if facility else None,
         "status": "ready" if facility else "unavailable"},
        {"key": "emergency_services", "label": "Emergency services (108)", "name": "108 Ambulance", "status": "call_now"},
    ]
    db.add(ev)
    db.flush()
    who = (mother.name if mother and mother.name else "A pregnant woman")
    if asha and notify_asha:
        _notify_asha(db, asha, ev, who)
        _step(chain, "assigned_asha").update(status="notified", at=_now_iso())
    if mother:
        for c in mother.emergency_contacts[:2]:
            send_sms(c.phone, f"Janamdatri SOS: {who} needs help now. {_maps(ev.latitude, ev.longitude) or ''} "
                              f"Nearest hospital: {facility['name'] if facility else 'call 108'}. Call 108 for an ambulance.")
    ev.escalation_chain = chain
    return ev


def update_location(db: Session, ev: EmergencyEvent, lat: float, lon: float) -> EmergencyEvent:
    loc = resolve_location(db, lat=lat, lon=lon)
    ev.latitude, ev.longitude = lat, lon
    if loc:
        ev.location_text = ", ".join(x for x in [loc.village, loc.district] if x) or ev.location_text
        ev.pincode = loc.pincode or ev.pincode
        ev.nearest_facility = recommend_facility(db, loc, CareLevel.IMMEDIATE_CARE) or ev.nearest_facility
    for s in ev.escalation_chain or []:
        if s["key"] in ("assigned_asha", "backup_asha") and s.get("status") in ("notified", "acknowledged") and s.get("asha_id"):
            asha = db.get(AshaWorker, uuid.UUID(s["asha_id"]))
            if asha and asha.user_id:
                notify(db, asha.user_id, NotificationType.EMERGENCY, "Live location updated",
                       f"New location: {_maps(lat, lon)}", data={"emergency_id": str(ev.id), "maps_url": _maps(lat, lon)},
                       priority="urgent")
    return ev


def acknowledge(db: Session, ev: EmergencyEvent, user_id, asha: AshaWorker | None) -> EmergencyEvent:
    ev.status = EmergencyStatus.ACKNOWLEDGED
    ev.acknowledged_by_user_id = user_id
    ev.acknowledged_at = utcnow()
    chain = [dict(s) for s in ev.escalation_chain or []]
    for s in chain:
        if asha and s.get("asha_id") == str(asha.id):
            s.update(status="acknowledged", at=_now_iso())
    ev.escalation_chain = chain
    if ev.mother_id:
        mother = db.get(Mother, ev.mother_id)
        if mother and mother.user_id:
            notify(db, mother.user_id, NotificationType.EMERGENCY, "Help is on the way",
                   f"{asha.name if asha else 'Your health worker'} has seen your alert and is responding.",
                   data={"emergency_id": str(ev.id)}, priority="urgent")
    return ev


def escalate(db: Session, ev: EmergencyEvent) -> bool:
    """Move an unacknowledged alert one step up the chain. Returns True if something changed."""
    if ev.status != EmergencyStatus.ACTIVE:
        return False
    chain = [dict(s) for s in ev.escalation_chain or []]
    who = "A pregnant woman"
    if ev.mother_id and (m := db.get(Mother, ev.mother_id)) and m.name:
        who = m.name
    for level in range(ev.escalation_level + 1, len(CHAIN)):
        step = chain[level]
        if step["status"] in ("unavailable",):
            continue
        key = step["key"]
        if key == "backup_asha" and step.get("asha_id"):
            _notify_asha(db, db.get(AshaWorker, uuid.UUID(step["asha_id"])), ev, who, " (escalated)")
        elif key == "anm":
            src = next((db.get(AshaWorker, uuid.UUID(s["asha_id"])) for s in chain if s.get("asha_id")), None)
            if src and src.anm_phone:
                send_sms(src.anm_phone, f"Janamdatri ESCALATION: {who} needs urgent help, ASHA not responding. "
                                        f"{_maps(ev.latitude, ev.longitude) or ev.location_text or ''}")
        elif key == "nearest_facility" and ev.nearest_facility and ev.nearest_facility.get("phone"):
            send_sms(ev.nearest_facility["phone"], f"Janamdatri ESCALATION: obstetric emergency, {who}. "
                                                    f"{_maps(ev.latitude, ev.longitude) or ev.location_text or ''}")
        elif key == "emergency_services" and ev.mother_id:
            m = db.get(Mother, ev.mother_id)
            if m and m.user_id:
                notify(db, m.user_id, NotificationType.EMERGENCY, "Please call 108 now",
                       "We could not reach your health workers yet. Call 108 for a free ambulance.",
                       priority="urgent", sms=True)
        step.update(status="notified", at=_now_iso())
        ev.escalation_level = level
        ev.last_escalated_at = utcnow()
        ev.escalation_chain = chain
        return True
    return False


def escalate_due(db: Session) -> int:
    cutoff = utcnow() - timedelta(minutes=settings.escalation_step_minutes)
    rows = db.scalars(select(EmergencyEvent).where(EmergencyEvent.status == EmergencyStatus.ACTIVE,
                                                   EmergencyEvent.last_escalated_at <= cutoff)).all()
    n = sum(1 for ev in rows if escalate(db, ev))
    db.commit()
    return n


def event_dict(ev: EmergencyEvent, *, include_token: bool = False) -> dict:
    f = ev.nearest_facility or {}
    chain = ev.escalation_chain or []
    asha_step = next((s for s in chain if s["key"] == "assigned_asha"), {})
    backup = next((s for s in chain if s["key"] == "backup_asha"), {})
    live = [
        {"key": "location", "done": ev.latitude is not None, "text": f"Location found · {ev.location_text}" if ev.location_text
         else ("Location found" if ev.latitude is not None else "Share your location"),
         "text_hi": "लोकेशन मिल गई" if ev.latitude is not None else "लोकेशन भेजें"},
        {"key": "facility", "done": bool(f), "text": f"Nearest facility found · {f.get('type', '')} · {f.get('distance_km', '?')} km" if f
         else "Finding nearest facility…", "text_hi": "नज़दीकी अस्पताल मिल गया" if f else "अस्पताल ढूँढ रहे हैं…"},
        {"key": "asha", "done": asha_step.get("status") in ("notified", "acknowledged"),
         "text": (f"ASHA {(asha_step.get('name') or '').split(' ')[0]} " + ("is responding" if asha_step.get("status") == "acknowledged" else "notified"))
         if asha_step.get("name") else "No ASHA linked; call 108", "text_hi": "आशा दीदी को सूचना दी गई" if asha_step.get("name") else "108 पर कॉल करें"},
        {"key": "support", "done": ev.status == EmergencyStatus.ACKNOWLEDGED,
         "text": "Support connected" if ev.status == EmergencyStatus.ACKNOWLEDGED else "Connecting support…",
         "text_hi": "सहायता जुड़ गई" if ev.status == EmergencyStatus.ACKNOWLEDGED else "सहायता जोड़ रहे हैं…"},
        {"key": "backup", "done": bool(backup.get("name")), "text": "Backup ASHA · Contact ready" if backup.get("name") else "Backup ASHA not available",
         "text_hi": "बैकअप आशा तैयार" if backup.get("name") else "बैकअप आशा उपलब्ध नहीं"},
    ]
    d = {
        "id": str(ev.id), "status": ev.status, "created_at": ev.created_at, "emergency_type": ev.emergency_type,
        "location": {"latitude": ev.latitude, "longitude": ev.longitude, "text": ev.location_text, "pincode": ev.pincode,
                     "maps_url": _maps(ev.latitude, ev.longitude)},
        "nearest_facility": ev.nearest_facility, "escalation_level": ev.escalation_level,
        "escalation_chain": [{k: v for k, v in s.items() if k != "asha_id"} for s in chain],
        "live_status": live, "numbers": NUMBERS,
        "actions": [{"type": "call", "number": "108", "label": "Call 108"},
                    {"type": "share_location", "label": "Share Location"},
                    {"type": "find_facility", "label": "To Hospital", "directions_url": f.get("directions_url")},
                    {"type": "notify_asha", "label": "Notify ASHA", "done": asha_step.get("status") in ("notified", "acknowledged")}],
        "acknowledged_at": ev.acknowledged_at,
    }
    if include_token:
        d["public_token"] = ev.public_token
    return d


def nearest_emergency_facilities(db: Session, loc: Location, limit: int = 5) -> list[dict]:
    items = nearby_facilities(db, loc, emergency_only=True, radius_km=100, limit=limit)
    return items or nearby_facilities(db, loc, radius_km=100, limit=limit)
