"""Tests for the streaming notifications cog."""
from __future__ import annotations

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aioresponses import aioresponses


# ---------------------------------------------------------------------------
# Twitch token manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_twitch_token_manager_fetches_token():
    from axitools.cogs.streaming import _TwitchTokenManager
    import aiohttp

    manager = _TwitchTokenManager("client_id_abc", "client_secret_xyz")

    with aioresponses() as m:
        m.post(
            "https://id.twitch.tv/oauth2/token",
            payload={"access_token": "tok123", "expires_in": 5000000},
        )
        async with aiohttp.ClientSession() as session:
            token = await manager.get_token(session)

    assert token == "tok123"
    assert manager._token == "tok123"


@pytest.mark.asyncio
async def test_twitch_token_manager_caches_token():
    from axitools.cogs.streaming import _TwitchTokenManager
    import aiohttp

    manager = _TwitchTokenManager("id", "secret")
    manager._token = "cached_token"

    with aioresponses() as m:
        async with aiohttp.ClientSession() as session:
            token = await manager.get_token(session)

    assert token == "cached_token"
    assert len(m.requests) == 0


@pytest.mark.asyncio
async def test_twitch_token_manager_refresh():
    from axitools.cogs.streaming import _TwitchTokenManager
    import aiohttp

    manager = _TwitchTokenManager("id", "secret")
    manager._token = "old_token"

    with aioresponses() as m:
        m.post(
            "https://id.twitch.tv/oauth2/token",
            payload={"access_token": "new_token", "expires_in": 5000000},
        )
        async with aiohttp.ClientSession() as session:
            token = await manager.refresh_token(session)

    assert token == "new_token"
    assert manager._token == "new_token"


def test_twitch_token_manager_auth_headers():
    from axitools.cogs.streaming import _TwitchTokenManager

    manager = _TwitchTokenManager("my_client_id", "secret")
    headers = manager.auth_headers("my_token")

    assert headers["Client-ID"] == "my_client_id"
    assert headers["Authorization"] == "Bearer my_token"


# ---------------------------------------------------------------------------
# Twitch helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_twitch_user_returns_user_data():
    from axitools.cogs.streaming import _fetch_twitch_user, _TwitchTokenManager
    import aiohttp

    tokens = _TwitchTokenManager("cid", "csecret")
    tokens._token = "tok"

    with aioresponses() as m:
        m.post("https://id.twitch.tv/oauth2/token", payload={"access_token": "tok"})
        m.get(
            "https://api.twitch.tv/helix/users?login=arenanet",
            payload={"data": [{"login": "arenanet", "display_name": "ArenaNet", "id": "123"}]},
        )
        async with aiohttp.ClientSession() as session:
            user = await _fetch_twitch_user(session, tokens, "arenanet")

    assert user is not None
    assert user["login"] == "arenanet"
    assert user["display_name"] == "ArenaNet"


@pytest.mark.asyncio
async def test_fetch_twitch_user_returns_none_for_unknown():
    from axitools.cogs.streaming import _fetch_twitch_user, _TwitchTokenManager
    import aiohttp

    tokens = _TwitchTokenManager("cid", "csecret")
    tokens._token = "tok"

    with aioresponses() as m:
        m.get(
            "https://api.twitch.tv/helix/users?login=doesnotexist",
            payload={"data": []},
        )
        async with aiohttp.ClientSession() as session:
            user = await _fetch_twitch_user(session, tokens, "doesnotexist")

    assert user is None


@pytest.mark.asyncio
async def test_fetch_twitch_stream_returns_stream_data():
    from axitools.cogs.streaming import _fetch_twitch_stream, _TwitchTokenManager
    import aiohttp

    tokens = _TwitchTokenManager("cid", "csecret")
    tokens._token = "tok"

    stream_payload = {
        "data": [{
            "user_login": "arenanet",
            "user_name": "ArenaNet",
            "title": "Playing GW2!",
            "game_name": "Guild Wars 2",
            "viewer_count": 500,
            "started_at": "2026-06-07T12:00:00Z",
            "thumbnail_url": "https://example.com/{width}x{height}.jpg",
        }]
    }

    with aioresponses() as m:
        m.get(
            "https://api.twitch.tv/helix/streams?user_login=arenanet",
            payload=stream_payload,
        )
        async with aiohttp.ClientSession() as session:
            stream = await _fetch_twitch_stream(session, tokens, "arenanet")

    assert stream is not None
    assert stream["title"] == "Playing GW2!"
    assert stream["viewer_count"] == 500


