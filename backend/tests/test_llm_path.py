"""Gemma integration: the real HTTP clients (Ollama + OpenAI-compatible) against a fake model server,
and the safety layer applied to model output."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai import llm as llm_mod
from app.ai.llm import ChatMessage, LLMClient
from app.core.config import settings
from tests.conftest import auth, create_passport, signup_mother

REPLY = "Anaemia has an 89% chance here. Eat palak and chana daily, and take 200 mg iron."
seen: list[dict] = []


class FakeModel(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append({"path": self.path, "body": body})
        if self.path == "/api/chat":
            out = {"message": {"role": "assistant", "content": REPLY}, "done": True}
        else:
            out = {"choices": [{"message": {"role": "assistant", "content": REPLY}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("provider,path", [("ollama", "/api/chat"), ("openai", "/v1/chat/completions")])
def test_llm_client_providers(fake_server, monkeypatch, provider, path):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "llm_base_url", fake_server)
    seen.clear()
    out = LLMClient().generate([ChatMessage("system", "s"), ChatMessage("user", "hi")])
    assert out == REPLY and seen[0]["path"] == path and seen[0]["body"]["model"] == settings.llm_model


def test_llm_down_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 1.0)
    assert LLMClient().generate([ChatMessage("user", "hi")]) is None


def test_gemma_answers_are_grounded_and_scrubbed(client, fake_server, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_base_url", fake_server)
    monkeypatch.setattr(llm_mod, "_client", None)
    token, _ = signup_mother(client, name="Sita Devi")
    create_passport(client, token, week=20, name="Sita Devi")
    seen.clear()
    r = client.post("/api/v1/assistant/chat", json={"message": "What should I eat for anaemia?"}, headers=auth(token)).json()
    assert r["generated_by"] == "ai"
    assert "89" not in r["reply"] and "%" not in r["reply"] and "Eat palak and chana daily" in r["reply"]
    assert "dose your doctor prescribed" in r["reply"]
    system = seen[-1]["body"]["messages"][0]["content"]
    assert "Never give percentages" in system and "Context:" in system and "Pregnancy week: 20" in system
    e = client.post("/api/v1/emotional/chat", json={"message": "I am worried about delivery"}, headers=auth(token)).json()
    assert "%" not in e["reply"]
    assert "NOT a questionnaire" in seen[-1]["body"]["messages"][0]["content"]
    k = client.post("/api/v1/knowledge/query", json={"question": "iron rich food"}).json()
    assert k["generated_by"] == "ai+retrieval" and "%" not in k["answer"]
    monkeypatch.setattr(llm_mod, "_client", None)
