"""Engine and session factory."""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (uuid.UUID, Decimal)):
        return str(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _json_dumps(value) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


def _make_engine(url: str):
    kwargs: dict = {"echo": settings.db_echo, "future": True, "json_serializer": _json_dumps}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - sqlite only
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
