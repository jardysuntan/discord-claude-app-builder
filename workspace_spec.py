"""
workspace_spec.py — structured per-workspace product context.

Specs are lightweight JSON artifacts that capture the product intent outside of
ephemeral model sessions, which makes the build pipeline more portable across
LLM providers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


SPEC_DIR = ".bridge"
SPEC_FILE = "workspace_spec.json"


def spec_path(workspace_path: str) -> Path:
    return Path(workspace_path) / SPEC_DIR / SPEC_FILE


def load_workspace_spec(workspace_path: str) -> Optional[dict]:
    path = spec_path(workspace_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def save_workspace_spec(workspace_path: str, spec: dict) -> Path:
    path = spec_path(workspace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**spec, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def build_workspace_spec(
    *,
    app_name: str,
    description: str,
    plan: dict | None = None,
    schema_sql: str | None = None,
    db_schema: str | None = None,
    provider: str | None = None,
) -> dict:
    spec = {
        "app_name": app_name,
        "description": description,
        "provider": provider or "claude",
    }
    if plan:
        spec["plan"] = plan
    if schema_sql:
        spec["schema_sql"] = schema_sql
    if db_schema:
        spec["db_schema"] = db_schema
    return spec


def format_spec_context(spec: dict) -> str:
    parts = [
        "WORKSPACE SPEC",
        f"App name: {spec.get('app_name', 'Unknown')}",
        f"Description: {spec.get('description', '')}",
    ]

    plan = spec.get("plan") or {}
    summary = plan.get("summary")
    if summary:
        parts.append(f"Plan summary: {summary}")

    screens = plan.get("screens") or []
    if screens:
        parts.append("Screens:")
        for screen in screens[:6]:
            name = screen.get("name", "Screen")
            description = screen.get("description", "")
            parts.append(f"- {name}: {description}")

    features = plan.get("features") or []
    if features:
        parts.append("Features:")
        for feature in features[:8]:
            parts.append(f"- {feature}")

    if spec.get("db_schema"):
        parts.append(f"Database schema: {spec['db_schema']}")

    if spec.get("schema_sql"):
        parts.append("Schema SQL:")
        parts.append("```sql")
        parts.append(spec["schema_sql"][:4000])
        parts.append("```")

    return "\n".join(parts)
