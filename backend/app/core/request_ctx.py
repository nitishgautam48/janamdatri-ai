"""The address the current visitor used (e.g. a Cloudflare Tunnel link), for links the app hands out."""
from __future__ import annotations

from contextvars import ContextVar

from app.core.config import settings

_base: ContextVar[str | None] = ContextVar("public_base", default=None)

LOCAL = ("localhost", "127.0.0.1")


def set_from_request(headers, scheme: str, host: str | None) -> None:
    proto = (headers.get("x-forwarded-proto") or scheme or "http").split(",")[0].strip()
    fwd_host = (headers.get("x-forwarded-host") or host or "").split(",")[0].strip()
    _base.set(f"{proto}://{fwd_host}" if fwd_host else None)


def public_base() -> str:
    """PUBLIC_BASE_URL when it is set to a real address; otherwise the address of the current request
    (so share links work through a tunnel), falling back to the configured value."""
    configured = settings.public_base_url.rstrip("/")
    if not any(h in configured for h in LOCAL):
        return configured
    return (_base.get() or configured).rstrip("/")
