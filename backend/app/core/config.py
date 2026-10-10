"""Application settings, read from environment variables (or a .env file)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # Reads janamdatri-ai/.env (project root) and backend/.env; real environment variables win.
    model_config = SettingsConfigDict(env_file=(BASE_DIR.parent / ".env", BASE_DIR / ".env"), env_file_encoding="utf-8",
                                      extra="ignore")

    # --- App ---------------------------------------------------------------
    app_name: str = "Janamdatri-AI"
    environment: str = Field("development", description="development | staging | production | test")
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    public_base_url: str = "http://localhost:8000"

    # --- Database / cache --------------------------------------------------
    database_url: str = f"sqlite:///{BASE_DIR / 'janamdatri.db'}"  # or postgresql+psycopg://user:pass@host/db
    db_echo: bool = False
    auto_create_tables: bool = False  # tests / quick demos only; production uses Alembic
    seed_reference_data: bool = True
    redis_url: str | None = None  # optional; without it OTPs and rate limits are kept in memory

    # --- Security ----------------------------------------------------------
    jwt_secret: str = "change-me-in-production-please-32b+"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    refresh_token_days: int = 30
    guest_token_hours: int = 12
    otp_length: int = 6
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_requests_per_hour: int = 5
    otp_dev_echo: bool = False  # also return the OTP in the API response (automated tests only)
    admin_email: str | None = "admin@janamdatri.local"
    admin_password: str | None = "ChangeMe!2026"
    asha_mock_registry: bool = True  # accept well-formed ASHA IDs not found in the registry (prototype)

    # --- Messaging relay / SMS ---------------------------------------------
    # SMS are delivered to Mailpit (local mail catcher): OTPs and alerts appear at MAILPIT_UI_URL.
    sms_channel: str = "mailpit"  # mailpit | log
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mailpit_ui_url: str = "http://localhost:8025"
    sms_email_domain: str = "sms.janamdatri.local"

    relay_number: str = "+91-80-4710-0000"  # virtual number used for masked calls

    # --- Storage -----------------------------------------------------------
    media_root: Path = BASE_DIR / "media"
    frontend_dir: Path | None = BASE_DIR.parent / "frontend"  # served at "/" when present (set empty to disable)
    max_upload_mb: int = 15
    referral_share_hours: int = 72

    # --- AI stack ----------------------------------------------------------
    # Several local models share the work; every one is optional and falls back cleanly.
    llm_provider: str = "ollama"  # ollama | openai (any OpenAI-compatible server, e.g. vLLM) | mock; falls back to the knowledge base if unreachable
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "gemma3:12b"  # main model: Health Assistant, report explanations, knowledge answers
    llm_secondary_model: str | None = "gemma2:9b"  # second model: its own tasks below, and backup for the main one
    llm_secondary_tasks: str = "emotional,tip"  # Talk To Me and the daily tip use the second model
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.3

    whisper_provider: str = "auto"  # auto (faster-whisper if installed) | faster_whisper | mock (tests)
    whisper_model: str = "base"  # base is fast on a laptop CPU; small / medium / large-v3 are more accurate but slower
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"

    tts_provider: str = "auto"  # auto (Coqui TTS if installed) | coqui | off (the browser's voice is used)
    tts_model_en: str = "tts_models/en/ljspeech/vits"
    tts_model_hi: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    tts_speaker_hi: str = "Ana Florence"  # a built-in XTTS v2 voice
    tts_device: str = "cpu"

    ocr_provider: str = "auto"  # auto (PaddleOCR, then Tesseract) | paddle | tesseract | text_only
    ocr_lang: str = "en"
    tesseract_cmd: str | None = None  # path to tesseract(.exe) if it is not on PATH
    tesseract_lang: str = "eng+hin"

    embedding_provider: str = "auto"  # auto (hybrid if the embedding model is pulled in Ollama) | hybrid | hashing | sentence_transformers
    embedding_ollama_model: str = "nomic-embed-text"
    embedding_model: str = "intfloat/multilingual-e5-small"  # for sentence_transformers
    knowledge_top_k: int = 4

    risk_model_path: Path = BASE_DIR / "models" / "risk_gbm.joblib"

    # --- Emergency ---------------------------------------------------------
    escalation_step_minutes: int = 5  # unacknowledged alerts move to the next level after this
    rural_avg_speed_kmph: float = 25.0

    # --- Maps (OpenStreetMap) ----------------------------------------------
    # Outside the bundled gazetteer, places and health facilities are looked up on OpenStreetMap
    # (Nominatim for addresses and pincodes, Overpass for hospitals / PHC / CHC). Needs internet;
    # without it the app keeps working with the bundled data. Set OSM_ENABLED=false to stay fully offline.
    osm_enabled: bool = True
    osm_nominatim_url: str = "https://nominatim.openstreetmap.org"
    osm_overpass_urls: str = "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter"
    osm_pincode_api: str = "https://api.postalpincode.in/pincode/"  # India Post: pincode → post offices, district, state
    osm_user_agent: str = "Janamdatri-AI/1.0 (maternal health prototype; local install)"
    osm_timeout_seconds: float = 12.0

    # --- Celery ------------------------------------------------------------
    celery_enabled: bool = False
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
