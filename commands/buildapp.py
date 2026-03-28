"""
commands/buildapp.py — One-message "idea to running app" for KMP.

/buildapp <description>
  → scaffold KMP project → Claude builds features → auto-fix → demo all platforms
"""

import re
import time
from typing import Callable, Awaitable, Optional

import config
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary
from commands.create import create_kmp_project
from platforms import AndroidPlatform, iOSPlatform, WebPlatform
from supabase_client import run_sql, extract_sql
from helpers.schema_manager import schema_name_for_workspace, ensure_schema, set_search_path_sql
import glob
import os


def _patch_supabase_credentials(ws_path: str) -> int:
    """Replace placeholder Supabase credentials in generated source files.
    Returns the number of files patched."""
    if not config.SUPABASE_PROJECT_REF or not config.SUPABASE_ANON_KEY:
        return 0
    real_url = f"https://{config.SUPABASE_PROJECT_REF}.supabase.co"
    placeholders = {
        "https://YOUR_PROJECT.supabase.co": real_url,
        "YOUR_PROJECT.supabase.co": f"{config.SUPABASE_PROJECT_REF}.supabase.co",
        "YOUR_ANON_KEY": config.SUPABASE_ANON_KEY,
        "your-project-ref.supabase.co": f"{config.SUPABASE_PROJECT_REF}.supabase.co",
        "https://your-project-ref.supabase.co": real_url,
    }
    patched = 0
    for ext in ("*.kt", "*.swift", "*.ts", "*.js"):
        for filepath in glob.glob(os.path.join(ws_path, "**", ext), recursive=True):
            try:
                content = open(filepath).read()
                original = content
                for placeholder, real in placeholders.items():
                    content = content.replace(placeholder, real)
                if content != original:
                    open(filepath, "w").write(content)
                    patched += 1
            except Exception:
                pass
    return patched


def infer_app_name(description: str) -> str:
    fillers = {"a", "an", "the", "with", "and", "for", "that", "this", "my",
               "app", "application", "make", "create", "build"}
    words = description.split()
    meaningful = [w for w in words if w.lower() not in fillers and len(w) > 2]
    name_words = meaningful[:3] if len(meaningful) >= 2 else words[:2]
    return "".join(w.capitalize() for w in name_words if w.isalpha()) or "MyApp"


SCHEMA_PROMPT = """You are a database architect. Given the app description below,
generate a PostgreSQL schema for Supabase.

App name: {app_name}
Description: {description}
{data_section}
{schema_section}
Rules:
- Output ONLY a single SQL block (```sql ... ```) — no explanation.
- {search_path_instruction}
- Use CREATE TABLE IF NOT EXISTS.
- Use "camelCase" quoted column names so Kotlin @Serializable data classes work
  without @SerialName (e.g. "createdAt" timestamptz).
- Every table gets: id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  "createdAt" timestamptz DEFAULT now().
- Include an app_meta table with columns: id (int default 1, PK, CHECK id=1),
  version text, "lastUpdated" timestamptz DEFAULT now(), "joinCode" text, "organizerCode" text.
- Add a get_app_config() RPC that returns all rows from every table as a single
  JSON object via json_build_object + (SELECT json_agg(...) FROM <table>).
- Add permissive RLS: ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
  CREATE POLICY "public_access" ON <t> FOR ALL USING (true) WITH CHECK (true);
  for every table.
- ALL primary keys must be uuid. ALL foreign keys must also be uuid and reference
  the uuid primary key. Never mix types (e.g. text FK → uuid PK).
- Keep it minimal — only tables the app clearly needs.
"""


