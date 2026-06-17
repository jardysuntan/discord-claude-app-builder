"""Unit tests for llm_providers.py.

  - detect_provider(api_key) -> provider name str | None (pure, prefix routing).
  - extract_json(...)        -> async; does a network call via get_provider().query().

extract_json is NOT a pure function — it builds a provider and awaits query().
To exercise its real fence-stripping / JSON-parse path without network, we
monkeypatch get_provider to return a fake provider whose query() returns canned
content. The contract: success -> {"data": ..., "error": False}; bad JSON ->
{"error": True, "error_message": ...}.
"""

import asyncio

import pytest

import llm_providers as lp


# ── detect_provider (pure prefix routing) ─────────────────────────────────────

@pytest.mark.parametrize("key,expected", [
    ("sk-ant-abc123", "anthropic"),
    ("sk-or-xyz", "openrouter"),
    ("gsk_somekey", "groq"),
    ("AIzaSyABC123", "google"),
    ("sk-proj-abc", "openai"),
    ("sk-abc123", "openai"),        # bare sk- is treated as openai (ambiguous)
])
def test_detect_provider_known_prefixes(key, expected):
    assert lp.detect_provider(key) == expected


def test_detect_provider_empty_returns_none():
    assert lp.detect_provider("") is None
    assert lp.detect_provider(None) is None


def test_detect_provider_unknown_prefix_returns_none():
    assert lp.detect_provider("zzz-unrecognized-key") is None


def test_detect_provider_order_or_before_bare_sk():
    # sk-or- must match openrouter, not fall through to the generic sk- openai branch.
    assert lp.detect_provider("sk-or-v1-deadbeef") == "openrouter"


# ── extract_json parse path (provider monkeypatched, no network) ──────────────

class _FakeProvider:
    name = "fake"
    default_model = "fake-model"

    def __init__(self, content):
        self._content = content

    async def query(self, model, messages, temperature=0.1, timeout=120.0):
        return {"content": self._content, "error": False}


class _ErrorProvider:
    name = "fake"
    default_model = "fake-model"

    async def query(self, model, messages, temperature=0.1, timeout=120.0):
        return {"error": True, "error_message": "boom"}


def _run_extract(monkeypatch, content):
    monkeypatch.setattr(lp, "get_provider", lambda key, prov=None: _FakeProvider(content))
    return asyncio.run(lp.extract_json("sk-ant-x", "doc text", {"type": "object"}))


def test_extract_json_clean_bare_json(monkeypatch):
    res = _run_extract(monkeypatch, '{"a": 1, "b": "two"}')
    assert res["error"] is False
    assert res["data"] == {"a": 1, "b": "two"}


def test_extract_json_fenced_json_block(monkeypatch):
    res = _run_extract(monkeypatch, '```json\n{"a": 1}\n```')
    assert res["error"] is False
    assert res["data"] == {"a": 1}


def test_extract_json_plain_fence_no_lang(monkeypatch):
    res = _run_extract(monkeypatch, '```\n{"a": 2}\n```')
    assert res["error"] is False
    assert res["data"] == {"a": 2}


def test_extract_json_garbage_returns_error(monkeypatch):
    # Non-JSON content -> {"error": True, "error_message": ...} (does NOT raise).
    res = _run_extract(monkeypatch, "this is not json at all")
    assert res["error"] is True
    assert "not valid JSON" in res["error_message"]


def test_extract_json_empty_returns_error(monkeypatch):
    res = _run_extract(monkeypatch, "")
    assert res["error"] is True


def test_extract_json_trailing_junk_after_json_fails(monkeypatch):
    # json.loads is strict: leading prose / trailing junk are NOT salvaged here.
    # This documents the real (brittle) behavior: extract_json only strips fences,
    # it does not hunt for an embedded JSON object.
    res = _run_extract(monkeypatch, '{"a": 1} and some trailing prose')
    assert res["error"] is True


def test_extract_json_leading_prose_fails(monkeypatch):
    res = _run_extract(monkeypatch, 'Here is your JSON: {"a": 1}')
    assert res["error"] is True


def test_extract_json_provider_error_propagates(monkeypatch):
    monkeypatch.setattr(lp, "get_provider", lambda key, prov=None: _ErrorProvider())
    res = asyncio.run(lp.extract_json("sk-ant-x", "doc", {"type": "object"}))
    assert res["error"] is True
    assert res["error_message"] == "boom"
    assert res["provider"] == "fake"


# ── get_provider routing ──────────────────────────────────────────────────────

def test_get_provider_autodetects_anthropic():
    prov = lp.get_provider("sk-ant-abc")
    assert prov.name == "anthropic"


def test_get_provider_explicit_overrides_detection():
    prov = lp.get_provider("AIzaXYZ", provider="google")
    assert prov.name == "google"


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError):
        lp.get_provider("zzz-unknown-key")


def test_list_providers_sorted_and_complete():
    provs = lp.list_providers()
    assert "anthropic" in provs and "google" in provs and "openai" in provs
    assert provs == sorted(provs)
