"""Key-value store used for OTPs, rate limits, token revocation and relay sessions.

Uses Redis when ``REDIS_URL`` is set and reachable; otherwise falls back to an in-process
store (fine for tests and single-process demos, not for multi-worker production).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> tuple[str, float | None] | None:
        item = self._data.get(key)
        if item and item[1] is not None and item[1] < time.time():
            self._data.pop(key, None)
            return None
        return item

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._alive(key)
            return item[0] if item else None

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        with self._lock:
            self._data[key] = (str(value), time.time() + ex if ex else None)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ex: int | None = None) -> int:
        with self._lock:
            item = self._alive(key)
            value = int(item[0]) + 1 if item else 1
            expiry = item[1] if item else (time.time() + ex if ex else None)
            self._data[key] = (str(value), expiry)
            return value

    def flush(self) -> None:
        with self._lock:
            self._data.clear()


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        self._r.ping()

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._r.set(key, value, ex=ex)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def incr(self, key: str, ex: int | None = None) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        if ex:
            pipe.expire(key, ex, nx=True)
        return int(pipe.execute()[0])

    def flush(self) -> None:  # pragma: no cover - test helper only
        self._r.flushdb()


_store: MemoryStore | RedisStore | None = None


def get_store() -> MemoryStore | RedisStore:
    global _store
    if _store is None:
        if settings.redis_url:
            try:
                _store = RedisStore(settings.redis_url)
            except Exception as exc:  # pragma: no cover - depends on environment
                if settings.is_production:
                    raise
                log.warning("Redis unavailable (%s); using in-memory store", exc)
                _store = MemoryStore()
        else:
            _store = MemoryStore()
    return _store


def set_json(key: str, value: Any, ex: int | None = None) -> None:
    get_store().set(key, json.dumps(value, default=str), ex=ex)


def get_json(key: str) -> Any | None:
    raw = get_store().get(key)
    return json.loads(raw) if raw else None
