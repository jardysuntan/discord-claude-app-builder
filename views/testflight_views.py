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


class _RenameAndRetryModal(discord.ui.Modal, title="Set app name & retry"):
    """Modal to rename the app everywhere and retry /testflight."""

    name_input = discord.ui.TextInput(
        label="App name (must match what's in App Store Connect)",
        placeholder="e.g. Golf Leaderboard Pro",
        required=True,
        max_length=50,
    )

    def __init__(self, parent_view: TestFlightSetupView):
        super().__init__()
        self.parent_view = parent_view
        self.name_input.default = parent_view.app_name

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.name_input.value.strip()
        if not new_name:
            return await interaction.response.send_message("Name can't be empty.", ephemeral=True)

        await interaction.response.edit_message(view=None)
        ch = interaction.channel
        pv = self.parent_view

        # Rename workspace + files to match the name used in ASC
        from handlers.publish_commands import _rename_app_in_workspace
        new_key = new_name.lower().replace(" ", "-")
        if new_key != pv.ws_key:
            if not pv.ctx.registry.get_path(new_key):
                pv.ctx.registry.rename(pv.ws_key, new_key)
                pv.ws_key = new_key
        _rename_app_in_workspace(pv.ws_path, new_name)
        pv.app_name = new_name

        await pv.ctx.send(ch, f"Renamed to **{new_name}** — retrying TestFlight...")

        async def tf_status(msg, fpath=None):
            await pv.ctx.send(ch, msg, file_path=fpath)
        result = await handle_testflight(pv.ws_key, pv.ws_path, on_status=tf_status)
        if result and result.needs_setup:
            embed, view = _testflight_setup_embed(
                pv.ctx, pv.owner_id, pv.ws_key, pv.ws_path,
                pv.app_name, result.bundle_id,
            )
            await ch.send(embed=embed, view=view)
        elif result and result.success:
            from views.testflight_views import _testflight_success_embed
            await ch.send(embed=_testflight_success_embed(pv.ws_key, result.bundle_id))


class TestFlightSetupView(discord.ui.View):
    """Shown when no app record exists — lets user rename or retry after admin creates it."""

    def __init__(self, ctx: BotContext, owner_id: int, ws_key: str, ws_path: str,
                 app_name: str, bundle_id: str):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.app_name = app_name
        self.bundle_id = bundle_id

    @discord.ui.button(label="Change Name & Retry", style=discord.ButtonStyle.primary, emoji="\u270f\ufe0f")
    async def change_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(_RenameAndRetryModal(self))

    @discord.ui.button(label="Retry", style=discord.ButtonStyle.secondary, emoji="\U0001f504")
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
        elif result and result.success:
            await ch.send(embed=_testflight_success_embed(self.ws_key, result.bundle_id))

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except Exception:
            pass


async def _notify_admin(ctx: BotContext, requester_id: int, app_name: str, bundle_id: str):
    """DM the admin to create the app in App Store Connect."""
    import config
    admin_id = config.DISCORD_ALLOWED_USER_ID
    if requester_id == admin_id:
        return  # admin is the requester, no need to notify themselves
    try:
        admin = await ctx.client.fetch_user(admin_id)
        if admin:
            await admin.send(
                f"\U0001f4f2 **App creation needed**\n\n"
                f"<@{requester_id}> is trying to upload **{app_name}** to TestFlight "
                f"but it doesn't exist in App Store Connect yet.\n\n"
                f"**Create it here:** https://appstoreconnect.apple.com/apps\n"
                f"1. Click **\uff0b** \u2192 **New App**\n"
                f"2. **Name:** `{app_name}`\n"
                f"3. **Bundle ID:** select `{bundle_id}`\n"
                f"4. **SKU:** `{bundle_id}`\n\n"
                f"Once created, they can tap **Retry** in the channel."
            )
    except Exception:
        pass  # don't block the flow if DM fails


def _testflight_setup_embed(ctx: BotContext, owner_id, ws_key, ws_path, app_name, bundle_id):
    """Build the embed + view for when no app record exists."""
    is_admin = (owner_id == ctx.client.user.id) if ctx.client.user else False
    import config
    is_admin = owner_id == config.DISCORD_ALLOWED_USER_ID

    if is_admin:
        description = (
            f"No app record found for **{app_name}** (`{bundle_id}`).\n\n"
            "Create it in "
            "[App Store Connect](https://appstoreconnect.apple.com/apps):\n"
            f"1. Click **\uff0b** \u2192 **New App**\n"
            f"2. **Name:** `{app_name}`\n"
            f"3. **Bundle ID:** select `{bundle_id}`\n"
            f"4. **SKU:** `{bundle_id}`\n\n"
            "Then tap **Retry** below."
        )
    else:
        description = (
            f"**{app_name}** needs to be registered with Apple before uploading.\n\n"
            "The admin has been notified and will set it up shortly.\n"
            "Tap **Retry** once they confirm it's done."
        )

    embed = discord.Embed(
        title="\U0001f4f2 One-time setup needed",
        description=description,
        color=0xFF9500,  # orange/warning
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
