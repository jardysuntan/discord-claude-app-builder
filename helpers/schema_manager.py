"""
helpers/schema_manager.py — Per-app Postgres schema isolation within a single Supabase project.

Each app gets its own schema so tables, functions, and policies don't collide.
"""

import re

from supabase_client import run_sql


def schema_name_for_workspace(ws_key: str) -> str:
    """Convert a workspace key to a valid Postgres schema name.

    Rules: lowercase, replace hyphens with underscores, strip non-alphanumeric,
    prefix with 'app_' to avoid collisions with built-in schemas.
    """
    name = ws_key.lower().replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "", name)
    # Ensure it doesn't start with a digit
    if name and name[0].isdigit():
        name = f"_{name}"
    return f"app_{name}"


async def ensure_schema(schema_name: str) -> tuple[bool, str]:
    """Create the schema if it doesn't exist and grant usage to Supabase roles.

    Returns (success, error_message_or_empty).
    """
    sql = f"""
CREATE SCHEMA IF NOT EXISTS {schema_name};
GRANT USAGE ON SCHEMA {schema_name} TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA {schema_name} TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name}
    GRANT ALL ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name}
    GRANT ALL ON SEQUENCES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name}
    GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;
"""
    return await run_sql(sql)


def set_search_path_sql(schema_name: str) -> str:
    """Return SQL to SET search_path for a session, always including public."""
    return f"SET search_path TO {schema_name}, public;"
