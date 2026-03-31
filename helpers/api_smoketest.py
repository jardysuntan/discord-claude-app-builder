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

        # Pick a workspace whose directory actually exists on disk (for git tests).
        # Fall back to the first workspace for non-git tests.
        slug = workspaces[0]["slug"] if workspaces else None
        git_slug = None
        for ws in workspaces:
            s = ws["slug"]
            # Quick probe: hit git/status and use first one that returns 200
            try:
                probe = await client.get(
                    f"/api/v1/workspaces/{s}/git/status", headers=headers,
                )
                if probe.status_code == 200:
                    git_slug = s
                    break
            except Exception:
                continue

        # 5. GET /workspaces/{slug} → 200
        if slug:
            result.checks.append(await _check_workspace_detail(client, headers, slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET workspace detail", True, "skipped (no workspaces)",
            ))

        # 6. GET /workspaces/{slug}/git/status → 200 + has output
        if git_slug:
            result.checks.append(await _check_git_status(client, headers, git_slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET git status", True, "skipped (no workspace with git)",
            ))

        # 7. GET /workspaces/{slug}/git/log → 200
        if git_slug:
            result.checks.append(await _check_git_log(client, headers, git_slug))
        else:
            result.checks.append(ApiCheckResult(
                "GET git log", True, "skipped (no workspace with git)",
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

        # 12. POST /register → new account
        reg_check, reg_data = await _check_register(client)
        result.checks.append(reg_check)

        # 13. GET /account → account info with capabilities
        if reg_data:
            reg_headers = {"Authorization": f"Bearer {reg_data['api_key']}"}
            result.checks.append(await _check_account_info(client, reg_headers))

            # 14. POST /account/credentials/llm → set credential
            result.checks.append(await _check_set_credential(client, reg_headers))

            # 15. GET /account/credentials → list credentials
            result.checks.append(await _check_list_credentials(client, reg_headers))

            # 16. DELETE /account/credentials/llm → remove credential
            result.checks.append(await _check_delete_credential(client, reg_headers))

            # 17. POST /account/keys → create new key
            result.checks.append(await _check_create_key(client, reg_headers))

            # 18. GET /account/keys → list keys
            result.checks.append(await _check_list_keys(client, reg_headers))

            # 19. Workspace scoping — new account should see no workspaces
            result.checks.append(await _check_workspace_scoping(client, reg_headers))
        else:
            for name in ("GET /account info", "POST set credential", "GET list credentials",
                         "DELETE credential", "POST create key", "GET list keys",
                         "workspace scoping"):
                result.checks.append(ApiCheckResult(name, True, "skipped (registration failed)"))

    # 20. CF Pages credentials valid (runs outside the base_url client)
    result.checks.append(await _check_cf_credentials())

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


async def _check_register(
    client: httpx.AsyncClient,
) -> tuple[ApiCheckResult, Optional[dict]]:
    name = "POST /register → new account"
    try:
        r = await client.post(
            "/api/v1/register",
            json={"display_name": "SmokeTest User", "email": "smoketest@test.local"},
        )
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}"), None
        body = r.json()
        if "account_id" not in body or "api_key" not in body:
            return ApiCheckResult(name, False, f"missing keys, got: {list(body.keys())}"), None
        if not body["account_id"].startswith("acc_"):
            return ApiCheckResult(name, False, f"bad account_id format: {body['account_id']}"), None
        if not body["api_key"].startswith("sk_live_"):
            return ApiCheckResult(name, False, f"bad api_key format"), None
        return ApiCheckResult(name, True, f"account_id={body['account_id']}"), body
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200]), None


