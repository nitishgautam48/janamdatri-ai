"""Reference data: facilities, gazetteer (pincode/village → coordinates & codes), ASHA registry, admin.

Idempotent. Coordinates around Bareilly (UP) match the design prototype (Rampur village,
PHC Rampur, CHC Bhojipura, District Hospital Bareilly). Replace with your state's
facility master (e.g. NIN / HMIS export) and ASHA registry for a real deployment.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import Role
from app.core.security import hash_secret
from app.database.models import AshaWorker, Facility, GISMapping, User

log = logging.getLogger(__name__)

UP, BRL = "Uttar Pradesh", "Bareilly"

FACILITIES = [
    dict(name="Rampur Sub Centre", facility_type="SC", village="Rampur", district=BRL, state=UP, pincode="243001",
         latitude=28.2640, longitude=79.3520, phone="+919800070001", services=["ANC", "Immunisation", "IFA"], opd_hours="9 AM – 4 PM"),
    dict(name="PHC Rampur", facility_type="PHC", village="Rampur", district=BRL, state=UP, pincode="243001",
         latitude=28.2765, longitude=79.3500, phone="+919800070002", is_24x7=True,
         services=["ANC", "Normal delivery", "Lab: Hb, urine, sugar", "Family planning"], opd_hours="8 AM – 2 PM"),
    dict(name="CHC Bhojipura", facility_type="CHC", village="Bhojipura", district=BRL, state=UP, pincode="243202",
         latitude=28.3030, longitude=79.3950, phone="+919800070003", is_24x7=True, has_emergency=True,
         services=["Obstetrician", "Ultrasound", "C-section (FRU)", "Blood storage"], opd_hours="OPD till 2 PM"),
    dict(name="District Hospital Bareilly", facility_type="DH", address="Civil Lines, Bareilly", district=BRL, state=UP,
         pincode="243001", latitude=28.3670, longitude=79.4304, phone="+915812510000", is_24x7=True, has_emergency=True,
         has_blood_bank=True, services=["Emergency obstetric care", "ICU", "SNCU", "Blood bank", "C-section"], opd_hours="24×7"),
    dict(name="District Women Hospital Bareilly", facility_type="DH", address="Kutubkhana, Bareilly", district=BRL, state=UP,
         pincode="243003", latitude=28.3640, longitude=79.4150, phone="+915812420000", is_24x7=True, has_emergency=True,
         has_blood_bank=True, services=["Labour room (LaQshya)", "C-section", "SNCU", "Blood bank"], opd_hours="24×7"),
    dict(name="PHC Vigyan Nagar", facility_type="PHC", village="Vigyan Nagar", district=BRL, state=UP, pincode="243122",
         latitude=28.3450, longitude=79.4100, phone="+919800070004", services=["ANC", "Normal delivery", "Lab"], opd_hours="8 AM – 2 PM"),
    dict(name="CHC Faridpur", facility_type="CHC", village="Faridpur", district=BRL, state=UP, pincode="243503",
         latitude=28.2090, longitude=79.5410, phone="+919800070005", is_24x7=True, has_emergency=True,
         services=["Obstetrician", "C-section (FRU)"], opd_hours="OPD till 2 PM"),
    dict(name="PHC Nawabganj", facility_type="PHC", village="Nawabganj", district=BRL, state=UP, pincode="262406",
         latitude=28.5400, longitude=79.6330, phone="+919800070006", services=["ANC", "Normal delivery"], opd_hours="8 AM – 2 PM"),
    dict(name="CHC Nawabganj", facility_type="CHC", village="Nawabganj", district=BRL, state=UP, pincode="262406",
         latitude=28.5450, longitude=79.6400, phone="+919800070007", is_24x7=True, has_emergency=True,
         services=["Obstetrician", "Ultrasound"], opd_hours="OPD till 2 PM"),
]

GAZETTEER = [
    dict(pincode="243001", village="Rampur", block="Bhojipura", district=BRL, state=UP, latitude=28.2620, longitude=79.3500,
         sub_centre_code="SC-2208", sector_code="SEC-07"),
    dict(pincode="243001", village="Kesarpur", block="Bhojipura", district=BRL, state=UP, latitude=28.2500, longitude=79.3350,
         sub_centre_code="SC-2208", sector_code="SEC-07"),
    dict(pincode="243001", village="Mohanpur", block="Bhojipura", district=BRL, state=UP, latitude=28.2750, longitude=79.3650,
         sub_centre_code="SC-2208", sector_code="SEC-07"),
    dict(pincode="243202", village="Bhojipura", block="Bhojipura", district=BRL, state=UP, latitude=28.3000, longitude=79.3920,
         sub_centre_code="SC-2215", sector_code="SEC-08"),
    dict(pincode="243122", village="Vigyan Nagar", block="Bareilly", district=BRL, state=UP, latitude=28.3450, longitude=79.4080,
         sub_centre_code="SC-2230", sector_code="SEC-09"),
    dict(pincode="243503", village="Faridpur", block="Faridpur", district=BRL, state=UP, latitude=28.2100, longitude=79.5400,
         sub_centre_code="SC-2301", sector_code="SEC-12"),
    dict(pincode="262406", village="Nawabganj", block="Nawabganj", district=BRL, state=UP, latitude=28.5410, longitude=79.6350,
         sub_centre_code="SC-2402", sector_code="SEC-15"),
]

ASHAS = [
    dict(name="Sunita Devi", phone="+919800044421", asha_reg_no="UP-BRL-04417", rch_id="RCH-778812", awc_code="AWC-0912",
         sub_centre_code="SC-2208", sub_centre_name="Rampur Sub Centre", sector_code="SEC-07", village="Rampur",
         villages_served=["Rampur", "Kesarpur"], district=BRL, state=UP, pincode="243001", latitude=28.2630, longitude=79.3510,
         anm_name="Anita Sharma (ANM)", anm_phone="+919800055501"),
    dict(name="Kamla Yadav", phone="+919800044422", asha_reg_no="UP-BRL-04418", rch_id="RCH-778813", awc_code="AWC-0913",
         sub_centre_code="SC-2208", sub_centre_name="Rampur Sub Centre", sector_code="SEC-07", village="Mohanpur",
         villages_served=["Mohanpur", "Rampur"], district=BRL, state=UP, pincode="243001", latitude=28.2750, longitude=79.3650,
         anm_name="Anita Sharma (ANM)", anm_phone="+919800055501"),
    dict(name="Geeta Kumari", phone="+919800044501", asha_reg_no="UP-BRL-04502", rch_id="RCH-779020", awc_code="AWC-1010",
         sub_centre_code="SC-2215", sub_centre_name="Bhojipura Sub Centre", sector_code="SEC-08", village="Bhojipura",
         villages_served=["Bhojipura"], district=BRL, state=UP, pincode="243202", latitude=28.3010, longitude=79.3930,
         anm_name="Rubina Khan (ANM)", anm_phone="+919800055502"),
    dict(name="Rekha Singh", phone="+919800044610", asha_reg_no="UP-BRL-04610", rch_id="RCH-779311", awc_code="AWC-1104",
         sub_centre_code="SC-2230", sub_centre_name="Vigyan Nagar Sub Centre", sector_code="SEC-09", village="Vigyan Nagar",
         villages_served=["Vigyan Nagar"], district=BRL, state=UP, pincode="243122", latitude=28.3450, longitude=79.4090,
         anm_name="Meera Verma (ANM)", anm_phone="+919800055503"),
]


def seed_reference_data(db: Session) -> None:
    if not db.scalar(select(func.count()).select_from(Facility)):
        db.add_all(Facility(**f) for f in FACILITIES)
    for g in GAZETTEER:
        if not db.scalar(select(GISMapping.id).where(GISMapping.pincode == g["pincode"], GISMapping.village == g["village"])):
            db.add(GISMapping(**g))
    for a in ASHAS:
        if not db.scalar(select(AshaWorker.id).where(AshaWorker.asha_reg_no == a["asha_reg_no"])):
            db.add(AshaWorker(verified=True, available=True, **a))  # verified = present in the government registry
    db.commit()


def seed_admin(db: Session) -> None:
    if not settings.admin_email or not settings.admin_password:
        return
    if db.scalar(select(User.id).where(func.lower(User.email) == settings.admin_email.lower())):
        return
    db.add(User(role=Role.ADMIN, email=settings.admin_email.lower(), password_hash=hash_secret(settings.admin_password),
                full_name="Administrator"))
    db.commit()
    log.info("Created admin %s", settings.admin_email)


def run(db: Session) -> None:
    if settings.seed_reference_data:
        seed_reference_data(db)
    seed_admin(db)
