
import pytest
from unittest.mock import MagicMock
from discord import app_commands
from axitools.cogs.guild_roles import GuildRolesCog

@pytest.fixture
def mock_bot_accounts():
    bot = MagicMock()
    return bot

@pytest.mark.asyncio
async def test_guild_roles_init(mock_bot_accounts):
    cog = GuildRolesCog(mock_bot_accounts)
    assert cog is not None

@pytest.mark.asyncio
async def test_guild_roles_strip_emoji():
    # Helper method test
    # GuildRolesCog._strip_emoji removes emoji but might leave spaces depending on impl
    # "Hello 😃" -> "Hello "
    assert GuildRolesCog._strip_emoji("Hello 😃").strip() == "Hello"
    assert GuildRolesCog._strip_emoji("Guild [TAG]") == "Guild [TAG]"


def _qualified_command_names():
    names = set()
    for command in GuildRolesCog.guild_roles.walk_commands():
        if isinstance(command, app_commands.Command):
            names.add(command.qualified_name)
    return names


def test_guild_roles_new_qualified_names_present():
    names = _qualified_command_names()
    expected = {
        "guildroles set",
        "guildroles list",
        "guildroles remove",
        "guildroles audit",
        "guildroles alliance set",
        "guildroles alliance clear",
        "guildroles allowlist add",
        "guildroles allowlist remove",
        "guildroles allowlist list",
    }
    assert expected <= names


def test_guild_roles_old_qualified_names_absent():
    names = _qualified_command_names()
    stale = {
        "guildroles setalliance",
        "guildroles clearalliance",
        "guildroles whitelist add",
        "guildroles whitelist remove",
        "guildroles whitelist list",
    }
    assert not (stale & names)