@pytest.mark.asyncio
async def test_fetch_twitch_stream_returns_none_when_offline():
    from axitools.cogs.streaming import _fetch_twitch_stream, _TwitchTokenManager
    import aiohttp

    tokens = _TwitchTokenManager("cid", "csecret")
    tokens._token = "tok"

    with aioresponses() as m:
        m.get(
            "https://api.twitch.tv/helix/streams?user_login=arenanet",
            payload={"data": []},
        )
        async with aiohttp.ClientSession() as session:
            stream = await _fetch_twitch_stream(session, tokens, "arenanet")

    assert stream is None


@pytest.mark.asyncio
async def test_fetch_twitch_stream_refreshes_token_on_401():
    from axitools.cogs.streaming import _fetch_twitch_stream, _TwitchTokenManager
    import aiohttp

    tokens = _TwitchTokenManager("cid", "csecret")
    tokens._token = "expired_token"

    with aioresponses() as m:
        m.get(
            "https://api.twitch.tv/helix/streams?user_login=streamer",
            status=401,
            payload={"error": "Unauthorized"},
        )
        m.post(
            "https://id.twitch.tv/oauth2/token",
            payload={"access_token": "fresh_token"},
        )
        m.get(
            "https://api.twitch.tv/helix/streams?user_login=streamer",
            payload={"data": []},
        )
        async with aiohttp.ClientSession() as session:
            result = await _fetch_twitch_stream(session, tokens, "streamer")

    assert tokens._token == "fresh_token"
    assert result is None


def test_build_twitch_live_embed():
    from axitools.cogs.streaming import _build_twitch_live_embed

    stream = {
        "user_login": "arenanet",
        "user_name": "ArenaNet",
        "title": "Friday night GW2!",
        "game_name": "Guild Wars 2",
        "viewer_count": 1234,
        "thumbnail_url": "https://example.com/{width}x{height}.jpg",
    }
    embed = _build_twitch_live_embed(stream)

    assert "ArenaNet" in embed.title
    assert embed.url == "https://twitch.tv/arenanet"
    assert embed.color.value == 0x9146FF
    assert embed.image.url == "https://example.com/1280x720.jpg"
    field_names = [f.name for f in embed.fields]
    assert any("Guild Wars 2" in f.value for f in embed.fields)
    assert any("1,234" in f.value or "1234" in f.value for f in embed.fields)


# ---------------------------------------------------------------------------
# YouTube channel resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_youtube_channel_from_handle():
    from axitools.cogs.streaming import _resolve_youtube_channel
    import aiohttp

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/channels?part=id%2Csnippet&forHandle=arenanet&key=test_key",
            payload={
                "items": [{
                    "id": "UCvC_LIfovqvkalSolejNlrQ",
                    "snippet": {"title": "ArenaNet"},
                }]
            },
        )
        async with aiohttp.ClientSession() as session:
            result = await _resolve_youtube_channel(session, "@arenanet", "test_key")

    assert result is not None
    channel_id, display_name = result
    assert channel_id == "UCvC_LIfovqvkalSolejNlrQ"
    assert display_name == "ArenaNet"


@pytest.mark.asyncio
async def test_resolve_youtube_channel_from_uc_id():
    from axitools.cogs.streaming import _resolve_youtube_channel
    import aiohttp

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/channels?part=id%2Csnippet&id=UCvC_LIfovqvkalSolejNlrQ&key=test_key",
            payload={
                "items": [{
                    "id": "UCvC_LIfovqvkalSolejNlrQ",
                    "snippet": {"title": "ArenaNet"},
                }]
            },
        )
        async with aiohttp.ClientSession() as session:
            result = await _resolve_youtube_channel(
                session, "UCvC_LIfovqvkalSolejNlrQ", "test_key"
            )

    assert result is not None
    channel_id, display_name = result
    assert channel_id == "UCvC_LIfovqvkalSolejNlrQ"
    assert display_name == "ArenaNet"  # add this line


