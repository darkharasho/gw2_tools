import pytest
from unittest.mock import AsyncMock, MagicMock

from axitools.cogs.dev import DevCog


def _make_cog_with_delegate(attr):
    delegate = MagicMock()
    setattr(delegate, attr, AsyncMock())
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=delegate)
    cog = DevCog(bot)
    return cog, delegate


@pytest.mark.asyncio
async def test_dev_arcdps_delegates():
    cog, delegate = _make_cog_with_delegate("run_force_notification")
    interaction = MagicMock()

    await cog.arcdps.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("ArcDpsUpdatesCog")
    delegate.run_force_notification.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_dev_updatenotes_delegates():
    cog, delegate = _make_cog_with_delegate("run_force_notification")
    interaction = MagicMock()

    await cog.updatenotes.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("UpdateNotesCog")
    delegate.run_force_notification.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_dev_rsstest_delegates():
    cog, delegate = _make_cog_with_delegate("run_test_feed")
    interaction = MagicMock()

    await cog.rsstest.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("RssFeedsCog")
    delegate.run_test_feed.assert_awaited_once_with(interaction)


def test_dev_command_surface():
    cog = DevCog(MagicMock())
    qualified_names = {cmd.qualified_name for cmd in cog.walk_app_commands()}
    assert qualified_names == {
        "dev arcdps",
        "dev updatenotes",
        "dev rsstest",
    }
