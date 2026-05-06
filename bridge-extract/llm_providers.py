"""
llm_providers.py — Multi-provider LLM adapter for the bridge's /extract endpoint.

Constructor-injected API key (not global settings) so each request can route to
a different account's BYOK credential. Each adapter exposes a single async
`query(model, messages, temperature, timeout) -> {"content": str} | {"error": True, "error_message": str}`.

Provider list and prefix detection:
    sk-ant-*  → Anthropic
    sk-proj-* / sk-*  → OpenAI
    AIza*     → Google (Gemini)
    gsk_*     → Groq
    sk-or-*   → OpenRouter
    (explicit) → DeepSeek, Mistral

Design borrowed from ~/aibert-prime/llm-council-plus/backend/providers/ (MIT,
Jacob Ben David 2025), rewritten for constructor-based auth.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx


# ── Base ─────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    name: str = "base"
    default_model: str = ""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    async def query(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        ...


def _ok(content: str) -> dict[str, Any]:
    return {"content": content, "error": False}


def _err(msg: str) -> dict[str, Any]:
    return {"error": True, "error_message": msg}


# ── Anthropic ────────────────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-haiku-4-5-20251001"
    BASE_URL = "https://api.anthropic.com/v1"

    async def query(self, model, messages, temperature=0.2, timeout=120.0, max_tokens=4096):
        system = ""
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                filtered.append(m)
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": filtered,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self.BASE_URL}/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            if r.status_code != 200:
                return _err(f"Anthropic {r.status_code}: {r.text[:300]}")
            return _ok(r.json()["content"][0]["text"])
        except Exception as e:
            return _err(str(e))


# ── OpenAI-compatible base (OpenAI, Groq, DeepSeek, OpenRouter, Mistral) ─────

class OpenAICompatProvider(LLMProvider):
    BASE_URL = "https://api.openai.com/v1"
    EXTRA_HEADERS: dict[str, str] = {}

    async def query(self, model, messages, temperature=0.2, timeout=120.0, max_tokens=4096):
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        **self.EXTRA_HEADERS,
                    },
                    json=payload,
                )
            if r.status_code != 200:
                return _err(f"{self.name} {r.status_code}: {r.text[:300]}")
            return _ok(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            return _err(str(e))


class OpenAIProvider(OpenAICompatProvider):
    name = "openai"
    default_model = "gpt-4o-mini"


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    default_model = "llama-3.3-70b-versatile"
    BASE_URL = "https://api.groq.com/openai/v1"


class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"
    default_model = "deepseek-chat"
    BASE_URL = "https://api.deepseek.com/v1"


class MistralProvider(OpenAICompatProvider):
    name = "mistral"
    default_model = "mistral-small-latest"
    BASE_URL = "https://api.mistral.ai/v1"


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    default_model = "anthropic/claude-3.5-haiku"
    BASE_URL = "https://openrouter.ai/api/v1"
    EXTRA_HEADERS = {"HTTP-Referer": "https://discord-claude-bridge.local", "X-Title": "discord-claude-bridge"}


# ── Google Gemini ────────────────────────────────────────────────────────────

class GoogleProvider(LLMProvider):
    name = "google"
    default_model = "gemini-2.5-flash"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    async def query(self, model, messages, temperature=0.2, timeout=120.0, max_tokens=4096):
        contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_instruction:
            payload["system_instruction"] = system_instruction
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self.BASE_URL}/{model or self.default_model}:generateContent",
                    params={"key": self.api_key},
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
            if r.status_code != 200:
                return _err(f"Google {r.status_code}: {r.text[:300]}")
            data = r.json()
            return _ok(data["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e:
            return _err(str(e))


# ── Registry ─────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "groq": GroqProvider,
    "google": GoogleProvider,
    "deepseek": DeepSeekProvider,
    "mistral": MistralProvider,
    "openrouter": OpenRouterProvider,
}


def detect_provider(api_key: str) -> str | None:
    """Guess provider from api key prefix. Returns None if ambiguous."""
    if not api_key:
        return None
    if api_key.startswith("sk-ant-"):
        return "anthropic"
    if api_key.startswith("sk-or-"):
        return "openrouter"
    if api_key.startswith("gsk_"):
        return "groq"
    if api_key.startswith("AIza"):
        return "google"
    if api_key.startswith("sk-proj-") or api_key.startswith("sk-"):
        return "openai"  # ambiguous; may be DeepSeek/Mistral — user must specify
    return None


def get_provider(api_key: str, provider: str | None = None) -> LLMProvider:
    """Build a provider instance. If provider is None, auto-detect from key prefix."""
    p = (provider or detect_provider(api_key) or "").lower()
    if p not in _PROVIDERS:
        raise ValueError(f"Unknown or unset provider '{p}'. Pass provider= explicitly. Supported: {sorted(_PROVIDERS)}")
    return _PROVIDERS[p](api_key)


def list_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())


# ── JSON-mode helper for /extract ────────────────────────────────────────────

async def extract_json(
    api_key: str,
    text: str,
    json_schema: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """High-level helper: run one extraction call, parse JSON, return {"data": ...} or {"error": True, ...}."""
    prov = get_provider(api_key, provider)
    sys = system_prompt or (
        "You are a precise data extractor. Given a document, extract structured data that matches "
        "the provided JSON Schema. Output ONLY a JSON object (no prose, no code fences) that validates "
        "against the schema. If a field is absent from the document, omit it or use null."
    )
    user = (
        f"JSON Schema:\n```json\n{json.dumps(json_schema, indent=2)}\n```\n\n"
        f"Document:\n\n{text}\n\n"
        "Respond with a single JSON object. No prose. No markdown fences."
    )
    res = await prov.query(
        model=model or prov.default_model,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=temperature,
        timeout=timeout,
    )
    if res.get("error"):
        return {"error": True, "error_message": res["error_message"], "provider": prov.name}
    raw = res["content"].strip()
    # Strip accidental code fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        # remove optional leading "json\n"
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": True, "error_message": f"Model output was not valid JSON: {e}. Raw: {raw[:500]}", "provider": prov.name}
    return {"data": data, "provider": prov.name, "model": model or prov.default_model, "error": False}
