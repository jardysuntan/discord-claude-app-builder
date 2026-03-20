"""
commands/integrate.py — Quick-add pre-configured integrations to a workspace.

/integrate <name>
  → inject integration template + deps into the active workspace
  → pass context to Claude so it wires the integration into the app
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary


# ── Integration definitions ───────────────────────────────────────────────────

@dataclass
class Integration:
    key: str
    label: str
    emoji: str
    description: str
    dependencies: list[str]
    claude_prompt: str


INTEGRATIONS: dict[str, Integration] = {
    "stripe": Integration(
        key="stripe",
        label="Stripe Payments",
        emoji="💳",
        description="Accept payments with Stripe Checkout — products, prices, and a checkout flow.",
        dependencies=[
            "io.ktor:ktor-client-core:3.1.1",
            "io.ktor:ktor-client-content-negotiation:3.1.1",
            "io.ktor:ktor-serialization-kotlinx-json:3.1.1",
        ],
        claude_prompt="""\
Add Stripe payment integration to this KMP app.

Requirements:
1. Create a `payments/` package under the shared `commonMain` source set.
2. Add a `StripeClient` object that calls the Stripe REST API via Ktor:
   - `createCheckoutSession(priceId: String, successUrl: String, cancelUrl: String)` → returns a session URL.
   - The Stripe secret key should be read from a `STRIPE_SECRET_KEY` constant in a `Config.kt` file (placeholder value).
3. Add a simple **PaymentScreen** composable that:
   - Shows a list of 2-3 placeholder products with prices.
   - Has a "Buy" button that opens the Stripe checkout URL in the platform browser.
4. Add navigation to PaymentScreen from the main screen (e.g. a "Shop" or "Upgrade" button).
5. Add the required Ktor dependencies to `libs.versions.toml` if they aren't already present.

Keep it minimal — no webhooks, no server component. Just client-side checkout via Stripe's API.
""",
    ),
    "supabase-auth": Integration(
        key="supabase-auth",
        label="Supabase Auth",
        emoji="🔐",
        description="Email/password and OAuth sign-in with Supabase Auth.",
        dependencies=[],  # already in template
        claude_prompt="""\
Add Supabase Auth (email + password) to this KMP app.

Requirements:
1. Create an `auth/` package under the shared `commonMain` source set.
2. Add an `AuthManager` object using the existing Supabase client that provides:
   - `signUp(email: String, password: String)`
   - `signIn(email: String, password: String)`
   - `signOut()`
   - `currentUser()` returning a nullable user object
   - A `StateFlow<Boolean>` for `isLoggedIn`
3. Add a **LoginScreen** composable with email/password fields, sign-in and sign-up buttons, and error display.
4. Gate the main app content behind auth — if not logged in, show LoginScreen; otherwise show the existing main screen.
5. Add a sign-out button (e.g. in a top bar or settings area).
6. Use the Supabase URL and anon key already configured in the project.

Keep it simple — email/password only, no OAuth providers.
""",
    ),
    "firebase-push": Integration(
        key="firebase-push",
        label="Firebase Push Notifications",
        emoji="🔔",
        description="Send and receive push notifications with Firebase Cloud Messaging.",
        dependencies=[],
        claude_prompt="""\
Add Firebase Cloud Messaging (push notifications) support to this KMP app.

Requirements:
1. Create a `notifications/` package under the shared `commonMain` source set.
2. Add an `expect`/`actual` pattern for `PushNotificationManager`:
   - `expect` in commonMain with `requestPermission()`, `getToken()`, and `onNotificationReceived` callback.
   - `actual` for Android using Firebase Messaging (`FirebaseMessagingService`).
   - `actual` for iOS as a stub (comment explaining Swift-side setup needed).
   - `actual` for Web/WASM as a stub.
3. For Android specifically:
   - Add the `google-services` plugin and `firebase-messaging` dependency references in comments at the top of the Android actual file (the user will need to add `google-services.json` manually).
   - Create a `MyFirebaseMessagingService` that extends `FirebaseMessagingService`.
4. Add a **NotificationsScreen** composable that shows:
   - Current push token (for testing).
   - A log of received notifications.
   - A button to request notification permission.
5. Add navigation to NotificationsScreen from the main screen.

Include clear TODO comments for manual setup steps (google-services.json, Firebase console).
""",
    ),
    "revenuecat": Integration(
        key="revenuecat",
        label="RevenueCat In-App Purchases",
        emoji="🛒",
        description="In-app purchases and subscriptions via RevenueCat.",
        dependencies=[],
        claude_prompt="""\
Add RevenueCat in-app purchase integration to this KMP app.

Requirements:
1. Create a `purchases/` package under the shared `commonMain` source set.
2. Add an `expect`/`actual` pattern for `PurchaseManager`:
   - `expect` in commonMain with:
     - `configure(apiKey: String)`
     - `getOfferings()` returning a list of available packages
     - `purchase(packageId: String)` returning success/failure
     - `restorePurchases()`
     - `StateFlow<Boolean>` for `isPro` (whether user has active subscription)
   - `actual` for Android: stub with TODO comments explaining RevenueCat SDK setup.
   - `actual` for iOS: stub with TODO comments explaining RevenueCat SDK setup.
   - `actual` for Web/WASM: stub returning empty/false.
3. Add a **PaywallScreen** composable that shows:
   - Available subscription plans (monthly/yearly placeholder).
   - "Subscribe" buttons for each plan.
   - "Restore Purchases" button.
   - Current subscription status.
4. Add a `Config.kt` with a placeholder `REVENUECAT_API_KEY` constant.
5. Add navigation to PaywallScreen from the main screen (e.g. "Upgrade to Pro" button).

Keep it as stubs with clear TODO comments — RevenueCat requires native SDK setup.
""",
    ),
}


def list_integrations() -> list[Integration]:
    """Return all available integrations in display order."""
    return list(INTEGRATIONS.values())


def get_integration(key: str) -> Optional[Integration]:
    """Look up an integration by key (case-insensitive)."""
    return INTEGRATIONS.get(key.lower().strip())


# ── Run integration ───────────────────────────────────────────────────────────

async def run_integration(
    integration: Integration,
    workspace_key: str,
    workspace_path: str,
    claude: ClaudeRunner,
    on_status: Callable[[str], Awaitable[None]],
    platform: str = "android",
) -> bool:
    """Run the agent loop to wire an integration into the workspace.

    Returns True on success.
    """
    prompt = (
        f"INTEGRATION TASK: {integration.label}\n\n"
        f"{integration.claude_prompt}\n\n"
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
