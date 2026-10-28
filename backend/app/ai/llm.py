"""Language models: cloud first, local backup, knowledge base last.

``LLM_PROVIDER=auto`` (default) tries the providers in ``LLM_CHAIN`` in order and skips any without a key
or not running:
- ``gemini``     – Google Gemini (AI Studio key), via its OpenAI-compatible endpoint
- ``groq``       – Groq (gpt-oss models), OpenAI-compatible
- ``openrouter`` – OpenRouter free models, OpenAI-compatible
- ``ollama``     – local models (``LLM_MODEL`` / ``LLM_SECONDARY_MODEL``), if Ollama is running
Each provider has a main and a lighter second model; tasks in ``LLM_SECONDARY_TASKS`` (Talk To Me, daily tip)
use the lighter one first. ``LLM_PROVIDER=ollama`` / ``openai`` keeps the old local-only behaviour and
``mock`` turns models off (tests).

Before anything leaves the server for a cloud provider, names, phone numbers, pincodes and IDs are removed
(``app.ai.redact``) and the mother's first name is put back into the reply. Cloud calls count towards
``AI_DAILY_LIMIT``; a failed or rate-limited model is skipped for a while.

Every caller handles ``None`` (no model answered) by falling back to deterministic,
knowledge-base-grounded text, so the app keeps working offline and when free limits run out.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.ai import redact, usage
from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


_down_until: dict[str, float] = {}  # model → time until which it is skipped after a failure
DOWN_SECONDS = 120
_local_check = {"at": -1e9, "ok": False}


class LLMClient:
    """Local model server: Ollama, or any OpenAI-compatible server (vLLM, LM Studio)."""

    def __init__(self, model: str | None = None, provider: str | None = None) -> None:
        provider = provider or settings.llm_provider
        self.provider = "ollama" if provider == "auto" else provider
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


CLOUD = ("gemini", "groq", "openrouter")


def _cloud_conf(provider: str) -> tuple[str | None, str, list[str]]:
    """(api key, base url, [main model, second model]) for a cloud provider."""
    if provider == "gemini":
        return settings.gemini_api_key, settings.gemini_base_url, [settings.gemini_model, settings.gemini_secondary_model]
    if provider == "groq":
        return settings.groq_api_key, settings.groq_base_url, [settings.groq_model, settings.groq_secondary_model]
    if provider == "openrouter":
        return settings.openrouter_api_key, settings.openrouter_base_url, [settings.openrouter_model]
    return None, "", []


def cloud_ready(provider: str) -> bool:
    key, _, models = _cloud_conf(provider)
    return bool(key and any(models))


class CloudClient:
    """One OpenAI-compatible chat call to Gemini, Groq or OpenRouter (with privacy redaction)."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider, self.model = provider, model
        self.key, self.base, _ = _cloud_conf(provider)
        self.tag = f"{provider}:{model}"

    @property
    def available(self) -> bool:
        return bool(self.key and self.model)

    def _extra(self) -> dict:
        if self.provider == "gemini" and settings.gemini_reasoning_effort:
            return {"reasoning_effort": settings.gemini_reasoning_effort}
        if self.provider == "groq" and "gpt-oss" in self.model:
            return {"reasoning_effort": "low"}
        return {}

    def generate(self, messages: list[ChatMessage], *, temperature: float | None = None,
                 max_tokens: int = 600) -> str | None:
        if not self.available or _down_until.get(self.tag, 0) > time.monotonic():
            return None
        if not usage.allow("ai", settings.ai_daily_limit):
            return None
        if settings.ai_redact:
            payload_msgs, first_name = redact.scrub_messages(messages)
        else:
            payload_msgs, first_name = [{"role": m.role, "content": m.content} for m in messages], None
        body = {"model": self.model, "messages": payload_msgs, "max_tokens": max_tokens,
                "temperature": settings.llm_temperature if temperature is None else temperature, **self._extra()}
        headers = {"Authorization": f"Bearer {self.key}"}
        if self.provider == "openrouter":
            headers |= {"HTTP-Referer": settings.public_base_url, "X-Title": settings.app_name}
        usage.add("ai")
        try:
            with httpx.Client(timeout=settings.ai_cloud_timeout_seconds) as client:
                r = client.post(f"{self.base.rstrip('/')}/chat/completions", headers=headers, json=body)
                if r.status_code == 400 and self._extra():  # a model that does not take reasoning_effort
                    for k in self._extra():
                        body.pop(k, None)
                    r = client.post(f"{self.base.rstrip('/')}/chat/completions", headers=headers, json=body)
                if r.status_code in (401, 403):
                    log.error("%s rejected the API key (HTTP %s): check it in .env", self.provider, r.status_code)
                    _down_until[self.tag] = time.monotonic() + 3600
                    return None
                if r.status_code == 429:
                    wait = r.headers.get("retry-after")
                    _down_until[self.tag] = time.monotonic() + (float(wait) if wait and wait.isdigit() else 60)
                    log.warning("%s rate limit reached: trying the next model", self.tag)
                    return None
                r.raise_for_status()
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
                return redact.restore(content, first_name) or None
        except Exception as exc:
            log.warning("Cloud LLM call failed (%s): %s", self.tag, exc)
            _down_until[self.tag] = time.monotonic() + DOWN_SECONDS
            return None


