
import pytest
from unittest.mock import AsyncMock, MagicMock
from gw2_tools_bot.cogs.comps import CompCog

@pytest.fixture
def mock_bot_comps():
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot

@pytest.mark.asyncio
async def test_comps_init(mock_bot_comps, monkeypatch):
    monkeypatch.setenv("GW2TOOLS_EMOJI_GUILD_ID", "123456789")
    cog = CompCog(mock_bot_comps)
    assert cog is not None
    # Clean up the task to avoid warnings
    cog.poster_loop.cancel()
