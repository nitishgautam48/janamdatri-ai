"""Local file storage for uploads (reports, voice notes, images).

Files are saved under ``MEDIA_ROOT`` with random names; they are only served through
authenticated endpoints that check ownership. Swap for S3/GCS by replacing this module.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import ValidationFailed

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
PDF_TYPES = {"application/pdf": ".pdf"}
AUDIO_TYPES = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
    "audio/aac": ".aac", "audio/3gpp": ".3gp", "video/webm": ".webm",
}
DOCUMENT_TYPES = {**IMAGE_TYPES, **PDF_TYPES}

_MAGIC = {
    ".pdf": [b"%PDF"],
    ".png": [b"\x89PNG"],
    ".jpg": [b"\xff\xd8\xff"],
    ".webp": [b"RIFF"],
}


def _sniff_ok(ext: str, head: bytes) -> bool:
    sigs = _MAGIC.get(ext)
    return True if not sigs else any(head.startswith(s) for s in sigs)


async def save_upload(upload: UploadFile, folder: str, allowed: dict[str, str]) -> tuple[str, int, str]:
    """Persist an upload. Returns (relative_path, size_bytes, content_type)."""
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in allowed:
        raise ValidationFailed(
            f"Unsupported file type {content_type or 'unknown'}", code="unsupported_file_type",
            details={"allowed": sorted(allowed)},
        )
    data = await upload.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if not data:
        raise ValidationFailed("The file is empty", code="empty_file")
    if len(data) > limit:
        raise ValidationFailed(f"File is larger than {settings.max_upload_mb} MB", code="file_too_large")
    ext = allowed[content_type]
    if not _sniff_ok(ext, data[:8]):
        raise ValidationFailed("File content does not match its type", code="file_type_mismatch")
    rel = Path(folder) / f"{uuid.uuid4().hex}{ext}"
    dest = settings.media_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel.as_posix(), len(data), content_type


def save_bytes(data: bytes, folder: str, ext: str) -> str:
    rel = Path(folder) / f"{uuid.uuid4().hex}{ext}"
    dest = settings.media_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel.as_posix()


def absolute_path(rel: str) -> Path:
    root = settings.media_root.resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValidationFailed("Invalid file path", code="invalid_path")
    return path


def delete_file(rel: str | None) -> None:
    if rel:
        try:
            absolute_path(rel).unlink(missing_ok=True)
        except Exception:  # pragma: no cover - best effort
            pass
