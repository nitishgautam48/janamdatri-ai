"""Text extraction from report images and PDFs.

- Report photos: cloud vision first when configured (Gemini reads printed and handwritten lab reports very
  accurately; OCR.space as a second cloud engine), then the local engines below. ``OCR_CLOUD_ENABLED=false``
  keeps every photo on the server.
- PDFs with a text layer: read directly with pypdf (fast, exact).
- Scanned PDFs and photos: PaddleOCR (``pip install paddleocr paddlepaddle``), pages
  rendered with pypdfium2.
- Tesseract OCR (``pytesseract`` + the tesseract program with the ``hin`` language pack) is the second
  engine: used when PaddleOCR is not installed, or when PaddleOCR reads almost nothing (Hindi lab
  reports, faint photos).
If neither is installed, images return no text and the caller may supply text read on the device
(e.g. Android ML Kit) via ``client_text``.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import threading
from pathlib import Path

import httpx

from app.ai import usage
from app.core.config import settings

log = logging.getLogger(__name__)
_paddle = None
_lock = threading.Lock()


def _get_paddle():
    global _paddle
    if _paddle is None:
        with _lock:
            if _paddle is None:
                from paddleocr import PaddleOCR  # optional heavy dependency

                _paddle = PaddleOCR(use_angle_cls=True, lang=settings.ocr_lang, show_log=False)
    return _paddle


def paddle_available() -> bool:
    if settings.ocr_provider == "text_only":
        return False
    try:
        import paddleocr  # noqa: F401

        return True
    except Exception:
        return False


_tess_ok: bool | None = None


def tesseract_available() -> bool:
    global _tess_ok
    if settings.ocr_provider in ("text_only", "paddle", "gemini", "ocrspace"):
        return False
    if _tess_ok is None:
        try:
            import pytesseract

            if settings.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
            pytesseract.get_tesseract_version()
            _tess_ok = True
        except Exception:
            _tess_ok = False
    return _tess_ok


def _tesseract_image(path: str) -> str:
    import pytesseract
    from PIL import Image

    langs = settings.tesseract_lang
    try:
        available = set(pytesseract.get_languages(config=""))
        wanted = [lang for lang in langs.split("+") if lang in available]
        langs = "+".join(wanted) or "eng"
    except Exception:
        pass
    with Image.open(path) as img:
        return pytesseract.image_to_string(img.convert("L"), lang=langs, config="--psm 6")


VISION_PROMPT = ("Transcribe all the text in this medical / lab report image exactly as printed, line by line. "
                 "Keep every test name with its value, unit and reference range on one line. Do not explain, "
                 "summarise or add anything; if there is no readable text, reply with nothing.")


def _cloud_ok() -> bool:
    return settings.ocr_cloud_enabled and settings.ocr_provider in ("auto", "gemini", "ocrspace")


def _gemini_image(path: str) -> str:
    if not usage.allow("ai", settings.ai_daily_limit):
        return ""
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    data = base64.b64encode(Path(path).read_bytes()).decode()
    body = {"model": settings.gemini_model, "max_tokens": 2000, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}]}]}
    if settings.gemini_reasoning_effort:
        body["reasoning_effort"] = settings.gemini_reasoning_effort
    usage.add("ai")
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{settings.gemini_base_url.rstrip('/')}/chat/completions", json=body,
                   headers={"Authorization": f"Bearer {settings.gemini_api_key}"})
        r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _ocrspace_image(path: str) -> str:
    with open(path, "rb") as fh, httpx.Client(timeout=60) as c:
        r = c.post("https://api.ocr.space/parse/image",
                   data={"apikey": settings.ocrspace_api_key, "language": "eng", "OCREngine": "2",
                         "scale": "true", "isTable": "true"},
                   files={"file": (Path(path).name, fh, mimetypes.guess_type(path)[0] or "image/jpeg")})
        r.raise_for_status()
    j = r.json()
    if j.get("IsErroredOnProcessing"):
        raise RuntimeError(str(j.get("ErrorMessage")))
    return "\n".join(p.get("ParsedText", "") for p in j.get("ParsedResults") or []).strip()


def ocr_engines() -> list[str]:
    engines = []
    if _cloud_ok() and settings.gemini_api_key and settings.ocr_provider in ("auto", "gemini"):
        engines.append("gemini")
    if _cloud_ok() and settings.ocrspace_api_key and settings.ocr_provider in ("auto", "ocrspace"):
        engines.append("ocrspace")
    if paddle_available() and settings.ocr_provider in ("auto", "paddle"):
        engines.append("paddleocr")
    if tesseract_available():
        engines.append("tesseract")
    return engines


def _ocr_image(path: str) -> tuple[str, str]:
    """Best text from the available engines, in order (cloud vision, PaddleOCR, Tesseract); stops once one reads
    the report well."""
    text, engine = "", "unavailable"
    readers = {"gemini": _gemini_image, "ocrspace": _ocrspace_image, "paddleocr": _paddle_image,
               "tesseract": _tesseract_image}
    for name in ocr_engines():
        try:
            out = readers[name](path)
        except Exception as exc:
            log.warning("%s failed on %s: %s", name, Path(path).name, exc)
            continue
        if len(out.strip()) > len(text.strip()):
            text, engine = out, name
        if len(text.strip()) >= 40:
            break
    return text, engine


def _paddle_image(path: str) -> str:
    result = _get_paddle().ocr(path, cls=True)
    lines: list[str] = []
    for page in result or []:
        for item in page or []:
            try:
                lines.append(item[1][0])
            except (IndexError, TypeError):
                continue
    return "\n".join(lines)


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:10])


def _ocr_pdf(path: Path) -> tuple[str, str]:
    import tempfile

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    texts, engine = [], "unavailable"
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(min(len(pdf), 5)):
            img = pdf[i].render(scale=2).to_pil()
            out = Path(tmp) / f"p{i}.png"
            img.save(out)
            text, engine = _ocr_image(str(out))
            texts.append(text)
    return "\n".join(texts), engine


def extract_text(path: Path, content_type: str) -> tuple[str, str]:
    """Returns (text, engine_name)."""
    try:
        if content_type == "application/pdf":
            text = _pdf_text(path)
            if len(text.strip()) >= 30:
                return text, "pdf-text"
            if ocr_engines():
                ocr_text, engine = _ocr_pdf(path)
                return (ocr_text, engine) if len(ocr_text.strip()) > len(text.strip()) else (text, "pdf-text")
            return text, "pdf-text"
        if ocr_engines():
            return _ocr_image(str(path))
        return "", "unavailable"
    except Exception as exc:
        log.error("OCR failed for %s: %s", path.name, exc)
        return "", "error"
