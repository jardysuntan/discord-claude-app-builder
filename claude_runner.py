"""
claude_runner.py — Invoke Claude Code CLI with session continuity.
Uses -r <session_id> to resume sessions per workspace.
Uses --output-format stream-json for real-time progress updates.
"""

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

import config


@dataclass
class ClaudeResult:
    stdout: str
    stderr: str
    exit_code: int
    session_id: Optional[str] = None
    total_cost_usd: float = 0.0


def _resolve_claude_bin() -> str:
    """Resolve the claude binary path, falling back to PATH lookup."""
    if os.path.isabs(config.CLAUDE_BIN):
        return config.CLAUDE_BIN
    found = shutil.which(config.CLAUDE_BIN)
    return found or config.CLAUDE_BIN


def _progress_from_event(event: dict) -> Optional[str]:
    """Extract a human-readable progress message from a stream-json event."""
    etype = event.get("type")

    if etype == "assistant":
        content = event.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {})
                # Build a short description of what Claude is doing
                if name in ("Read", "Glob", "Grep"):
                    target = inp.get("file_path") or inp.get("pattern") or ""
                    short = Path(target).name if "/" in target else target
                    return f"📖 Reading `{short}`" if name == "Read" else f"🔍 {name}: `{short}`"
                elif name in ("Write", "Edit"):
                    target = inp.get("file_path", "")
                    short = Path(target).name if "/" in target else target
                    return f"📝 {'Writing' if name == 'Write' else 'Editing'} `{short}`"
                elif name == "Bash":
                    cmd = inp.get("command", "")
                    if len(cmd) > 60:
                        cmd = cmd[:57] + "..."
                    return f"⚙️ Running `{cmd}`"
                else:
                    return f"🔧 Using {name}"
            elif block.get("type") == "text":
                text = block.get("text", "")
                if text and len(text) > 5:
                    preview = text[:200] + "…" if len(text) > 200 else text
                    return f"💬 {preview}"
    return None


class ClaudeRunner:
    def __init__(self):
        self._sessions: dict[str, str] = {}
        self._claude_bin = _resolve_claude_bin()
        print(f"  Claude binary:   {self._claude_bin}")

    def get_session(self, workspace: str) -> Optional[str]:
        return self._sessions.get(workspace)

    def clear_session(self, workspace: str):
        self._sessions.pop(workspace, None)

    async def run(
        self,
        prompt: str,
        workspace_key: str,
        workspace_path: str,
        context_prefix: str = "",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ClaudeResult:
        full_prompt = f"{context_prefix}\n\n{prompt}".strip() if context_prefix else prompt

        # All flags MUST come before the positional prompt argument.
        cmd = [
            self._claude_bin,
            "--dangerously-skip-permissions",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
        ]
        session_id = self._sessions.get(workspace_key)
        if session_id:
            cmd += ["-r", session_id]
        cmd.append(full_prompt)  # positional prompt goes last

        print(f"[claude] Starting: workspace={workspace_key} prompt_len={len(full_prompt)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                limit=10 * 1024 * 1024,  # 10MB — default 64KB is too small for large stream-json events
            )
            print(f"[claude] Process started: pid={proc.pid}")

            result_text = ""
            result_session_id = None
            result_cost_usd = 0.0
            stderr_chunks = []
            last_progress_time = time.time()
            MIN_PROGRESS_INTERVAL = 3  # seconds between Discord updates

            # Read stderr in background
            async def read_stderr():
                while True:
                    chunk = await proc.stderr.read(1024)
                    if not chunk:
                        break
                    decoded = chunk.decode("utf-8", errors="replace")
                    stderr_chunks.append(decoded)
                    for line in decoded.splitlines():
                        if line.strip():
                            print(f"[claude:stderr] {line.strip()}")

            stderr_task = asyncio.create_task(read_stderr())

            # Read stream-json events line by line
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=config.CLAUDE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    print(f"[claude] Timed out after {config.CLAUDE_TIMEOUT}s")
                    return ClaudeResult(
                        stdout=result_text,
                        stderr=f"Timed out after {config.CLAUDE_TIMEOUT}s",
                        exit_code=-1,
                    )

                if not line:
                    break

                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                # Extract final result
                if etype == "result":
                    result_text = event.get("result", "")
                    result_session_id = event.get("session_id")
                    is_error = event.get("is_error", False)
                    cost = event.get("total_cost_usd", 0) or 0
                    result_cost_usd = float(cost)
                    duration = event.get("duration_ms", 0) / 1000
                    print(f"[claude] Result: error={is_error} cost=${cost:.4f} duration={duration:.1f}s")

                # Send progress updates to Discord
                elif on_progress:
                    progress_msg = _progress_from_event(event)
                    if progress_msg:
                        now = time.time()
                        if now - last_progress_time >= MIN_PROGRESS_INTERVAL:
                            try:
                                await on_progress(progress_msg)
                            except Exception:
                                pass
                            last_progress_time = now
                        print(f"[claude] {progress_msg}")

            await stderr_task
            await proc.wait()
            stderr = "".join(stderr_chunks)

        except Exception as e:
            print(f"[claude] Exception: {e}")
            return ClaudeResult(stdout="", stderr=str(e), exit_code=-1)

        exit_code = proc.returncode or 0
        print(f"[claude] Done: exit_code={exit_code} result_len={len(result_text)} stderr_len={len(stderr)}")

        if result_session_id:
            self._sessions[workspace_key] = result_session_id

        return ClaudeResult(
            stdout=result_text,
            stderr=stderr,
            exit_code=exit_code,
            session_id=result_session_id or session_id,
            total_cost_usd=result_cost_usd,
        )
