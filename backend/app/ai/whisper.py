"""Speech-to-text with Whisper Large V3 (via faster-whisper / CTranslate2)."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    language: str | None
    duration_seconds: float | None
    engine: str


class WhisperService:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel  # heavy optional dependency

                    self._model = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                                               compute_type=settings.whisper_compute_type)
        return self._model

    def transcribe(self, path: Path, language: str | None = None) -> Transcript:
        provider = settings.whisper_provider
        if provider == "auto":
            try:
                import faster_whisper  # noqa: F401

                provider = "faster_whisper"
            except ImportError:
                return Transcript(text="", language=language, duration_seconds=None, engine="not_installed")
        if provider == "faster_whisper":
            try:
                model = self._load()
                segments, info = model.transcribe(str(path), language=language, vad_filter=True, beam_size=5)
                text = " ".join(s.text.strip() for s in segments).strip()
                return Transcript(text=text, language=info.language, duration_seconds=info.duration,
                                  engine=f"whisper-{settings.whisper_model}")
            except Exception as exc:
                log.error("Whisper transcription failed: %s", exc)
                return Transcript(text="", language=language, duration_seconds=None, engine="unavailable")
        # Development stand-in: lets the voice pipeline run without a GPU or model download.
        return Transcript(text=os.environ.get("WHISPER_MOCK_TEXT", ""), language=language or "hi",
                          duration_seconds=None, engine="mock")


_service: WhisperService | None = None


def get_whisper() -> WhisperService:
    global _service
    if _service is None:
        _service = WhisperService()
    return _service
