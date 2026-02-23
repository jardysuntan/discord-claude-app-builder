"""commands/run_cmd.py — /run and /runsh handlers."""

import asyncio
import shlex
import config
import safety


async def handle_run(raw_cmd, workspace_path):
    if not raw_cmd:
        return "Usage: `/run <command>`"
    err = safety.validate_run(raw_cmd)
    if err:
        return f"🛑 {err}"
    try:
        args = shlex.split(raw_cmd)
    except ValueError as e:
        return f"🛑 Parse error: {e}"
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=workspace_path)
        out, err_b = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return "⏱️ Timed out (30s)."
    except FileNotFoundError:
        return f"❌ Not found: `{args[0]}`"
    except Exception as e:
        return f"❌ {e}"
    output = (out.decode(errors="replace") + err_b.decode(errors="replace")).strip()
    if len(output) > config.MAX_DISCORD_MSG_LEN:
        output = output[:config.MAX_DISCORD_MSG_LEN] + "\n…"
    return f"```\n{output or '(no output)'}\n```"


async def handle_runsh(raw_cmd, workspace_path):
    if not raw_cmd:
        return "Usage: `/runsh <command>`"
    err = safety.validate_runsh(raw_cmd)
    if err:
        return f"🛑 {err}"
    try:
        proc = await asyncio.create_subprocess_shell(
            f"bash -lc {shlex.quote(raw_cmd)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=workspace_path)
        out, err_b = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return "⏱️ Timed out (30s)."
    except Exception as e:
        return f"❌ {e}"
    output = (out.decode(errors="replace") + err_b.decode(errors="replace")).strip()
    if len(output) > config.MAX_DISCORD_MSG_LEN:
        output = output[:config.MAX_DISCORD_MSG_LEN] + "\n…"
    return f"```\n{output or '(no output)'}\n```"
