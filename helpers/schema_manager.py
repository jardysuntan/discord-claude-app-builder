"""
helpers/schema_manager.py — Per-app Postgres schema isolation within a single Supabase project.

Each app gets its own schema so tables, functions, and policies don't collide.
"""

import re

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