def build_feature_prompt(
    app_name: str, description: str, schema_sql: Optional[str] = None,
    db_schema: Optional[str] = None,
) -> str:
    base = f"""Build a complete Kotlin Multiplatform app called "{app_name}".

Description: {description}

This is a Compose Multiplatform project. Write ALL shared UI code in
composeApp/src/commonMain/ using Compose Multiplatform APIs.

Requirements:
- Material 3 components and theming
- Clean, polished UI that looks great on first launch
- All shared logic and UI in commonMain
- Platform-specific code only where absolutely necessary (use expect/actual)
- Make sure ALL imports exist and ALL dependencies are in build.gradle.kts
- Verify the code compiles for Android target first
- IMPORTANT: Do NOT use emoji characters (Unicode emoji) in the UI. They render as broken boxes on the Web (WASM) target. Use Material Icons from `androidx.compose.material.icons.Icons` instead.

Write complete, working code. No TODOs or placeholders."""

    if schema_sql and config.SUPABASE_ANON_KEY:
        supabase_url = f"https://{config.SUPABASE_PROJECT_REF}.supabase.co"
        schema_note = ""
        if db_schema:
            schema_note = (
                f"\n- **Database schema:** `{db_schema}` (all tables live here, NOT in public)\n"
                f"- When running SQL, always start with: `SET search_path TO {db_schema}, public;`\n"
            )
        base += f"""

## Supabase Backend

The database has been provisioned. Connect to it using these details:

- Supabase URL: {supabase_url}
- Anon key: {config.SUPABASE_ANON_KEY}
{schema_note}
IMPORTANT: Use these EXACT values above in your code. Do NOT use placeholders
like "YOUR_PROJECT" or "YOUR_ANON_KEY". The real credentials are provided above.

Schema that was created:
```sql
{schema_sql}
```

Instructions for the backend integration:
- Add Ktor client + kotlinx.serialization dependencies to commonMain build.gradle.kts
  (io.ktor:ktor-client-core, io.ktor:ktor-client-content-negotiation,
   io.ktor:ktor-serialization-kotlinx-json, and platform engines:
   io.ktor:ktor-client-okhttp for Android, io.ktor:ktor-client-darwin for iOS,
   io.ktor:ktor-client-js for wasmJs/js).
- Create a ConfigRepository in commonMain that:
  1. Calls GET {supabase_url}/rest/v1/rpc/get_app_config with headers:
     apikey: <anon_key>, Authorization: Bearer <anon_key>
  2. Parses the JSON response into @Serializable data classes
  3. Falls back to hardcoded demo data if the network call fails
- The UI should load data from ConfigRepository on launch.
- Use "camelCase" property names in data classes — they match the DB column names exactly.
"""

    return base


