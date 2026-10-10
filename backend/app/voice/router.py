from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field

from app.ai import tts
from app.ai.whisper import get_whisper
from app.api.deps import CurrentPrincipal
from app.core.exceptions import ServiceUnavailable
from app.core.ratelimit import limit
from app.core.storage import AUDIO_TYPES, absolute_path, delete_file, save_upload

router = APIRouter(prefix="/voice", tags=["Voice"])


async def transcribe_upload(file: UploadFile, language: str | None, keep: bool = False) -> dict:
    rel, size, ctype = await save_upload(file, "voice", AUDIO_TYPES)
    try:
        t = get_whisper().transcribe(absolute_path(rel), language=language)
    finally:
        if not keep:
            delete_file(rel)
    return {"text": t.text, "language": t.language, "duration_seconds": t.duration_seconds, "engine": t.engine,
            "file_path": rel if keep else None}


@router.post("/transcribe", dependencies=[Depends(limit("voice", 60, 3600))])
async def transcribe(p: CurrentPrincipal, file: UploadFile = File(...), language: str | None = Form(None)):
    """Speech → text with Whisper (Hindi, English, Bhojpuri-Hindi…). Audio is not stored."""
    out = await transcribe_upload(file, language)
    out.pop("file_path", None)
    return out


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    language: str = Field("en", pattern="^(hi|en)$")


@router.post("/speak", dependencies=[Depends(limit("speak", 120, 3600))],
             responses={200: {"content": {"audio/wav": {}}}})
def speak(body: SpeakIn, p: CurrentPrincipal):
    """Text → speech with Coqui TTS (VITS English, XTTS v2 Hindi). 503 ``tts_unavailable`` when it is not
    installed: the app then uses the phone or browser voice."""
    audio = tts.speak(body.text, body.language)
    if audio is None:
        raise ServiceUnavailable("Text to speech is not installed on this server", code="tts_unavailable")
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})
