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
    # Cloud AI first (better Hindi, fast, no GPU), local models as backup, and the built-in knowledge base last,
    # so the app always answers. Safety decisions (care level, SOS) never depend on any AI model.
    #   LLM_PROVIDER=auto   → try LLM_CHAIN in order: providers without a key / not running are skipped
    #   LLM_PROVIDER=gemini | groq | openrouter → that provider first, then the rest of LLM_CHAIN
    #   LLM_PROVIDER=ollama | openai → local models only (LLM_MODEL / LLM_SECONDARY_MODEL)
    #   LLM_PROVIDER=mock   → no model (tests): answers come from the knowledge base
    llm_provider: str = "auto"
    llm_chain: str = "gemini,groq,openrouter,ollama"
    llm_secondary_tasks: str = "emotional,tip"  # these tasks use each provider's second (lighter) model

    gemini_api_key: str | None = None  # Google AI Studio
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-2.5-flash"
    gemini_secondary_model: str | None = "gemini-2.5-flash-lite"
    gemini_reasoning_effort: str | None = "none"  # turn "thinking" off: faster replies, fewer tokens used

    groq_api_key: str | None = None  # console.groq.com
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_secondary_model: str | None = "openai/gpt-oss-20b"

    openrouter_api_key: str | None = None  # openrouter.ai (optional third backup)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-oss-20b:free"

    # Local models (optional; docker-compose.local-ai.yml): Ollama or any OpenAI-compatible server
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b"
    llm_secondary_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.3

    # Privacy and limits for cloud AI
    ai_redact: bool = True  # remove names, phone numbers, pincodes and IDs before text leaves the server
    ai_daily_limit: int = 900  # cloud AI requests per day (all providers); after it, local model / knowledge base
    ai_cloud_timeout_seconds: float = 30.0

    # Speech to text: auto = Groq Whisper → Azure Speech → local faster-whisper (whichever is set up)
    whisper_provider: str = "auto"  # auto | groq | azure | faster_whisper | mock (tests)
    groq_whisper_model: str = "whisper-large-v3"  # most accurate for Hindi; falls back to whisper-large-v3-turbo
    stt_daily_limit: int = 1500  # voice messages per day sent to cloud speech-to-text
    whisper_model: str = "base"  # local faster-whisper (optional)
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"

    # Azure AI Speech (free F0 tier: 5 audio hours/month speech-to-text, 0.5M characters/month neural voices)
    azure_speech_key: str | None = None
    azure_speech_region: str = "centralindia"
    azure_voice_hi: str = "hi-IN-SwaraNeural"
    azure_voice_en: str = "en-IN-NeerjaNeural"
    azure_stt_monthly_seconds: int = 16200  # 4.5 h: stays inside the free 5 h
    azure_tts_monthly_chars: int = 450000  # stays inside the free 0.5M characters

    # Text to speech (read aloud): auto = Azure neural voice → local Coqui → the phone / browser voice
    tts_provider: str = "auto"  # auto | azure | coqui | off
    tts_model_en: str = "tts_models/en/ljspeech/vits"
    tts_model_hi: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    tts_speaker_hi: str = "Ana Florence"
    tts_device: str = "cpu"

    # Reading report photos: auto = cloud vision (Gemini, then OCR.space) → local PaddleOCR / Tesseract
    ocr_provider: str = "auto"  # auto | gemini | ocrspace | paddle | tesseract | text_only
    ocr_cloud_enabled: bool = True  # report photos are sent to Gemini / OCR.space; set false to keep them on the server
    ocrspace_api_key: str | None = None
    ocr_lang: str = "en"
    tesseract_cmd: str | None = None
    tesseract_lang: str = "eng+hin"

    # Knowledge search: hybrid = keywords + semantic vectors (Gemini embeddings, or nomic-embed in Ollama)
    embedding_provider: str = "auto"  # auto | gemini | hybrid (ollama) | hashing | sentence_transformers
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_ollama_model: str = "nomic-embed-text"
    embedding_model: str = "intfloat/multilingual-e5-small"
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
    # Accuracy upgrades: Azure Maps (hospital search + road routes) when AZURE_MAPS_KEY is set, otherwise the free
    # OSRM road router. Distances and travel times then follow real roads instead of a straight-line estimate.
    azure_maps_key: str | None = None
    routing_provider: str = "auto"  # auto (azure → ors → osrm, by which keys are set) | azure | ors | osrm | off
    ors_api_key: str | None = None  # openrouteservice.org free key: road routes and distances (2,000 routes / day)
    maptiler_key: str | None = None  # maptiler.com free key: map pictures (served through /gis/tiles, key stays on the server)
    osrm_url: str = "https://router.project-osrm.org"
    # Map pictures: served by our own server (/gis/tiles), fetched from these sources in order and cached on disk.
    # osm = openstreetmap.org, osmfr = OpenStreetMap France (humanitarian style), esri = Esri World Street Map.
    map_tile_sources: str = "maptiler,osm,osmfr,esri"  # maptiler is used only when MAPTILER_KEY is set
    map_tile_url: str = "/api/v1/gis/tiles/{z}/{x}/{y}.png"  # or a full https URL of another tile server
    map_attribution: str = "© OpenStreetMap contributors"

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
