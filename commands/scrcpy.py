"""commands/scrcpy.py — ws-scrcpy mirror for Android emulator."""

import asyncio
import os
from typing import Optional
import config

SCRCPY_DIR = config.SCRCPY_DIR
SCRCPY_PORT = config.SCRCPY_PORT
_scrcpy_process: Optional[asyncio.subprocess.Process] = None


async def _is_running():
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsof", "-i", f":{SCRCPY_PORT}", "-t",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return bool(out.strip())
    except:
        return False


def _get_url():
    host = config.TAILSCALE_HOSTNAME or "localhost"
    return f"http://{host}:{SCRCPY_PORT}"


async def start():
    global _scrcpy_process
    if await _is_running():
        return f"📱 Mirror already running → {_get_url()}"
    dist_dir = os.path.join(SCRCPY_DIR, "dist")
    if not os.path.isfile(os.path.join(dist_dir, "index.js")):
        return f"❌ ws-scrcpy not built. Run `npm run dist` in `{SCRCPY_DIR}`."
    try:
        _scrcpy_process = await asyncio.create_subprocess_exec(
            "node", "index.js", f"--port={SCRCPY_PORT}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            cwd=dist_dir)
        await asyncio.sleep(3)
        return f"📱 **Mirror started** → {_get_url()}\nTap, swipe, interact! Use `/mirror stop` when done."
    except Exception as e:
        return f"❌ Failed: {e}"


async def stop():
    global _scrcpy_process
    if _scrcpy_process and _scrcpy_process.returncode is None:
        _scrcpy_process.terminate()
        _scrcpy_process = None
        return "🛑 Mirror stopped."
    return "Mirror wasn't running."


async def handle_mirror(sub):
    match sub:
        case "start" | "on":  return await start()
        case "stop" | "off":  return await stop()
        case "status":
            return f"📱 {'Running' if await _is_running() else 'Not running'} → {_get_url()}"
        case _:
            return "`/mirror start` · `/mirror stop` · `/mirror status`"
