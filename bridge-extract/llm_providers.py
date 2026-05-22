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
    # Switched from llama-3.3-70b-versatile (12K TPM, free tier) →
    # llama-3.1-8b-instant (30K TPM, free tier) on 2026-05-21 after
    # large trip-doc extracts started failing the per-minute token
    # budget. 8b loses some accuracy on edge cases but fits a 16K
    # output reservation alongside a 5-10K input doc without 413ing.
    name = "groq"
    default_model = "llama-3.1-8b-instant"
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
    # gemini-2.5-flash is the right balance for structured extraction:
    # large output context (64K tokens), better long-output reliability than
    # the lite variant (which kept truncating mid-string), and still cheap
    # (~$0.30/1M in, ~$2.50/1M out — well under our $300 trial budget).
    # The query() method below zeros thinkingBudget for any 2.5-* model so
    # maxOutputTokens applies only to visible output, and sets
    # response_mime_type=application/json so the model commits to valid
    # JSON instead of trailing off mid-string.
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
        gen_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # Force JSON mode so the model commits to producing a valid
            # JSON object and doesn't trail off mid-string when it gets
            # bored / hits internal heuristics. Combined with the
            # explicit "respond with a single JSON object" prompt this
            # is Google's recommended path for structured extraction.
            "responseMimeType": "application/json",
        }
        # Belt-and-suspenders for callers who override to gemini-2.5-* —
        # explicitly zero the thinking budget so maxOutputTokens applies
        # solely to visible output. (No-op on 2.0 models — Google ignores it.)
        chosen_model = (model or self.default_model)
        if "2.5" in chosen_model:
            gen_config["thinkingConfig"] = {"thinkingBudget": 0}
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system_instruction:
            payload["system_instruction"] = system_instruction
        # Retry on transient Google failures:
        #   - 429: per-minute rate limit (free tier RPM, etc.)
        #   - 503: model overload ("currently experiencing high demand")
        # Both typically clear in a few seconds. Total retry budget ≤ ~15s
        # so we stay well inside the client's 120s socket timeout. For 429
        # we honor Google's retryDelay hint when present.
        import asyncio
        import re
        RETRYABLE = {429, 503}
        delays = [4.0, 10.0]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                attempt = 0
                while True:
                    r = await client.post(
                        f"{self.BASE_URL}/{chosen_model}:generateContent",
                        params={"key": self.api_key},
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                    if r.status_code not in RETRYABLE or attempt >= len(delays):
                        break
                    delay = delays[attempt]
                    if r.status_code == 429:
                        # Honor Google's retryDelay (e.g. "11s") when present.
                        m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', r.text)
                        if m:
                            delay = max(delay, float(m.group(1)) + 1)
                    await asyncio.sleep(delay)
                    attempt += 1
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

# Rough per-provider token budgets that the free tier on a fresh account
# tends to allow per minute. Conservative — we'd rather chunk than 413.
# When the user enters a paid-tier key these still hold; we just won't
# exploit the extra headroom (which is fine: a single ~5K-token doc
# still completes in one call on any of these).
_PROVIDER_TPM_BUDGET: dict[str, int] = {
    "groq":      5500,    # free tier ranges 6K–12K depending on the model + org
    "openai":    25000,
    "anthropic": 40000,
    # GoogleProvider.name is "google" — the legacy "gemini" key was a no-op
    # alias, which silently fell through to the 8000 default and starved
    # /api/v1/extract of output budget (only ~2.6K tokens for response,
    # which truncated WSB trip-doc extracts AFTER `events` and dropped
    # the `housings` + `golfRounds` sections entirely).
    "google":    250000,  # actual quota is ~1M; we cap to keep one call snappy
    "gemini":    250000,  # legacy alias for any caller passing provider="gemini"
    "openrouter": 15000,
    "deepseek":  60000,
    "mistral":   30000,
}


def _est_tokens(s: str) -> int:
    """Crude char→token estimate. 4 chars / token is reasonable for English
    plus JSON keys; we deliberately over-estimate by rounding up to keep
    requests under provider limits."""
    return max(1, (len(s) + 3) // 4)


def _split_for_chunking(text: str, target_chars: int) -> list[str]:
    """Split a document into chunks of ≤target_chars on paragraph/section
    boundaries when possible, falling back to hard splits if a paragraph
    is itself too big. Output is in original order so a downstream merge
    by id deduplicates correctly."""
    if len(text) <= target_chars:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        # If a single paragraph is too big, hard-slice it.
        if len(p) > target_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(p), target_chars):
                chunks.append(p[i:i + target_chars])
            continue
        if len(buf) + len(p) + 2 > target_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def _merge_extracted(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Union list fields by id, dict fields field-by-field preferring the
    earliest non-empty value, scalar fields by first non-empty."""
    out: dict[str, Any] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k not in out:
                out[k] = v
                continue
            existing = out[k]
            if isinstance(existing, list) and isinstance(v, list):
                seen_ids = set()
                merged: list[Any] = []
                for item in existing + v:
                    if isinstance(item, dict) and "id" in item:
                        if item["id"] in seen_ids:
                            continue
                        seen_ids.add(item["id"])
                    merged.append(item)
                out[k] = merged
            elif isinstance(existing, dict) and isinstance(v, dict):
                for sk, sv in v.items():
                    if sk not in existing or existing[sk] in (None, "", []):
                        existing[sk] = sv
            elif existing in (None, "", []) and v not in (None, "", []):
                out[k] = v
            # else: keep existing
    return out


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    return raw


def _parse_json_lenient(raw: str) -> tuple[Any, bool]:
    """Try strict json.loads first, fall back to json_repair on failure.

    Returns (parsed_obj, repaired_flag). The repair library closes open
    braces/brackets, terminates unterminated strings, strips trailing
    commas, normalizes smart-quotes, and skips prose before the first
    `{`/`[` — exactly the failure modes LLM-emitted JSON exhibits when
    output gets truncated mid-string or generation drifts.

    Raises json.JSONDecodeError when both strict and repair fail (which
    only happens for truly empty / non-JSON input).
    """
    try:
        return json.loads(raw), False
    except json.JSONDecodeError:
        pass
    # Lazy import so a missing dep doesn't break the strict path.
    from json_repair import repair_json
    fixed = repair_json(raw, return_objects=False)
    # repair_json returns a string by default; strict-parse the repaired
    # form so we surface any remaining errors with a clean message.
    return json.loads(fixed), True


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
    """High-level helper: run one (or several) extraction calls, parse JSON,
    return {"data": ...} or {"error": True, ...}.

    For docs that fit a single call within the provider's per-minute token
    budget we issue one request. For larger docs we split on paragraph
    boundaries, extract each chunk independently, and union the results
    by `id` — this keeps us out of TPM 413 errors on free-tier Groq /
    Mistral keys and still completes in a few sequential calls.
    """
    prov = get_provider(api_key, provider)
    sys = system_prompt or (
        "You are a precise data extractor. Given a document, extract structured data that matches "
        "the provided JSON Schema. Output ONLY a JSON object (no prose, no code fences) that validates "
        "against the schema. If a field is absent from the document, omit it or use null."
    )
    schema_str = json.dumps(json_schema, indent=2)
    schema_tokens = _est_tokens(schema_str)
    sys_tokens = _est_tokens(sys)

    # Per-provider budget. Reserve ~30% for output, ~70% for input. If the
    # estimated input + schema + sys overflows the input share we chunk.
    # Output cap raised from 8K → 32K to fit a full WSB trip-doc extract
    # (trip_meta + ~15 guests + ~12 venues + ~15 events + housings + golf
    # rounds + decisions + contests = ~6-10K tokens of JSON; 8K cap was
    # truncating mid-extract and silently losing the tail sections).
    budget = _PROVIDER_TPM_BUDGET.get(prov.name, 8000)
    out_reserve = min(32000, max(2000, budget // 3))
    input_budget = budget - out_reserve - sys_tokens - schema_tokens - 200

    doc_tokens = _est_tokens(text)

    async def _call(chunk_text: str) -> dict[str, Any]:
        user = (
            f"JSON Schema:\n```json\n{schema_str}\n```\n\n"
            f"Document:\n\n{chunk_text}\n\n"
            "Respond with a single JSON object. No prose. No markdown fences."
        )
        return await prov.query(
            model=model or prov.default_model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            temperature=temperature,
            timeout=timeout,
            max_tokens=out_reserve,
        )

    # Fast path: single call.
    if doc_tokens <= max(1000, input_budget):
        res = await _call(text)
        if res.get("error"):
            return {"error": True, "error_message": res["error_message"], "provider": prov.name}
        stripped = _strip_fences(res["content"])
        try:
            data, repaired = _parse_json_lenient(stripped)
        except json.JSONDecodeError as e:
            tail = stripped[-200:] if len(stripped) > 200 else stripped
            return {
                "error": True,
                "error_message": (
                    f"Model output was not valid JSON (repair failed): {e}. "
                    f"len={len(stripped)}, tail={tail!r}"
                ),
                "provider": prov.name,
            }
        return {
            "data": data,
            "provider": prov.name,
            "model": model or prov.default_model,
            "error": False,
            "repaired": repaired or None,
        }

    # Chunked path.
    target_chars = max(2000, input_budget * 4)  # ~4 chars/token
    chunks = _split_for_chunking(text, target_chars=target_chars)
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, chunk in enumerate(chunks):
        res = await _call(chunk)
        if res.get("error"):
            errors.append(f"chunk {idx + 1}/{len(chunks)}: {res['error_message']}")
            continue
        try:
            obj, _ = _parse_json_lenient(_strip_fences(res["content"]))
            parsed.append(obj)
        except json.JSONDecodeError as e:
            errors.append(f"chunk {idx + 1}/{len(chunks)}: invalid JSON ({e})")
    if not parsed:
        joined = "; ".join(errors) or "all chunks failed"
        return {"error": True, "error_message": f"All extraction chunks failed: {joined}", "provider": prov.name}
    merged = _merge_extracted(parsed)
    return {
        "data": merged,
        "provider": prov.name,
        "model": model or prov.default_model,
        "error": False,
        "chunks": len(chunks),
        "chunk_errors": errors or None,
    }
