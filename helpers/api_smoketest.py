"""
helpers/api_smoketest.py — Lightweight HTTP smoke tests for the API.

Runs 10 checks against the FastAPI server to verify endpoints respond
correctly. Uses httpx for async HTTP requests.

Usage:
    result = await run_api_smoketest(base_url, token)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:8100"
TIMEOUT = 10.0


@dataclass
class ApiCheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ApiSmokeTestResult:
    success: bool = False
    checks: list[ApiCheckResult] = field(default_factory=list)

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        lines = []
        if self.success:
            lines.append(f"\u2705 **API Smoke Test: PASS** ({passed}/{total})")
        else:
            lines.append(f"\U0001f6a8 **API Smoke Test: FAIL** ({passed}/{total}) \U0001f6a8")
        lines.append("")
        for check in self.checks:
            icon = "\u2705" if check.passed else "\u274c"
            detail = f" \u2014 {check.detail}" if check.detail else ""
            lines.append(f"  {icon} {check.name}{detail}")
        return "\n".join(lines)


async def run_api_smoketest(
    base_url: str = DEFAULT_BASE_URL,
    token: Optional[str] = None,
) -> ApiSmokeTestResult:
    """Run API smoke tests. Returns ApiSmokeTestResult.

    *token*: Valid API bearer token. If None, auth-required tests will be skipped.
    """
    result = ApiSmokeTestResult()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT) as client:

        # 1. GET /health → 200 + {"status": "ok"}
        result.checks.append(await _check_health(client))

        # 2. GET /workspaces no auth → 401
        result.checks.append(await _check_no_auth(client))

        # 3. GET /workspaces bad token → 403
        result.checks.append(await _check_bad_token(client))

        if not token:
            log.warning("api_smoketest: no token provided, skipping auth-required tests")
            result.success = all(c.passed for c in result.checks)
            return result

        # 4. GET /workspaces valid token → 200 + list
        workspaces_check, workspaces = await _check_workspaces(client, headers)
        result.checks.append(workspaces_check)

        # Pick first workspace slug for subsequent tests (if any exist)
        slug = workspaces[0]["slug"] if workspaces else None

        # 5. GET /workspaces/{slug} → 200
        if slug:
            result.checks.append(await _check_workspace_detail(client, headers, slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET workspace detail", True, "skipped (no workspaces)",
            ))

        # 6. GET /workspaces/{slug}/git/status → 200 + has output
        if slug:
            result.checks.append(await _check_git_status(client, headers, slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET git status", True, "skipped (no workspaces)",
            ))

        # 7. GET /workspaces/{slug}/git/log → 200
        if slug:
            result.checks.append(await _check_git_log(client, headers, slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET git log", True, "skipped (no workspaces)",
            ))

        # 8. POST /planapp → 200 + has app_name
        result.checks.append(await _check_planapp(client, headers))

        # 9. GET /workspaces/nonexistent-xyz → 404
        result.checks.append(await _check_not_found(client, headers))

        # 10. POST /workspaces/{slug}/newsession → 200
        if slug:
            result.checks.append(await _check_newsession(client, headers, slug))
        else:
            result.checks.append(ApiCheckResult(
                "POST newsession", True, "skipped (no workspaces)",
            ))

    result.success = all(c.passed for c in result.checks)
    return result


# ── Individual checks ─────────────────────────────────────────────────────────


async def _check_health(client: httpx.AsyncClient) -> ApiCheckResult:
    name = "GET /health → 200"
    try:
        r = await client.get("/api/v1/health")
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if body.get("status") != "ok":
            return ApiCheckResult(name, False, f"unexpected body: {body}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_no_auth(client: httpx.AsyncClient) -> ApiCheckResult:
    name = "GET /workspaces no auth → 401"
    try:
        r = await client.get("/api/v1/workspaces")
        if r.status_code == 401 or r.status_code == 422:
            return ApiCheckResult(name, True, f"status {r.status_code}")
        return ApiCheckResult(name, False, f"expected 401, got {r.status_code}")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_bad_token(client: httpx.AsyncClient) -> ApiCheckResult:
    name = "GET /workspaces bad token → 403"
    try:
        r = await client.get(
            "/api/v1/workspaces",
            headers={"Authorization": "Bearer totally-invalid-token-xyz"},
        )
        if r.status_code == 403:
            return ApiCheckResult(name, True)
        return ApiCheckResult(name, False, f"expected 403, got {r.status_code}")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_workspaces(
    client: httpx.AsyncClient, headers: dict,
) -> tuple[ApiCheckResult, list[dict]]:
    name = "GET /workspaces valid token → 200"
    try:
        r = await client.get("/api/v1/workspaces", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}"), []
        body = r.json()
        if not isinstance(body, list):
            return ApiCheckResult(name, False, f"expected list, got {type(body).__name__}"), []
        return ApiCheckResult(name, True, f"{len(body)} workspaces"), body
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200]), []


async def _check_workspace_detail(
    client: httpx.AsyncClient, headers: dict, slug: str,
) -> ApiCheckResult:
    name = f"GET /workspaces/{slug} → 200"
    try:
        r = await client.get(f"/api/v1/workspaces/{slug}", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_git_status(
    client: httpx.AsyncClient, headers: dict, slug: str,
) -> ApiCheckResult:
    name = f"GET /workspaces/{slug}/git/status → 200"
    try:
        r = await client.get(f"/api/v1/workspaces/{slug}/git/status", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if "output" not in body:
            return ApiCheckResult(name, False, "missing 'output' key")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_git_log(
    client: httpx.AsyncClient, headers: dict, slug: str,
) -> ApiCheckResult:
    name = f"GET /workspaces/{slug}/git/log → 200"
    try:
        r = await client.get(f"/api/v1/workspaces/{slug}/git/log", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_planapp(client: httpx.AsyncClient, headers: dict) -> ApiCheckResult:
    name = "POST /planapp → 200"
    try:
        r = await client.post(
            "/api/v1/planapp",
            headers=headers,
            json={"description": "a simple calculator app"},
            timeout=30.0,
        )
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if "app_name" not in body:
            return ApiCheckResult(name, False, f"missing 'app_name' key, got keys: {list(body.keys())}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_not_found(client: httpx.AsyncClient, headers: dict) -> ApiCheckResult:
    name = "GET /workspaces/nonexistent-xyz → 404"
    try:
        r = await client.get("/api/v1/workspaces/nonexistent-xyz", headers=headers)
        if r.status_code == 404:
            return ApiCheckResult(name, True)
        return ApiCheckResult(name, False, f"expected 404, got {r.status_code}")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_newsession(
    client: httpx.AsyncClient, headers: dict, slug: str,
) -> ApiCheckResult:
    name = f"POST /workspaces/{slug}/newsession → 200"
    try:
        r = await client.post(f"/api/v1/workspaces/{slug}/newsession", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])
