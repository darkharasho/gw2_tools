
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.rss import (
    RssFeedsCog,
    _extract_entry_description,
    _parse_github_repo,
    _github_tag_from_entry,
    _release_is_complete,
    _release_content_hash,
    _within_grace_window,
    _append_seen_id,
)

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


def test_release_is_complete_requires_published_and_content():
    assert _release_is_complete({"draft": False, "assets": [{"name": "App.exe"}], "body": ""}) is True
    assert _release_is_complete({"draft": False, "assets": [], "body": "## Notes"}) is True
    assert _release_is_complete({"draft": False, "assets": [], "body": "   "}) is False
    assert _release_is_complete({"draft": True, "assets": [{"name": "App.exe"}], "body": "x"}) is False


def test_release_content_hash_changes_with_assets_and_body():
    base = {"name": "v1", "body": "notes", "assets": [{"name": "a.exe"}]}
    changed_body = {"name": "v1", "body": "notes updated", "assets": [{"name": "a.exe"}]}
    changed_assets = {"name": "v1", "body": "notes", "assets": [{"name": "a.exe"}, {"name": "b.AppImage"}]}
    assert _release_content_hash(base) == _release_content_hash(dict(base))
    assert _release_content_hash(base) != _release_content_hash(changed_body)
    assert _release_content_hash(base) != _release_content_hash(changed_assets)


def test_release_content_hash_ignores_asset_order():
    a = {"name": "v1", "body": "n", "assets": [{"name": "a"}, {"name": "b"}]}
    b = {"name": "v1", "body": "n", "assets": [{"name": "b"}, {"name": "a"}]}
    assert _release_content_hash(a) == _release_content_hash(b)


def test_within_grace_window():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    assert _within_grace_window("2026-06-22T11:00:00Z", now, hours=2) is True
    assert _within_grace_window("2026-06-22T09:30:00Z", now, hours=2) is False
    assert _within_grace_window(None, now, hours=2) is False


def test_append_seen_id_dedupes_and_caps():
    seen = []
    for i in range(55):
        seen = _append_seen_id(seen, f"id-{i}", cap=50)
    assert len(seen) == 50
    assert "id-0" not in seen
    assert "id-54" in seen
    # re-adding an existing id moves it to the end, no duplicate
    seen2 = _append_seen_id(seen, "id-54", cap=50)
    assert seen2.count("id-54") == 1
    assert len(seen2) == 50
