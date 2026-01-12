
import pytest
from unittest.mock import AsyncMock, MagicMock
from gw2_tools_bot.cogs.arcdps import ArcDpsUpdatesCog

@pytest.fixture
def mock_bot_arcdps():
    return MagicMock()

@pytest.mark.asyncio
async def test_arcdps_init(mock_bot_arcdps):
    cog = ArcDpsUpdatesCog(mock_bot_arcdps)
    assert cog is not None
