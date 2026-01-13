
import pytest
from unittest.mock import AsyncMock, MagicMock
from gw2_tools_bot.cogs.wvw_alliance import AllianceMatchupCog

@pytest.fixture
def mock_bot_alliance():
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot

@pytest.mark.asyncio
async def test_wvw_alliance_init(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    assert cog is not None
    cog._poster_loop.cancel()
