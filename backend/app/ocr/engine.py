"""Text extraction from report images and PDFs.

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

import logging
import threading
from pathlib import Path

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
    if settings.ocr_provider in ("text_only", "paddle"):
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


def ocr_engines() -> list[str]:
    engines = []
    if paddle_available() and settings.ocr_provider in ("auto", "paddle"):
        engines.append("paddleocr")
    if tesseract_available():
        engines.append("tesseract")
    return engines


def _ocr_image(path: str) -> tuple[str, str]:
    """Best text from the available engines: PaddleOCR first, Tesseract if Paddle is missing or reads little."""
    text, engine = "", "unavailable"
    for name in ocr_engines():
        try:
            out = _paddle_image(path) if name == "paddleocr" else _tesseract_image(path)
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
