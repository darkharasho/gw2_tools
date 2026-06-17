from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.config import GameNewsChannelSelect, MoreChannelsView


def _view():
    view = MagicMock()
    view.config = MagicMock()
    view.config.game_news_channel_id = None
    view.persist = MagicMock()
    return view


@pytest.mark.asyncio
async def test_game_news_select_sets_channel():
    view = _view()
    select = GameNewsChannelSelect(view, None)
    channel = MagicMock()
    channel.id = 777
    channel.mention = "#news"
    select._values = [channel]  # discord.py stores resolved values internally
    # Patch the `values` property access used in callback:
    type(select).values = property(lambda self: [channel])
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await select.callback(interaction)
    assert view.config.game_news_channel_id == 777
    view.persist.assert_called_once()


@pytest.mark.asyncio
async def test_game_news_select_clears_channel():
    view = _view()
    view.config.game_news_channel_id = 5
    select = GameNewsChannelSelect(view, None)
    type(select).values = property(lambda self: [])
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await select.callback(interaction)
    assert view.config.game_news_channel_id is None
    view.persist.assert_called_once()


@pytest.mark.asyncio
async def test_more_channels_view_holds_game_news_select():
    # NOTE: discord.py View.__init__ calls asyncio.get_running_loop() internally,
    # so this must run inside an event loop even though it has no awaits.
    parent = _view()
    view = MoreChannelsView(parent, default_channel=None)
    assert any(isinstance(item, GameNewsChannelSelect) for item in view.children)