@pytest.mark.asyncio
async def test_resolve_youtube_channel_from_url():
    from axitools.cogs.streaming import _resolve_youtube_channel
    import aiohttp

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/channels?part=id%2Csnippet&id=UCvC_LIfovqvkalSolejNlrQ&key=test_key",
            payload={
                "items": [{
                    "id": "UCvC_LIfovqvkalSolejNlrQ",
                    "snippet": {"title": "ArenaNet"},
                }]
            },
        )
        async with aiohttp.ClientSession() as session:
            result = await _resolve_youtube_channel(
                session,
                "https://youtube.com/channel/UCvC_LIfovqvkalSolejNlrQ",
                "test_key",
            )

    assert result is not None
    assert result[0] == "UCvC_LIfovqvkalSolejNlrQ"


@pytest.mark.asyncio
async def test_resolve_youtube_channel_returns_none_for_unknown():
    from axitools.cogs.streaming import _resolve_youtube_channel
    import aiohttp

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/channels?part=id%2Csnippet&forHandle=nobody&key=test_key",
            payload={"items": []},
        )
        async with aiohttp.ClientSession() as session:
            result = await _resolve_youtube_channel(session, "@nobody", "test_key")

    assert result is None


# ---------------------------------------------------------------------------
# YouTube RSS + video classification
# ---------------------------------------------------------------------------

YOUTUBE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <id>yt:video:abc123</id>
    <title>My New Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <published>2026-06-07T12:00:00+00:00</published>
    <author><name>ArenaNet</name></author>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_fetch_youtube_rss_returns_entries():
    from axitools.cogs.streaming import _fetch_youtube_rss
    import aiohttp

    with aioresponses() as m:
        m.get(
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCvC",
            body=YOUTUBE_RSS_XML,
            content_type="application/atom+xml",
        )
        async with aiohttp.ClientSession() as session:
            entries = await _fetch_youtube_rss(session, "UCvC")

    assert len(entries) == 1
    assert entries[0]["id"] == "yt:video:abc123"


@pytest.mark.asyncio
async def test_fetch_youtube_video_details_regular():
    from axitools.cogs.streaming import _fetch_youtube_video_details
    import aiohttp

    payload = {
        "items": [{
            "id": "abc123",
            "snippet": {
                "title": "My Video",
                "channelTitle": "ArenaNet",
                "publishedAt": "2026-06-07T12:00:00Z",
                "liveBroadcastContent": "none",
            },
        }]
    }

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/videos?part=snippet%2CliveStreamingDetails&id=abc123&key=testkey",
            payload=payload,
        )
        async with aiohttp.ClientSession() as session:
            details = await _fetch_youtube_video_details(session, "abc123", "testkey")

    assert details is not None
    assert details["snippet"]["title"] == "My Video"
    assert details["snippet"]["liveBroadcastContent"] == "none"


@pytest.mark.asyncio
async def test_fetch_youtube_video_details_live():
    from axitools.cogs.streaming import _fetch_youtube_video_details
    import aiohttp

    payload = {
        "items": [{
            "id": "live456",
            "snippet": {
                "title": "Live Stream!",
                "channelTitle": "ArenaNet",
                "publishedAt": "2026-06-07T12:00:00Z",
                "liveBroadcastContent": "live",
            },
            "liveStreamingDetails": {
                "actualStartTime": "2026-06-07T12:00:00Z",
            },
        }]
    }

    with aioresponses() as m:
        m.get(
            "https://www.googleapis.com/youtube/v3/videos?part=snippet%2CliveStreamingDetails&id=live456&key=testkey",
            payload=payload,
        )
        async with aiohttp.ClientSession() as session:
            details = await _fetch_youtube_video_details(session, "live456", "testkey")

    assert details["snippet"]["liveBroadcastContent"] == "live"
    assert "liveStreamingDetails" in details


