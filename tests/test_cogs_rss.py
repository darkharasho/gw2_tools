
import pytest
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.rss import RssFeedsCog, _extract_entry_description, _parse_github_repo, _github_tag_from_entry

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


def test_parse_github_repo_matches_releases_atom():
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO/releases.atom") == ("darkharasho", "TopStatsAIO")
    assert _parse_github_repo("https://github.com/baaron4/GW2-Elite-Insights-Parser/releases") == ("baaron4", "GW2-Elite-Insights-Parser")


def test_parse_github_repo_rejects_non_release_urls():
    assert _parse_github_repo("https://www.guildwars2.com/en/feed/") is None
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO") is None
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO/commits.atom") is None


def test_github_tag_from_entry_prefers_link():
    entry = {
        "link": "https://github.com/darkharasho/TopStatsAIO/releases/tag/v3.4.4",
        "id": "tag:github.com,2008:Repository/954501083/v3.4.4",
    }
    assert _github_tag_from_entry(entry) == "v3.4.4"


def test_github_tag_from_entry_falls_back_to_id():
    entry = {"id": "tag:github.com,2008:Repository/954501083/v3.4.4"}
    assert _github_tag_from_entry(entry) == "v3.4.4"
    assert _github_tag_from_entry({}) is None
