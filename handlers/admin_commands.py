"""
handlers/admin_commands.py — Admin commands
(allow, disallow, setcap, users/admin, invite, run, runsh).

Extracted from bot.py lines 2278-2456.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import config
from commands import run_cmd
from helpers.collaborate_email import send_collaborate_email
from helpers.invite_email import send_invite_email
from helpers.ui_helpers import send_workspace_footer

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_allow(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(channel, "Usage: `/allow @user`")
    else:
        # Extract user ID from mention or raw ID
        m = re.match(r"<@!?(\d+)>", cmd.raw_cmd.strip())
        target_id = int(m.group(1)) if m else None
        if not target_id and cmd.raw_cmd.strip().isdigit():
            target_id = int(cmd.raw_cmd.strip())
        if not target_id:
            await ctx.send(channel, "Usage: `/allow @user`")
        elif ctx.allowlist.is_allowed(target_id):
            name = ctx.allowlist.get_display_name(target_id) or str(target_id)
            await ctx.send(channel, f"**{name}** is already allowed.")
        else:
            target_user = None
            try:
                target_user = await ctx.client.fetch_user(target_id)
                display = target_user.display_name
            except Exception:
                display = str(target_id)
            ctx.allowlist.add(target_id, display)
            await ctx.send(
                channel,
                f"✅ **{display}** added to allowlist "
                f"(cap: ${config.DEFAULT_USER_DAILY_CAP_USD:.2f}/day).",
            )
            # DM the user
            try:
                if target_user:
                    await target_user.send(
                        "You've been granted access to the app builder bot! "
                        "Send `/help` to get started."
                    )
            except Exception:
                pass


async def handle_disallow(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(channel, "Usage: `/disallow @user`")
    else:
        m = re.match(r"<@!?(\d+)>", cmd.raw_cmd.strip())
        target_id = int(m.group(1)) if m else None
        if not target_id and cmd.raw_cmd.strip().isdigit():
            target_id = int(cmd.raw_cmd.strip())
        if not target_id:
            await ctx.send(channel, "Usage: `/disallow @user`")
        elif not ctx.allowlist.remove(target_id):
            await ctx.send(channel, "Cannot remove the bootstrap admin.")
        else:
            await ctx.send(channel, f"✅ User `{target_id}` removed from allowlist.")


async def handle_setcap(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(channel, "Usage: `/setcap @user <amount>`")
    else:
        parts = cmd.raw_cmd.strip().split()
        m = re.match(r"<@!?(\d+)>", parts[0]) if parts else None
        target_id = int(m.group(1)) if m else None
        if not target_id and parts and parts[0].isdigit():
            target_id = int(parts[0])
        amount_str = parts[1] if len(parts) > 1 else None
        if not target_id or not amount_str:
            await ctx.send(channel, "Usage: `/setcap @user <amount>`")
        else:
            try:
                amount = float(amount_str)
            except ValueError:
                await ctx.send(channel, "Invalid amount. Use a number like `15.00`.")
                await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
                return
            if ctx.allowlist.set_daily_cap(target_id, amount):
                name = ctx.allowlist.get_display_name(target_id) or str(target_id)
                await ctx.send(channel, f"✅ **{name}** daily cap set to ${amount:.2f}.")
            else:
                await ctx.send(channel, f"User `{target_id}` is not in the allowlist.")


async def handle_users_admin(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    """Handles both /users and /admin commands."""
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    users = ctx.allowlist.list_users()
    pending = ctx.allowlist.get_pending_invites()
    if not users and not pending:
        await ctx.send(channel, "No users in allowlist.")
    else:
        lines = ["**Allowed Users**"]
        for uid, info in users:
            role = info.get("role", "user")
            name = info.get("display_name", str(uid))
            email = info.get("email", "")
            cap = info.get("daily_cap_usd", config.DEFAULT_USER_DAILY_CAP_USD)
            spent = ctx.cost_tracker.today_spent(uid)
            tasks = ctx.cost_tracker.today_tasks(uid)
            badge = "\U0001f451" if role == "admin" else "\U0001f464"
            email_str = f" \u00b7 {email}" if email else ""
            lines.append(
                f"{badge} **{name}**{email_str}\n"
                f"\u2003\u2003${spent:.2f}/${cap:.2f} today, {tasks} tasks"
            )
        if pending:
            lines.append("\n**Pending invites** (not yet joined)")
            for email in pending:
                lines.append(f"  \U0001f4e7 {email}")
        await ctx.send(channel, "\n".join(lines))


async def handle_invite(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(
            channel,
            "Usage: `/invite someone@email.com` or `/invite Jamie someone@email.com`",
        )
    else:
        parts = cmd.raw_cmd.strip().split()
        # If first part isn't an email, treat it as a name
        if len(parts) >= 2 and "@" not in parts[0]:
            invite_name = parts[0]
            to_email = parts[1]
        else:
            invite_name = ""
            to_email = parts[0]
        if "@" not in to_email or "." not in to_email:
            await ctx.send(channel, "That doesn't look like an email address.")
        else:
            await ctx.send(channel, f"📧 Sending invite to `{to_email}`...")
            # Generate a server invite link (1 use, 7 day expiry)
            invite_url = None
            for guild in ctx.client.guilds:
                try:
                    channels = [
                        c for c in guild.text_channels
                        if c.permissions_for(guild.me).create_instant_invite
                    ]
                    if channels:
                        inv = await channels[0].create_invite(
                            max_uses=1, max_age=604800, unique=True,
                        )
                        invite_url = str(inv)
                        break
                except Exception:
                    pass
            if not invite_url:
                await ctx.send(
                    channel,
                    "❌ Could not generate a server invite link. "
                    "Make sure the bot has invite permissions.",
                )
            else:
                ok = await send_invite_email(to_email, invite_url, name=invite_name)
                if ok:
                    # Track the invited email
                    ctx.allowlist.add_pending_invite(to_email)
                    await ctx.send(channel, f"✅ Invite sent to `{to_email}`!")
                else:
                    await ctx.send(
                        channel,
                        "❌ Failed to send email. Check GMAIL_ADDRESS / "
                        "GMAIL_APP_PASSWORD in `.env`.",
                    )


async def handle_collaborate(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "\U0001f512 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(channel, "Usage: `/collaborate <workspace> <name> <email>`")
        return

    parts = cmd.raw_cmd.strip().split()
    if len(parts) < 3:
        await ctx.send(channel, "Usage: `/collaborate <workspace> <name> <email>`")
        return

    ws_key = parts[0].lower()
    collab_name = parts[1]
    collab_email = parts[2]

    if not ctx.registry.exists(ws_key):
        await ctx.send(channel, f"\u274c Unknown workspace: `{ws_key}`")
        return

    if "@" not in collab_email or "." not in collab_email:
        await ctx.send(channel, "That doesn't look like an email address.")
        return

    # Check if already a collaborator
    existing = ctx.registry.get_collaborators(ws_key)
    if any(c.get("email", "").lower() == collab_email.lower() for c in existing):
        await ctx.send(channel, f"**{collab_name}** is already a collaborator on `{ws_key}`.")
        return

    # Check if user is already on Discord
    existing_uid = ctx.allowlist.find_by_email(collab_email)
    app_name = ws_key.replace("-", " ").replace("_", " ").title()

    admin_display = ctx.allowlist.get_display_name(user_id) or "The admin"

    if existing_uid:
        # Already on Discord — add with user_id, DM them
        ctx.registry.add_collaborator(ws_key, collab_name, collab_email, user_id=existing_uid)
        try:
            target_user = await ctx.client.fetch_user(existing_uid)
            await target_user.send(
                f"**{admin_display}** invited you to collaborate on **{app_name}**! "
                f"Send `@{ws_key} hello` to get started."
            )
        except Exception:
            pass
        await ctx.send(
            channel,
            f"\u2705 **{collab_name}** added as collaborator on `{ws_key}` (already on Discord).",
        )
    else:
        # Not on Discord — generate invite, send email
        await ctx.send(channel, f"\U0001f4e7 Sending collaboration invite to `{collab_email}`...")
        invite_url = None
        for guild in ctx.client.guilds:
            try:
                channels = [
                    c for c in guild.text_channels
                    if c.permissions_for(guild.me).create_instant_invite
                ]
                if channels:
                    inv = await channels[0].create_invite(
                        max_uses=1, max_age=604800, unique=True,
                    )
                    invite_url = str(inv)
                    break
            except Exception:
                pass

        if not invite_url:
            await ctx.send(
                channel,
                "\u274c Could not generate a server invite link. "
                "Make sure the bot has invite permissions.",
            )
            return

        ok = await send_collaborate_email(
            to_email=collab_email,
            invite_url=invite_url,
            name=collab_name,
            app_name=app_name,
            admin_name=admin_display,
            workspace_key=ws_key,
        )
        if ok:
            # Add to allowlist so they can use the bot when they join
            ctx.allowlist.add_pending_invite(collab_email)
            ctx.registry.add_collaborator(ws_key, collab_name, collab_email, user_id=None)
            await ctx.send(channel, f"\u2705 Collaboration invite sent to `{collab_email}` for `{ws_key}`!")
        else:
            await ctx.send(
                channel,
                "\u274c Failed to send email. Check GMAIL_ADDRESS / GMAIL_APP_PASSWORD in `.env`.",
            )


async def handle_run(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/run` is admin-only.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            await ctx.send(channel, await run_cmd.handle_run(cmd.raw_cmd or "", ws_path))


async def handle_runsh(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/runsh` is admin-only.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            await ctx.send(channel, await run_cmd.handle_runsh(cmd.raw_cmd or "", ws_path))


HANDLERS = {
    "allow": handle_allow,
    "disallow": handle_disallow,
    "setcap": handle_setcap,
    "users": handle_users_admin,
    "admin": handle_users_admin,
    "invite": handle_invite,
    "collaborate": handle_collaborate,
    "run": handle_run,
    "runsh": handle_runsh,
}
