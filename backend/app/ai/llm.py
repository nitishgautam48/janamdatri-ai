"""Local language models.

Two models share the work (both served by Ollama by default):
- the main model (``LLM_MODEL``, e.g. ``llama3.1:8b`` or ``gemma3:12b``): Health Assistant, reports, knowledge answers
- the second model (``LLM_SECONDARY_MODEL``, e.g. ``gemma2:9b``): the tasks in ``LLM_SECONDARY_TASKS``
  (Talk To Me and the daily tip by default)
Each one is the other's backup: if a model is not pulled, busy or fails, the other answers.

Providers:
- ``ollama``  – Ollama's /api/chat (e.g. ``gemma3:12b``)
- ``openai``  – any OpenAI-compatible server, e.g. vLLM serving ``google/gemma-3-12b-it``
- ``mock``    – no model; ``generate`` returns None and callers use their grounded templates

Every caller must handle ``None`` (model down/slow) by falling back to deterministic,
knowledge-base-grounded text, so the app keeps working offline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


_down_until: dict[str, float] = {}  # model → time until which it is skipped after a failure
DOWN_SECONDS = 120


class LLMClient:
    def __init__(self, model: str | None = None) -> None:
        self.provider = settings.llm_provider
        self.model = model or settings.llm_model

    @property
    def available(self) -> bool:
        return self.provider in ("ollama", "openai")

    def reachable(self) -> bool:
        """Quick check that the model server answers (used by /health)."""
        if not self.available:
            return False
        path = "/api/tags" if self.provider == "ollama" else "/v1/models"
        try:
            return httpx.get(settings.llm_base_url.rstrip("/") + path, timeout=1.5).status_code < 500
        except httpx.HTTPError:
            return False

    def generate(self, messages: list[ChatMessage], *, temperature: float | None = None,
                 max_tokens: int = 600) -> str | None:
        if not self.available:
            return None
        if _down_until.get(self.model, 0) > time.monotonic():
            return None
        temp = settings.llm_temperature if temperature is None else temperature
        # Gemma has no separate system role in its chat template; servers fold it into
        # the first user turn, so sending it as "system" is fine for Ollama and vLLM.
        payload_msgs = [{"role": m.role, "content": m.content} for m in messages]
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                if self.provider == "ollama":
                    r = client.post(f"{settings.llm_base_url.rstrip('/')}/api/chat", json={
                        "model": self.model, "messages": payload_msgs, "stream": False,
                        "options": {"temperature": temp, "num_predict": max_tokens},
                    })
                    r.raise_for_status()
                    return (r.json().get("message") or {}).get("content", "").strip() or None
                headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
                r = client.post(f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions", headers=headers, json={
                    "model": self.model, "messages": payload_msgs, "temperature": temp, "max_tokens": max_tokens,
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip() or None
        except Exception as exc:
            log.warning("LLM call failed (%s %s): %s", self.provider, self.model, exc)
            _down_until[self.model] = time.monotonic() + DOWN_SECONDS
            return None


class RoutedLLM:
    """Picks the model for a task and falls back to the other model if the first gives no answer."""

    def __init__(self, task: str | None = None) -> None:
        self.task = task
        primary, secondary = settings.llm_model, settings.llm_secondary_model
        tasks = {t.strip() for t in (settings.llm_secondary_tasks or "").split(",") if t.strip()}
        order = [secondary, primary] if (secondary and task in tasks) else [primary, secondary]
        self.models = [m for i, m in enumerate(order) if m and m not in order[:i]]
        self.provider = settings.llm_provider
        self.model = self.models[0] if self.models else settings.llm_model
        self.used_model: str | None = None

    @property
    def available(self) -> bool:
        return self.provider in ("ollama", "openai")

    def reachable(self) -> bool:
        return LLMClient(self.model).reachable()

    def generate(self, messages: list[ChatMessage], *, temperature: float | None = None,
                 max_tokens: int = 600) -> str | None:
        for model in self.models:
            out = LLMClient(model).generate(messages, temperature=temperature, max_tokens=max_tokens)
            if out:
                self.used_model = model
                return out
        return None


def ollama_models() -> set[str] | None:
    """Model names pulled into Ollama (with and without the ':latest' tag), or None if Ollama is not reachable."""
    if settings.llm_provider != "ollama":
        return None
    try:
        r = httpx.get(settings.llm_base_url.rstrip("/") + "/api/tags", timeout=1.5)
        r.raise_for_status()
        names = set()
        for m in r.json().get("models", []):
            n = m.get("name") or m.get("model") or ""
            names |= {n, n.removesuffix(":latest")}
        return names
    except (httpx.HTTPError, ValueError):
        return None


def has_model(name: str | None, names: set[str] | None) -> bool:
    return bool(name and names and (name in names or name.removesuffix(":latest") in names))


_client: LLMClient | None = None


def get_llm(task: str | None = None):
    """The model for a task ("emotional", "tip", "assistant", "report", ...). Tests can swap it with ``set_llm``."""
    if _client is not None:
        return _client
    return RoutedLLM(task)


def set_llm(client: LLMClient) -> None:
    """Swap the client (tests, or a custom provider)."""
    global _client
    _client = client
