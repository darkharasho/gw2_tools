
import pytest
from unittest.mock import AsyncMock, MagicMock
import discord
from gw2_tools_bot.cogs.accounts import AccountsCog

@pytest.fixture
def mock_bot_accounts():
    bot = MagicMock()
    return bot

@pytest.mark.asyncio
async def test_accounts_init(mock_bot_accounts):
    cog = AccountsCog(mock_bot_accounts)
    assert cog is not None
    # We can add more specific layout/logic tests if we mock the fetching and interaction flow,
    # but `AccountsCog` is complex heavily relying on external API calls which are mocked in generic fixtures.
    # For now, ensure it instantiates and basic command structure exists.

    # Check for commands availability if we could access app_commands structure easily
    # or just trust the class definition is valid.

@pytest.mark.asyncio
async def test_accounts_strip_emoji():
    # Helper method test
    # AccountsCog._strip_emoji removes emoji but might leave spaces depending on impl
    # "Hello 😃" -> "Hello "
    assert AccountsCog._strip_emoji("Hello 😃").strip() == "Hello"
    assert AccountsCog._strip_emoji("Guild [TAG]") == "Guild [TAG]"
