"""Speech-to-text: Groq Whisper (cloud, free tier) → Azure AI Speech (free F0) → local faster-whisper.

``WHISPER_PROVIDER=auto`` uses the first one that is set up. Cloud calls stay inside the free limits
(``STT_DAILY_LIMIT`` messages a day, ``AZURE_STT_MONTHLY_SECONDS`` of Azure audio a month); after that the
next engine is used, and if none is left the app asks her to type instead.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.ai import usage
from app.core.config import settings

log = logging.getLogger(__name__)

# Whisper "hears" these on silence or noise (it learnt them from video subtitles); alone they are never real speech.
PHANTOM = re.compile(r"^\W*(thank(s| you)( so much)?( for watching| for listening)?|please (like|subscribe)[\w ,&]*|"
                     r"subscribe[\w ,]*|subtitles?( by)?[\w .]*|amara\.org[\w .]*|bye[- ]?bye|you|hmm+|"
                     r"धन्यवाद|शुक्रिया|सब्सक्राइब[\w ]*)\W*$", re.I)
# Hints: words she is likely to say, so Whisper expects them instead of guessing. They are short word lists in the
# script of the app's language, not sentences: on short or unclear audio Whisper copies whole sentences from its
# prompt into the text. A name gets no hint at all (any word in it could end up in her name).
HINTS = {
    "chat": {"hi": "गर्भावस्था, आशा दीदी, डॉक्टर, दर्द, पेट दर्द, कमर दर्द, चक्कर, उल्टी, सूजन, खून, बच्चे की हलचल, "
                   "आयरन की गोली, नाश्ता, खाना, नींद, थकान, सुबह, शाम, बजे",
             "en": "pregnancy, ASHA didi, doctor, pain, back pain, dizzy, vomiting, swelling, bleeding, baby movement, "
                   "iron tablet, breakfast, lunch, sleep, tired, morning, evening, o'clock"},
    "visit": {"hi": "BP, Hb, हीमोग्लोबिन, वज़न, किलो, आयरन, IFA, TT, ANC, सूजन, खून की कमी, पेशाब, शुगर, अगली विज़िट",
              "en": "BP, Hb, hemoglobin, weight, kg, IFA, iron, TT, ANC, swelling, anaemia, urine, sugar, next visit"},
    "name": {"hi": "", "en": ""},
}
PROMPT = HINTS["chat"]["en"]  # kept for callers that import it
NAME_FILLER = re.compile(r"^\W*(my name is|i am|i'm|mera naam|mera nam|main|मेरा नाम|मैं)\s+|"
                         r"\s+(hai|hoon|hun|है|हूँ|हूं)\W*$|[.।!?,]+$", re.I)
TURBO = "whisper-large-v3-turbo"
WORDS = re.compile(r"[^\s,.।!?;:\"'()]+")  # whole words; \w would split Hindi at its vowel signs


def hint(context: str, language: str | None) -> str:
    h = HINTS.get(context) or HINTS["chat"]
    return h.get(language or "hi", h["hi"])


def echoes(text: str, prompt: str) -> bool:
    """True when the 'transcript' is mostly words from the hint: Whisper repeated its prompt."""
    if not prompt:
        return False
    pw, tw = set(WORDS.findall(prompt.lower())), WORDS.findall(text.lower())
    return len(tw) >= 3 and sum(w in pw for w in tw) / len(tw) >= 0.8


def clean_name(text: str) -> str:
    for _ in range(3):
        text = NAME_FILLER.sub("", text).strip()
    return text


def quality(j: dict) -> float:
    """Average log-probability of the words (higher is surer); -9 when there is nothing to judge."""
    segs = [sg for sg in j.get("segments") or [] if (sg.get("text") or "").strip()]
    if not segs:
        return 0.0 if (j.get("text") or "").strip() else -9.0
    total = sum(len(sg["text"]) for sg in segs)
    return sum((sg.get("avg_logprob") or 0) * len(sg["text"]) for sg in segs) / total


def _lang(detected: str) -> str:
    d = (detected or "").lower()
    return {"hindi": "hi", "english": "en"}.get(d, d)


@dataclass
class Transcript:
    text: str
    language: str | None
    duration_seconds: float | None
    engine: str
    confident: bool = True  # False: the engine itself was unsure; the app asks her to check the words first


def assess(j: dict) -> tuple[str, bool]:
    """Text without the segments Whisper marks as 'probably no speech', and whether the rest is trustworthy."""
    segs = j.get("segments") or []
    if not segs:
        text = (j.get("text") or "").strip()
        return ("" if PHANTOM.match(text) else text), True
    kept, confident = [], True
    for sg in segs:
        no_speech, logprob = sg.get("no_speech_prob", 0) or 0, sg.get("avg_logprob", 0) or 0
        if no_speech > 0.6 and logprob < -0.7:  # silence or noise that Whisper turned into words
            continue
        if logprob < -1.0 or (sg.get("compression_ratio", 1) or 1) > 2.4 or no_speech > 0.5:
            confident = False
        kept.append((sg.get("text") or "").strip())
    text = " ".join(t for t in kept if t).strip()
    return ("" if PHANTOM.match(text) else text), confident


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

    def _local_installed(self) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except ImportError:
            return False

    def engines(self) -> list[str]:
        p = settings.whisper_provider
        if p == "mock":
            return ["mock"]
        if p != "auto":
            return [p]
        out = []
        if settings.groq_api_key:
            out.append("groq")
        if settings.azure_speech_key and shutil.which("ffmpeg"):
            out.append("azure")
        if self._local_installed():
            out.append("faster_whisper")
        return out

    def transcribe(self, path: Path, language: str | None = None, context: str = "chat") -> Transcript:
        engines = self.engines()
        if engines == ["mock"]:
            # Development stand-in: lets the voice pipeline run without a model or key.
            return Transcript(text=os.environ.get("WHISPER_MOCK_TEXT", ""), language=language or "hi",
                              duration_seconds=None, engine="mock")
        for name in engines:
            fn = {"groq": self._groq, "azure": self._azure, "faster_whisper": self._local}.get(name)
            out = fn(path, language, context) if fn else None
            if out and out.text:
                if context == "name":
                    out.text = clean_name(out.text)
                return out
        return Transcript(text="", language=language, duration_seconds=None,
                          engine="not_installed" if not engines else "unavailable")

    def _groq(self, path: Path, language: str | None, context: str = "chat") -> Transcript | None:
        if not usage.allow("stt", settings.stt_daily_limit):
            return None
        usage.add("stt")
        app = language if language in ("hi", "en") else None
        prompt = hint(context, app)
        try:
            # The app's language is NOT forced at first: a mother may speak Hindi while the app is in English
            # (forcing "en" makes Whisper invent English words). Whisper detects the language; an unexpected one
            # (often Urdu for Hindi speech) is retried as the app's language / Hindi.
            j = self._groq_call(path, None, prompt)
            if _lang(j.get("language")) not in ("hi", "en", ""):
                j = self._groq_call(path, app or "hi", prompt)
            text, confident = assess(j)
            if text and echoes(text, prompt):  # it repeated the hint: listen again without one
                j = self._groq_call(path, _lang(j.get("language")) or app, "")
                text, confident = assess(j)
            detected = _lang(j.get("language"))
            # Unsure, and heard in the other language than the app's: also try the app's language, keep the surer.
            if not confident and app and detected in ("hi", "en") and detected != app:
                j2 = self._groq_call(path, app, hint(context, app))
                t2, c2 = assess(j2)
                if t2 and not echoes(t2, prompt) and quality(j2) > quality(j) + 0.15:
                    j, text, confident, detected = j2, t2, c2, app
            return Transcript(text=text, language=detected or language, duration_seconds=j.get("duration"),
                              engine=f"groq-{j.get('_model', settings.groq_whisper_model)}", confident=confident)
        except Exception as exc:
            log.warning("Groq speech-to-text failed: %s", exc)
            return None

    def _groq_call(self, path: Path, language: str | None, prompt: str = "") -> dict:
        """One transcription. The accurate large-v3 model first; if it is busy or unavailable, the turbo model."""
        models = [settings.groq_whisper_model] + ([TURBO] if settings.groq_whisper_model != TURBO else [])
        for i, model in enumerate(models):
            data = {"model": model, "response_format": "verbose_json", "temperature": "0"}
            if prompt:
                data["prompt"] = prompt
            if language:
                data["language"] = language
            with open(path, "rb") as fh, httpx.Client(timeout=settings.ai_cloud_timeout_seconds) as c:
                r = c.post(f"{settings.groq_base_url.rstrip('/')}/audio/transcriptions",
                           headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                           data=data, files={"file": (path.name, fh, "application/octet-stream")})
            if r.status_code in (400, 404, 429, 503) and i + 1 < len(models):
                log.info("Groq %s answered %s; trying %s", model, r.status_code, models[i + 1])
                continue
            r.raise_for_status()
            j = r.json()
            j["_model"] = model
            return j
        raise RuntimeError("no Groq speech model answered")

    def _azure(self, path: Path, language: str | None, context: str = "chat") -> Transcript | None:
        """Azure AI Speech short-audio REST API (up to 60 s). Audio is converted to 16 kHz mono WAV with ffmpeg."""
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "audio.wav"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-t", "59", "-ar", "16000",
                                "-ac", "1", "-f", "wav", str(wav)], check=True, timeout=60)
                audio = wav.read_bytes()
            seconds = max(1, int((len(audio) - 44) / 32000))  # 16-bit mono at 16 kHz
            if not usage.allow("azure_stt", settings.azure_stt_monthly_seconds, "month", seconds):
                return None
            usage.add("azure_stt", seconds, "month")
            locale = "en-IN" if language == "en" else "hi-IN"
            url = (f"https://{settings.azure_speech_region}.stt.speech.microsoft.com/speech/recognition/"
                   f"conversation/cognitiveservices/v1")
            with httpx.Client(timeout=settings.ai_cloud_timeout_seconds) as c:
                r = c.post(url, params={"language": locale, "format": "simple"}, content=audio,
                           headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                                    "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"})
            r.raise_for_status()
            j = r.json()
            if j.get("RecognitionStatus") != "Success":
                return None
            return Transcript(text=(j.get("DisplayText") or "").strip(), language=locale[:2],
                              duration_seconds=(j.get("Duration") or 0) / 1e7 or seconds, engine="azure-speech")
        except Exception as exc:
            log.warning("Azure speech-to-text failed: %s", exc)
            return None

    def _local(self, path: Path, language: str | None, context: str = "chat") -> Transcript | None:
        try:
            model = self._load()
            segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5,
                                                initial_prompt=hint(context, language) or None)
            text, confident = assess({"segments": [{"text": sg.text, "no_speech_prob": sg.no_speech_prob,
                                                    "avg_logprob": sg.avg_logprob,
                                                    "compression_ratio": sg.compression_ratio} for sg in segments]})
            return Transcript(text=text, language=info.language, duration_seconds=info.duration,
                              engine=f"whisper-{settings.whisper_model}", confident=confident)
        except Exception as exc:
            log.error("Whisper transcription failed: %s", exc)
            return None


_service: WhisperService | None = None


def get_whisper() -> WhisperService:
    global _service
    if _service is None:
        _service = WhisperService()
    return _service
