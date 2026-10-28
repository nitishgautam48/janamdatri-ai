from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentAsha, CurrentMother, CurrentPrincipal, asha_can_access, get_current_mother
from app.asha import area, report, service, worklist
from app.asha.assignment import assign_asha, reason_text
from app.core.audit import audit
from app.core.enums import MessageType, Role
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import normalize_phone
from app.database.models import AshaWorker, Mother, User
from app.messaging import service as messaging
from app.messaging.service import presence
from app.notifications.sms import send_sms
from app.passport import service as passport_service
from app.passport.schemas import AboutYou, PassportIn, PregnancyIn

router = APIRouter(prefix="/asha", tags=["ASHA Worker"])


class LinkIn(BaseModel):
    asha_reg_no: str | None = Field(None, description="Mother: link to a specific ASHA. Empty = re-run auto matching.")
    mother_id: uuid.UUID | None = Field(None, description="ASHA: link an existing mother to yourself")
    mother_phone: str | None = Field(None, description="ASHA: find the mother by phone")
    as_backup: bool = False


class RegisterMotherIn(BaseModel):
    name: str = Field(max_length=120)
    age: int | None = Field(None, ge=12, le=60)
    phone: str
    village: str | None = Field(None, max_length=120)
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    rch_id: str | None = Field(None, max_length=32)
    status: Literal["pregnant", "postnatal", "planning"] = "pregnant"
    language: str = "hi"
    lmp: date | None = Field(None, description="Optional; fills the passport's pregnancy step")
    create_passport: bool = True


class AshaUpdate(BaseModel):
    available: bool | None = None
    villages_served: list[str] | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


def asha_public(a: AshaWorker | None, mother: Mother | None = None) -> dict | None:
    if not a:
        return None
    return {"id": str(a.id), "name": a.name, "role": "ASHA Worker", "role_hi": "आशा कार्यकर्ता", "verified": a.verified,
            "available": a.available, "presence": presence(a) if a.available else "unavailable",
            "sub_centre": a.sub_centre_name or a.sub_centre_code, "district": a.district,
            "phone": "hidden", "contact": {"message": "/api/v1/messages/send", "secure_call": "/api/v1/messages/call"}}


@router.get("/assigned")
def assigned(db: DB, mother: CurrentMother):
    """The mother's ASHA (and backup). Phone numbers are never shown; use messages or secure call."""
    if not mother.assigned_asha_id and (mother.village or mother.pincode or mother.latitude is not None):
        assign_asha(db, mother)
        db.commit()
    a = db.get(AshaWorker, mother.assigned_asha_id) if mother.assigned_asha_id else None
    b = db.get(AshaWorker, mother.backup_asha_id) if mother.backup_asha_id else None
    if not a:
        return {"assigned": None, "backup": None,
                "message": ("No ASHA worker has joined the app near you yet. You will be connected automatically as soon "
                            "as one does. Meanwhile the Health Assistant and SOS (108) are always available."
                            if mother.village or mother.pincode or mother.latitude is not None else
                            "Add your village or pincode to your passport and we'll match you with your ASHA."),
                "message_hi": ("आपके पास अभी कोई आशा दीदी ऐप से नहीं जुड़ी हैं। जैसे ही कोई जुड़ेंगी, आप अपने-आप जुड़ जाएँगी। "
                               "तब तक स्वास्थ्य सहायक और SOS (108) हमेशा उपलब्ध हैं।")}
    return {"assigned": asha_public(a, mother), "backup": asha_public(b, mother),
            "matched_by": mother.assignment_reason, "privacy": "Your phone number remains private.",
            "privacy_hi": "आपका नंबर निजी रहेगा।"}


@router.post("/link")
def link(body: LinkIn, db: DB, p: CurrentPrincipal):
    if p.role == Role.MOTHER:
        mother = get_current_mother(db, p)
        if body.asha_reg_no:
            a = db.scalar(select(AshaWorker).where(func.upper(AshaWorker.asha_reg_no) == body.asha_reg_no.strip().upper(),
                                                   AshaWorker.verified.is_(True)))
            if not a:
                raise NotFound("No verified ASHA with that registration number")
            if a.id != mother.assigned_asha_id:
                mother.backup_asha_id = mother.assigned_asha_id or mother.backup_asha_id
            mother.assigned_asha_id = a.id
            mother.assignment_reason = "Linked by you using the ASHA registration number"
        else:
            c = assign_asha(db, mother, force=True)
            if not c:
                raise NotFound("No ASHA found near you yet. Check your village and pincode.", code="no_asha_nearby")
        db.commit()
        return assigned(db, mother)
    if p.role == Role.ASHA:
        asha = db.scalar(select(AshaWorker).where(AshaWorker.user_id == p.user.id))
        if not asha or not asha.verified:
            raise Forbidden("ASHA verification pending")
        mother = db.get(Mother, body.mother_id) if body.mother_id else None
        if not mother and body.mother_phone:
            u = db.scalar(select(User).where(User.phone == normalize_phone(body.mother_phone), User.role == Role.MOTHER))
            mother = u.mother if u else None
        if not mother:
            raise NotFound("Mother not found")
        if body.as_backup:
            mother.backup_asha_id = asha.id
        elif mother.assigned_asha_id and mother.assigned_asha_id != asha.id:
            raise Conflict("This mother already has an assigned ASHA; link as backup or ask your ANM to transfer")
        else:
            mother.assigned_asha_id = asha.id
            mother.assignment_reason = "Linked by ASHA"
        audit(db, p.id, "asha.link", "mother", mother.id, as_backup=body.as_backup)
        db.commit()
        return {"linked": True, "mother": service.mother_row(db, mother, asha)}
    raise Forbidden("Not available for this account type")


