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
