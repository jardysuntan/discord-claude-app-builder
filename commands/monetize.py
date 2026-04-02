"""
commands/monetize.py — RevenueCat subscription integration for KMP apps.

/monetize plan:monthly price:4.99 features:"premium themes, export PDF"
  → adds RevenueCat SDK to shared KMP module
  → generates paywall composable with configurable tiers
  → runs auto-fix loop to validate builds
  → outputs next-steps checklist for App Store Connect / Play Console
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary


@dataclass
class MonetizeArgs:
    plan: str = "monthly"
    price: str = "4.99"
    features: str = ""


def parse_monetize_args(raw: str | None) -> MonetizeArgs:
    """Parse key:value pairs from raw command string.

    Examples:
        plan:monthly price:4.99 features:"premium themes, export PDF"
        monthly 4.99
    """
    args = MonetizeArgs()
    if not raw:
        return args

    # Try key:value parsing first
    plan_m = re.search(r'plan:(\S+)', raw)
    price_m = re.search(r'price:(\S+)', raw)
    features_m = re.search(r'features:"([^"]+)"', raw) or re.search(r'features:(\S+)', raw)

    if plan_m:
        args.plan = plan_m.group(1).lower()
    if price_m:
        args.price = price_m.group(1)
    if features_m:
        args.features = features_m.group(1)

    # Fallback: if no key:value pairs matched, try positional
    if not plan_m and not price_m and not features_m:
        parts = raw.split()
        if parts:
            for p in parts:
                if p.replace(".", "").isdigit():
                    args.price = p
                elif p.lower() in ("monthly", "yearly", "both"):
                    args.plan = p.lower()

    return args


def _build_prompt(args: MonetizeArgs) -> str:
    """Build the Claude prompt for RevenueCat integration."""
    plan_desc = {
        "monthly": "a monthly subscription plan",
        "yearly": "a yearly subscription plan",
        "both": "both monthly and yearly subscription plans",
    }.get(args.plan, "a monthly subscription plan")

    features_section = ""
    if args.features:
        feature_list = [f.strip() for f in args.features.split(",")]
        features_bullets = "\n".join(f"   - {f}" for f in feature_list)
        features_section = (
            f"\n6. Gate the following premium features behind the subscription entitlement check:\n"
            f"{features_bullets}\n"
            "   Add a clear visual distinction between free and premium features in the UI.\n"
        )

    return f"""\
MONETIZATION TASK: Add RevenueCat subscription integration

Add RevenueCat in-app subscriptions to this KMP app with {plan_desc} at ${args.price}/month.

Requirements:
1. Add the RevenueCat Purchases SDK dependencies:
   - For the shared KMP module in `libs.versions.toml` and `build.gradle.kts`:
     - `com.revenuecat.purchases:purchases-kmp-core` (latest stable)
     - `com.revenuecat.purchases:purchases-kmp-ui` for PaywallUI (if available), otherwise build custom
   - For Android: `com.revenuecat.purchases:purchases` and `com.revenuecat.purchases:purchases-ui`
   - For iOS: Add RevenueCat SPM dependency in the Xcode project comments/instructions

2. Create a `monetization/` package under the shared `commonMain` source set with:
   - `RevenueCatManager.kt` — singleton that wraps RevenueCat SDK:
     - `configure(apiKey: String)` — call on app start
     - `getOfferings()` → list of available packages/plans
     - `purchase(activity: Any?, packageToPurchase: Package)` → success/failure
     - `restorePurchases()` → restore previous purchases
     - `customerInfo` as `StateFlow<CustomerInfo?>` — observe entitlement status
     - `isPro` as `StateFlow<Boolean>` — derived from customerInfo entitlement check
   - Platform `expect`/`actual` declarations where needed for Android Activity context

3. Create a `monetization/ui/PaywallScreen.kt` composable that displays:
   - App name/logo area at the top
   - A compelling "Upgrade to Pro" header
   - {"Both monthly (${args.price}/mo) and yearly plan cards with savings badge" if args.plan == "both" else f"A {args.plan} plan card at ${args.price}" + ("/mo" if args.plan == "monthly" else "/yr")}
   - Each plan card shows: price, billing period, and a "Subscribe" button
   - A "Restore Purchases" text button at the bottom
   - Loading and error states
   - Use Material 3 design with the app's existing theme/colors

4. Create a `monetization/Config.kt` with:
   - `REVENUECAT_API_KEY` placeholder constant (with TODO comment)
   - `ENTITLEMENT_ID = "pro"` constant
   - `MONTHLY_PRODUCT_ID` and `YEARLY_PRODUCT_ID` placeholder constants

5. Wire it into the app:
   - Call `RevenueCatManager.configure()` in the app's main entry point / Application class
   - Add a "Pro" / "Upgrade" button in the main screen or navigation that opens PaywallScreen
   - Add navigation route for PaywallScreen
{features_section}
7. Add entitlement check utility:
   - `@Composable fun PremiumGate(content: @Composable () -> Unit)` that shows
     an upgrade prompt if user is not subscribed, otherwise shows the content

After making all code changes, do a quick compile check to make sure nothing is broken.

IMPORTANT: Use placeholder API keys and product IDs — the user will configure real values later.
Do NOT add any test framework dependencies. Focus on production code only.
"""


def _next_steps_checklist(args: MonetizeArgs) -> str:
    """Generate the post-integration setup checklist."""
    plan_label = {
        "monthly": f"monthly at ${args.price}/mo",
        "yearly": f"yearly at ${args.price}/yr",
        "both": f"monthly at ${args.price}/mo + yearly option",
    }.get(args.plan, f"monthly at ${args.price}/mo")

    return f"""\
**Next steps to go live with subscriptions ({plan_label}):**

**RevenueCat Dashboard** (https://app.revenuecat.com)
- [ ] Create a new project for your app
- [ ] Copy your public API key → replace `REVENUECAT_API_KEY` in `Config.kt`
- [ ] Create an entitlement called `pro`
- [ ] Create product identifiers matching your App Store / Play Console products

**Apple App Store Connect**
- [ ] Go to App Store Connect → Your App → In-App Purchases
- [ ] Create a subscription group (e.g. "Pro")
- [ ] Add subscription product(s) with your pricing (${args.price})
- [ ] Submit for review with your next app update
- [ ] Add the product IDs to RevenueCat dashboard

**Google Play Console**
- [ ] Go to Play Console → Your App → Monetize → Products → Subscriptions
- [ ] Create subscription(s) matching your pricing
- [ ] Add base plan with pricing ${args.price}
- [ ] Link your Play Console to RevenueCat (Service Credentials JSON)

**Testing**
- [ ] Use RevenueCat sandbox mode to test purchases
- [ ] Test restore purchases flow
- [ ] Verify entitlement gating works correctly
- [ ] Test on both iOS and Android before going live"""


async def run_monetize(
    raw_args: str | None,
    workspace_key: str,
    workspace_path: str,
    claude: ClaudeRunner,
    on_status: Callable[[str], Awaitable[None]],
    platform: str = "android",
) -> tuple[bool, str]:
    """Run the monetization integration.

    Returns (success, next_steps_message).
    """
    args = parse_monetize_args(raw_args)
    prompt = _build_prompt(args)

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

    next_steps = _next_steps_checklist(args)
    return result.success, next_steps
