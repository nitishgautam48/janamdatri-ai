from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, model_validator

from app.api.deps import DB, CurrentMother, CurrentUser
from app.core.enums import Mood
from app.core.exceptions import NotFound
from app.emotional import service

router = APIRouter(prefix="/emotional", tags=["Emotional Companion"])


class EmotionalChatIn(BaseModel):
    message: str | None = Field(None, max_length=2000)
    mood: Mood | None = Field(None, description="Optional check-in: good | okay | low | worried")
    session_id: uuid.UUID | None = None
    language: str | None = None

    @model_validator(mode="after")
    def something(self):
        if not self.message and not self.mood:
            raise ValueError("Send a message or a mood")
        return self


@router.post("/chat")
def chat(body: EmotionalChatIn, db: DB, mother: CurrentMother, user: CurrentUser):
    """Friend-like conversation. Private to the mother; no questionnaires, no scores."""
    return service.chat(db, mother, user, body.message, body.mood, body.session_id, body.language)


@router.get("/history")
def history(db: DB, mother: CurrentMother, days: int = Query(30, ge=1, le=365),
            session_id: uuid.UUID | None = Query(None)):
    if session_id:
        msgs = service.session_messages(db, mother, session_id)
        if not msgs:
            raise NotFound("Conversation not found")
        return {"session_id": session_id, "messages": msgs}
    return service.history(db, mother, days)
