"""helpers/parallel_build.py — lightweight parallel Claude build orchestration.

This is a focused first slice for complex /buildapp requests:
- detect when a description is complex enough to benefit from parallel work
- split the feature request into 2-4 scoped tasks
- run Claude in separate git worktrees concurrently
- merge successful task branches back into the main workspace before the normal
  build/fix loop continues

The existing build loop still owns compilation, screenshots, and autofix. This
module only accelerates the initial implementation pass.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import config

StatusCallback = Optional[Callable[[str], Awaitable[None]]]


@dataclass
class ParallelTask:
    slug: str
    title: str
    prompt: str


@dataclass
class ParallelTaskResult:
    task: ParallelTask
    branch: str
    worktree_path: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass
class ParallelBuildResult:
    enabled: bool
    success: bool
    tasks: list[ParallelTaskResult]
    merged_branches: list[str]
    summary: str


def should_parallelize(description: str) -> bool:
    text = (description or "").lower()
    if not text:
        return False
    if "parallel:true" in text or "parallel build" in text or "parallel agents" in text:
        return True

    complexity_signals = [
        "authentication",
        "backend",
        "database",
        "schema",
        "payments",
        "subscription",
        "api middleware",
        "platform-specific",
        "ios and android",
        "tests",
        "real-time",
        "sync",
    ]
    score = sum(1 for signal in complexity_signals if signal in text)
    return score >= 2 or len(text.split()) >= 30


def build_parallel_tasks(app_name: str, description: str) -> list[ParallelTask]:
    base_rules = (
        f'You are working on the Kotlin Multiplatform app "{app_name}". '
        "Make focused, production-style changes only for your assigned slice. "
        "Do not refactor unrelated code. Keep file edits scoped so merges are easy."
    )
    return [
        ParallelTask(
            slug="ui",
            title="UI / Compose Multiplatform",
            prompt=(
                f"{base_rules}\n\n"
                f"App request: {description}\n\n"
                "Own the UI slice:\n"
                "- implement screens, navigation structure, state holders, and polished Compose UI\n"
                "- prefer commonMain shared UI\n"
                "- stub interfaces if another slice owns data integration\n"
                "- avoid changing backend or platform glue unless strictly needed\n"
                "Return a short summary of files changed."
            ),
        ),
        ParallelTask(
            slug="logic",
            title="Shared logic / data layer",
            prompt=(
                f"{base_rules}\n\n"
                f"App request: {description}\n\n"
                "Own the shared logic slice:\n"
                "- implement repositories, models, services, and app state wiring\n"
                "- keep APIs simple for the UI layer to consume\n"
                "- add any minimal backend/client integration scaffolding implied by the request\n"
                "- avoid heavy UI churn unless needed to expose the new logic\n"
                "Return a short summary of files changed."
            ),
        ),
        ParallelTask(
            slug="platform",
            title="Platform integration",
            prompt=(
                f"{base_rules}\n\n"
                f"App request: {description}\n\n"
                "Own the platform slice:\n"
                "- implement expect/actual or platform-specific integration needed for Android/iOS/Web\n"
                "- update build files or platform wiring when required\n"
                "- keep changes minimal and compatible with the shared code slices\n"
                "Return a short summary of files changed."
            ),
        ),
        ParallelTask(
            slug="tests",
            title="Validation / tests",
            prompt=(
                f"{base_rules}\n\n"
                f"App request: {description}\n\n"
                "Own the validation slice:\n"
                "- add or update focused tests/docs/sanity checks for the new feature\n"
                "- if the repo lacks obvious automated tests for this area, add lightweight guardrails only\n"
                "- do not make broad production code changes unless needed for testability\n"
                "Return a short summary of files changed."
            ),
        ),
    ]


async def _emit(on_status: StatusCallback, message: str) -> None:
    if on_status:
        await on_status(message)


async def _run_cmd(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    result = subprocess.CompletedProcess(
        args=args,
        returncode=proc.returncode,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}")
    return result


async def _run_claude_task(task: ParallelTask, workspace_key: str, worktree_path: str) -> ParallelTaskResult:
    branch = f"parallel/{workspace_key}/{task.slug}"
    claude_bin = shutil.which(config.CLAUDE_BIN) if not os.path.isabs(config.CLAUDE_BIN) else config.CLAUDE_BIN
    claude_bin = claude_bin or config.CLAUDE_BIN
    cmd = [
        claude_bin,
        "--dangerously-skip-permissions",
        "-p",
        task.prompt,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000"},
    )
    stdout_b, stderr_b = await proc.communicate()
    return ParallelTaskResult(
        task=task,
        branch=branch,
        worktree_path=worktree_path,
        success=proc.returncode == 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        exit_code=proc.returncode or 0,
    )


async def maybe_run_parallel_build(
    app_name: str,
    description: str,
    workspace_key: str,
    workspace_path: str,
    on_status: StatusCallback = None,
) -> ParallelBuildResult:
    if not should_parallelize(description):
        return ParallelBuildResult(
            enabled=False,
            success=True,
            tasks=[],
            merged_branches=[],
            summary="Parallel mode not needed.",
        )

    repo_root = Path(workspace_path)
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return ParallelBuildResult(
            enabled=True,
            success=False,
            tasks=[],
            merged_branches=[],
            summary="Parallel mode skipped because the workspace is not a git repo.",
        )

    tasks = build_parallel_tasks(app_name, description)
    parallel_root = repo_root / ".parallel-builds"
    parallel_root.mkdir(exist_ok=True)

    current_branch = (await _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path)).stdout.strip()
    created: list[tuple[ParallelTask, str, str]] = []

    try:
        for task in tasks:
            branch = f"parallel/{workspace_key}/{task.slug}"
            worktree_path = str(parallel_root / task.slug)
            if os.path.exists(worktree_path):
                await _run_cmd(["git", "worktree", "remove", "--force", worktree_path], cwd=workspace_path, check=False)
            await _run_cmd(["git", "branch", "-D", branch], cwd=workspace_path, check=False)
            await _run_cmd(["git", "worktree", "add", "-b", branch, worktree_path, current_branch], cwd=workspace_path)
            created.append((task, branch, worktree_path))

        await _emit(on_status, f"⚡ Parallel mode enabled — launching {len(created)} Claude worktrees.")
        runs = [
            _run_claude_task(task, workspace_key, worktree_path)
            for task, _branch, worktree_path in created
        ]
        results = await asyncio.gather(*runs)

        failed = [r for r in results if not r.success]
        if failed:
            details = "; ".join(f"{r.task.slug}: exit {r.exit_code}" for r in failed)
            return ParallelBuildResult(
                enabled=True,
                success=False,
                tasks=results,
                merged_branches=[],
                summary=f"Parallel task failed before merge ({details}).",
            )

        merged: list[str] = []
        for task, branch, _worktree_path in created:
            diff = await _run_cmd(["git", "diff", "--name-only", f"{current_branch}..{branch}"], cwd=workspace_path)
            if not diff.stdout.strip():
                continue
            merge = await _run_cmd(["git", "merge", "--no-ff", branch, "-m", f"Merge parallel task: {task.title}"], cwd=workspace_path, check=False)
            if merge.returncode != 0:
                await _run_cmd(["git", "merge", "--abort"], cwd=workspace_path, check=False)
                return ParallelBuildResult(
                    enabled=True,
                    success=False,
                    tasks=results,
                    merged_branches=merged,
                    summary=f"Merge conflict while applying {branch}: {(merge.stderr or merge.stdout)[:300]}",
                )
            merged.append(branch)
            await _emit(on_status, f"✅ Merged parallel task: {task.title}")

        summary_bits = []
        for result in results:
            output = (result.stdout or result.stderr).strip().splitlines()
            tail = output[-1][:120] if output else "done"
            summary_bits.append(f"{result.task.slug}: {tail}")

        return ParallelBuildResult(
            enabled=True,
            success=True,
            tasks=results,
            merged_branches=merged,
            summary="Parallel implementation pass complete — " + "; ".join(summary_bits),
        )
    finally:
        for _task, branch, worktree_path in created:
            await _run_cmd(["git", "worktree", "remove", "--force", worktree_path], cwd=workspace_path, check=False)
            await _run_cmd(["git", "branch", "-D", branch], cwd=workspace_path, check=False)
