from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.whisper import get_whisper
from app.api.deps import DB, CurrentAsha, CurrentPrincipal
from app.core.enums import MessageType
from app.core.exceptions import Forbidden, NotFound, ValidationFailed
from app.core.storage import AUDIO_TYPES, IMAGE_TYPES, absolute_path, save_upload
from app.database.models import Message, Mother
from app.messaging import service
from app.passport.service import asha_brief

router = APIRouter(prefix="/messages", tags=["Mother ↔ ASHA Messaging"])


class SendIn(BaseModel):
    mother_id: uuid.UUID | None = Field(None, description="Required for ASHA workers")
    body: str | None = Field(None, max_length=4000)
    message_type: MessageType = MessageType.TEXT
    report_id: uuid.UUID | None = None
    referral_id: uuid.UUID | None = None
    quick_action: str | None = Field(None, description=f"One of {list(service.QUICK_ACTIONS)}")


class LocationIn(BaseModel):
    mother_id: uuid.UUID | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str | None = Field(None, max_length=200)


class CallIn(BaseModel):
    mother_id: uuid.UUID | None = None


@router.get("/quick-actions")
def quick_actions():
    return [{"code": k, "label": en, "label_hi": hi} for k, (en, hi) in service.QUICK_ACTIONS.items()]


@router.post("/send", status_code=status.HTTP_201_CREATED)
def send(body: SendIn, db: DB, p: CurrentPrincipal):
    mother, asha = service.conversation(db, p, body.mother_id)
    mtype = body.message_type
    if body.report_id and mtype == MessageType.TEXT:
        mtype = MessageType.REPORT
    if body.referral_id and mtype == MessageType.TEXT:
        mtype = MessageType.REFERRAL
    if mtype in (MessageType.VOICE, MessageType.IMAGE, MessageType.LOCATION, MessageType.SYSTEM):
        raise ValidationFailed("Use /messages/voice, /messages/image or /messages/location for this type")
    if not (body.body or body.report_id or body.referral_id or body.quick_action):
        raise ValidationFailed("Message is empty", code="empty_message")
    m = service.send(db, p, mother, asha, message_type=mtype, body=body.body, report_id=body.report_id,
                     referral_id=body.referral_id, quick_action=body.quick_action)
    db.commit()
    return service.message_dict(m, p.role)


@router.post("/voice", status_code=status.HTTP_201_CREATED)
async def voice(db: DB, p: CurrentPrincipal, file: UploadFile = File(...), mother_id: uuid.UUID | None = Form(None),
                duration_seconds: float | None = Form(None), transcribe: bool = Form(True)):
    mother, asha = service.conversation(db, p, mother_id)
    rel, _, ctype = await save_upload(file, f"messages/{mother.id}", AUDIO_TYPES)
    transcript = None
    if transcribe:
        t = get_whisper().transcribe(absolute_path(rel), language=mother.language)
        transcript = t.text or None
        duration_seconds = duration_seconds or t.duration_seconds
    m = service.send(db, p, mother, asha, message_type=MessageType.VOICE, attachment_path=rel,
                     attachment_content_type=ctype, duration_seconds=duration_seconds, transcript=transcript)
    db.commit()
    return service.message_dict(m, p.role)


@router.post("/image", status_code=status.HTTP_201_CREATED)
async def image(db: DB, p: CurrentPrincipal, file: UploadFile = File(...), mother_id: uuid.UUID | None = Form(None),
                caption: str | None = Form(None)):
    mother, asha = service.conversation(db, p, mother_id)
    rel, _, ctype = await save_upload(file, f"messages/{mother.id}", IMAGE_TYPES)
    m = service.send(db, p, mother, asha, message_type=MessageType.IMAGE, body=caption, attachment_path=rel,
                     attachment_content_type=ctype)
    db.commit()
    return service.message_dict(m, p.role)


@router.post("/location", status_code=status.HTTP_201_CREATED)
def location(body: LocationIn, db: DB, p: CurrentPrincipal):
    mother, asha = service.conversation(db, p, body.mother_id)
    m = service.send(db, p, mother, asha, message_type=MessageType.LOCATION, body=body.label or "Shared location",
                     latitude=body.latitude, longitude=body.longitude)
    db.commit()
    return service.message_dict(m, p.role)


@router.get("/history")
def history(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None),
            before: datetime | None = None, limit: int = Query(50, le=200)):
    mother, asha = service.conversation(db, p, mother_id)
    rows = service.history(db, p, mother, asha, limit, before)
    counterpart = ({**asha_brief(asha), "presence": service.presence(asha), "phone": "hidden"} if p.role == "mother"
                   else {"mother_id": str(mother.id), "name": mother.name, "phone": "hidden"})
    return {"counterpart": counterpart, "messages": [service.message_dict(m, p.role) for m in rows]}


@router.get("/conversations")
def conversations(db: DB, asha: CurrentAsha):
    """ASHA inbox: one row per mother with the last message and unread count."""
    mothers = db.scalars(select(Mother).where((Mother.assigned_asha_id == asha.id) | (Mother.backup_asha_id == asha.id))).all()
    out = []
    for mo in mothers:
        last = db.scalar(select(Message).where(Message.mother_id == mo.id, Message.asha_id == asha.id)
                         .order_by(Message.created_at.desc()))
        if last:
            out.append({"mother_id": str(mo.id), "name": mo.name, "last_message": service.message_dict(last, "asha"),
                        "unread": service.unread_count(db, mo.id, asha.id, "asha")})
    out.sort(key=lambda c: c["last_message"]["created_at"], reverse=True)
    return {"conversations": out}


@router.post("/call")
def call(body: CallIn, db: DB, p: CurrentPrincipal):
    """Secure, number-masked call through the relay."""
    mother, asha = service.conversation(db, p, body.mother_id)
    return service.start_relay_call(db, p, mother, asha)


@router.get("/{message_id}/attachment")
def attachment(message_id: uuid.UUID, db: DB, p: CurrentPrincipal):
    m = db.get(Message, message_id)
    if not m or not m.attachment_path:
        raise NotFound("Attachment not found")
    mother, asha = service.conversation(db, p, m.mother_id)
    if m.mother_id != mother.id or m.asha_id != asha.id:
        raise Forbidden("Not your conversation")
    return FileResponse(absolute_path(m.attachment_path), media_type=m.attachment_content_type)
