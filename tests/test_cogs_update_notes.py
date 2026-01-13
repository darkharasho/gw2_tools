
import pytest
from unittest.mock import AsyncMock, MagicMock
from gw2_tools_bot.cogs.update_notes import UpdateNotesCog

@pytest.fixture
def mock_bot_update_notes():
    return MagicMock()

@pytest.mark.asyncio
async def test_update_notes_init(mock_bot_update_notes):
    cog = UpdateNotesCog(mock_bot_update_notes)
    assert cog is not None
