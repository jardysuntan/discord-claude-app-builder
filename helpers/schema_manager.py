"""
helpers/schema_manager.py — Per-app Postgres schema isolation within a single Supabase project.

Each app gets its own schema so tables, functions, and policies don't collide.
"""

import re

import aiohttp

import config
from supabase_client import run_sql, query_sql


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
    """Create the schema if it doesn't exist, grant usage, and expose via PostgREST.

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
    ok, err = await run_sql(sql)
    if not ok:
        return ok, err

    # Expose schema in PostgREST so the REST API can reach it
    expose_ok, expose_err = await _expose_schema(schema_name)
    if not expose_ok:
        # Non-fatal: schema exists but REST API won't see it until manually exposed
        return True, f"Schema created but PostgREST exposure failed: {expose_err}"
    return True, ""


async def _expose_schema(schema_name: str) -> tuple[bool, str]:
    """Add schema to PostgREST's db_extra_search_path if not already present."""
    if not config.SUPABASE_PROJECT_REF or not config.SUPABASE_MANAGEMENT_KEY:
        return False, "SUPABASE_PROJECT_REF or SUPABASE_MANAGEMENT_KEY not set"

    base = f"https://api.supabase.com/v1/projects/{config.SUPABASE_PROJECT_REF}"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_MANAGEMENT_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            # GET current PostgREST config
            async with session.get(f"{base}/postgrest", headers=headers) as resp:
                if resp.status != 200:
                    return False, f"GET config failed: HTTP {resp.status}"
                cfg = await resp.json()

            # Check both db_schema (exposed) and db_extra_search_path
            db_schema = cfg.get("db_schema", "public")
            extra_path = cfg.get("db_extra_search_path", "public")
            db_schemas = [s.strip() for s in db_schema.split(",") if s.strip()]
            extra_schemas = [s.strip() for s in extra_path.split(",") if s.strip()]

            if schema_name in db_schemas and schema_name in extra_schemas:
                return True, ""  # Already exposed

            if schema_name not in db_schemas:
                db_schemas.append(schema_name)
            if schema_name not in extra_schemas:
                extra_schemas.append(schema_name)

            # PATCH to expose the schema
            async with session.patch(
                f"{base}/postgrest",
                headers=headers,
                json={
                    "db_schema": ", ".join(db_schemas),
                    "db_extra_search_path": ", ".join(extra_schemas),
                },
            ) as resp:
                if resp.status in (200, 201, 204):
                    return True, ""
                text = await resp.text()
                return False, f"PATCH config failed: HTTP {resp.status} {text[:200]}"
    except Exception as e:
        return False, str(e)


def set_search_path_sql(schema_name: str) -> str:
    """Return SQL to SET search_path for a session, always including public."""
    return f"SET search_path TO {schema_name}, public;"


async def list_schemas() -> list[str]:
    """Discover all app_* schemas in the database."""
    sql = "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'app_%' ORDER BY schema_name;"
    ok, data = await query_sql(sql)
    if not ok or not data:
        return []
    # query_sql returns list of result sets; rows are in the first (or only) set
    rows = data[0] if isinstance(data[0], list) else data
    return [r["schema_name"] for r in rows if isinstance(r, dict) and "schema_name" in r]


async def get_all_app_meta() -> list[dict]:
    """Gather app_meta from every discovered app schema.

    Returns a list of dicts, each with 'schema_name' plus all app_meta columns.
    Schemas without an app_meta table are silently skipped.
    """
    schemas = await list_schemas()
    results = []
    for s in schemas:
        sql = f"SELECT * FROM {s}.app_meta LIMIT 1;"
        ok, data = await query_sql(sql)
        if not ok or not data:
            continue
        rows = data[0] if isinstance(data[0], list) else data
        if rows and isinstance(rows[0], dict):
            row = dict(rows[0])
            row["schema_name"] = s
            results.append(row)
    return results


async def ensure_dashboard_function() -> tuple[bool, str]:
    """Create or replace the public.get_all_apps() PL/pgSQL function.

    This lets any client (including the dashboard Kotlin app) call
    GET /rest/v1/rpc/get_all_apps to discover all apps and their metadata.
    """
    sql = """
CREATE OR REPLACE FUNCTION public.get_all_apps()
RETURNS json LANGUAGE plpgsql STABLE AS $$
DECLARE
  result json;
  schemas text[];
  s text;
  app_row json;
  apps json[] := '{}';
BEGIN
  SELECT array_agg(schema_name) INTO schemas
  FROM information_schema.schemata
  WHERE schema_name LIKE 'app_%';

  IF schemas IS NOT NULL THEN
    FOREACH s IN ARRAY schemas LOOP
      BEGIN
        EXECUTE format(
          'SELECT row_to_json(t) FROM (SELECT %L as "schemaName", * FROM %I.app_meta LIMIT 1) t', s, s
        ) INTO app_row;
        IF app_row IS NOT NULL AND (app_row->>'app_name') IS NOT NULL THEN
          apps := array_append(apps, app_row);
        END IF;
      EXCEPTION WHEN undefined_table THEN
        NULL;  -- skip schemas without app_meta
      END;
    END LOOP;
  END IF;

  RETURN json_build_object('apps', to_json(apps));
END; $$;
"""
    return await run_sql(sql)