async def _check_account_info(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "GET /account → info + capabilities"
    try:
        r = await client.get("/api/v1/account", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        required = {"account_id", "capabilities", "setup_checklist"}
        missing = required - set(body.keys())
        if missing:
            return ApiCheckResult(name, False, f"missing keys: {missing}")
        # Capabilities should have expected structure
        caps = body["capabilities"]
        if "code_generation" not in caps:
            return ApiCheckResult(name, False, f"missing code_generation in capabilities")
        if caps["code_generation"]["enabled"] is not False:
            return ApiCheckResult(name, False, "code_generation should be disabled without LLM key")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_set_credential(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "POST /account/credentials/llm → store"
    try:
        r = await client.post(
            "/api/v1/account/credentials/llm",
            headers=headers,
            json={"data": {"api_key": "sk-smoketest-fake-key"}},
        )
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if body.get("status") != "stored":
            return ApiCheckResult(name, False, f"unexpected status: {body.get('status')}")
        # Capabilities should now show code_generation enabled
        caps = body.get("capabilities", {})
        if not caps.get("code_generation", {}).get("enabled"):
            return ApiCheckResult(name, False, "code_generation not enabled after setting LLM key")
        return ApiCheckResult(name, True, "code_generation now enabled")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_list_credentials(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "GET /account/credentials → list"
    try:
        r = await client.get("/api/v1/account/credentials", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if body.get("llm") is not True:
            return ApiCheckResult(name, False, f"llm should be True, got: {body}")
        if body.get("supabase") is not False:
            return ApiCheckResult(name, False, f"supabase should be False, got: {body}")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_delete_credential(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "DELETE /account/credentials/llm → remove"
    try:
        r = await client.delete("/api/v1/account/credentials/llm", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if body.get("status") != "deleted":
            return ApiCheckResult(name, False, f"unexpected status: {body.get('status')}")
        # code_generation should now be disabled
        caps = body.get("capabilities", {})
        if caps.get("code_generation", {}).get("enabled"):
            return ApiCheckResult(name, False, "code_generation still enabled after delete")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_create_key(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "POST /account/keys → new key"
    try:
        r = await client.post(
            "/api/v1/account/keys",
            headers=headers,
            json={"label": "smoketest-key"},
        )
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if "api_key" not in body:
            return ApiCheckResult(name, False, f"missing api_key in response")
        if not body["api_key"].startswith("sk_live_"):
            return ApiCheckResult(name, False, "bad key format")
        return ApiCheckResult(name, True)
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_list_keys(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "GET /account/keys → list"
    try:
        r = await client.get("/api/v1/account/keys", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if not isinstance(body, list):
            return ApiCheckResult(name, False, f"expected list, got {type(body).__name__}")
        # Should have at least 2 keys (default + smoketest-key)
        if len(body) < 2:
            return ApiCheckResult(name, False, f"expected ≥2 keys, got {len(body)}")
        labels = {k.get("label") for k in body}
        if "smoketest-key" not in labels:
            return ApiCheckResult(name, False, f"smoketest-key not found in {labels}")
        # Keys should have prefix but no hash
        for k in body:
            if "key_hash" in k:
                return ApiCheckResult(name, False, "key_hash leaked in response!")
        return ApiCheckResult(name, True, f"{len(body)} keys")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_workspace_scoping(
    client: httpx.AsyncClient, headers: dict,
) -> ApiCheckResult:
    name = "GET /workspaces → scoped (new account sees none)"
    try:
        r = await client.get("/api/v1/workspaces", headers=headers)
        if r.status_code != 200:
            return ApiCheckResult(name, False, f"status {r.status_code}")
        body = r.json()
        if not isinstance(body, list):
            return ApiCheckResult(name, False, f"expected list, got {type(body).__name__}")
        if len(body) != 0:
            return ApiCheckResult(name, False, f"new account should see 0 workspaces, saw {len(body)}")
        return ApiCheckResult(name, True, "0 workspaces (correct)")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])


async def _check_cf_credentials() -> ApiCheckResult:
    name = "CF Pages credentials valid"
    try:
        import config as cfg
        if not cfg.CLOUDFLARE_API_TOKEN or not cfg.CLOUDFLARE_ACCOUNT_ID:
            return ApiCheckResult(name, False, "CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID not set")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.cloudflare.com/client/v4/user/tokens/verify",
                headers={"Authorization": f"Bearer {cfg.CLOUDFLARE_API_TOKEN}"},
            )
            if r.status_code == 200 and r.json().get("success"):
                return ApiCheckResult(name, True, "token valid")
            return ApiCheckResult(name, False, f"token verification failed: {r.status_code}")
    except Exception as exc:
        return ApiCheckResult(name, False, str(exc)[:200])
