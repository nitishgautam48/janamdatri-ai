"""Janamdatri-AI backend: FastAPI application."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.database.session import SessionLocal, engine

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("janamdatri")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_production:
        problems = [name for name, bad in (
            ("JWT_SECRET", settings.jwt_secret.startswith("change-me") or len(settings.jwt_secret) < 32),
            ("ADMIN_PASSWORD", settings.admin_password in ("ChangeMe!2026", "change-me-strong-password")),
        ) if bad]
        if problems:
            raise RuntimeError(f"Refusing to start in production with insecure settings: {', '.join(problems)}")
    if settings.auto_create_tables:
        from app.database import models  # noqa: F401
        from app.database.base import Base

        Base.metadata.create_all(engine)
    try:
        from app.database.seed import run as seed

        with SessionLocal() as db:
            seed(db)
    except Exception as exc:  # tables may not exist yet (run `alembic upgrade head`)
        log.warning("Seeding skipped: %s", exc)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Janamdatri-AI API",
    version="1.0.0",
    description=(
        "Maternal Health Companion Ecosystem for rural and semi-urban Indian mothers: Health Passport, "
        "AI Health Assistant, Pregnancy & Emotional Companions, risk assessment (care levels, never percentages), "
        "OCR report understanding, GIS navigation, ASHA integration, secure messaging, referrals and emergency "
        "escalation. It supports, and never replaces, healthcare professionals."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url=f"{settings.api_prefix}/openapi.json",
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "no-referrer"
    log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code,
             (time.perf_counter() - start) * 1000, rid)
    return response


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}})


@app.exception_handler(AppError)
async def app_error(_: Request, exc: AppError):
    return _error(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def request_validation(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return _error(422, "validation_failed", "Some fields need attention", {"errors": errors})


@app.exception_handler(ValidationError)
async def model_validation(_: Request, exc: ValidationError):
    errors = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return _error(422, "validation_failed", "Some fields need attention", {"errors": errors})


@app.get("/health", tags=["System"])
def health():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    from app.ai import tts
    from app.ai.llm import has_model, ollama_models
    from app.knowledge.service import get_kb
    from app.ocr.engine import ocr_engines

    names = ollama_models() if settings.llm_provider == "ollama" else None

    def model_state(name):
        if settings.llm_provider not in ("ollama", "openai") or not name:
            return "off"
        if settings.llm_provider == "openai":
            return "configured"
        if names is None:
            return "not reachable"
        return "ready" if has_model(name, names) else "not pulled yet"

    main_state = model_state(settings.llm_model)
    second_state = model_state(settings.llm_secondary_model)
    llm_state = ("connected" if "ready" in (main_state, second_state) else
                 "not reachable · answering from the knowledge base") if settings.llm_provider in ("ollama", "openai") else "off"
    try:
        import faster_whisper  # noqa: F401

        whisper = f"whisper-{settings.whisper_model}"
    except ImportError:
        whisper = "not installed (voice input off)"
    kb = get_kb()
    engines = ocr_engines()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok,
            "llm": {"provider": settings.llm_provider, "model": settings.llm_model, "state": llm_state,
                    "models": {"main": {"name": settings.llm_model, "state": main_state},
                               "second": {"name": settings.llm_secondary_model, "state": second_state,
                                          "tasks": settings.llm_secondary_tasks}}},
            "search": {"documents": len(kb.store.docs), "embedder": getattr(kb.store.embedder, "name", "keywords"),
                       "embedding_model": {"name": settings.embedding_ollama_model,
                                           "state": model_state(settings.embedding_ollama_model)}},
            "speech": whisper if settings.whisper_provider != "mock" else "mock",
            "text_to_speech": tts.status(),
            "ocr": " + ".join(engines) if engines else "pdf text only (install requirements-ai.txt for photos)",
            "maps": "openstreetmap (online) + bundled gazetteer" if settings.osm_enabled else "bundled gazetteer only"}


app.include_router(api_router, prefix=settings.api_prefix)

# The web app (landing page, live app and design screens) is served from the same origin as the API.
if settings.frontend_dir and Path(settings.frontend_dir).is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
