
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.comps import CompCog
from axitools.storage import CompClassConfig

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


@pytest.mark.asyncio
async def test_get_class_emoji_returns_none_for_unusable_id(mock_bot_comps, monkeypatch):
    # A class points at an emoji_id the bot can't resolve to a real emoji. It must
    # return None — never a PartialEmoji with that id, which Discord rejects with
    # 50035 "Invalid emoji" and breaks the whole comp message.
    monkeypatch.setenv("GW2TOOLS_EMOJI_GUILD_ID", "123456789")
    mock_bot_comps.get_emoji.return_value = None  # not resolvable anywhere
    cog = CompCog(mock_bot_comps)
    try:
        guild = SimpleNamespace(id=1, me=None, emojis=[])  # me=None → external allowed
        entry = CompClassConfig(name="Luminary", emoji_id=1411120187004031047)
        assert cog._get_class_emoji(entry, guild=guild, channel=None) is None
    finally:
        cog.poster_loop.cancel()
