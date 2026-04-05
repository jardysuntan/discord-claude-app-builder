"""
agent_protocol.py — provider-agnostic runner contract for coding agents.

This keeps the rest of the app focused on "run an agent task in a workspace"
instead of binding orchestration directly to a single model vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol


ProgressCallback = Optional[Callable[[str], Awaitable[None]]]


@dataclass
class AgentRunResult:
    stdout: str
    stderr: str
    exit_code: int
    session_id: Optional[str] = None
    total_cost_usd: float = 0.0
    context_tokens: int = 0


class AgentRunner(Protocol):
    async def run(
        self,
        prompt: str,
        workspace_key: str,
        workspace_path: str,
        context_prefix: str = "",
        on_progress: ProgressCallback = None,
    ) -> AgentRunResult:
        ...

    def cancel(self, workspace: str) -> bool:
        ...

    def clear_session(self, workspace: str) -> None:
        ...

    def get_resume_count(self, workspace: str) -> int:
        ...

    def get_session(self, workspace: str) -> Optional[str]:
        ...
