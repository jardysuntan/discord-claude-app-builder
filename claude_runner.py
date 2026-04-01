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
    context_tokens: int = 0  # total input context (input + cache_creation + cache_read)


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


def _progress_from_event(event: dict, state: Optional[dict] = None) -> Optional[str]:
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
    _SESSIONS_FILE = Path(config.WORKSPACES_PATH).parent / "claude_sessions.json"
    _SESSION_TOKENS_FILE = Path(config.WORKSPACES_PATH).parent / "claude_session_tokens.json"
    _SUMMARIES_DIR = Path(config.SESSION_SUMMARIES_DIR)

    def __init__(self):
        self._sessions: dict[str, str] = {}
        self._session_tokens: dict[str, int] = {}  # workspace → last known context size in tokens
        self._run_durations: dict[str, list[float]] = {}  # workspace → last N durations
        self._active_procs: dict[str, asyncio.subprocess.Process] = {}
        self._claude_bin = _resolve_claude_bin()
        self._load_sessions()
        self._load_session_tokens()
        self._SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Claude binary:   {self._claude_bin}")
        print(f"  Persisted sessions: {len(self._sessions)}")

    def _load_sessions(self):
        if self._SESSIONS_FILE.exists():
            try:
                with open(self._SESSIONS_FILE) as f:
                    self._sessions = json.load(f)
            except (json.JSONDecodeError, ValueError):
                self._sessions = {}

    def _save_sessions(self):
        with open(self._SESSIONS_FILE, "w") as f:
            json.dump(self._sessions, f, indent=2)

    def _load_session_tokens(self):
        if self._SESSION_TOKENS_FILE.exists():
            try:
                with open(self._SESSION_TOKENS_FILE) as f:
                    self._session_tokens = {k: int(v) for k, v in json.load(f).items()}
            except (json.JSONDecodeError, ValueError):
                self._session_tokens = {}

    def _save_session_tokens(self):
        with open(self._SESSION_TOKENS_FILE, "w") as f:
            json.dump(self._session_tokens, f, indent=2)

    def _record_context_tokens(self, workspace: str, tokens: int):
        """Update the last known context size for this workspace session."""
        self._session_tokens[workspace] = tokens
        self._save_session_tokens()

    def _session_needs_rotation(self, workspace: str) -> bool:
        """Check if session context size exceeds the rotation threshold."""
        return self._session_tokens.get(workspace, 0) >= config.SESSION_CONTEXT_ROTATION_TOKENS

    def _get_summary_path(self, workspace: str) -> Path:
        return self._SUMMARIES_DIR / f"{workspace}.md"

    def _load_handoff_summary(self, workspace: str) -> str:
        """Load the handoff summary for a workspace, if one exists."""
        path = self._get_summary_path(workspace)
        if path.exists():
            try:
                return path.read_text().strip()
            except Exception:
                return ""
        return ""

    def _save_handoff_summary(self, workspace: str, summary: str):
        """Persist a handoff summary for the next session."""
        path = self._get_summary_path(workspace)
        path.write_text(summary)

    async def _generate_handoff_summary(self, workspace: str, workspace_path: str) -> Optional[str]:
        """Ask Claude to summarize the current session before rotating."""
        summary_prompt = (
            "Summarize this session in 5-10 concise bullet points for a future AI assistant "
            "continuing work on this project. Include:\n"
            "- Key changes made (files, features, architecture decisions)\n"
            "- User preferences or style choices expressed\n"
            "- Current state (what works, what's broken, what's next)\n"
            "- Any open threads or incomplete work\n"
            "Keep it under 500 words. Be specific — mention file names, component names, decisions."
        )
        try:
            result = await self._run_raw(summary_prompt, workspace, workspace_path)
            if result.exit_code == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as e:
            print(f"[claude] Handoff summary generation failed: {e}")
        return None

    async def _rotate_session(self, workspace: str, workspace_path: str,
                              on_progress: Optional[Callable[[str], Awaitable[None]]] = None):
        """Generate handoff summary, clear session, reset cost counter."""
        tokens = self._session_tokens.get(workspace, 0)
        print(f"[claude] Rotating session for {workspace} (context: {tokens:,} tokens)")

        if on_progress:
            try:
                await on_progress("🔄 Session getting long — saving context and starting fresh...")
            except Exception:
                pass

        # Generate summary from current session
        summary = await self._generate_handoff_summary(workspace, workspace_path)
        if summary:
            # Append to any existing summary (rolling context)
            existing = self._load_handoff_summary(workspace)
            if existing:
                combined = f"{existing}\n\n---\n_Previous session context:_\n{summary}"
                # Keep only last 2 summaries worth of context
                parts = combined.split("\n---\n")
                if len(parts) > 2:
                    combined = "\n---\n".join(parts[-2:])
                self._save_handoff_summary(workspace, combined)
            else:
                self._save_handoff_summary(workspace, summary)
            print(f"[claude] Handoff summary saved ({len(summary)} chars)")
        else:
            print("[claude] No handoff summary generated — rotating anyway")

        # Clear session and reset token counter
        self.clear_session(workspace)
        self._session_tokens.pop(workspace, None)
        self._save_session_tokens()

        if on_progress:
            try:
                await on_progress("✅ Context saved — continuing with fresh session")
            except Exception:
                pass

    def cancel(self, workspace: str) -> bool:
        """Kill the active Claude process for a workspace. Returns True if killed."""
        proc = self._active_procs.pop(workspace, None)
        if proc and proc.returncode is None:
            proc.kill()
            print(f"[claude] Cancelled: workspace={workspace} pid={proc.pid}")
            return True
        return False

    def _record_duration(self, workspace: str, duration: float):
        """Record a run duration for ETA estimates."""
        if workspace not in self._run_durations:
            self._run_durations[workspace] = []
        self._run_durations[workspace].append(duration)
        # Keep last 10
        self._run_durations[workspace] = self._run_durations[workspace][-10:]

    def _estimated_duration(self, workspace: str) -> Optional[float]:
        """Return average duration for this workspace, or None if no history."""
        durations = self._run_durations.get(workspace, [])
        if not durations:
            return None
        return sum(durations) / len(durations)

    def get_session(self, workspace: str) -> Optional[str]:
        return self._sessions.get(workspace)

    def clear_session(self, workspace: str):
        if self._sessions.pop(workspace, None) is not None:
            self._save_sessions()

    async def _run_raw(
        self,
        prompt: str,
        workspace_key: str,
        workspace_path: str,
    ) -> ClaudeResult:
        """Run Claude without rotation checks or progress callbacks. Used for internal calls like summary generation."""
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
        cmd.append(prompt)

        env = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"}
        env.pop("CLAUDECODE", None)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_path,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
        result_text = ""
        for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
                if event.get("type") == "result":
                    result_text = event.get("result", "")
            except json.JSONDecodeError:
                continue
        return ClaudeResult(
            stdout=result_text,
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
        )

    async def run(
        self,
        prompt: str,
        workspace_key: str,
        workspace_path: str,
        context_prefix: str = "",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ClaudeResult:
        # Check if session needs rotation before this call
        if self._session_needs_rotation(workspace_key) and self._sessions.get(workspace_key):
            await self._rotate_session(workspace_key, workspace_path, on_progress)

        # Inject handoff summary if starting a fresh session
        if not self._sessions.get(workspace_key):
            summary = self._load_handoff_summary(workspace_key)
            if summary:
                summary_context = (
                    "CONTEXT FROM PREVIOUS SESSION — use this to maintain continuity:\n"
                    f"{summary}\n\n"
                    "Continue working with this context in mind. The user should not notice any disruption."
                )
                context_prefix = f"{summary_context}\n\n{context_prefix}".strip() if context_prefix else summary_context

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
            env.pop("CLAUDECODE", None)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                env=env,
                limit=10 * 1024 * 1024,  # 10MB — default 64KB is too small for large stream-json events
            )
            self._active_procs[workspace_key] = proc
            print(f"[claude] Process started: pid={proc.pid}")

            result_text = ""
            result_session_id = None
            result_cost_usd = 0.0
            result_context_tokens = 0
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

            # Heartbeat: send "still working" with elapsed time + ETA
            run_start_time = time.time()
            est = self._estimated_duration(workspace_key)

            async def heartbeat():
                nonlocal last_progress_time
                while not process_done:
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    if process_done:
                        break
                    now = time.time()
                    if on_progress and now - last_progress_time >= HEARTBEAT_INTERVAL:
                        elapsed = int(now - run_start_time)
                        mins, secs = divmod(elapsed, 60)
                        elapsed_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                        if est and est > 30:
                            remaining = max(0, int(est - elapsed))
                            r_mins, r_secs = divmod(remaining, 60)
                            if remaining <= 0:
                                eta_str = "almost done"
                            elif r_mins > 0:
                                eta_str = f"~{r_mins}m {r_secs}s left"
                            else:
                                eta_str = f"~{r_secs}s left"
                            msg = f"⏳ Still working… ({elapsed_str} · {eta_str})"
                        else:
                            msg = f"⏳ Still working… ({elapsed_str})"
                        try:
                            await on_progress(msg)
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
                    # Extract context token usage
                    usage = event.get("usage", {})
                    result_context_tokens = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    print(f"[claude] Result: error={is_error} cost=${cost:.4f} duration={duration:.1f}s context={result_context_tokens:,} tokens")

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
        finally:
            self._active_procs.pop(workspace_key, None)

        exit_code = proc.returncode or 0
        run_duration = time.time() - run_start_time
        print(f"[claude] Done: exit_code={exit_code} result_len={len(result_text)} stderr_len={len(stderr)} duration={run_duration:.1f}s")

        # Record duration for future ETA estimates
        if exit_code == 0:
            self._record_duration(workspace_key, run_duration)

        if result_session_id:
            self._sessions[workspace_key] = result_session_id
            self._save_sessions()

        # Track context size for rotation
        if result_context_tokens > 0:
            self._record_context_tokens(workspace_key, result_context_tokens)
            threshold = config.SESSION_CONTEXT_ROTATION_TOKENS
            print(f"[claude] Context for {workspace_key}: {result_context_tokens:,} / {threshold:,} token threshold")

        return ClaudeResult(
            stdout=result_text,
            stderr=stderr,
            exit_code=exit_code,
            session_id=result_session_id or session_id,
            total_cost_usd=result_cost_usd,
            context_tokens=result_context_tokens,
        )
