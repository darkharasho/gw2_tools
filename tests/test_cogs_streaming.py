"""Tests for the streaming notifications cog."""
from __future__ import annotations

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
