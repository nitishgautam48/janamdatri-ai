from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.core.audit import audit
from app.core.exceptions import Conflict, NotFound
from app.core.security import normalize_phone
from app.database.models import EmergencyContact
from app.passport import service
from app.passport.schemas import Completion, EmergencyContactIn, EmergencyContactOut, PassportIn

router = APIRouter(prefix="/passport", tags=["Maternal Health Passport"])


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_passport(body: PassportIn, db: DB, p: CurrentPrincipal):
    """Create the passport (any subset of the 5 steps). ASHA workers pass ``mother_id``."""
    mother = resolve_mother(db, p, body.mother_id)
    if mother.passport:
        raise Conflict("Passport already exists; use PUT /passport/update", code="passport_exists")
    service.apply_passport(db, mother, body, p.user)
    audit(db, p.id, "passport.create", "mother", mother.id)
    db.commit()
    db.refresh(mother)
    return service.passport_view(db, mother, p.role)


@router.get("")
def get_passport(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    if not mother.passport:
        raise NotFound("No passport yet. Create one to get started.", code="passport_not_found")
    if p.role != "mother":
        audit(db, p.id, "passport.view", "mother", mother.id)
        db.commit()
    return service.passport_view(db, mother, p.role)


@router.put("/update")
def update_passport(body: PassportIn, db: DB, p: CurrentPrincipal):
    mother = resolve_mother(db, p, body.mother_id)
    service.apply_passport(db, mother, body, p.user)
    audit(db, p.id, "passport.update", "mother", mother.id, sections=[
        k for k in ("about", "pregnancy", "medical_history", "emergency_contacts") if getattr(body, k) is not None])
    db.commit()
    db.refresh(mother)
    return service.passport_view(db, mother, p.role)


@router.get("/completion", response_model=Completion)
def get_completion(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    return service.completion(db, resolve_mother(db, p, mother_id))


@router.post("/emergency-contacts", response_model=EmergencyContactOut, status_code=201)
def add_contact(body: EmergencyContactIn, db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    if len(mother.emergency_contacts) >= 5:
        raise Conflict("You can save up to 5 emergency contacts")
    if body.is_primary:
        for c in mother.emergency_contacts:
            c.is_primary = False
    c = EmergencyContact(mother_id=mother.id, name=body.name, relation=body.relation,
                         phone=normalize_phone(body.phone), is_primary=body.is_primary or not mother.emergency_contacts)
    db.add(c)
    db.commit()
    return EmergencyContactOut(id=c.id, name=c.name, relation=c.relation, phone=c.phone, is_primary=c.is_primary)


@router.delete("/emergency-contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: uuid.UUID, db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    c = db.get(EmergencyContact, contact_id)
    if not c or c.mother_id != mother.id:
        raise NotFound("Contact not found")
    db.delete(c)
    db.commit()
