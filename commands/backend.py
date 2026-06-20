"""
commands/backend.py — One-tap backend provisioning for KMP workspaces.

/add-backend <provider>
  → Claude adds backend SDK, auth, data persistence, and platform config
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from agent_protocol import AgentRunner
from agent_loop import run_agent_loop, format_loop_summary


# ── Backend definitions ──────────────────────────────────────────────────────

@dataclass
class Backend:
    key: str
    label: str
    emoji: str
    description: str
    claude_prompt: str


BACKENDS: dict[str, Backend] = {
    "firebase": Backend(
        key="firebase",
        label="Firebase (Firestore + Auth)",
        emoji="🔥",
        description="Add Firestore data persistence and Firebase Auth (email/password, Google Sign-In) to your KMP app.",
        claude_prompt="""\
Add Firebase backend (Firestore + Authentication) to this KMP app.

Requirements:

## 1. Firebase SDK — shared module
- Add the Firebase Kotlin SDK (`dev.gitlive:firebase-firestore` and `dev.gitlive:firebase-auth`) to `libs.versions.toml` and the shared module's `commonMain` dependencies.
- Create a `backend/` package under `commonMain`.

## 2. Firestore data persistence
- Add a `FirestoreRepository` object in `backend/` that provides:
  - `suspend fun save(collection: String, id: String, data: Map<String, Any>)`
  - `suspend fun get(collection: String, id: String): Map<String, Any>?`
  - `suspend fun query(collection: String): List<Map<String, Any>>`
  - `suspend fun delete(collection: String, id: String)`
- Wire it into the app's existing data model — if the app stores items, save/load them from Firestore instead of in-memory.

## 3. Firebase Auth
- Add an `AuthManager` object in `backend/` that provides:
  - `signUp(email: String, password: String)`
  - `signIn(email: String, password: String)`
  - `signOut()`
  - `currentUser()` returning nullable user info
  - `StateFlow<Boolean>` for `isLoggedIn`
- Add a **LoginScreen** composable with email/password fields, sign-in/sign-up buttons, and error display.
- Gate the main app behind auth — show LoginScreen when not logged in.
- Add a sign-out button in the app's top bar or settings.

## 4. Platform config files
- **Android:** Create a placeholder `composeApp/google-services.json` with TODO comments explaining what values to fill in from the Firebase Console. Add the `google-services` Gradle plugin to the Android build.
- **iOS:** Create a placeholder `iosApp/GoogleService-Info.plist` with TODO comments for Firebase Console values.
- **Web/WASM:** Add Firebase JS SDK initialization in a `firebase-config.kt` file with placeholder project config.

## 5. Keep it building
- After making all changes, run a compile check to make sure the project builds.
- Add clear TODO comments for any manual setup steps (Firebase Console project creation, downloading real config files).
""",
    ),
    "supabase": Backend(
        key="supabase",
        label="Supabase (Database + Auth)",
        emoji="⚡",
        description="Add Supabase Postgres database and Auth (email/password, OAuth) to your KMP app.",
        claude_prompt="""\
Add Supabase backend (Postgres database + Auth) to this KMP app.

Requirements:

## 1. Supabase SDK — shared module
- Add the Supabase Kotlin SDK (`io.github.jan-tennert.supabase:postgrest-kt`, `io.github.jan-tennert.supabase:auth-kt`, `io.github.jan-tennert.supabase:supabase-kt`) to `libs.versions.toml` and the shared module's `commonMain` dependencies.
- Also add Ktor client engine dependencies for each platform.
- Create a `backend/` package under `commonMain`.

## 2. Supabase client setup
- Add a `SupabaseProvider` object in `backend/` that initializes the Supabase client with:
  - Placeholder `SUPABASE_URL` and `SUPABASE_ANON_KEY` constants in a `Config.kt` file.
  - PostgREST and Auth plugins installed.

## 3. Database (PostgREST)
- Add a `DatabaseRepository` object in `backend/` that provides:
  - `suspend fun insert(table: String, data: Map<String, Any>)`
  - `suspend fun select(table: String): List<Map<String, Any>>`
  - `suspend fun update(table: String, id: String, data: Map<String, Any>)`
  - `suspend fun delete(table: String, id: String)`
- Wire it into the app's existing data model — if the app stores items, save/load them via Supabase instead of in-memory.

## 4. Supabase Auth
- Add an `AuthManager` object in `backend/` that provides:
  - `signUp(email: String, password: String)`
  - `signIn(email: String, password: String)`
  - `signOut()`
  - `currentUser()` returning nullable user info
  - `StateFlow<Boolean>` for `isLoggedIn`
- Add a **LoginScreen** composable with email/password fields, sign-in/sign-up buttons, and error display.
- Gate the main app behind auth — show LoginScreen when not logged in.
- Add a sign-out button in the app's top bar or settings.

## 5. Platform config
- All config is via the shared `Config.kt` constants — no platform-specific config files needed.
- Add clear TODO comments for Supabase project setup (create project at supabase.com, copy URL and anon key).

## 6. Keep it building
- After making all changes, run a compile check to make sure the project builds.
""",
    ),
}


# ── Persistence keywords for auto-detection ──────────────────────────────────

PERSISTENCE_KEYWORDS = {
    "save user", "save data", "persist", "database", "login", "sign in",
    "sign up", "signup", "signin", "authentication", "user account",
    "user preferences", "save preferences", "remember me", "store data",
    "cloud storage", "sync data", "backend", "firestore", "firebase",
    "supabase", "user profile", "add login", "add auth",
}


def detect_persistence_need(prompt: str) -> bool:
    """Return True if the user's prompt implies they need a backend."""
    lower = prompt.lower()
    return any(kw in lower for kw in PERSISTENCE_KEYWORDS)


# ── Public helpers ───────────────────────────────────────────────────────────

def list_backends() -> list[Backend]:
    """Return all available backends in display order."""
    return list(BACKENDS.values())


def get_backend(key: str) -> Optional[Backend]:
    """Look up a backend by key (case-insensitive)."""
    return BACKENDS.get(key.lower().strip())


# ── Run backend provisioning ─────────────────────────────────────────────────

async def run_backend(
    backend: Backend,
    workspace_key: str,
    workspace_path: str,
    claude: AgentRunner,
    on_status: Callable[[str], Awaitable[None]],
    platform: str = "android",
) -> bool:
    """Run the agent loop to provision a backend in the workspace.

    Returns True on success.
    """
    prompt = (
        f"BACKEND PROVISIONING: {backend.label}\n\n"
        f"{backend.claude_prompt}\n\n"
        "After making all code changes, do a quick compile check "
        "to make sure nothing is broken."
    )

    result = await run_agent_loop(
        initial_prompt=prompt,
        workspace_key=workspace_key,
        workspace_path=workspace_path,
        claude=claude,
        platform=platform,
        on_status=on_status,
    )

    summary = format_loop_summary(result)
    await on_status(summary)
    return result.success