@router.get("/me")
def me(asha: CurrentAsha):
    return {**asha_public(asha), "asha_reg_no": asha.asha_reg_no, "rch_id": asha.rch_id, "awc_code": asha.awc_code,
            "sub_centre_code": asha.sub_centre_code, "sector_code": asha.sector_code, "village": asha.village,
            "villages_served": asha.villages_served, "pincode": asha.pincode, "anm_name": asha.anm_name}


@router.patch("/me")
def update_me(body: AshaUpdate, db: DB, asha: CurrentAsha):
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(asha, k, v)
    db.commit()
    return me(asha)


class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


@router.post("/me/location")
def set_location(body: LocationIn, db: DB, asha: CurrentAsha):
    """Set her working area from the phone's GPS; mothers nearby who have no ASHA yet are connected to her."""
    return area.set_area(db, asha, body.latitude, body.longitude)


@router.get("/map")
def asha_map(db: DB, asha: CurrentAsha):
    """Her mothers on the map (care level, week, distance) and the PHC / CHC / DH near her."""
    return area.map_data(db, asha)


@router.get("/worklist")
def todays_worklist(db: DB, asha: CurrentAsha, lat: float | None = Query(None, ge=-90, le=90),
                    lon: float | None = Query(None, ge=-180, le=180)):
    """Today's smart work list: each mother's tasks (with reasons), ordered by urgency and then the shortest way
    from her current position (or her area), with distance and time for each stop."""
    return worklist.worklist(db, asha, lat, lon)


class DoneIn(BaseModel):
    done: bool = True


def _my_mother(db, asha: AshaWorker, mother_id: uuid.UUID) -> Mother:
    mother = db.get(Mother, mother_id)
    if not mother or not asha_can_access(asha, mother):
        raise Forbidden("This mother is not assigned to you")
    return mother


@router.post("/worklist/{mother_id}/done")
def worklist_done(mother_id: uuid.UUID, body: DoneIn, db: DB, asha: CurrentAsha):
    """Tick a mother off today's list (recording a visit does this automatically)."""
    _my_mother(db, asha, mother_id)
    worklist.mark_done(db, asha, str(mother_id), body.done, date.today())
    return {"mother_id": str(mother_id), "done": body.done}


REMINDERS = {
    "anc": ("Namaste {name}, your ANC check-up is due. Please go to your PHC on the next check-up day, or tell me a "
            "good time and I will come. – {asha}, ASHA",
            "नमस्ते {name}, आपकी ANC जाँच का समय हो गया है। अगले जाँच दिवस पर PHC जाएँ, या मुझे बताएँ कब आऊँ। – {asha}, आशा दीदी"),
    "ifa": ("Namaste {name}, please take your iron tablet every day after lunch, with lemon water, not tea. – {asha}, ASHA",
            "नमस्ते {name}, आयरन की गोली रोज़ दोपहर के खाने के बाद नींबू पानी से लें, चाय से नहीं। – {asha}, आशा दीदी"),
    "birth_prep": ("Namaste {name}, your due date is near. Keep your MCP card, a bag for the hospital and the 102 number "
                   "ready. – {asha}, ASHA",
                   "नमस्ते {name}, प्रसव का समय पास है। MCP कार्ड, अस्पताल का थैला और 102 नंबर तैयार रखें। – {asha}, आशा दीदी"),
    "visit": ("Namaste {name}, I will visit you today. Please call me in the app if this is not a good time. – {asha}, ASHA",
              "नमस्ते {name}, मैं आज आपसे मिलने आऊँगी। समय ठीक न हो तो ऐप में बताएँ। – {asha}, आशा दीदी"),
}


class RemindIn(BaseModel):
    kind: Literal["anc", "ifa", "birth_prep", "visit"] = "visit"


@router.post("/worklist/{mother_id}/remind")
def worklist_remind(mother_id: uuid.UUID, body: RemindIn, db: DB, p: CurrentPrincipal, asha: CurrentAsha):
    """Send her a friendly reminder: in the app chat and by SMS (Mailpit inbox in the prototype). Her number stays hidden."""
    mother = _my_mother(db, asha, mother_id)
    en, hi = REMINDERS[body.kind]
    text = (hi if mother.language == "hi" else en).format(name=(mother.name or "").split(" ")[0] or "didi",
                                                           asha=asha.name.split(" ")[0])
    messaging.send(db, p, mother, asha, message_type=MessageType.TEXT, body=text)
    sms = bool(mother.user and mother.user.phone and send_sms(mother.user.phone, text))
    db.commit()
    return {"sent": True, "sms": sms, "text": text}


