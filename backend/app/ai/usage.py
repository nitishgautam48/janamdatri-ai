"""Usage counters that keep cloud AI inside its free limits.

Counters live in the shared store (Redis in Docker, memory otherwise) so the API and the workers share one count.
When a limit is reached the cloud provider is skipped and the next fallback answers (local model, browser voice,
local OCR or the knowledge base), so the app never stops working and never runs up a bill.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.cache import get_store

log = logging.getLogger(__name__)

DAY = 2 * 24 * 3600
MONTH = 35 * 24 * 3600


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _key(name: str, period: str) -> str:
    return f"usage:{name}:{_day() if period == 'day' else _month()}"


def used(name: str, period: str = "day") -> int:
    try:
        return int(get_store().get(_key(name, period)) or 0)
    except Exception:  # store down: do not block requests
        return 0


def allow(name: str, limit: int | None, period: str = "day", amount: int = 1) -> bool:
    """True if ``amount`` more units fit under ``limit`` for this day / month (limit 0 or None = unlimited)."""
    if not limit:
        return True
    ok = used(name, period) + amount <= limit
    if not ok:
        log.info("Free limit reached for %s (%s %s): using the fallback", name, limit, period)
    return ok


def add(name: str, amount: int = 1, period: str = "day") -> int:
    try:
        store = get_store()
        return store.incr(_key(name, period), ex=DAY if period == "day" else MONTH, amount=max(1, int(amount)))
    except Exception:
        return 0


def summary() -> dict:
    from app.core.config import settings

    return {
        "cloud_ai_today": {"used": used("ai"), "limit": settings.ai_daily_limit},
        "speech_to_text_today": {"used": used("stt"), "limit": settings.stt_daily_limit},
        "azure_stt_seconds_this_month": {"used": used("azure_stt", "month"), "limit": settings.azure_stt_monthly_seconds},
        "azure_tts_chars_this_month": {"used": used("azure_tts", "month"), "limit": settings.azure_tts_monthly_chars},
    }
