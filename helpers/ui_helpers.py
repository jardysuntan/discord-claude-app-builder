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
        "**Build Apps:**",
        "`/build app <description>` — idea → running app",
        "`/build web` — build web target",
        "`/demo web` — build + preview",
        "`/fix [instructions]` — auto-fix build errors",
        "`/testflight` — upload to TestFlight",
        "`/playstore` — upload to Google Play",
    ]
    if is_admin:
        lines += [
            "`/build android|ios` — build native target",
            "`/demo android|ios` — native build + screenshot",
            "`/deploy ios|android` — install on device",
            "`/vid` — record Android emulator",
            "`/widget <desc>` — add iOS widget",
        ]
    lines += [
        "",
        "**Workspaces:**",
        "`@<ws> <prompt>` — talk to Claude in a workspace",
        "`/use <ws>` · `/ls` — switch / list workspaces",
        "`/create <Name>` — scaffold new project",
        "`/remove <ws>` · `/rename <old> <new>`",
        "",
        "**Save:**",
        "`/save` — save your progress",
        "`/save list` — see your save history",
        "`/save undo` · `/save redo`",
        "`/save github` — upload to GitHub",
        "",
        "**Git:**",
        "`/status` · `/diff` · `/commit [msg]` · `/log`",
        "`/branch [name]` · `/stash` · `/pr [title]`",
        "`/undo` · `/repo`",
        "",
        "**Tools:**",
        "`/queue task1 --- task2` — batch tasks",
        "`/spend` — daily budget",
    ]
    if is_admin:
        lines += [
            "`/run <cmd>` — run shell command in workspace",
            "`/dashboard` — web launcher for all apps",
            "`/bot-todo` — track improvements",
        ]
    lines += [
        "`/memory show|pin|reset` — project memory",
        "`/fixes show|clear` — build fix log",
        "",
        "**System:**",
        "`/health` · `/newsession`",
    ]
    if is_admin:
        lines += [
            "`/setup` · `/reload`",
            "",
            "**Admin:**",
            "`/allow @user` — grant bot access",
            "`/disallow @user` — revoke access",
            "`/setcap @user <amount>` — set daily spend cap",
            "`/admin` — list allowed users, emails, spend, pending invites",
            "`/invite [name] <email>` — email someone an invite to the bot",
            "`/maintenance [msg|off]` — toggle maintenance",
            "`/announce <msg>` — post to announcement channel",
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
        keys = ctx.registry.list_keys(owner_id=None if is_admin else user_id)
        if keys:
            view = WorkspaceSelectorView(ctx, user_id, keys)
            await channel.send("📂 No workspace set — pick one:", view=view)
