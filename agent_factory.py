"""
agent_factory.py — runner selection and provider metadata.

Today we default to Claude Code for full coding workflows, but the rest of the
application can depend on this module instead of hard-coding Claude-specific
construction everywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_protocol import AgentRunner
from claude_runner import ClaudeRunner


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    supports_sessions: bool
    supports_tool_streaming: bool
    supports_vision: bool
    recommended_for_codegen: bool
    notes: str = ""


_CAPABILITIES = {
    "claude": ProviderCapabilities(
        provider="claude",
        supports_sessions=True,
        supports_tool_streaming=True,
        supports_vision=True,
        recommended_for_codegen=True,
        notes="Default backend via Claude Code CLI.",
    ),
    "openai": ProviderCapabilities(
        provider="openai",
        supports_sessions=False,
        supports_tool_streaming=False,
        supports_vision=True,
        recommended_for_codegen=False,
        notes="Reserved for future Codex/OpenAI runner integration.",
    ),
    "codex": ProviderCapabilities(
        provider="codex",
        supports_sessions=False,
        supports_tool_streaming=False,
        supports_vision=True,
        recommended_for_codegen=False,
        notes="Reserved for future Codex-native runner integration.",
    ),
}


def get_provider_name() -> str:
    return os.getenv("AGENT_PROVIDER", "claude").strip().lower() or "claude"


def get_provider_capabilities(provider: str | None = None) -> ProviderCapabilities:
    return _CAPABILITIES.get(provider or get_provider_name(), _CAPABILITIES["claude"])


def create_agent_runner(provider: str | None = None) -> AgentRunner:
    selected = provider or get_provider_name()
    if selected in {"claude", "anthropic"}:
        return ClaudeRunner()
    raise ValueError(
        f"Unsupported AGENT_PROVIDER '{selected}'. "
        "Supported values: claude. OpenAI/Codex adapters are not wired yet."
    )