class RoutedLLM:
    """Tries each provider/model for a task in order until one answers."""

    def __init__(self, task: str | None = None) -> None:
        self.task = task
        tasks = {t.strip() for t in (settings.llm_secondary_tasks or "").split(",") if t.strip()}
        light = task in tasks
        self.provider = settings.llm_provider
        self.routes: list[CloudClient | LLMClient] = []
        if self.provider in ("ollama", "openai"):
            providers = ["local"]
        elif self.provider == "mock":
            providers = []
        else:
            chain = [c.strip() for c in settings.llm_chain.split(",") if c.strip()]
            if self.provider in CLOUD:
                chain = [self.provider] + [c for c in chain if c != self.provider]
            providers = ["local" if c in ("ollama", "local") else c for c in chain]
        for prov in providers:
            if prov == "local":
                primary, secondary = settings.llm_model, settings.llm_secondary_model
                order = [secondary, primary] if (secondary and light) else [primary, secondary]
                local = self.provider if self.provider in ("ollama", "openai") else "ollama"
                for m in [m for i, m in enumerate(order) if m and m not in order[:i]]:
                    self.routes.append(LLMClient(m, local))
            elif prov in CLOUD and cloud_ready(prov):
                models = [m for m in _cloud_conf(prov)[2] if m]
                if light and len(models) > 1:
                    models = models[::-1]
                self.routes += [CloudClient(prov, m) for m in models]
        self.models = [r.model for r in self.routes]
        self.model = self.models[0] if self.models else settings.llm_model
        self.used_model: str | None = None

    @property
    def available(self) -> bool:
        return any(isinstance(r, CloudClient) for r in self.routes) or (
            any(isinstance(r, LLMClient) for r in self.routes) and self._local_up())

    def _local_up(self) -> bool:
        # In auto mode Ollama is optional: only count it when it actually answers.
        if self.provider in ("ollama", "openai"):
            return True
        if self.provider != "auto":
            return False
        now = time.monotonic()
        if _local_check["at"] + 60 < now:  # check Ollama at most once a minute
            _local_check.update(at=now, ok=LLMClient(settings.llm_model, "ollama").reachable())
        return _local_check["ok"]

    def reachable(self) -> bool:
        return any(isinstance(r, CloudClient) for r in self.routes) or self._local_up()

    def generate(self, messages: list[ChatMessage], *, temperature: float | None = None,
                 max_tokens: int = 600) -> str | None:
        for route in self.routes:
            if isinstance(route, LLMClient) and not self._local_up():
                continue
            out = route.generate(messages, temperature=temperature, max_tokens=max_tokens)
            if out:
                self.used_model = getattr(route, "tag", route.model)
                return out
        return None


def ollama_models() -> set[str] | None:
    """Model names pulled into Ollama (with and without the ':latest' tag), or None if Ollama is not reachable."""
    if settings.llm_provider not in ("ollama", "auto"):
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