MONTH = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; default this month")


def _report(db, asha, month):
    r = report.build(db, asha, month)
    r.pop("_mothers", None)
    return r


@router.get("/report")
def monthly_report(db: DB, asha: CurrentAsha, month: str | None = MONTH,
                   format: Literal["json", "csv", "html"] = Query("json")):
    """Monthly work report for the ANM (visits, ANC, high-risk, referrals, deliveries, IFA) and estimated incentives.
    ``format=csv`` downloads a spreadsheet; ``format=html`` is a printable page (Print → Save as PDF)."""
    r = _report(db, asha, month)
    if format == "csv":
        return Response(report.to_csv(r), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="asha-report-{r["month"]}.csv"'})
    if format == "html":
        return Response(report.to_html(r), media_type="text/html")
    return r


@router.post("/report/share")
def share_report(db: DB, asha: CurrentAsha, month: str | None = MONTH):
    """Send the month's summary to her ANM by SMS (Mailpit inbox in the prototype)."""
    if not asha.anm_phone:
        raise ValidationFailed("No ANM mobile number is saved for you", code="anm_phone_missing")
    r = _report(db, asha, month)
    text = report.sms_summary(r)
    return {"sent": send_sms(asha.anm_phone, text), "to": asha.anm_name or "ANM", "text": text}


@router.get("/dashboard")
def dashboard(db: DB, asha: CurrentAsha):
    """Assigned mothers, today's visits, ANC due, follow-ups, emergencies, referrals, messages, reports."""
    return service.dashboard(db, asha)


@router.get("/mothers")
def mothers(db: DB, asha: CurrentAsha, filter: str | None = Query(None, description="anc_due | follow_up | high_risk | visit_today"),
            q: str | None = Query(None, description="Search name or village")):
    if filter and filter not in service.FILTERS:
        raise ValidationFailed("Unknown filter", details={"allowed": sorted(service.FILTERS)})
    rows = service.list_mothers(db, asha, filter, q)
    return {"count": len(rows), "mothers": rows}


@router.get("/mothers/{mother_id}")
def mother_detail(mother_id: uuid.UUID, db: DB, p: CurrentPrincipal, asha: CurrentAsha):
    mother = db.get(Mother, mother_id)
    if not mother or not asha_can_access(asha, mother):
        raise Forbidden("This mother is not assigned to you")
    audit(db, p.id, "mother.view", "mother", mother.id)
    db.commit()
    return {"summary": service.mother_row(db, mother, asha),
            "passport": passport_service.passport_view(db, mother, Role.ASHA) if mother.passport else None}


@router.post("/mothers/register", status_code=status.HTTP_201_CREATED)
def register_mother(body: RegisterMotherIn, db: DB, p: CurrentPrincipal, asha: CurrentAsha):
    """ASHA registers a mother (creates her account, which she can claim by OTP) and optionally her passport."""
    phone = normalize_phone(body.phone)
    user = db.scalar(select(User).where(User.phone == phone))
    if user and (user.role != Role.MOTHER or user.mother is None):
        raise Conflict("This number is already used by another account")
    if user:
        raise Conflict("This mother is already registered; use /asha/link", code="mother_exists",
                       details={"mother_id": str(user.mother.id)})
    user = User(role=Role.MOTHER, phone=phone, full_name=body.name, language=body.language)
    db.add(user)
    db.flush()
    mother = Mother(user_id=user.id, name=body.name, age=body.age, village=body.village, pincode=body.pincode,
                    rch_id=body.rch_id, status=body.status, language=body.language,
                    registered_by_asha_id=asha.id, assigned_asha_id=asha.id, sub_centre_code=asha.sub_centre_code,
                    sector_code=asha.sector_code, assignment_reason=reason_text(["registration by ASHA"]))
    db.add(mother)
    db.flush()
    if body.create_passport:
        preg = PregnancyIn(lmp=body.lmp) if body.lmp else None
        passport_service.apply_passport(db, mother, PassportIn(
            about=AboutYou(name=body.name, age=body.age, village=body.village, pincode=body.pincode, rch_id=body.rch_id),
            pregnancy=preg), p.user)
        if mother.assigned_asha_id != asha.id:  # registering ASHA stays primary
            mother.backup_asha_id = mother.assigned_asha_id if mother.assigned_asha_id != asha.id else mother.backup_asha_id
            mother.assigned_asha_id = asha.id
    audit(db, p.id, "mother.register", "mother", mother.id)
    db.commit()
    return {"mother_id": str(mother.id), "summary": service.mother_row(db, mother, asha),
            "next": "create_passport" if not body.create_passport else "upload_reports"}