def test_build_youtube_live_embed():
    from axitools.cogs.streaming import _build_youtube_live_embed

    details = {
        "id": "live456",
        "snippet": {
            "title": "Live now!",
            "channelTitle": "ArenaNet",
            "liveBroadcastContent": "live",
        },
    }
    embed = _build_youtube_live_embed(details)

    assert "🔴" in embed.title
    assert "ArenaNet" in embed.title
    assert embed.url == "https://youtube.com/watch?v=live456"
    assert embed.color.value == 0xFF0000
    assert "live456" in embed.image.url


def test_build_youtube_video_embed():
    from axitools.cogs.streaming import _build_youtube_video_embed

    details = {
        "id": "abc123",
        "snippet": {
            "title": "New Video!",
            "channelTitle": "ArenaNet",
            "publishedAt": "2026-06-07T12:00:00Z",
            "liveBroadcastContent": "none",
        },
    }
    embed = _build_youtube_video_embed(details, is_vod=False)

    assert "📺" in embed.title
    assert "ArenaNet" in embed.title
    assert "new video" in embed.title.lower()
    assert embed.url == "https://youtube.com/watch?v=abc123"
    assert embed.color.value == 0xFF0000


def test_build_youtube_vod_embed():
    from axitools.cogs.streaming import _build_youtube_video_embed

    details = {
        "id": "vod789",
        "snippet": {
            "title": "Last Night Stream",
            "channelTitle": "ArenaNet",
            "publishedAt": "2026-06-07T12:00:00Z",
            "liveBroadcastContent": "none",
        },
        "liveStreamingDetails": {
            "actualStartTime": "2026-06-07T10:00:00Z",
            "actualEndTime": "2026-06-07T12:00:00Z",
        },
    }
    embed = _build_youtube_video_embed(details, is_vod=True)

    assert "vod" in embed.title.lower() or "VOD" in embed.title


def test_youtube_video_id_from_entry_id():
    from axitools.cogs.streaming import _youtube_video_id

    assert _youtube_video_id("yt:video:abc123") == "abc123"
    assert _youtube_video_id("yt:video:XYZ_-abc") == "XYZ_-abc"
    assert _youtube_video_id("not_a_yt_id") is None


# ---------------------------------------------------------------------------
# /stream add command
# ---------------------------------------------------------------------------

def _make_bot(tmp_path):
    from axitools.storage import StorageManager
    bot = MagicMock()
    bot.storage = StorageManager(tmp_path)
    bot.ensure_authorised = AsyncMock(return_value=True)
    return bot


