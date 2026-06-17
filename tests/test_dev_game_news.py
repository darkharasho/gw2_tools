from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.dev import DevCog


@pytest.mark.asyncio
async def test_gamenewstest_delegates_to_cog():
    bot = MagicMock()
    game_news = MagicMock()
    game_news.run_force_notification = AsyncMock()
    bot.get_cog.return_value = game_news

    cog = DevCog.__new__(DevCog)
    cog.bot = bot
    interaction = MagicMock()

    await DevCog.gamenewstest.callback(cog, interaction)

    bot.get_cog.assert_called_with("GameNewsCog")
    game_news.run_force_notification.assert_awaited_once_with(interaction)
