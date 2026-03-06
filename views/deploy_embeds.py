"""
deploy_embeds.py — Deployment info embeds (_ios_deploy_info_embed).
"""

from __future__ import annotations

import discord


def _ios_deploy_info_embed() -> discord.Embed:
    """Info embed explaining how iOS deployment works via TestFlight."""
    embed = discord.Embed(
        title="\U0001f34e Deploying to iOS",
        description="Here's how your app gets from Discord to people's iPhones.",
        color=0x0A84FF,
    )
    embed.add_field(
        name="How it works",
        value=(
            "1. Build your app with `@workspace` prompts \u2014 preview it live on the web\n"
            "2. When ready, run `/testflight` \u2014 the bot archives, signs, and uploads to Apple\n"
            "3. Apple processes the build (~5-30 min the first time)\n"
            "4. Share an invite link \u2014 testers install it natively on their iPhone\n"
            "5. Every future `/testflight` pushes an update \u2014 testers get it automatically"
        ),
        inline=False,
    )
    embed.add_field(
        name="What testers need",
        value=(
            "Just the free **[TestFlight](https://apps.apple.com/app/testflight/id899247664)** "
            "app and an invite link. No Apple Developer account, no Xcode, no fees. "
            "Builds expire after 90 days."
        ),
        inline=False,
    )
    embed.add_field(
        name="TestFlight vs App Store",
        value=(
            "**TestFlight** is for testing and demos \u2014 up to 100 testers, "
            "no Apple review, builds shared instantly. "
            "All TestFlight builds go through the bot operator's account at no cost to testers.\n\n"
            "**App Store** publication requires your own "
            "**[Apple Developer Program](https://developer.apple.com/programs/)** "
            "account ($99/year). Apple reviews every submission and the account holder "
            "is legally responsible for the app. "
            "Multi-user App Store support is coming soon."
        ),
        inline=False,
    )
    return embed