async def handle_buildapp(
    description: str,
    registry: WorkspaceRegistry,
    claude: ClaudeRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    on_ask: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    is_admin: bool = True,
    owner_id: Optional[int] = None,
    app_name: Optional[str] = None,
) -> Optional[str]:
    if not description:
        await on_status("Usage: `/buildapp <description of the app>`", None)
        return None

    start_time = time.time()
    if not app_name:
        app_name = infer_app_name(description)

    # 1. Scaffold
    await on_status(f"🏗️ Creating **{app_name}** (Kotlin Multiplatform)...", None)
    await on_status("💡 *I'm still listening — feel free to send other commands while this runs.*", None)
    scaffold_result = await create_kmp_project(app_name, registry, owner_id=owner_id)
    await on_status(scaffold_result.message, None)

    if not scaffold_result.success:
        return None

    slug = scaffold_result.slug
    app_name = slug  # use actual name (may have been incremented)
    ws_path = registry.get_path(slug)
    if not ws_path:
        await on_status(f"❌ Could not find workspace `{slug}`.", None)
        return None

    # 1.5a Create per-app Postgres schema for isolation
    app_schema = None
    if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
        app_schema = schema_name_for_workspace(slug)
        await on_status(f"🗄️ Creating schema `{app_schema}` for isolation...", None)
        ok, err = await ensure_schema(app_schema)
        if ok:
            registry.set_schema(slug, app_schema)
        else:
            await on_status(f"⚠️ Schema creation failed: {err[:200]}. Using public.", None)
            app_schema = None

    # 1.5b Data-modeling interview (only when Supabase is configured)
    data_description: Optional[str] = None
    if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY and on_ask:
        question = (
            "**What data does your app need to store?**\n"
            "Describe what users create or manage. For example:\n"
            "*Each recipe has a title, ingredients list, cook time, and photo.*\n\n"
            "This helps me design a better database. Or hit **Skip** to let me figure it out from the description."
        )
        data_description = await on_ask(question)
        if data_description:
            await on_status("Got it — designing the database around your data description.", None)

    # 2. Supabase schema (if configured)
    schema_sql = None
    if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
        await on_status("🗄️ Designing database schema...", None)
        data_section = ""
        if data_description:
            data_section = (
                f"User's data requirements:\n{data_description}\n"
                "Use these as the primary guide for table and column design."
            )
        if app_schema:
            schema_section = f"This app uses Postgres schema: {app_schema}. All tables must be created in this schema."
            search_path_instruction = f"Start the SQL with: SET search_path TO {app_schema}, public;"
        else:
            schema_section = ""
            search_path_instruction = "Tables go in the public schema (default)"
        schema_prompt = SCHEMA_PROMPT.format(
            description=description, app_name=app_name, data_section=data_section,
            schema_section=schema_section, search_path_instruction=search_path_instruction,
        )
        schema_result = await claude.run(schema_prompt, slug, ws_path)
        schema_sql = extract_sql(schema_result.stdout)

        if schema_sql:
            await on_status("🗄️ Creating Supabase tables...", None)
            from supabase_client import patch_idempotent
            schema_sql = patch_idempotent(schema_sql)
            ok, err = await run_sql(schema_sql, schema=app_schema)
            if ok:
                await on_status("✅ Database ready.", None)
            else:
                await on_status(
                    f"⚠️ DB setup failed: {err[:200]}. Continuing without backend.", None
                )
                schema_sql = None
        else:
            await on_status("⚠️ Could not extract SQL from schema response. Continuing without backend.", None)

    # 3. Claude builds features + auto-fix for Android first
    feature_prompt = build_feature_prompt(app_name, description, schema_sql=schema_sql, db_schema=app_schema)

    async def loop_status(msg):
        await on_status(msg, None)

    loop_result = await run_agent_loop(
        initial_prompt=feature_prompt,
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="android",
        on_status=loop_status,
    )

    summary = format_loop_summary(loop_result)
    await on_status(summary, None)

    # Patch any placeholder Supabase credentials Claude may have left
    if schema_sql:
        patched = _patch_supabase_credentials(ws_path)
        if patched:
            await on_status(f"🔑 Injected Supabase credentials into {patched} file(s).", None)

    if not loop_result.success:
        await on_status(
            f"Android build didn't succeed. Try `@{slug} <fix instructions>`.",
            None,
        )
        return slug

    # 4. Web build + auto-fix (so anyone can try it in browser — show first)
    await on_status("🌐 **Web** — building and fixing browser version...", None)
    web_loop = await run_agent_loop(
        initial_prompt=(
            "The Android target compiles. Now ensure the wasmJs web target "
            "also compiles. Fix any web-specific issues. "
            "Only modify what's necessary for web compatibility."
        ),
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="web",
        on_status=loop_status,
    )
    web_summary = format_loop_summary(web_loop)
    await on_status(web_summary, None)

    web_demo_url = None
    if web_loop.success:
        url = await WebPlatform.serve(ws_path, workspace_key=slug)
        if url:
            web_demo_url = url
            await on_status(
                f"✅ Web version live → {url}\n"
                f"Anyone can try it in their browser!",
                None,
            )
        else:
            await on_status("✅ Web builds but couldn't start server.", None)
    else:
        await on_status(
            f"⚠️ Web build had issues (Android version works fine).\n"
            f"Use `@{slug} Fix the wasmJs web target` to resolve.",
            None,
        )

    # 5. Android demo (admin only — uses emulator)
    if is_admin:
        await on_status("📱 **Android** — launching demo...", None)
        android_demo = await AndroidPlatform.full_demo(ws_path)
        await on_status(android_demo.message, android_demo.screenshot_path)

    # 6. iOS build + auto-fix (admin only — uses simulator)
    ios_loop = None
    ios_demo = None
    if is_admin:
        await on_status("🍎 **iOS** — building and fixing simulator version...", None)
        ios_loop = await run_agent_loop(
            initial_prompt=(
                "The Android target compiles. Now ensure the iOS target "
                "also compiles. Fix any iOS-specific issues. "
                "Only modify what's necessary for iOS compatibility. "
                f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'"
            ),
            workspace_key=slug,
            workspace_path=ws_path,
            claude=claude,
            platform="ios",
            on_status=loop_status,
        )
        ios_loop_summary = format_loop_summary(ios_loop)
        await on_status(ios_loop_summary, None)

        if ios_loop.success:
            await on_status("📱 Launching iOS demo...", None)
            ios_demo = await iOSPlatform.full_demo(ws_path)
            if ios_demo.success:
                await on_status(ios_demo.message, ios_demo.screenshot_path)
            else:
                await on_status("✅ iOS builds but demo failed.", None)
        else:
            await on_status(
                f"⚠️ iOS build had issues. Use `@{slug} Fix the iOS target` to resolve.",
                None,
            )

    # 7. Final summary
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    build_attempts = loop_result.total_attempts + web_loop.total_attempts
    if ios_loop:
        build_attempts += ios_loop.total_attempts

    platform_status = []
    if is_admin:
        platform_status.append(f"  📱 Android: {'✅' if loop_result.success else '❌'}")
    platform_status.append(f"  🌐 Web: {'✅ ' + (web_demo_url or '') if web_loop.success else '❌'}")
    if is_admin:
        ios_ok = ios_demo.success if ios_demo else False
        platform_status.append(f"  🍎 iOS: {'✅' if ios_ok else '❌'}")

    commands_hint = (
        f"  `@{slug} <prompt>` — add features\n"
        f"  `/demo web` — see it running\n"
        f"  `/build web` — rebuild\n"
        f"  `/fix` — auto-fix build errors"
    )
    if is_admin:
        commands_hint = (
            f"  `@{slug} <prompt>` — add features\n"
            f"  `/demo android|ios|web` — see it running\n"
            f"  `/build android|ios|web` — rebuild a target\n"
            f"  `/fix` — auto-fix build errors"
        )

    await on_status(
        f"🎉 **{app_name}** built!\n\n"
        f"  ⏱️ Total: {mins}m {secs}s\n"
        f"  🔨 Build attempts: {build_attempts}\n\n"
        + "\n".join(platform_status) + "\n\n"
        f"Commands:\n" + commands_hint,
        None,
    )

    return slug
