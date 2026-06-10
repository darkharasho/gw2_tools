
import pytest
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.rss import RssFeedsCog, _extract_entry_description

@pytest.fixture
def mock_bot_rss():
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot

@pytest.mark.asyncio
async def test_rss_init(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    assert cog is not None
    cog._feed_poll.cancel()


@pytest.mark.asyncio
async def test_rss_test_command_removed(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    qualified_names = {cmd.qualified_name for cmd in cog.walk_app_commands()}
    assert "rss test" not in qualified_names
    assert hasattr(cog, "run_test_feed")


def test_extract_entry_description_preserves_markdown():
    entry = {
        "summary": (
            "<p><strong>Release Notes</strong></p>"
            "<ul><li>Fixed <em>major</em> issue</li><li>Added feature</li></ul>"
        )
    }

    description = _extract_entry_description(entry)

    assert description == "**Release Notes**\n\n- Fixed *major* issue\n- Added feature"
