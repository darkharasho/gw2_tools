
import types
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
from axitools.storage import RssFeedConfig, TrackedRelease


def _atom_entry(tag, repo_id="1"):
    return {
        "id": f"tag:github.com,2008:Repository/{repo_id}/{tag}",
        "link": f"https://github.com/o/r/releases/tag/{tag}",
        "title": tag,
    }

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


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            import aiohttp
            raise aiohttp.ClientResponseError(MagicMock(), (), status=self.status)

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_fetch_github_release_returns_json(mock_bot_rss, monkeypatch):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(200, {"name": "v1", "draft": False}))
    cog._get_session = AsyncMock(return_value=session)

    release = await cog._fetch_github_release("darkharasho", "TopStatsAIO", "v1")
    assert release == {"name": "v1", "draft": False}
    url = session.get.call_args[0][0]
    assert url == "https://api.github.com/repos/darkharasho/TopStatsAIO/releases/tags/v1"


@pytest.mark.asyncio
async def test_fetch_github_release_handles_404(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(404, {}))
    cog._get_session = AsyncMock(return_value=session)

    assert await cog._fetch_github_release("o", "r", "v9") is None


@pytest.mark.asyncio
async def test_fetch_github_release_sends_token_when_present(mock_bot_rss, monkeypatch):
    monkeypatch.setenv("AXITOOLS_GITHUB_TOKEN", "ghp_test")
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(200, {"name": "v1"}))
    cog._get_session = AsyncMock(return_value=session)

    await cog._fetch_github_release("o", "r", "v1")
    headers = session.get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer ghp_test"


@pytest.mark.asyncio
async def test_build_github_release_embed(mock_bot_rss):
    from axitools.storage import RssFeedConfig

    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    feed = RssFeedConfig(name="TSA", url="https://github.com/darkharasho/TopStatsAIO/releases.atom", channel_id=1)
    release = {
        "name": "v3.4.4",
        "tag_name": "v3.4.4",
        "html_url": "https://github.com/darkharasho/TopStatsAIO/releases/tag/v3.4.4",
        "body": "## What's new\n- Fixed crash",
        "assets": [
            {"name": "TopStatsAIO-Setup.exe", "browser_download_url": "https://example/exe"},
        ],
        "published_at": "2026-06-22T12:00:00Z",
    }
    embed = cog._build_github_release_embed(feed, release)
    assert embed.title == "v3.4.4"
    assert embed.url == release["html_url"]
    assert "Fixed crash" in embed.description
    # asset listed
    field_text = "\n".join(f.value for f in embed.fields)
    assert "TopStatsAIO-Setup.exe" in field_text


@pytest.mark.asyncio
async def test_github_feed_skips_incomplete_release(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=777))
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock(return_value={"draft": False, "assets": [], "body": "   "})

    feed = RssFeedConfig(name="r", url="https://github.com/o/r/releases.atom", channel_id=1)
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    channel.send.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_github_feed_posts_complete_release_once(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    msg = MagicMock(id=777)
    channel = MagicMock()
    channel.send = AsyncMock(return_value=msg)
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock(
        return_value={"name": "v1", "tag_name": "v1", "draft": False,
                      "assets": [{"name": "a.exe"}], "body": "notes",
                      "html_url": "https://github.com/o/r/releases/tag/v1"}
    )

    feed = RssFeedConfig(name="r", url="https://github.com/o/r/releases.atom", channel_id=1)
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    channel.send.assert_awaited_once()
    assert result is not None
    entry_id = _atom_entry("v1")["id"]
    assert entry_id in result.seen_entry_ids
    tracked = result.tracked_releases[entry_id]
    assert tracked.message_id == 777
    assert tracked.content_hash

    # Second poll with same feed state => no re-post.
    channel.send.reset_mock()
    result2 = await cog._process_github_feed(MagicMock(), result, parsed, "o", "r")
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_github_feed_edits_message_when_content_changes(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    edited = AsyncMock()
    message = MagicMock(id=777)
    message.edit = edited
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock(return_value=message)
    cog._resolve_channel = AsyncMock(return_value=channel)
    # New body => new hash vs the stored one.
    cog._fetch_github_release = AsyncMock(
        return_value={"name": "v1", "tag_name": "v1", "draft": False,
                      "assets": [{"name": "a.exe"}], "body": "notes UPDATED",
                      "html_url": "https://github.com/o/r/releases/tag/v1"}
    )

    entry_id = _atom_entry("v1")["id"]
    now = datetime.now(timezone.utc)
    feed = RssFeedConfig(
        name="r", url="https://github.com/o/r/releases.atom", channel_id=1,
        seen_entry_ids=[entry_id],
        tracked_releases={entry_id: TrackedRelease(
            entry_id=entry_id, message_id=777, content_hash="STALE",
            first_posted_at=now.isoformat(), finalized=False)},
    )
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    edited.assert_awaited_once()
    channel.send.assert_not_called()
    assert result.tracked_releases[entry_id].content_hash != "STALE"


@pytest.mark.asyncio
async def test_github_feed_finalizes_after_grace_window(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock()

    entry_id = _atom_entry("v1")["id"]
    old = "2026-06-20T00:00:00Z"  # well past 2h
    feed = RssFeedConfig(
        name="r", url="https://github.com/o/r/releases.atom", channel_id=1,
        seen_entry_ids=[entry_id],
        tracked_releases={entry_id: TrackedRelease(
            entry_id=entry_id, message_id=777, content_hash="x",
            first_posted_at=old, finalized=False)},
    )
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    cog._fetch_github_release.assert_not_called()  # no API call once past window
    channel.fetch_message.assert_not_called()
    assert result.tracked_releases[entry_id].finalized is True


@pytest.mark.asyncio
async def test_non_github_feed_uses_generic_path(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    cog._fetch_feed = AsyncMock(return_value=types.SimpleNamespace(
        entries=[{"id": "x", "title": "GW2 news", "link": "https://www.guildwars2.com/x"}],
        feed={},
    ))
    cog._post_entries = AsyncMock(return_value=None)
    github_path = AsyncMock()
    cog._process_github_feed = github_path

    feed = RssFeedConfig(name="GW2", url="https://www.guildwars2.com/en/feed/", channel_id=1)
    await cog._process_feed(MagicMock(), feed)
    github_path.assert_not_called()
    cog._post_entries.assert_awaited_once()
