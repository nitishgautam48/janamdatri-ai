"""Text to speech (read aloud): Azure neural voices → local Coqui TTS → the phone / browser voice.

- Azure AI Speech (``AZURE_SPEECH_KEY``): natural Indian voices (hi-IN-SwaraNeural, en-IN-NeerjaNeural) on the
  free F0 tier; usage is kept under ``AZURE_TTS_MONTHLY_CHARS`` a month.
- Coqui TTS (optional, local: VITS English, XTTS v2 Hindi; XTTS v2 is non-commercial, ``COQUI_TOS_AGREED=1``).
When neither is available ``speak`` returns None and the app uses the phone or browser's own voice, so
read-aloud always works. Audio is cached by text so repeated answers play instantly.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import threading
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from app.ai import usage
from app.core.config import settings

log = logging.getLogger(__name__)
_models: dict[str, object] = {}
_failed: set[str] = set()
_lock = threading.Lock()
MAX_CHARS = 600


def azure_ready() -> bool:
    return settings.tts_provider in ("auto", "azure") and bool(settings.azure_speech_key)


def installed() -> bool:
    """Coqui TTS (local) is installed."""
    if settings.tts_provider not in ("auto", "coqui"):
        return False
    try:
        import TTS  # noqa: F401  (the coqui-tts package)

        return True
    except Exception:
        return False


def status() -> str:
    if settings.tts_provider == "off":
        return "browser voice"
    parts = []
    if azure_ready():
        parts.append(f"azure neural ({settings.azure_voice_hi}, {settings.azure_voice_en})")
    if installed():
        parts.append("coqui (vits en · xtts_v2 hi)")
    return " → ".join(parts + ["browser voice"])


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


def _azure(text: str, lang: str) -> bytes | None:
    if not usage.allow("azure_tts", settings.azure_tts_monthly_chars, "month", len(text)):
        return None
    voice = settings.azure_voice_hi if lang == "hi" else settings.azure_voice_en
    ssml = (f"<speak version='1.0' xml:lang='{'hi-IN' if lang == 'hi' else 'en-IN'}'>"
            f"<voice name='{voice}'><prosody rate='-5%'>{escape(text)}</prosody></voice></speak>")
    try:
        usage.add("azure_tts", len(text), "month")
        with httpx.Client(timeout=settings.ai_cloud_timeout_seconds) as c:
            r = c.post(f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
                       content=ssml.encode("utf-8"),
                       headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                                "Content-Type": "application/ssml+xml",
                                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                                "User-Agent": "janamdatri-ai"})
        r.raise_for_status()
        return r.content or None
    except Exception as exc:
        log.warning("Azure text-to-speech failed: %s", exc)
        return None


def media_type(audio: bytes) -> str:
    return "audio/wav" if audio[:4] == b"RIFF" else "audio/mpeg"


def speak(text: str, lang: str = "en") -> bytes | None:
    """MP3/WAV audio for ``text`` in Hindi (``hi``) or English, or None (the app then uses the browser voice)."""
    text = " ".join((text or "").replace("*", " ").split())[:MAX_CHARS]
    if not text or settings.tts_provider == "off":
        return None
    lang = "hi" if lang == "hi" else "en"
    key = hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:24]
    for ext in ("mp3", "wav"):
        cached = _cache_dir() / f"{key}.{ext}"
        if cached.exists():
            return cached.read_bytes()
    if azure_ready():
        audio = _azure(text, lang)
        if audio:
            (_cache_dir() / f"{key}.mp3").write_bytes(audio)
            return audio
    if not installed():
        return None
    cached = _cache_dir() / f"{key}.wav"
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
