from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, CurrentUser, get_current_mother
from app.assistant import service
from app.core.enums import MessageType, Role
from app.core.exceptions import ValidationFailed
from app.core.ratelimit import limit
from app.database.models import ConversationMessage, Mother
from app.messaging import service as messaging
from app.voice.router import transcribe_upload

router = APIRouter(prefix="/assistant", tags=["Health Assistant"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: uuid.UUID | None = None
    language: str | None = Field(None, description="hi | en; detected when omitted")


class TellAshaIn(BaseModel):
    thread_id: uuid.UUID
    note: str | None = Field(None, max_length=500)


def _mother(db, p) -> Mother | None:
    if p.role == Role.MOTHER and p.user:
        return db.scalar(select(Mother).where(Mother.user_id == p.user.id))
    return None


@router.post("/chat", dependencies=[Depends(limit("assistant", 120, 3600))])
def chat(body: ChatIn, db: DB, p: CurrentPrincipal):
    """Maternal health questions, symptoms, concerns, report explanations. Guests can ask; nothing is saved for them."""
    return service.chat(db, p, _mother(db, p), body.message, body.thread_id, body.language)


@router.post("/voice", dependencies=[Depends(limit("assistant-voice", 60, 3600))])
async def voice(db: DB, p: CurrentPrincipal, file: UploadFile = File(...), thread_id: uuid.UUID | None = Form(None),
                language: str | None = Form(None)):
    """Audio → Whisper → text → assistant → response."""
    mother = _mother(db, p)
    t = await transcribe_upload(file, language or (mother.language if mother else None))
    if not t["text"].strip():
        if t["engine"] == "not_installed":
            raise ValidationFailed("Voice input needs Whisper: pip install -r requirements-ai.txt (or type your question).",
                                   code="voice_unavailable")
        raise ValidationFailed("We couldn't hear that clearly. Please try again or type your question.",
                               code="no_speech", details={"engine": t["engine"]})
    out = service.chat(db, p, mother, t["text"], thread_id, language, input_mode="voice")
    out["transcript"] = {"text": t["text"], "language": t["language"], "engine": t["engine"]}
    return out


@router.get("/history")
def history(db: DB, user: CurrentUser, thread_id: uuid.UUID | None = Query(None)):
    """Without ``thread_id``: the chat list. With it: that conversation's messages."""
    if thread_id:
        return {"thread_id": thread_id, "messages": service.thread_messages(db, user.id, thread_id)}
    return {"threads": service.threads(db, user.id)}


@router.post("/tell-asha")
def tell_asha(body: TellAshaIn, db: DB, p: CurrentPrincipal):
    """Share a short summary of an assistant conversation with the assigned ASHA."""
    mother = get_current_mother(db, p)
    msgs = db.scalars(select(ConversationMessage).where(ConversationMessage.user_id == p.user.id,
                                                        ConversationMessage.thread_id == body.thread_id,
                                                        ConversationMessage.role == "user")
                      .order_by(ConversationMessage.created_at)).all()
    if not msgs:
        raise ValidationFailed("Conversation not found", code="thread_not_found")
    _, asha = messaging.conversation(db, p, None)
    text = "Asked the health assistant: " + " | ".join(m.content[:120] for m in msgs[-3:])
    if body.note:
        text += f"\nNote: {body.note}"
    m = messaging.send(db, p, mother, asha, message_type=MessageType.TEXT, body=text)
    db.commit()
    return {"sent": True, "message_id": m.id}
