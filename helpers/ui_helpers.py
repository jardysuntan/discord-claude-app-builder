"""
helpers/ui_helpers.py — UI helper functions extracted from bot.py.

Contains:
  - help_text(is_admin)        — generates the /help text string
  - send_workspace_footer(…)   — sends the workspace indicator or selector
"""

import config


def help_text(is_admin: bool = True):
    agent = " *(agent ON)*" if config.AGENT_MODE else " *(agent OFF)*"
    lines = [
        "**discord-claude-bridge** — build apps from chat" + agent + "\n",
        "**Build & Preview:**",
        "`/planapp` — plan your app first (screens, data model, features)",
        "`/build app <description>` — idea \u2192 running app",
        "`/demo` — build + preview your app",
        "`/testflight` — when happy, publish to TestFlight (iOS). Ping `jared.e.tan@gmail.com` for setup.",
        "`/playstore` — when happy, publish to Google Play. Ping `jared.e.tan@gmail.com` for setup.",
        "",
        "**Chat:**",
        "Just send a message to chat with the bot about your current app.",
        "Or use `@myapp make the button blue` to talk to a specific app.",
        "Paste a screenshot to show the bot a bug or design — it can see images.",
        "`/ls` — list & switch apps",
        "`/rename <new name>` — rename current app",
        "`/remove <name>` — delete an app",
        "",
        "**Save:**",
        "`/save` — save your progress",
        "`/save list` — see your save history",
        "",
        "**Data:**",
        "`/data export` — download all tables as CSV",
        "`/data template` — get empty CSV templates to fill in",
        "`/data import` — bulk-import a CSV file",
        "",
        "**Tools:**",
        "`/spend` — daily budget",
        "",
        "-# Discord limits messages to 2000 characters. For longer prompts, split across multiple messages.",
    ]
    if is_admin:
        lines += [
            "",
            "**Admin:**",
            "`/demo android|ios` — native build + screenshot",
            "`/save github` — upload to GitHub",
            "`/status` `/diff` `/commit` `/log` `/branch` `/stash` `/pr` `/undo` `/repo` — git",
            "`/run <cmd>` — run shell command",
            "`/bot-todo` — track improvements",
            "`/allow @user` — grant bot access",
            "`/disallow @user` — revoke access",
            "`/setcap @user <amount>` — set daily spend cap",
            "`/admin` — list allowed users & spend",
            "`/invite [name] <email>` — email invite",
            "`/collaborate <ws> <name> <email>` — invite collaborator",
            "`/maintenance [msg|off]` — toggle maintenance",
            "`/announce <msg>` — announce to channel",
            "`/setup` `/health` `/reload` `/newsession` — system",
        ]
    return "\n".join(lines)


async def send_workspace_footer(ctx, channel, user_id: int, selector_view=None, is_admin: bool = False):
    """Send plain-text workspace indicator, or selector buttons when none is set."""
    from views.workspace_views import WorkspaceSelectorView

    ws = ctx.registry.get_default(user_id)
    if ws:
        msg = await channel.send(f"📂 workspace: **{ws}**")
        # Link footer to selector so button click can edit it
        if selector_view is not None:
            selector_view.footer_message = msg
    else:
        user_email = ctx.allowlist.get_email(user_id) if not is_admin else None
        keys = ctx.registry.list_keys(owner_id=None if is_admin else user_id, user_email=user_email)
        if keys:
            view = WorkspaceSelectorView(ctx, user_id, keys)
            await channel.send("📂 No workspace set — pick one:", view=view)
