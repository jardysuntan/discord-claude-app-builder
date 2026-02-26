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


_CODE_EXTENSIONS = {".kt", ".swift", ".kts", ".xml", ".gradle", ".java", ".py", ".js", ".ts"}

# Bash commands that are just investigation — skip entirely
_NOISE_COMMANDS = {"find", "ls", "cat", "head", "tail", "grep", "rg", "wc", "file",
                   "tree", "pwd", "echo", "which", "type", "stat", "du", "diff",
                   "sort", "uniq", "awk", "sed", "tr", "cut", "test", "["}

# Bash commands → friendly labels
_FRIENDLY_BASH = {
    "gradlew": "🔨 Building project…",
    "./gradlew": "🔨 Building project…",
    "gradle": "🔨 Building project…",
    "xcodebuild": "🍎 Building for iOS…",
    "swift": "🍎 Compiling Swift…",
    "npm": "📦 Running npm…",
    "npx": "📦 Running npx…",
    "yarn": "📦 Running yarn…",
    "pnpm": "📦 Running pnpm…",
    "pip": "📦 Installing dependencies…",
    "pod": "📦 Installing CocoaPods…",
    "cargo": "🔨 Building with Cargo…",
    "make": "🔨 Building…",
    "cmake": "🔨 Configuring build…",
    "adb": "📱 Communicating with device…",
    "xcrun": "🍎 Running Xcode tool…",
    "git": "📋 Updating repository…",
    "mkdir": "📁 Setting up folders…",
    "cp": "📁 Copying files…",
    "mv": "📁 Moving files…",
    "rm": "🗑️ Cleaning up…",
    "chmod": "🔧 Setting permissions…",
    "curl": "🌐 Downloading…",
    "wget": "🌐 Downloading…",
    "cd": None,  # skip — just navigation
}


def _friendly_bash(cmd: str, last_text: str) -> Optional[str]:
    """Turn a raw bash command into a user-friendly progress message, or None to skip."""
    # Get the base command (handle paths like ./gradlew, /usr/bin/git, etc.)
    first = cmd.split()[0] if cmd.split() else ""
    base = Path(first).name if "/" in first else first

    # Skip investigation noise
    if base in _NOISE_COMMANDS:
        return None

    # Known commands → friendly label
    # Check both full first token (./gradlew) and base name (gradlew)
    friendly = _FRIENDLY_BASH.get(first) or _FRIENDLY_BASH.get(base)
    if friendly is not None:
        return friendly
    if friendly is None and (first in _FRIENDLY_BASH or base in _FRIENDLY_BASH):
        return None  # explicitly skipped (like cd)

    # Unknown command — use preceding text as explanation if available
    if last_text:
        explanation = last_text.split("\n")[0][:120]
        return f"⚙️ {explanation}"

    return f"⚙️ Running a command…"


def _progress_from_event(event: dict, state: dict | None = None) -> Optional[str]:
    """Extract a human-readable progress message from a stream-json event.

    Filters out investigation noise (Read/Glob/Grep, exploratory bash) and text blocks.
    For Write/Edit on code files, uses the preceding text block as explanation.
    For Bash, classifies into friendly labels or skips noise.

    ``state`` is a mutable dict tracking ``last_text`` across calls within
    one assistant message.  The caller can pass ``{}`` once and reuse it.
    """
    if state is None:
        state = {}

    etype = event.get("type")

    if etype == "assistant":
        content = event.get("message", {}).get("content", [])
        last_text = ""
        result = None
        for block in content:
            if block.get("type") == "text":
                # Track text for use as explanation — don't emit it
                last_text = block.get("text", "").strip()
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input", {})

            # Skip investigation tools — noise to end users
            if name in ("Read", "Glob", "Grep"):
                continue

            if name in ("Write", "Edit"):
                target = inp.get("file_path", "")
                short = Path(target).name if "/" in target else target
                ext = Path(short).suffix.lower()
                if ext in _CODE_EXTENSIONS and last_text:
                    explanation = last_text.split("\n")[0][:120]
                    result = f"✏️ `{short}` — {explanation}"
                else:
                    result = f"📝 {'Writing' if name == 'Write' else 'Editing'} `{short}`"
            elif name == "Bash":
                result = _friendly_bash(inp.get("command", ""), last_text)
            else:
                result = f"🔧 Using {name}"
        return result
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
            env = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000"}
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                env=env,
                limit=10 * 1024 * 1024,  # 10MB — default 64KB is too small for large stream-json events
            )
            print(f"[claude] Process started: pid={proc.pid}")

            result_text = ""
            result_session_id = None
            result_cost_usd = 0.0
            stderr_chunks = []
            last_progress_time = time.time()
            process_done = False
            MIN_PROGRESS_INTERVAL = 3  # seconds between Discord updates
            HEARTBEAT_INTERVAL = 10    # seconds of silence before heartbeat

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

            # Heartbeat: send "still working" when user hasn't seen an update
            async def heartbeat():
                nonlocal last_progress_time
                while not process_done:
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    if process_done:
                        break
                    now = time.time()
                    if on_progress and now - last_progress_time >= HEARTBEAT_INTERVAL:
                        try:
                            await on_progress("⏳ Still working…")
                        except Exception:
                            pass
                        last_progress_time = now

            heartbeat_task = asyncio.create_task(heartbeat())

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
                    process_done = True
                    heartbeat_task.cancel()
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

            process_done = True
            heartbeat_task.cancel()
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
