
import pytest
from unittest.mock import MagicMock
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
