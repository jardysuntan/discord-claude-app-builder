"""
playstore_views.py — Play Store checklist and related views/embeds.

Classes: PlayStoreChecklistView, _ChecklistButton, _EmailModal,
         _InviteLinkModal, _EmailAABView
Functions: _playstore_checklist_embed, _playstore_success_embed
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import discord

from commands.playstore import handle_playstore
from commands.playstore_state import PlayStoreState

if TYPE_CHECKING:
    from bot_context import BotContext


class PlayStoreChecklistView(discord.ui.View):
    """Interactive checklist for Play Store setup — dynamic buttons based on state."""

    def __init__(self, ctx: BotContext, owner_id: int, ws_key: str, ws_path: str,
                 app_name: str, package_name: str):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.app_name = app_name
        self.package_name = package_name
        self.state = PlayStoreState.load(ws_path)
        self._rebuild_buttons()

    def _rebuild_buttons(self):
        self.clear_items()
        s = self.state

        if not s.developer_account_confirmed:
            self.add_item(_ChecklistButton(
                "I have a dev account", discord.ButtonStyle.success,
                "dev_account", self,
            ))
        else:
            self.add_item(_ChecklistButton(
                "Undo: dev account", discord.ButtonStyle.secondary,
                "undo_dev_account", self,
            ))

        if not s.app_created:
            self.add_item(_ChecklistButton(
                "App created", discord.ButtonStyle.success,
                "app_created", self,
            ))
        else:
            self.add_item(_ChecklistButton(
                "Undo: app created", discord.ButtonStyle.secondary,
                "undo_app_created", self,
            ))

        if not s.has_json_key():
            self.add_item(_ChecklistButton(
                "Upload JSON Key", discord.ButtonStyle.primary,
                "upload_key", self,
            ))

        if s.has_json_key() and not s.has_uploaded():
            self.add_item(_ChecklistButton(
                "Build & Upload", discord.ButtonStyle.success,
                "build_upload", self, emoji="\U0001f680",
            ))

        if s.has_uploaded() and not s.testers_confirmed:
            self.add_item(_ChecklistButton(
                "Add invite link", discord.ButtonStyle.primary,
                "add_invite_link", self, emoji="\U0001f517",
            ))

        if s.invite_link:
            self.add_item(_ChecklistButton(
                "Share link", discord.ButtonStyle.secondary,
                "share_link", self, emoji="\U0001f4e4",
            ))

        self.add_item(discord.ui.Button(
            label="Open Play Console",
            style=discord.ButtonStyle.link,
            url="https://play.google.com/console",
            emoji="\U0001f517",
        ))

    async def _handle_action(self, action: str, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)

        s = self.state
        ch = interaction.channel

        if action == "dev_account":
            s.developer_account_confirmed = True
            s.save(self.ws_path)
            self._rebuild_buttons()
            await interaction.response.edit_message(
                embed=_playstore_checklist_embed(self.ws_key, self.app_name, self.package_name, s),
                view=self,
            )

        elif action == "undo_dev_account":
            s.developer_account_confirmed = False
            s.save(self.ws_path)
            self._rebuild_buttons()
            await interaction.response.edit_message(
                embed=_playstore_checklist_embed(self.ws_key, self.app_name, self.package_name, s),
                view=self,
            )

        elif action == "app_created":
            s.app_created = True
            s.save(self.ws_path)
            self._rebuild_buttons()
            await interaction.response.edit_message(
                embed=_playstore_checklist_embed(self.ws_key, self.app_name, self.package_name, s),
                view=self,
            )

        elif action == "undo_app_created":
            s.app_created = False
            s.save(self.ws_path)
            self._rebuild_buttons()
            await interaction.response.edit_message(
                embed=_playstore_checklist_embed(self.ws_key, self.app_name, self.package_name, s),
                view=self,
            )

        elif action == "upload_key":
            self.ctx.awaiting_json_upload[interaction.user.id] = (
                self.ws_key, self.ws_path, interaction.message,
            )
            await interaction.response.send_message(
                "\U0001f4ce Send me the service account `.json` key file as an attachment.\n"
                "*(Play Console \u2192 Setup \u2192 API access \u2192 service account \u2192 key)*\n"
                "This will expire in 5 minutes.",
                ephemeral=True,
            )
            # Auto-expire after 5 min
            loop = asyncio.get_event_loop()
            uid = interaction.user.id
            loop.call_later(300, lambda: self.ctx.awaiting_json_upload.pop(uid, None))

        elif action == "build_upload":
            self.stop()
            await interaction.response.edit_message(view=None)
            async def ps_status(msg, fpath=None):
                await self.ctx.send(ch, msg, file_path=fpath)
            result = await handle_playstore(
                self.ws_key, self.ws_path, on_status=ps_status,
                key_path=s.json_key_path,
            )
            if result and result.success:
                s.api_access_verified = True
                s.last_upload_version_code = result.version_code
                s.last_upload_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                s.save(self.ws_path)
                await ch.send(embed=_playstore_success_embed(self.ws_key, self.package_name))
                if not s.testers_confirmed:
                    new_view = PlayStoreChecklistView(
                        self.ctx, self.owner_id, self.ws_key, self.ws_path,
                        self.app_name, self.package_name,
                    )
                    await ch.send(
                        embed=_playstore_checklist_embed(
                            self.ws_key, self.app_name, self.package_name, new_view.state,
                        ),
                        view=new_view,
                    )
            elif result and result.first_upload and result.aab_path:
                # First upload — ask for email to send the AAB
                email_view = _EmailAABView(
                    self.owner_id, result.aab_path,
                    self.ws_key, self.package_name, ch,
                )
                await ch.send(
                    "\U0001f4e7 **Enter your email** to receive the AAB file, then upload it to Play Console.\n"
                    "*(This is only needed for the first upload \u2014 future uploads are automatic)*",
                    view=email_view,
                )

        elif action == "add_invite_link":
            modal = _InviteLinkModal(self)
            await interaction.response.send_modal(modal)

        elif action == "share_link":
            if s.invite_link:
                await interaction.response.send_message(
                    f"\U0001f389 **{self.app_name}** is ready for testing!\n\n"
                    f"**[Tap here to install]({s.invite_link})**\n\n"
                    f"Open the link on your Android phone \u2192 accept the invite \u2192 "
                    f"install from Play Store.\n"
                    f"*(You must be added as a tester to access it)*",
                )
            else:
                await interaction.response.send_message(
                    "No invite link saved yet.", ephemeral=True,
                )

    async def on_timeout(self):
        try:
            for item in self.children:
                if not isinstance(item, discord.ui.Button) or item.style != discord.ButtonStyle.link:
                    item.disabled = True
        except Exception:
            pass


class _EmailModal(discord.ui.Modal, title="Email AAB"):
    email = discord.ui.TextInput(
        label="Your email address",
        placeholder="you@example.com",
        required=True,
        max_length=100,
    )

    def __init__(self, aab_path, ws_key, package_name, channel):
        super().__init__()
        self.aab_path = aab_path
        self.ws_key = ws_key
        self.package_name = package_name
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        to_email = self.email.value.strip()
        await interaction.response.send_message(f"Sending AAB to `{to_email}`...", ephemeral=True)

        from commands.playstore import _email_file
        sent = await _email_file(
            self.aab_path, to_email,
            subject=f"[{self.ws_key}] AAB build \u2014 upload to Play Console",
            body=(
                f"Your app {self.ws_key} ({self.package_name}) was built successfully.\n\n"
                f"Upload the attached .aab file to Google Play Console:\n\n"
                f"1. Open https://play.google.com/console\n"
                f"2. Select your app\n"
                f"3. Go to Testing > Internal testing\n"
                f"4. Tap 'Create new release'\n"
                f"5. Upload the attached .aab file\n"
                f"6. Tap 'Next' (skip any warnings about signing key)\n"
                f"7. Tap 'Save and roll out to Internal testing'\n"
                f"8. Confirm the rollout\n\n"
                f"After this first manual upload, future /playstore commands "
                f"will upload automatically."
            ),
        )
        if sent:
            await self.channel.send(
                f"\U0001f4e7 **AAB sent to `{to_email}`!**\n\n"
                f"**Steps:**\n"
                f"1. Open the email and download the `.aab` file\n"
                f"2. [Open Play Console](https://play.google.com/console) \u2192 your app\n"
                f"3. **Testing \u2192 Internal testing \u2192 Create new release**\n"
                f"4. Upload the `.aab` file\n"
                f"5. Tap **Next** (skip any signing key warnings)\n"
                f"6. Tap **Save and roll out to Internal testing**\n"
                f"7. Confirm the rollout\n\n"
                f"After this, future `/playstore` uploads will be fully automatic!"
            )
        else:
            await self.channel.send(
                "\u274c Failed to send email. Check that `GMAIL_ADDRESS` and "
                "`GMAIL_APP_PASSWORD` are set in `.env`.\n"
                "Get an app password at: https://myaccount.google.com/apppasswords"
            )


class _InviteLinkModal(discord.ui.Modal, title="Internal Testing Invite Link"):
    link = discord.ui.TextInput(
        label="Paste the invite link from Play Console",
        placeholder="https://play.google.com/apps/internaltest/...",
        required=True,
        max_length=300,
    )

    def __init__(self, checklist_view):
        super().__init__()
        self.checklist_view = checklist_view

    async def on_submit(self, interaction: discord.Interaction):
        url = self.link.value.strip()
        v = self.checklist_view
        v.state.invite_link = url
        v.state.testers_confirmed = True
        v.state.save(v.ws_path)
        v._rebuild_buttons()
        await interaction.response.edit_message(
            embed=_playstore_checklist_embed(v.ws_key, v.app_name, v.package_name, v.state),
            view=v,
        )
        # Send a shareable message
        await interaction.channel.send(
            f"\U0001f389 **{v.app_name}** is ready for testing!\n\n"
            f"**[Tap here to install]({url})**\n\n"
            f"Open the link on your Android phone \u2192 accept the invite \u2192 install from Play Store.\n"
            f"*(You must be added as a tester to access it)*"
        )


class _EmailAABView(discord.ui.View):
    """View with a button that opens the email modal."""

    def __init__(self, owner_id, aab_path, ws_key, package_name, channel):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.aab_path = aab_path
        self.ws_key = ws_key
        self.package_name = package_name
        self.channel = channel

    @discord.ui.button(label="Enter email", style=discord.ButtonStyle.primary, emoji="\U0001f4e7")
    async def email_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        modal = _EmailModal(self.aab_path, self.ws_key, self.package_name, self.channel)
        await interaction.response.send_modal(modal)


class _ChecklistButton(discord.ui.Button):
    """Generic button that delegates back to PlayStoreChecklistView."""

    def __init__(self, label, style, action, parent_view, emoji=None):
        super().__init__(label=label, style=style, emoji=emoji)
        self.action = action
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view._handle_action(self.action, interaction)


def _playstore_checklist_embed(ws_key, app_name, package_name, state: PlayStoreState):
    """Single embed showing all setup steps with check/uncheck status."""

    def _step(done, num, title, detail=""):
        icon = "\u2705" if done else "\u2b1c"
        line = f"{icon} **{num}.** {title}"
        if detail:
            line += f"\n\u2003\u2003{detail}"
        return line

    steps = "\n".join([
        _step(
            state.developer_account_confirmed, 1,
            "Google Play Developer Account",
            "confirmed" if state.developer_account_confirmed else
            "[Sign up here](https://play.google.com/console/signup) \u2014 costs $25\n"
            "\u2003\u2003Google will ask to verify your identity (takes a few days)\n"
            "\u2003\u2003Once verified, tap **I have a dev account** below",
        ),
        _step(
            state.app_created, 2,
            "Create app in Play Console",
            f"`{app_name}` \u00b7 `{package_name}`" if state.app_created else
            "[Open Play Console](https://play.google.com/console) \u2192 **Create app**\n"
            "\u2003\u2003Pick any name \u2014 the package name gets set automatically on first upload\n"
            "\u2003\u2003Then tap **App created** below",
        ),
        _step(
            state.has_json_key(), 3,
            "Set up API access",
            "key uploaded" if state.has_json_key() else
            "**a.** [Open Google Cloud Console](https://console.cloud.google.com) \u2192 **New Project** \u2192 name it anything\n"
            "\u2003\u2003**b.** Search **\"Google Play Android Developer API\"** \u2192 **Enable**\n"
            "\u2003\u2003**c.** Sidebar \u2192 **IAM & Admin \u2192 Service Accounts \u2192 Create Service Account**\n"
            "\u2003\u2003**d.** Click into it \u2192 **Keys** tab \u2192 **Add Key \u2192 JSON** \u2192 downloads a `.json` file\n"
            "\u2003\u2003**e.** [Play Console \u2192 Users](https://play.google.com/console/users-and-permissions) \u2192 "
            "**Invite** the service account email \u2192 give **Admin** access\n"
            "\u2003\u2003**f.** Tap **Upload JSON Key** below and send me the `.json` file",
        ),
        _step(
            state.has_uploaded(), 4,
            "Build & upload to Play Store",
            (f"v`{state.last_upload_version_code}` \u00b7 {state.last_upload_timestamp}"
             if state.has_uploaded()
             else "Tap **Build & Upload** below \u2014 I'll build the app and upload it\n"
             "\u2003\u2003First upload: I'll send you the file to upload manually in Play Console\n"
             "\u2003\u2003After that, future uploads are fully automatic"),
        ),
        _step(
            state.testers_confirmed, 5,
            "Share with testers",
            (f"[Tap here to install]({state.invite_link}) \u2014 tap **Share link** to reshare"
             if state.testers_confirmed and state.invite_link else
             "done" if state.testers_confirmed else
             "[Open Play Console](https://play.google.com/console) \u2192 your app \u2192 "
             "**Testing \u2192 Internal testing**\n"
             "\u2003\u2003**a.** Under **Testers**, tap **Create email list** \u2192 add your testers' emails \u2192 **Save**\n"
             "\u2003\u2003\u26a0\ufe0f **Make sure to add your own email too** so you can install & test it!\n"
             "\u2003\u2003**b.** Scroll down to **How testers join your test** \u2192 copy the **invite link**\n"
             "\u2003\u2003**c.** Tap **Add invite link** below and paste it"),
        ),
    ])

    embed = discord.Embed(
        title=f"\U0001f4f2 Play Store setup \u2014 {ws_key}",
        description=steps,
        color=0x01875F,
    )
    return embed


def _playstore_success_embed(workspace_key: str, package_name: str) -> discord.Embed:
    """Info embed shown after a successful Play Store upload."""
    embed = discord.Embed(
        title="\U0001f389 Your app is on Google Play internal testing!",
        description=(
            "Google Play is processing the build \u2014 this usually takes **1-5 minutes**.\n"
            "I'll notify you when it's ready."
        ),
        color=0x01875F,  # Play Store green
    )
    embed.add_field(
        name="Next: share with testers",
        value=(
            "1. [Open Play Console](https://play.google.com/console) \u2192 your app \u2192 "
            "**Testing \u2192 Internal testing**\n"
            "2. Under **Testers** \u2192 **Create email list** \u2192 add tester emails \u2192 **Save**\n"
            "3. Copy the **invite link** (under *How testers join your test*)\n"
            "4. Tap **Add invite link** on the checklist below to save & share it"
        ),
        inline=False,
    )
    embed.add_field(
        name="Pushing updates",
        value=(
            f"Just run `/playstore` again \u2014 future uploads are fully automatic.\n"
            "Internal testers see the new version automatically."
        ),
        inline=False,
    )
    return embed
