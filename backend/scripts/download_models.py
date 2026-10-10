"""Download the speech, voice and OCR models once (run at deploy time so the app works offline afterwards).

    python -m scripts.download_models
"""
from __future__ import annotations

import sys

from app.core.config import settings


def main() -> int:
    ok = True
    try:
        from faster_whisper import WhisperModel

        print(f"Downloading Whisper {settings.whisper_model} …", flush=True)
        WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        print("  Whisper ready")
    except Exception as exc:  # pragma: no cover - depends on network/hardware
        ok = False
        print(f"  Whisper download failed: {exc}", file=sys.stderr)
    try:
        from paddleocr import PaddleOCR

        print(f"Downloading PaddleOCR models ({settings.ocr_lang}) …", flush=True)
        PaddleOCR(use_angle_cls=True, lang=settings.ocr_lang, show_log=False)
        print("  PaddleOCR ready")
    except Exception as exc:  # pragma: no cover
        ok = False
        print(f"  PaddleOCR download failed: {exc}", file=sys.stderr)
    try:
        import TTS  # noqa: F401  (coqui-tts)

        from app.ai import tts

        print("Downloading Coqui TTS voices (VITS English, XTTS v2 Hindi) …", flush=True)
        for lang in ("en", "hi"):
            print(f"  {lang}: {'ready' if tts.speak('Namaste' if lang == 'hi' else 'Hello', lang) else 'unavailable'}")
    except ImportError:
        print("Coqui TTS not installed: read-aloud uses the browser voice")
    except Exception as exc:  # pragma: no cover
        ok = False
        print(f"  Coqui TTS download failed: {exc}", file=sys.stderr)
    from app.ocr.engine import tesseract_available

    print("Tesseract OCR:", "ready" if tesseract_available() else "not installed (PaddleOCR / PDF text are used)")
    if not ok:
        print("Some models could not be downloaded; those features stay unavailable until this step succeeds "
              "(re-run: docker compose run --rm models).", file=sys.stderr)
    return 0  # never block the app from starting


if __name__ == "__main__":
    raise SystemExit(main())
