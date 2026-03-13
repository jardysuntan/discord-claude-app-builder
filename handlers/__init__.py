"""
handlers — command handler modules for the bot refactor.

Each sub-module exports a HANDLERS dict mapping command name strings to
async handler functions with signature:

    async def handle_X(ctx, cmd, channel, user_id, is_admin)

This __init__ merges them into a single COMMAND_HANDLERS dict.
"""

from handlers import (
    workspace_commands,
    build_commands,
    publish_commands,
    save_git_commands,
    admin_commands,
    system_commands,
    data_commands,
)

COMMAND_HANDLERS = {
    **workspace_commands.HANDLERS,
    **build_commands.HANDLERS,
    **publish_commands.HANDLERS,
    **save_git_commands.HANDLERS,
    **admin_commands.HANDLERS,
    **system_commands.HANDLERS,
    **data_commands.HANDLERS,
}
