"""
platforms.web — Compose for Web / WASM build, serve, tunnel, deploy, demo.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

import config

from platforms.common import (
    BuildResult,
    DemoResult,
    _run,
    extract_build_error,
)


# ═══════════════════════════════════════════════════════════════════════════════
# WEB (Compose for Web / WASM)
# ═══════════════════════════════════════════════════════════════════════════════

_web_server_proc: Optional[asyncio.subprocess.Process] = None
_tunnel_proc: Optional[asyncio.subprocess.Process] = None


class WebPlatform:

    @staticmethod
    async def build(workspace_path: str) -> BuildResult:
        """Build the WASM/JS web target."""
        # Try wasmJsBrowserDistribution first (Compose Multiplatform WASM)
        rc, out, err = await _run(
            ["./gradlew", "composeApp:wasmJsBrowserDistribution"],
            cwd=workspace_path, timeout=300,
        )
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)

        # Only try JS fallback if the WASM task itself doesn't exist
        if "not found in project" in raw and "wasmJsBrowserDistribution" in raw:
            rc2, out2, err2 = await _run(
                ["./gradlew", "composeApp:jsBrowserDistribution"],
                cwd=workspace_path, timeout=300,
            )
            raw2 = out2 + err2
            if rc2 == 0:
                return BuildResult(success=True, output=raw2)
            # JS fallback also missing — return original WASM error
            if "not found in project" in raw2:
                return BuildResult(success=False, output=raw, error=extract_build_error(raw))
            return BuildResult(success=False, output=raw2, error=extract_build_error(raw2))

        # WASM task exists but build failed — return the real error
        return BuildResult(success=False, output=raw, error=extract_build_error(raw))

    @staticmethod
    def _find_dist_dir(workspace_path: str) -> Optional[Path]:
        """Find the built web distribution directory, generating index.html if needed.

        Checks standard locations first, then looks for a custom build dir
        (e.g. Claude may redirect builds to /tmp/ to work around path issues).
        """
        candidates = [
            Path(workspace_path) / "composeApp" / "build" / "dist" / "wasmJs" / "productionExecutable",
            Path(workspace_path) / "composeApp" / "build" / "dist" / "js" / "productionExecutable",
            Path(workspace_path) / "composeApp" / "build" / "distributions",
        ]
        # Check if build.gradle.kts redirects build dir to a custom location
        root_gradle = Path(workspace_path) / "build.gradle.kts"
        if root_gradle.exists():
            import re as _re
            text = root_gradle.read_text()
            m = _re.search(r'java\.io\.tmpdir.*?["\']([^"\']+)["\']', text)
            if m:
                import tempfile
                tmp_base = Path(tempfile.gettempdir()) / m.group(1)
                candidates.extend([
                    tmp_base / "composeApp" / "dist" / "wasmJs" / "productionExecutable",
                    tmp_base / "composeApp" / "dist" / "js" / "productionExecutable",
                ])
        for d in candidates:
            if not d.exists():
                continue
            if any(d.glob("*.html")):
                return d
            # WASM builds may not include index.html — generate one
            js_files = list(d.glob("*.js"))
            wasm_files = list(d.glob("*.wasm"))
            if js_files and wasm_files:
                # Find the main entry JS (usually composeApp.js or similar)
                entry_js = None
                for js in js_files:
                    if "LICENSE" not in js.name and ".map" not in js.name:
                        entry_js = js.name
                        break
                if entry_js:
                    (d / "index.html").write_text(
                        '<!DOCTYPE html>\n<html><head>\n'
                        '<meta charset="UTF-8">\n'
                        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                        '<title>App</title>\n'
                        '<style>html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}'
                        '#ComposeTarget{width:100%;height:100%}</style>\n'
                        '</head><body>\n'
                        '<canvas id="ComposeTarget"></canvas>\n'
                        f'<script src="{entry_js}"></script>\n'
                        '</body></html>\n'
                    )
                    return d
        return None

    @staticmethod
    async def _kill_port(port: int):
        """Kill any process listening on the given port."""
        rc, out, _ = await _run(["lsof", "-ti", f":{port}"])
        if rc == 0 and out.strip():
            for pid in out.strip().splitlines():
                pid = pid.strip()
                if pid.isdigit():
                    await _run(["kill", "-9", pid])
            await asyncio.sleep(0.5)

    @staticmethod
    async def deploy(dist_dir: Path, workspace_key: str) -> Optional[str]:
        """Deploy static files to Cloudflare Pages. Returns the pages.dev URL or None."""
        if not config.CLOUDFLARE_API_TOKEN or not config.CLOUDFLARE_ACCOUNT_ID:
            return None

        # Sanitize workspace key for CF project name (lowercase, alphanumeric + hyphens)
        project = re.sub(r"[^a-z0-9-]", "-", workspace_key.lower()).strip("-")
        if not project:
            return None

        # Write _headers file for COOP/COEP (required for Kotlin/WASM SharedArrayBuffer)
        headers_file = dist_dir / "_headers"
        headers_file.write_text(
            "/*\n"
            "  Cross-Origin-Opener-Policy: same-origin\n"
            "  Cross-Origin-Embedder-Policy: credentialless\n"
        )

        env = {
            **os.environ,
            "CLOUDFLARE_API_TOKEN": config.CLOUDFLARE_API_TOKEN,
            "CLOUDFLARE_ACCOUNT_ID": config.CLOUDFLARE_ACCOUNT_ID,
        }

        # Ensure the Pages project exists (idempotent — fails silently if already created)
        create_proc = await asyncio.create_subprocess_exec(
            "npx", "wrangler", "pages", "project", "create", project,
            "--production-branch=main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        create_out, create_err = await asyncio.wait_for(create_proc.communicate(), timeout=30)
        if create_proc.returncode and create_proc.returncode != 0:
            detail = (create_err or create_out or b"").decode(errors="replace")[:300]
            print(f"[CF Pages] project create failed for '{project}' (rc={create_proc.returncode}): {detail}")

        proc = await asyncio.create_subprocess_exec(
            "npx", "wrangler", "pages", "deploy", str(dist_dir),
            f"--project-name={project}",
            "--commit-dirty=true",
            "--branch=main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        out = out_bytes.decode(errors="replace")
        err = err_bytes.decode(errors="replace")
        combined = out + err

        if proc.returncode != 0:
            # Log so we can diagnose — caller will fall back to tunnel
            msg = f"[CF Pages] deploy failed (rc={proc.returncode}): {combined[:500]}"
            print(msg)
            import logging
            logging.warning(msg)
            return None

        # Parse deployment URL from wrangler output
        m = re.search(r"https://[a-z0-9-]+\.[a-z0-9-]+\.pages\.dev", combined)
        if m:
            # Return the stable project URL, not the per-deploy hash URL
            return f"https://{project}.pages.dev"
        # If we can't parse but deploy succeeded, return the expected URL
        return f"https://{project}.pages.dev"

    @staticmethod
    async def serve(workspace_path: str, workspace_key: str = "") -> Optional[str]:
        """Start a simple HTTP server for the built web app."""
        global _web_server_proc, _tunnel_proc

        # Stop existing server (ours + any orphan on the port)
        await WebPlatform.stop_server()
        await WebPlatform._kill_port(config.WEB_SERVE_PORT)

        dist_dir = WebPlatform._find_dist_dir(workspace_path)
        if not dist_dir:
            return None

        # Custom server with COOP/COEP headers required for Kotlin/WASM SharedArrayBuffer
        server_script = (
            "import http.server,sys,os\n"
            "os.chdir(sys.argv[2])\n"
            "class H(http.server.SimpleHTTPRequestHandler):\n"
            "  def end_headers(self):\n"
            "    self.send_header('Cross-Origin-Opener-Policy','same-origin')\n"
            "    self.send_header('Cross-Origin-Embedder-Policy','credentialless')\n"
            "    super().end_headers()\n"
            "  def log_message(self,*a):pass\n"
            "http.server.HTTPServer(('',int(sys.argv[1])),H).serve_forever()\n"
        )
        _web_server_proc = await asyncio.create_subprocess_exec(
            "python3", "-c", server_script,
            str(config.WEB_SERVE_PORT), str(dist_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1)

        # Try Cloudflare Pages deploy for a persistent URL
        if workspace_key:
            pages_url = await WebPlatform.deploy(dist_dir, workspace_key)
            if pages_url:
                return pages_url

        # Fallback: try cloudflared quick tunnel
        public_url = await WebPlatform._start_tunnel()
        if public_url:
            return public_url

        # Fallback to Tailscale / localhost
        host = config.TAILSCALE_HOSTNAME or "localhost"
        return f"http://{host}:{config.WEB_SERVE_PORT}"

    @staticmethod
    async def _start_tunnel() -> Optional[str]:
        """Start a cloudflared quick tunnel. Returns public URL or None."""
        global _tunnel_proc
        await WebPlatform._stop_tunnel()

        import shutil
        if not shutil.which("cloudflared"):
            return None

        _tunnel_proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://localhost:{config.WEB_SERVE_PORT}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # cloudflared prints the public URL to stderr
        # Wait up to 15 seconds for the URL to appear
        url = None
        import re as _re
        try:
            deadline = asyncio.get_event_loop().time() + 15
            buffer = b""
            while asyncio.get_event_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(_tunnel_proc.stderr.read(4096), timeout=2)
                    if not chunk:
                        break
                    buffer += chunk
                    m = _re.search(rb"https://[a-zA-Z0-9-]+\.trycloudflare\.com", buffer)
                    if m:
                        url = m.group(0).decode()
                        break
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

        if not url:
            await WebPlatform._stop_tunnel()
        return url

    @staticmethod
    async def _stop_tunnel():
        global _tunnel_proc
        if _tunnel_proc and _tunnel_proc.returncode is None:
            _tunnel_proc.terminate()
            try:
                await asyncio.wait_for(_tunnel_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _tunnel_proc.kill()
            _tunnel_proc = None

    @staticmethod
    async def stop_server():
        global _web_server_proc
        await WebPlatform._stop_tunnel()
        if _web_server_proc and _web_server_proc.returncode is None:
            _web_server_proc.terminate()
            try:
                await asyncio.wait_for(_web_server_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _web_server_proc.kill()
            _web_server_proc = None

    @staticmethod
    async def check_health(url: str) -> Optional[str]:
        """HTTP GET the url; returns error string or None if healthy."""
        import urllib.request
        loop = asyncio.get_event_loop()
        try:
            def _fetch():
                resp = urllib.request.urlopen(url, timeout=10)
                body = resp.read()
                if resp.status != 200:
                    return f"HTTP {resp.status}"
                if len(body) < 50:
                    return "Response body too small (likely empty page)"
                return None
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            return str(e)

    @staticmethod
    async def full_demo(workspace_path: str, workspace_key: str = "") -> DemoResult:
        result = await WebPlatform.build(workspace_path)
        if not result.success:
            return DemoResult(success=False, message=f"❌ Web build failed:\n```\n{result.error[:800]}\n```")
        url = await WebPlatform.serve(workspace_path, workspace_key)
        if not url:
            return DemoResult(success=False, message="❌ Built but could not find distribution directory.")
        return DemoResult(
            success=True,
            message=f"✅ Web app live!",
            demo_url=url,
        )
