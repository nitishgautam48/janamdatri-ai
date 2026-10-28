"""Cloud providers (Gemini, Groq, OpenRouter, Azure Speech, Azure Maps, OSRM) against recorded-style fake responses:
routing and fallback, privacy redaction, free-limit caps, speech, voice, OCR, search vectors and road routes."""
from __future__ import annotations

import httpx
import numpy as np
import pytest

from app.ai import embeddings, llm, redact, tts, usage, whisper
from app.ai.llm import ChatMessage, RoutedLLM
from app.core.config import settings
from app.gis import azure_maps, routing
from app.ocr import engine as ocr
from tests.conftest import auth, create_passport, signup_mother

API = "/api/v1"


class Fake:
    """Stand-in for httpx.Client / httpx.get: routes each URL to a handler and records the calls."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _answer(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for part, handler in self.routes.items():
            if part in url:
                status, body = handler(kw) if callable(handler) else handler
                req = httpx.Request(method, url)
                if isinstance(body, bytes):
                    return httpx.Response(status, content=body, request=req)
                return httpx.Response(status, json=body, request=req)
        return httpx.Response(404, json={}, request=httpx.Request(method, url))

    def post(self, url, **kw):
        return self._answer("POST", url, **kw)

    def get(self, url, **kw):
        return self._answer("GET", url, **kw)


def chat(text):
    return 200, {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "llm_chain", "gemini,groq,openrouter,ollama")
    monkeypatch.setattr(settings, "gemini_api_key", "g-test")
    monkeypatch.setattr(settings, "groq_api_key", "q-test")
    monkeypatch.setattr(llm, "_local_check", {"at": 1e18, "ok": False})  # no Ollama in tests
    llm._down_until.clear()
    yield
    llm._down_until.clear()


def test_redaction_removes_identity_and_restores_name():
    msgs = [ChatMessage("system", "Her health details:\nName: Priya Kumari\nPregnancy week: 24"),
            ChatMessage("user", "Priya here, call me on 9876543210, pincode 325001, RCH-778812. Hb 9.8, platelets 240000")]
    payload, first = redact.scrub_messages(msgs)
    text = " ".join(m["content"] for m in payload)
    assert "Priya" not in text and "Kumari" not in text and "9876543210" not in text and "325001" not in text
    assert "RCH-778812" not in text and "Pregnancy week: 24" in text and "Hb 9.8" in text and "240000" in text
    assert first == "Priya" and redact.restore("Hello [NAME], rest well.", first) == "Hello Priya, rest well."


def test_gemini_answers_with_redacted_prompt(cloud, monkeypatch):
    fake = Fake({"generativelanguage": lambda kw: chat("Namaste [NAME]! Drink water and rest.")})
    monkeypatch.setattr(llm.httpx, "Client", fake)
    r = RoutedLLM("assistant")
    out = r.generate([ChatMessage("system", "Name: Sita Devi"), ChatMessage("user", "Sita feels dizzy, 9123456789")])
    assert out == "Namaste Sita! Drink water and rest." and r.used_model == "gemini:gemini-2.5-flash"
    sent = fake.calls[0]["json"]
    assert "Sita" not in str(sent["messages"]) and "9123456789" not in str(sent["messages"])
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer g-test" and sent["reasoning_effort"] == "none"


def test_rate_limited_gemini_falls_back_to_groq_and_light_tasks_use_light_models(cloud, monkeypatch):
    fake = Fake({"generativelanguage": (429, {"error": "quota"}), "groq": lambda kw: chat(f"groq {kw['json']['model']}")})
    monkeypatch.setattr(llm.httpx, "Client", fake)
    assert RoutedLLM("assistant").generate([ChatMessage("user", "hi")]) == "groq openai/gpt-oss-120b"
    assert [r.model for r in RoutedLLM("emotional").routes][:2] == ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
    # the rate-limited model is now skipped without another call
    fake.calls.clear()
    RoutedLLM("assistant").generate([ChatMessage("user", "hi")])
    assert not any("generativelanguage" in c["url"] and c["json"]["model"] == "gemini-2.5-flash" for c in fake.calls)


def test_daily_limit_stops_cloud_calls_and_assistant_uses_knowledge_base(client, cloud, monkeypatch):
    monkeypatch.setattr(settings, "ai_daily_limit", 2)
    fake = Fake({"generativelanguage": lambda kw: chat("cloud answer"), "groq": lambda kw: chat("cloud answer")})
    monkeypatch.setattr(llm.httpx, "Client", fake)
    usage.add("ai", 2)
    token, _ = signup_mother(client, name="Limit Test")
    create_passport(client, token, week=20, name="Limit Test")
    r = client.post(f"{API}/assistant/chat", json={"message": "What should I eat for anaemia?"}, headers=auth(token)).json()
    assert r["generated_by"] == "knowledge_base" and not fake.calls


def test_groq_speech_to_text_and_daily_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "whisper_provider", "auto")
    monkeypatch.setattr(settings, "groq_api_key", "q-test")
    monkeypatch.setattr(settings, "azure_speech_key", "")
    fake = Fake({"audio/transcriptions": (200, {"text": " मुझे चक्कर आ रहे हैं ", "language": "hindi", "duration": 2.5})})
    monkeypatch.setattr(whisper.httpx, "Client", fake)
    audio = tmp_path / "v.webm"
    audio.write_bytes(b"\x1aE\xdf\xa3fake")
    w = whisper.WhisperService()
    t = w.transcribe(audio, "hi")
    assert t.text == "मुझे चक्कर आ रहे हैं" and t.language == "hi" and t.engine.startswith("groq-")
    assert fake.calls[0]["data"]["model"] == "whisper-large-v3"  # the accurate model first
    monkeypatch.setattr(settings, "stt_daily_limit", 1)
    assert w.transcribe(audio, "hi").text == ""  # over the free cap: no call, typing still works
    assert len(fake.calls) == 1


def test_azure_neural_voice_and_monthly_cap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tts_provider", "auto")
    monkeypatch.setattr(settings, "azure_speech_key", "az-test")
    monkeypatch.setattr(settings, "media_root", tmp_path)
    fake = Fake({"tts.speech.microsoft.com": (200, b"ID3\x03fake-mp3")})
    monkeypatch.setattr(tts.httpx, "Client", fake)
    token, _ = signup_mother(client, name="Voice Cloud")
    r = client.post(f"{API}/voice/speak", json={"text": "आयरन की गोली रोज़ लें", "language": "hi"}, headers=auth(token))
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    ssml = fake.calls[0]["content"].decode()
    assert "hi-IN-SwaraNeural" in ssml and fake.calls[0]["headers"]["Ocp-Apim-Subscription-Key"] == "az-test"
    monkeypatch.setattr(settings, "azure_tts_monthly_chars", 5)
    r = client.post(f"{API}/voice/speak", json={"text": "A new sentence to read", "language": "en"}, headers=auth(token))
    assert r.status_code == 503  # free monthly characters used: the app falls back to the browser voice


def test_gemini_embeddings_power_hybrid_search(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "g-test")
    monkeypatch.setattr(settings, "risk_model_path", tmp_path / "risk.joblib")
    vocab = ("baby", "milk", "feed", "iron", "food")

    def emb(kw):
        rows = [[float(w in t.lower()) + 0.01 for w in vocab] for t in kw["json"]["input"]]
        return 200, {"data": [{"index": i, "embedding": r} for i, r in enumerate(rows)]}

    monkeypatch.setattr(embeddings.httpx, "Client", Fake({"/embeddings": emb}))
    e = embeddings.HybridEmbedder(embeddings.GeminiEmbedder())
    docs = ["How to breastfeed and feed milk to the baby", "Iron rich food for anaemia"]
    e.fit(docs)
    m = e.embed(docs)
    assert e.sem_dim == len(vocab) and "gemini-embedding" in e.name
    assert int(np.argmax(m @ e.embed_query(["my baby wants milk"])[0])) == 0
    assert isinstance(embeddings.get_embedder(), embeddings.HashingEmbedder)  # tests use EMBEDDING_PROVIDER=hashing


def test_report_photo_read_by_gemini_vision_first(monkeypatch, tmp_path):
    img = tmp_path / "cbc.jpg"
    img.write_bytes(b"\xff\xd8fake")
    monkeypatch.setattr(settings, "ocr_provider", "auto")
    monkeypatch.setattr(settings, "gemini_api_key", "g-test")
    monkeypatch.setattr(ocr, "paddle_available", lambda: False)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    fake = Fake({"generativelanguage": lambda kw: chat("Haemoglobin 9.8 g/dL 11.5-16.5\nPlatelets 2.4 lakh/cumm")})
    monkeypatch.setattr(ocr.httpx, "Client", fake)
    text, engine = ocr.extract_text(img, "image/jpeg")
    assert engine == "gemini" and "Haemoglobin 9.8" in text
    content = fake.calls[0]["json"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    monkeypatch.setattr(settings, "ocr_cloud_enabled", False)  # photos stay on the server
    assert ocr.ocr_engines() == ["tesseract"]


def test_road_distance_and_route_line_from_osrm(client, monkeypatch):
    monkeypatch.setattr(settings, "routing_provider", "osrm")

    def table(kw):
        return 200, {"code": "Ok", "distances": [[0] + [4200.0 + i * 1000 for i in range(8)]],
                     "durations": [[0] + [540.0 + i * 60 for i in range(8)]]}

    route = (200, {"code": "Ok", "routes": [{"distance": 5300.0, "duration": 720.0,
                                             "geometry": {"coordinates": [[79.35, 28.262], [79.36, 28.27], [79.37, 28.28]]}}]})
    fake = Fake({"/table/v1/": table, "/route/v1/": route})
    monkeypatch.setattr(routing.httpx, "get", fake.get)
    r = client.get(f"{API}/gis/nearby?lat=28.262&lon=79.35&limit=3").json()
    f = r["facilities"][0]
    assert f["distance_source"] == "road" and f["distance_km"] == 4.2 and f["travel_minutes"] == 9 and "4.2 km" in f["display_meta"]
    rt = client.get(f"{API}/gis/route?from_lat=28.262&from_lon=79.35&to_lat=28.28&to_lon=79.37").json()
    assert rt["available"] and rt["distance_km"] == 5.3 and rt["minutes"] == 12 and rt["path"][0] == [28.262, 79.35]
    # router down: the straight-line estimate is used and the app still works
    monkeypatch.setattr(routing.httpx, "get", Fake({}).get)
    rt = client.get(f"{API}/gis/route?from_lat=28.0&from_lon=79.0&to_lat=28.1&to_lon=79.1").json()
    assert rt["available"] is False and rt["distance_km"] > 0


def test_azure_maps_route_parsing(monkeypatch):
    monkeypatch.setattr(settings, "routing_provider", "auto")
    monkeypatch.setattr(settings, "azure_maps_key", "am-test")
    body = {"routes": [{"summary": {"lengthInMeters": 12400, "travelTimeInSeconds": 1380},
                        "legs": [{"points": [{"latitude": 25.18, "longitude": 75.84}, {"latitude": 25.2, "longitude": 75.86}]}]}]}
    fake = Fake({"atlas.microsoft.com/route": (200, body)})
    monkeypatch.setattr(routing.httpx, "get", fake.get)
    r = routing.route((25.18, 75.84), (25.2, 75.86))
    assert r == {"distance_km": 12.4, "minutes": 23, "path": [[25.18, 75.84], [25.2, 75.86]], "provider": "azure"}
    assert fake.calls[0]["params"]["subscription-key"] == "am-test"


def test_azure_maps_adds_missing_hospitals_without_duplicates(client, monkeypatch):
    from app.database.models import Facility
    from app.database.session import SessionLocal

    monkeypatch.setattr(settings, "azure_maps_key", "am-test")
    results = [
        {"id": "IN/POI/p0/1", "poi": {"name": "Shri Ram Hospital", "phone": "+91 744 000 0000", "categories": ["hospital"]},
         "address": {"freeformAddress": "Talwandi, Kota", "postalCode": "324005", "countrySecondarySubdivision": "Kota",
                     "countrySubdivision": "Rajasthan"}, "position": {"lat": 25.141, "lon": 75.842}},
        {"id": "IN/POI/p0/2", "poi": {"name": "Apollo Pharmacy", "categories": ["pharmacy"]},
         "address": {}, "position": {"lat": 25.142, "lon": 75.843}},
        {"id": "IN/POI/p0/3", "poi": {"name": "Shri Ram Hospital", "categories": ["hospital"]},
         "address": {}, "position": {"lat": 25.1411, "lon": 75.8421}},
    ]
    monkeypatch.setattr(azure_maps.httpx, "get", Fake({"search/poi/category": (200, {"results": results})}).get)
    with SessionLocal() as db:
        added = azure_maps.load_area(db, 25.14, 75.84)
        rows = db.query(Facility).filter(Facility.source == "azure").all()
    assert added == 1 and len(rows) == 1 and rows[0].facility_type == "HOSP" and rows[0].pincode == "324005"


def test_health_shows_cloud_and_limits(client, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g-test")
    h = client.get("/health").json()
    assert h["cloud"]["gemini"]["state"] == "ready" and h["cloud"]["groq"]["state"] == "no key"
    assert "cloud_ai_today" in h["limits"] and h["privacy"]["redaction"] is True
    assert "routing" in h["maps"]
