"""Test setup: SQLite (or TEST_DATABASE_URL), in-memory store, temp media, offline AI providers."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="janamdatri-test-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}"),
    "REDIS_URL": os.environ.get("TEST_REDIS_URL", ""),
    "AUTO_CREATE_TABLES": "true",
    "SEED_REFERENCE_DATA": "true",
    "MEDIA_ROOT": str(_tmp / "media"),
    "RISK_MODEL_PATH": os.environ.get("TEST_RISK_MODEL_PATH", str(Path(tempfile.gettempdir()) / "janamdatri_risk_test.joblib")),
    "LLM_PROVIDER": "mock",
    "WHISPER_PROVIDER": "mock",
    "OCR_PROVIDER": "text_only",
    "OTP_DEV_ECHO": "true",
    "SMS_CHANNEL": "log",
    "OSM_ENABLED": "false",
    "EMBEDDING_PROVIDER": "hashing",
    "TTS_PROVIDER": "off",  # tests never call the internet; OpenStreetMap is tested with recorded responses
    # Real keys in a developer's .env must never reach the tests: cloud providers are tested with fakes.
    "GEMINI_API_KEY": "", "GROQ_API_KEY": "", "OPENROUTER_API_KEY": "", "AZURE_SPEECH_KEY": "",
    "AZURE_MAPS_KEY": "", "OCRSPACE_API_KEY": "", "ROUTING_PROVIDER": "off", "ORS_API_KEY": "", "MAPTILER_KEY": "",
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "AdminPass!123",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.cache import get_store  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    from app.ai import llm

    store = get_store()
    if hasattr(store, "flush"):
        store.flush()
    llm._down_until.clear()  # a model marked down by one test must not leak into the next
    yield


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_phone_counter = [7000000000]


def new_phone() -> str:
    _phone_counter[0] += 1
    return str(_phone_counter[0])


def signup_mother(client, name="Priya Kumari", phone=None) -> tuple[str, str]:
    phone = phone or new_phone()
    r = client.post("/api/v1/auth/otp/request", json={"phone": phone, "purpose": "mother_signup"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/auth/mother/signup", json={"phone": phone, "otp": r.json()["dev_otp"], "name": name})
    assert r.status_code == 201, r.text
    return r.json()["access_token"], phone


def asha_login(client, reg="UP-BRL-04417", sc="SC-2208", sector="SEC-07") -> str:
    r = client.post("/api/v1/auth/asha/verify", json={"asha_reg_no": reg, "sub_centre_code": sc, "sector_code": sector})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/auth/asha/login", json={"asha_reg_no": reg, "otp": r.json()["dev_otp"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def admin_login(client) -> str:
    r = client.post("/api/v1/auth/admin/login", json={"email": "admin@test.local", "password": "AdminPass!123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def lmp_for_week(week: int) -> str:
    return (date.today() - timedelta(weeks=week, days=1)).isoformat()


def create_passport(client, token, week=24, village="Rampur", pincode="243001", **extra) -> dict:
    body = {"about": {"name": extra.pop("name", "Priya Kumari"), "age": extra.pop("age", 23), "village": village,
                      "pincode": pincode, "language": "en"},
            "pregnancy": {"lmp": lmp_for_week(week), "first_pregnancy": True}}
    body.update(extra)
    r = client.post("/api/v1/passport/create", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def make_pdf(lines: list[str]) -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


CBC_LINES = [
    "City Diagnostics Lab, Bareilly", "Date: 07/10/2026", "COMPLETE BLOOD COUNT (CBC)",
    "Hemoglobin (Hb): 9.8 g/dL   Ref 11.0 - 15.0", "Total Leucocyte Count (TLC): 8200 /cumm",
    "Platelet Count: 2.4 lakh/cumm", "RBC Count: 3.9 million/cumm",
    "Advice: Continue iron tablets, repeat Hb after 4 weeks",
    "Tab Ferrous sulphate + folic acid 1 tab 0-1-0 after lunch", "Tab Calcium 500 mg 2 tabs at night",
]
