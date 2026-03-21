"""
helpers/prompt_suggest.py — Suggest an improved prompt before sending to Claude.

Uses a fast/cheap Claude Haiku call to rewrite vague user prompts into
specific, actionable instructions for the app-building agent.
"""

from __future__ import annotations

import os

import httpx

import config

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_SYSTEM_PROMPT = (
    "You are a prompt-improvement assistant for a Kotlin Multiplatform app builder. "
    "The user sends short, casual modification requests. Your job is to rewrite them "
    "into clear, specific instructions that a coding agent can act on.\n\n"
    "Rules:\n"
    "- Keep the user's intent exactly — do NOT add features they didn't ask for.\n"
    "- Be specific about UI elements, sizes, colors, or behavior where the original is vague.\n"
    "- Use concise, imperative language (e.g. 'Increase the primary action button …').\n"
    "- One short paragraph max. No bullet points, no markdown.\n"
    "- If the prompt is already specific enough, return it unchanged.\n"
    "- Reply with ONLY the improved prompt — no commentary, no quotes."
)


async def suggest(raw_prompt: str) -> str | None:
    """Call Claude Haiku to generate an improved version of *raw_prompt*.

    Returns the suggested prompt string, or ``None`` on any failure
    (missing API key, network error, etc.) so the caller can fall back
    to the original.
    """
    if not ANTHROPIC_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 300,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": raw_prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["content"][0]["text"].strip()
            return text if text else None
    except Exception as e:
        print(f"[prompt_suggest] Failed: {e}")
        return None
