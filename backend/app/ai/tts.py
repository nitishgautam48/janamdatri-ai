"""Text to speech with Coqui TTS: VITS for English, XTTS v2 for Hindi.

Optional (``pip install coqui-tts``, included in requirements-ai.txt). Models download on first use into the
Coqui cache. XTTS v2 is released under the Coqui Public Model License (non-commercial use); set
``COQUI_TOS_AGREED=1`` to accept it. When Coqui is not installed or a model cannot load, ``speak`` returns
None and the app falls back to the phone or browser's own voice, so read-aloud always works.
Synthesised audio is cached by text so repeated answers play instantly.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import threading
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)
_models: dict[str, object] = {}
_failed: set[str] = set()
_lock = threading.Lock()
MAX_CHARS = 600


def installed() -> bool:
    if settings.tts_provider == "off":
        return False
    try:
        import TTS  # noqa: F401  (the coqui-tts package)

        return True
    except Exception:
        return False


def status() -> str:
    if settings.tts_provider == "off":
        return "browser voice"
    return "coqui (vits en · xtts_v2 hi)" if installed() else "browser voice (install requirements-ai.txt for Coqui)"


def _model(lang: str):
    name = settings.tts_model_hi if lang == "hi" else settings.tts_model_en
    if name in _failed:
        return None
    with _lock:
        if name not in _models:
            try:
                from TTS.api import TTS

                _models[name] = TTS(name).to(settings.tts_device)
            except Exception as exc:  # model download blocked, licence not accepted, out of memory…
                log.warning("Coqui TTS model %s unavailable: %s", name, exc)
                _failed.add(name)
                return None
    return _models[name]


def _cache_dir() -> Path:
    d = Path(settings.media_root) / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def speak(text: str, lang: str = "en") -> bytes | None:
    """WAV audio for ``text`` in Hindi (``hi``) or English, or None if Coqui is unavailable."""
    text = " ".join((text or "").replace("*", " ").split())[:MAX_CHARS]
    if not text or not installed():
        return None
    lang = "hi" if lang == "hi" else "en"
    key = hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:24]
    cached = _cache_dir() / f"{key}.wav"
    if cached.exists():
        return cached.read_bytes()
    tts = _model(lang)
    if tts is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speech.wav"
            if lang == "hi":
                tts.tts_to_file(text=text, file_path=str(out), language="hi", speaker=settings.tts_speaker_hi)
            else:
                tts.tts_to_file(text=text, file_path=str(out))
            data = out.read_bytes()
        cached.write_bytes(data)
        return data
    except Exception as exc:
        log.warning("Coqui TTS failed: %s", exc)
        return None
