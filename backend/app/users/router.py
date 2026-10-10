from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.audit import audit
from app.core.enums import Role
from app.core.exceptions import NotFound
from app.core.security import mask_phone
from app.core.storage import delete_file
from app.database.models import ConversationMessage, Facility, Mother, Notification, Report, User
from app.passport.service import completion

router = APIRouter(prefix="/users", tags=["Users & Profile"])


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=120)
    language: str | None = Field(None, max_length=8)
    voice_read_aloud: bool | None = None
    shared_phone_mode: bool | None = None
    notifications: dict | None = Field(None, examples=[{"medication": True, "hydration": True, "anc": True}])


class AdminUserUpdate(BaseModel):
    is_active: bool


def profile(db, user: User) -> dict:
    out = {"id": str(user.id), "role": user.role, "name": user.full_name, "phone": mask_phone(user.phone),
           "email": user.email, "language": user.language,
           "voice_read_aloud": user.preferences.get("voice_read_aloud", True),
           "notifications": user.preferences.get("notifications", {"medication": True, "hydration": True, "anc": True}),
           "created_at": user.created_at}
    if user.role == Role.MOTHER:
        m = db.scalar(select(Mother).where(Mother.user_id == user.id))
        if m:
            fac = db.get(Facility, m.preferred_facility_id) if m.preferred_facility_id else None
            comp = completion(db, m)
            out.update(mother_id=str(m.id), village=m.village, district=m.district,
                       location_line=", ".join(x for x in [m.village, m.district] if x) or None,
                       profile_strength=comp.profile_strength, passport_complete=comp.completed_steps == comp.total_steps,
                       emergency_contacts=len(m.emergency_contacts), preferred_facility=fac.name if fac else None,
                       shared_phone_mode=m.shared_phone_mode, pin_set=bool(user.pin_hash))
    return out


@router.get("/me")
def me(db: DB, user: CurrentUser):
    return profile(db, user)


@router.patch("/me")
def update_me(body: ProfileUpdate, db: DB, user: CurrentUser):
    prefs = dict(user.preferences or {})
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.language is not None:
        user.language = body.language
        if user.mother:
            user.mother.language = body.language
    if body.voice_read_aloud is not None:
        prefs["voice_read_aloud"] = body.voice_read_aloud
    if body.notifications is not None:
        prefs["notifications"] = {**prefs.get("notifications", {}), **body.notifications}
    if body.shared_phone_mode is not None and user.mother:
        user.mother.shared_phone_mode = body.shared_phone_mode
    user.preferences = prefs
    db.commit()
    return profile(db, user)


@router.delete("/me", status_code=204)
def delete_me(db: DB, user: CurrentUser):
    """Right to erasure (DPDP Act): deletes the mother's records and anonymises the account."""
    audit(db, user.id, "user.delete", "user", user.id)
    if user.mother:
        for r in db.scalars(select(Report).where(Report.mother_id == user.mother.id)).all():
            delete_file(r.file_path)
        db.delete(user.mother)  # cascades to passport, reports, assessments, messages, plans…
    db.execute(delete(Notification).where(Notification.user_id == user.id))
    db.execute(delete(ConversationMessage).where(ConversationMessage.user_id == user.id))
    user.phone = None
    user.email = None
    user.full_name = None
    user.pin_hash = None
    user.password_hash = None
    user.is_active = False
    user.preferences = {}
    db.commit()


@router.get("")
def list_users(db: DB, _: AdminUser, role: Role | None = None, limit: int = Query(100, le=500), offset: int = 0):
    q = select(User)
    if role:
        q = q.where(User.role == role.value)
    rows = db.scalars(q.order_by(User.created_at.desc()).offset(offset).limit(limit)).all()
    return {"users": [{"id": str(u.id), "role": u.role, "name": u.full_name, "phone": mask_phone(u.phone),
                       "email": u.email, "is_active": u.is_active, "last_login_at": u.last_login_at,
                       "created_at": u.created_at} for u in rows]}


@router.patch("/{user_id}")
def admin_update(user_id: uuid.UUID, body: AdminUserUpdate, db: DB, admin: AdminUser):
    u = db.get(User, user_id)
    if not u:
        raise NotFound("User not found")
    u.is_active = body.is_active
    audit(db, admin.id, "user.set_active", "user", u.id, is_active=body.is_active)
    db.commit()
    return {"id": str(u.id), "is_active": u.is_active}
