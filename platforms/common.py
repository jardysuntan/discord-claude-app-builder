"""
platforms.common — Shared plumbing for all platform modules.

Holds the dataclasses (BuildResult, DemoResult, DeployResult), the async
subprocess helper `_run`, and `extract_build_error`. Imports nothing from
sibling platform modules to avoid circular imports.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class BuildResult:
    success: bool
    output: str
    error: str = ""


@dataclass
class DemoResult:
    success: bool
    message: str
    screenshot_path: Optional[str] = None
    video_path: Optional[str] = None
    demo_url: Optional[str] = None


@dataclass
class DeployResult:
    success: bool
    message: str


# ── Shared helpers ───────────────────────────────────────────────────────────

async def _run(cmd: list[str], cwd: str = None, timeout: int = 60) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Timed out"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def extract_build_error(raw_output: str, max_lines: int = 60) -> str:
    lines = raw_output.splitlines()
    # Gradle errors
    for i, line in enumerate(lines):
        if "FAILURE:" in line or "BUILD FAILED" in line:
            return "\n".join(lines[i:i + max_lines])
    # Xcode errors
    for i, line in enumerate(lines):
        if "error:" in line.lower() or "** BUILD FAILED **" in line:
            return "\n".join(lines[max(0, i - 5):i + max_lines])
    # Compiler errors
    error_lines = [l for l in lines if l.strip().startswith("e:") or "error:" in l.lower()]
    if error_lines:
        return "\n".join(error_lines[:max_lines])
    return "\n".join(lines[-max_lines:])
