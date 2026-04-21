
import pytest
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.builds import BuildsCog

@pytest.fixture
def mock_bot_builds():
    return MagicMock()

@pytest.mark.asyncio
async def test_builds_init(mock_bot_builds):
    cog = BuildsCog(mock_bot_builds)
    assert cog is not None