def _make_interaction(guild_id=123, channel_id=456):
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_stream_add_twitch_saves_subscription(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot
    cog._twitch_tokens = MagicMock()
    cog._twitch_tokens.get_token = AsyncMock(return_value="tok")
    cog._twitch_tokens.auth_headers = MagicMock(return_value={})
    cog._get_session = AsyncMock()

    interaction = _make_interaction()
    discord_channel = MagicMock(spec=discord.TextChannel)
    discord_channel.id = 789

    with aioresponses() as m:
        import aiohttp
        session = aiohttp.ClientSession()
        cog._get_session.return_value = session

        m.get(
            "https://api.twitch.tv/helix/users?login=arenanet",
            payload={"data": [{"login": "arenanet", "display_name": "ArenaNet", "id": "1"}]},
        )
        # Prime: fetch stream (offline at add time)
        m.get(
            "https://api.twitch.tv/helix/streams?user_login=arenanet",
            payload={"data": []},
        )

        with patch("axitools.cogs.streaming.TWITCH_CLIENT_ID", "fake_id"), \
             patch("axitools.cogs.streaming.TWITCH_CLIENT_SECRET", "fake_secret"):
            await cog._stream_add(interaction, "mystream", "twitch", "arenanet", discord_channel)
        await session.close()

    subs = bot.storage.get_stream_subscriptions(123)
    assert len(subs) == 1
    assert subs[0].name == "mystream"
    assert subs[0].platform == "twitch"
    assert subs[0].channel_id == "arenanet"
    assert subs[0].channel_display_name == "ArenaNet"
    assert subs[0].discord_channel_id == 789
    assert subs[0].is_live is False


@pytest.mark.asyncio
async def test_stream_add_twitch_unknown_channel_sends_error(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot
    cog._twitch_tokens = MagicMock()
    cog._twitch_tokens.get_token = AsyncMock(return_value="tok")
    cog._twitch_tokens.auth_headers = MagicMock(return_value={})
    cog._get_session = AsyncMock()

    interaction = _make_interaction()
    discord_channel = MagicMock(spec=discord.TextChannel)
    discord_channel.id = 789

    with aioresponses() as m:
        import aiohttp
        session = aiohttp.ClientSession()
        cog._get_session.return_value = session

        m.get(
            "https://api.twitch.tv/helix/users?login=nobody",
            payload={"data": []},
        )
        with patch("axitools.cogs.streaming.TWITCH_CLIENT_ID", "fake_id"), \
             patch("axitools.cogs.streaming.TWITCH_CLIENT_SECRET", "fake_secret"):
            await cog._stream_add(interaction, "test", "twitch", "nobody", discord_channel)
        await session.close()

    subs = bot.storage.get_stream_subscriptions(123)
    assert len(subs) == 0
    # stream_add defers then uses followup.send for all messages
    interaction.followup.send.assert_called()
    call_text = str(interaction.followup.send.call_args)
    assert "not found" in call_text.lower() or "could not" in call_text.lower()


# ---------------------------------------------------------------------------
# /stream list, remove, update commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_list_shows_subscriptions(tmp_path):
    from axitools.cogs.streaming import StreamingCog
    from axitools.storage import StreamSubscription

    bot = _make_bot(tmp_path)
    bot.storage.upsert_stream_subscription(123, StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=789,
    ))
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog._stream_list(interaction)

    interaction.response.send_message.assert_called_once()
    call_args = interaction.response.send_message.call_args
    embed = call_args.kwargs.get("embed") or (call_args.args[0] if call_args.args else None)
    assert embed is not None or "arenanet" in str(call_args).lower()


@pytest.mark.asyncio
async def test_stream_list_empty_guild(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog._stream_list(interaction)

    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_stream_remove_deletes_subscription(tmp_path):
    from axitools.cogs.streaming import StreamingCog
    from axitools.storage import StreamSubscription

    bot = _make_bot(tmp_path)
    bot.storage.upsert_stream_subscription(123, StreamSubscription(
        name="todelete",
        platform="twitch",
        channel_id="todelete",
        channel_display_name="To Delete",
        discord_channel_id=789,
    ))
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog._stream_remove(interaction, "todelete")

    assert bot.storage.get_stream_subscriptions(123) == []


@pytest.mark.asyncio
async def test_stream_remove_unknown_sends_error(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog._stream_remove(interaction, "doesnotexist")

    interaction.response.send_message.assert_called_once()
    assert "not found" in str(interaction.response.send_message.call_args).lower()


@pytest.mark.asyncio
async def test_stream_update_changes_channel(tmp_path):
    from axitools.cogs.streaming import StreamingCog
    from axitools.storage import StreamSubscription

    bot = _make_bot(tmp_path)
    bot.storage.upsert_stream_subscription(123, StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=789,
        ping_role_id=None,
    ))
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    new_channel = MagicMock(spec=discord.TextChannel)
    new_channel.id = 1111
    await cog._stream_update(interaction, "arenanet", discord_channel=new_channel, ping_role=None)

    updated = bot.storage.find_stream_subscription(123, "arenanet")
    assert updated.discord_channel_id == 1111
    assert updated.channel_id == "arenanet"  # unchanged
    assert updated.is_live is False           # unchanged


@pytest.mark.asyncio
async def test_stream_update_sets_ping_role(tmp_path):
    from axitools.cogs.streaming import StreamingCog
    from axitools.storage import StreamSubscription

    bot = _make_bot(tmp_path)
    bot.storage.upsert_stream_subscription(123, StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=789,
    ))
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    role = MagicMock(spec=discord.Role)
    role.id = 5555
    await cog._stream_update(interaction, "arenanet", discord_channel=None, ping_role=role)

    updated = bot.storage.find_stream_subscription(123, "arenanet")
    assert updated.ping_role_id == 5555
    assert updated.discord_channel_id == 789  # unchanged
