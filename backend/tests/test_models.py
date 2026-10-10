"""Multi-model stack: task routing with backup, hybrid search, text to speech fallback, OCR engines."""
from __future__ import annotations

import httpx
import numpy as np

from app.ai import embeddings, llm, tts
from app.ai.llm import ChatMessage, RoutedLLM
from app.core.config import settings
from app.ocr import engine as ocr
from tests.conftest import auth, signup_mother

API = "/api/v1"


class FakeOllama:
    """Stand-in for Ollama: chat answers per model, embeddings from a tiny bag-of-words."""

    def __init__(self, missing=(), vocab=("baby", "milk", "feed", "breast", "sleep", "food")):
        self.missing, self.vocab, self.chats = set(missing), vocab, []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        req = httpx.Request("POST", url)
        if url.endswith("/api/chat"):
            self.chats.append(json["model"])
            if json["model"] in self.missing:
                return httpx.Response(404, json={"error": "model not found"}, request=req)
            return httpx.Response(200, json={"message": {"content": f"answer from {json['model']}"}}, request=req)
        if url.endswith("/api/embed"):
            rows = [[float(w in t.lower()) + 0.01 for w in self.vocab] for t in json["input"]]
            return httpx.Response(200, json={"embeddings": rows}, request=req)
        return httpx.Response(404, request=req)


def test_tasks_use_their_model_and_back_each_other_up(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.1:8b")
    monkeypatch.setattr(settings, "llm_secondary_model", "gemma2:9b")
    monkeypatch.setattr(settings, "llm_secondary_tasks", "emotional,tip")
    llm._down_until.clear()
    fake = FakeOllama()
    monkeypatch.setattr(llm.httpx, "Client", fake)
    msgs = [ChatMessage("user", "hi")]
    assert RoutedLLM("assistant").generate(msgs) == "answer from llama3.1:8b"
    assert RoutedLLM("emotional").generate(msgs) == "answer from gemma2:9b"
    # the second model is not pulled: Talk To Me falls back to the main model
    fake.missing = {"gemma2:9b"}
    llm._down_until.clear()
    r = RoutedLLM("emotional")
    assert r.generate(msgs) == "answer from llama3.1:8b" and r.used_model == "llama3.1:8b"
    # a failed model is skipped for a while instead of being retried on every message
    fake.chats.clear()
    RoutedLLM("emotional").generate(msgs)
    assert fake.chats == ["llama3.1:8b"]
    llm._down_until.clear()


def test_hybrid_search_uses_semantic_vectors_and_survives_outage(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "risk_model_path", tmp_path / "risk.joblib")
    fake = FakeOllama()
    monkeypatch.setattr(embeddings.httpx, "Client", fake)
    emb = embeddings.HybridEmbedder()
    docs = ["How to breastfeed the baby and how often to feed milk", "Iron rich food for anaemia: dal, palak, gud"]
    emb.fit(docs)
    m = emb.embed(docs)
    assert emb.sem_dim == len(fake.vocab) and m.shape == (2, embeddings.HashingEmbedder.dim + emb.sem_dim)
    assert list(tmp_path.glob("kb_vectors_*.npy"))  # cached for the next start
    q = emb.embed_query(["my baby wants milk all night"])
    assert int(np.argmax(m @ q[0])) == 0
    # embedding model down at query time: keyword half still answers, same vector size
    monkeypatch.setattr(embeddings.httpx, "Client", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    q2 = emb.embed_query(["iron food for anaemia"])
    assert q2.shape == q.shape and int(np.argmax(m @ q2[0])) == 1


def test_speak_falls_back_when_coqui_missing(client, monkeypatch):
    token, _ = signup_mother(client, name="Voice Test")
    r = client.post(f"{API}/voice/speak", json={"text": "Namaste", "language": "hi"}, headers=auth(token))
    assert r.status_code == 503 and r.json()["error"]["code"] == "tts_unavailable"
    monkeypatch.setattr(tts, "speak", lambda text, lang: b"RIFF....WAVEfmt ")
    r = client.post(f"{API}/voice/speak", json={"text": "Namaste", "language": "hi"}, headers=auth(token))
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_ocr_uses_tesseract_when_paddle_is_missing(monkeypatch, tmp_path):
    img = tmp_path / "cbc.png"
    img.write_bytes(b"not really an image")
    monkeypatch.setattr(settings, "ocr_provider", "auto")
    monkeypatch.setattr(ocr, "paddle_available", lambda: False)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr, "_tesseract_image", lambda path: "Hemoglobin 9.8 g/dL\nPlatelets 2.4 lakh/cumm")
    text, engine = ocr.extract_text(img, "image/png")
    assert engine == "tesseract" and "Hemoglobin 9.8" in text
    # both engines: the one that reads more wins
    monkeypatch.setattr(ocr, "paddle_available", lambda: True)
    monkeypatch.setattr(ocr, "_paddle_image", lambda path: "Hb")
    text, engine = ocr.extract_text(img, "image/png")
    assert engine == "tesseract"


def test_health_lists_every_model(client):
    h = client.get("/health").json()
    assert set(h["llm"]["models"]) == {"main", "second"}
    assert h["search"]["documents"] >= 200
    assert "text_to_speech" in h and "maps" in h
