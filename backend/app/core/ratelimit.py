"""Fixed-window rate limiting backed by the shared store."""
from __future__ import annotations

from fastapi import Request

from app.core.cache import get_store
from app.core.exceptions import RateLimited


def hit(key: str, limit: int, window_seconds: int) -> None:
    count = get_store().incr(f"rl:{key}", ex=window_seconds)
    if count > limit:
        raise RateLimited("Too many requests, please try again later")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit(name: str, limit_: int, window_seconds: int):
    """FastAPI dependency: limit per client IP."""

    def _dep(request: Request) -> None:
        hit(f"{name}:{client_ip(request)}", limit_, window_seconds)

    return _dep
