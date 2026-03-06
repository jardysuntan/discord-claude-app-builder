"""
testflight_views.py — TestFlight setup view and embed helpers
(TestFlightSetupView, _testflight_setup_embed, _testflight_success_embed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from commands.testflight import handle_testflight

if TYPE_CHECKING:
    from bot_context import BotContext


class TestFlightSetupView(discord.ui.View):
    """Prompt to create app in App Store Connect, then retry /testflight."""

    def __init__(self, ctx: BotContext, owner_id: int, ws_key: str, ws_path: str,
                 app_name: str, bundle_id: str):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.app_name = app_name
        self.bundle_id = bundle_id
        # Link button to App Store Connect (link buttons don't need a callback)
        self.add_item(discord.ui.Button(
            label="Open App Store Connect",
            style=discord.ButtonStyle.link,
            url="https://appstoreconnect.apple.com/apps",
            emoji="\U0001f517",
        ))

    @discord.ui.button(label="Retry /testflight", style=discord.ButtonStyle.success, emoji="\U0001f680")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        ch = interaction.channel
        async def tf_status(msg, fpath=None):
            await self.ctx.send(ch, msg, file_path=fpath)
        result = await handle_testflight(self.ws_key, self.ws_path, on_status=tf_status)
        if result and result.needs_setup:
            embed, view = _testflight_setup_embed(
                self.ctx, self.owner_id, self.ws_key, self.ws_path,
                result.app_name, result.bundle_id,
            )
            await ch.send(embed=embed, view=view)

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except Exception:
            pass


def _testflight_setup_embed(ctx: BotContext, owner_id, ws_key, ws_path, app_name, bundle_id):
    """Build the embed + view for TestFlight app setup."""
    embed = discord.Embed(
        title="\U0001f4f2 One-time App Store Connect setup",
        description=(
            "Apple requires creating the app record on their website.\n"
            "This only needs to be done **once per app** \u2014 takes about 30 seconds.\n\n"
            "\U0001f4a1 *Right-click the link below \u2192 **Open in Browser** "
            "(Discord's built-in browser can be flaky).*"
        ),
        color=0x0A84FF,  # iOS blue
    )
    embed.add_field(
        name="Step 1",
        value='Click **Open App Store Connect** below, then tap **\uff0b** \u2192 **New App**',
        inline=False,
    )
    embed.add_field(
        name="Step 2",
        value=(
            f"**Name:** `{app_name}` *(common names are often taken \u2014 "
            "try something like \"{app_name} App\" or \"{app_name} by You\". "
            "It's just a display label, you can change it anytime)*\n"
            f"**Bundle ID:** select `{bundle_id}`\n"
            f"**SKU:** `{bundle_id}`\n"
            "**Access:** Full Access"
        ),
        inline=False,
    )
    embed.add_field(
        name="Step 3",
        value=(
            "Click **Create**, then come back here and tap **Retry**.\n\n"
            "\u26a0\ufe0f **Do NOT click \"Add for Review\"** \u2014 that submits to the public App Store "
            "under Jared's developer account. For now, just use TestFlight for testing. "
            "App Store submission will be available later once we add app readiness checks "
            "and support for your own Apple Developer account."
        ),
        inline=False,
    )
    view = TestFlightSetupView(ctx, owner_id, ws_key, ws_path, app_name, bundle_id)
    return embed, view


def _testflight_success_embed(workspace_key: str, bundle_id: str) -> discord.Embed:
    """Info embed shown after a successful TestFlight upload."""
    embed = discord.Embed(
        title="\U0001f389 Your app is on its way to TestFlight!",
        description=(
            "Apple is processing the build \u2014 this usually takes **5-30 minutes**.\n"
            "I'll send a message here when it's ready. "
            "Apple will also email `jared.e.tan@gmail.com`."
        ),
        color=0x34C759,  # iOS green
    )
    embed.add_field(
        name="What happens next",
        value=(
            "1. Apple processes the build (**5-30 min** \u2014 you don't need to do anything)\n"
            "2. You'll receive an email: **\"has completed processing\"**\n"
            "3. Go to [App Store Connect](https://appstoreconnect.apple.com/apps) "
            f"\u2192 **{workspace_key}** \u2192 **TestFlight**\n"
            "4. **First time only:** click **Manage** next to the build "
            "\u2192 select **No** for encryption \u2192 **Save**\n"
            "5. The build is now available for testers!"
        ),
        inline=False,
    )
    embed.add_field(
        name="Inviting testers",
        value=(
            "1. In the TestFlight tab, add **internal testers** (your team) or "
            "create a **public link** anyone can use\n"
            "2. Testers install the free "
            "**[TestFlight app](https://apps.apple.com/app/testflight/id899247664)** "
            "(one-time)\n"
            "3. They tap the invite link \u2192 app installs in seconds\n"
            "4. Future `/testflight` updates show up automatically \u2014 "
            "testers get a push notification"
        ),
        inline=False,
    )
    embed.add_field(
        name="Pushing updates",
        value=(
            f"Just run `/testflight` again \u2014 `{bundle_id}` is already set up, "
            "so it skips straight to building and uploading. "
            "Testers see the new version automatically."
        ),
        inline=False,
    )
    return embed
