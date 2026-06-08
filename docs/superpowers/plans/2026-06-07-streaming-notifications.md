# Streaming Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/stream` commands so guild admins can subscribe to YouTube channels and Twitch streamers, receiving rich embed notifications when a channel goes live or posts a new video.

**Architecture:** Single new cog (`axitools/cogs/streaming.py`) with a 5-minute polling loop that iterates per-guild subscriptions. YouTube uses its public RSS feed (no quota) plus one `videos.list` call (1 quota unit) per new entry to detect live vs. regular video. Twitch uses the Helix `streams` endpoint with bot-level OAuth2 app credentials. State and subscriptions are persisted in `guild_<id>/stream_subscriptions.json` via a new `StreamSubscription` dataclass and two new `StorageManager` methods.

**Tech Stack:** `discord.py`, `aiohttp`, `feedparser`, YouTube Data API v3, Twitch Helix API

---

## File Map

| Action | File | Purpose |
|---|---|---|
| Modify | `axitools/storage.py` | Add `StreamSubscription` dataclass + `get/save/upsert/delete/find` storage methods |
| Create | `axitools/cogs/streaming.py` | All streaming logic: token manager, HTTP helpers, embeds, polling loop, commands |
| Modify | `axitools/bot.py` | Register `axitools.cogs.streaming` extension |
| Modify | `tests/test_storage.py` | Add storage round-trip tests for `StreamSubscription` |
| Create | `tests/test_cogs_streaming.py` | Unit tests for helpers, polling, and commands |

---

## Task 1: StreamSubscription dataclass + storage methods

**Files:**
- Modify: `axitools/storage.py` (add after `RssFeedConfig` dataclass ~line 414, and after RSS storage methods ~line 1910)
- Modify: `tests/test_storage.py` (append tests)

- [ ] **Step 1: Write the failing storage test**

Append to `tests/test_storage.py`:

```python
def test_stream_subscriptions_round_trip(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 111

    sub = StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=999,
        ping_role_id=None,
        last_vod_id=None,
        last_live_at=None,
        is_live=False,
    )
    storage.save_stream_subscriptions(guild_id, [sub])
    loaded = storage.get_stream_subscriptions(guild_id)

    assert len(loaded) == 1
    assert loaded[0].name == "arenanet"
    assert loaded[0].platform == "twitch"
    assert loaded[0].channel_display_name == "ArenaNet"
    assert loaded[0].is_live is False


def test_stream_subscriptions_upsert(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 222

    sub = StreamSubscription(
        name="mychannel",
        platform="youtube",
        channel_id="UCxxxxxxx",
        channel_display_name="My Channel",
        discord_channel_id=888,
    )
    storage.upsert_stream_subscription(guild_id, sub)
    storage.upsert_stream_subscription(guild_id, StreamSubscription(
        name="mychannel",
        platform="youtube",
        channel_id="UCxxxxxxx",
        channel_display_name="My Channel Updated",
        discord_channel_id=777,
    ))
    loaded = storage.get_stream_subscriptions(guild_id)
    assert len(loaded) == 1
    assert loaded[0].discord_channel_id == 777
    assert loaded[0].channel_display_name == "My Channel Updated"


def test_stream_subscriptions_delete(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 333

    sub = StreamSubscription(
        name="todelete",
        platform="twitch",
        channel_id="todelete",
        channel_display_name="To Delete",
        discord_channel_id=555,
    )
    storage.upsert_stream_subscription(guild_id, sub)
    deleted = storage.delete_stream_subscription(guild_id, "todelete")
    assert deleted is True
    assert storage.get_stream_subscriptions(guild_id) == []

    not_deleted = storage.delete_stream_subscription(guild_id, "todelete")
    assert not_deleted is False


def test_stream_subscriptions_empty(tmp_path):
    from axitools.storage import StorageManager
    storage = StorageManager(tmp_path)
    assert storage.get_stream_subscriptions(99999) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /var/home/mstephens/Documents/GitHub/axitools
python -m pytest tests/test_storage.py::test_stream_subscriptions_round_trip -v --maxWorkers=2
```

Expected: `ImportError` or `AttributeError` — `StreamSubscription` doesn't exist yet.

- [ ] **Step 3: Add StreamSubscription dataclass to storage.py**

In `axitools/storage.py`, after the `RssFeedConfig` dataclass (~line 414), add:

```python
@dataclass
class StreamSubscription:
    """Persisted configuration for a YouTube or Twitch stream subscription."""

    name: str
    platform: str
    channel_id: str
    channel_display_name: str
    discord_channel_id: int
    ping_role_id: Optional[int] = None
    last_vod_id: Optional[str] = None
    last_live_at: Optional[str] = None
    is_live: bool = False
```

- [ ] **Step 4: Add storage methods to StorageManager**

In `axitools/storage.py`, after the `delete_rss_feed` method (~line 1909), add:

```python
    # ------------------------------------------------------------------
    # Stream subscriptions
    # ------------------------------------------------------------------
    def get_stream_subscriptions(self, guild_id: int) -> List[StreamSubscription]:
        path = self._guild_path(guild_id) / "stream_subscriptions.json"
        payload = self._read_json(path, [])
        subs: List[StreamSubscription] = []
        for item in payload:
            try:
                subs.append(StreamSubscription(**item))
            except TypeError:
                continue
        return subs

    def save_stream_subscriptions(self, guild_id: int, subs: List[StreamSubscription]) -> None:
        path = self._guild_path(guild_id) / "stream_subscriptions.json"
        self._write_json(path, [asdict(sub) for sub in subs])

    def upsert_stream_subscription(self, guild_id: int, sub: StreamSubscription) -> None:
        subs = self.get_stream_subscriptions(guild_id)
        updated: List[StreamSubscription] = []
        replaced = False
        for existing in subs:
            if existing.name.lower() == sub.name.lower():
                updated.append(sub)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(sub)
        self.save_stream_subscriptions(guild_id, updated)

    def find_stream_subscription(self, guild_id: int, name: str) -> Optional[StreamSubscription]:
        name_lower = name.lower()
        for sub in self.get_stream_subscriptions(guild_id):
            if sub.name.lower() == name_lower:
                return sub
        return None

    def delete_stream_subscription(self, guild_id: int, name: str) -> bool:
        subs = self.get_stream_subscriptions(guild_id)
        remaining = [s for s in subs if s.name.lower() != name.lower()]
        if len(remaining) == len(subs):
            return False
        self.save_stream_subscriptions(guild_id, remaining)
        return True
```

Also add `StreamSubscription` to the module's `__all__` if it exists, or just ensure it's importable.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_storage.py -v --maxWorkers=2
```

Expected: all storage tests PASS.

- [ ] **Step 6: Commit**

```bash
git add axitools/storage.py tests/test_storage.py
git commit -m "feat: add StreamSubscription dataclass and storage methods"
```

---

## Task 2: Streaming cog skeleton + Twitch token manager

**Files:**
- Create: `axitools/cogs/streaming.py`
- Create: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write the failing token manager test**

Create `tests/test_cogs_streaming.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_cogs_streaming.py -v --maxWorkers=2
```

Expected: `ModuleNotFoundError: No module named 'axitools.cogs.streaming'`

- [ ] **Step 3: Create streaming.py with skeleton and token manager**

Create `axitools/cogs/streaming.py`:

```python
"""YouTube and Twitch stream notification cog."""
from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import List, Optional

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import AxiToolsBot
from ..storage import StreamSubscription

LOGGER = logging.getLogger(__name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

TWITCH_COLOUR = 0x9146FF
YOUTUBE_COLOUR = 0xFF0000

TWITCH_ICON_URL = "https://static.twitchcdn.net/assets/favicon-32-e29e246c157142c1.png"
YOUTUBE_ICON_URL = "https://www.youtube.com/s/desktop/e06e5c1a/img/favicon_32x32.png"


class _TwitchTokenManager:
    """Manages a Twitch OAuth2 app access token with automatic refresh."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None

    async def get_token(self, session: aiohttp.ClientSession) -> str:
        if self._token is None:
            self._token = await self._fetch_token(session)
        return self._token

    async def refresh_token(self, session: aiohttp.ClientSession) -> str:
        self._token = await self._fetch_token(session)
        return self._token

    async def _fetch_token(self, session: aiohttp.ClientSession) -> str:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["access_token"]

    def auth_headers(self, token: str) -> dict:
        return {
            "Client-ID": self._client_id,
            "Authorization": f"Bearer {token}",
        }


class StreamingCog(commands.GroupCog, name="stream"):
    """Notify Discord channels when YouTube channels or Twitch streamers go live."""

    POLL_INTERVAL_MINUTES = 5

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._twitch_tokens = _TwitchTokenManager(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
        self._poll_loop.start()
        super().__init__()

    def cog_unload(self) -> None:  # pragma: no cover
        self._poll_loop.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def _poll_loop(self) -> None:
        if not self.bot.guilds:
            return
        session = await self._get_session()
        for guild in self.bot.guilds:
            try:
                await self._poll_guild(guild, session)
            except Exception:
                LOGGER.exception("Error polling stream subscriptions for guild %s", guild.id)

    @_poll_loop.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _poll_guild(self, guild: discord.Guild, session: aiohttp.ClientSession) -> None:
        subs = self.bot.storage.get_stream_subscriptions(guild.id)
        if not subs:
            return
        updated_subs: List[StreamSubscription] = []
        changed = False
        for sub in subs:
            try:
                if sub.platform == "twitch":
                    new_sub = await self._poll_twitch(guild, sub, session)
                else:
                    new_sub = await self._poll_youtube(guild, sub, session)
            except Exception:
                LOGGER.exception("Error polling subscription '%s' in guild %s", sub.name, guild.id)
                new_sub = sub
            if new_sub is not sub:
                changed = True
            updated_subs.append(new_sub)
        if changed:
            self.bot.storage.save_stream_subscriptions(guild.id, updated_subs)

    async def _poll_twitch(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        return sub  # implemented in Task 3

    async def _poll_youtube(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        return sub  # implemented in Task 5


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(StreamingCog(bot))
```

Note: `commands.GroupCog, name="stream"` is the same pattern used by `RssFeedsCog` — the cog itself becomes the `/stream` group, and `@app_commands.command()` decorated methods automatically become `/stream add`, `/stream list`, etc. No separate `stream_group` object or standalone command functions are needed.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cogs_streaming.py -v --maxWorkers=2
```

Expected: all 4 token manager tests PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add streaming cog skeleton and Twitch token manager"
```

---

## Task 3: Twitch stream checking + embed

**Files:**
- Modify: `axitools/cogs/streaming.py`
- Modify: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write failing Twitch tests**

Append to `tests/test_cogs_streaming.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cogs_streaming.py -k "twitch" -v --maxWorkers=2
```

Expected: `ImportError` — functions not defined yet.

- [ ] **Step 3: Add Twitch helpers to streaming.py**

In `axitools/cogs/streaming.py`, add these functions before the `StreamingCog` class:

```python
async def _fetch_twitch_user(
    session: aiohttp.ClientSession,
    tokens: _TwitchTokenManager,
    login: str,
) -> Optional[dict]:
    token = await tokens.get_token(session)
    async with session.get(
        "https://api.twitch.tv/helix/users",
        params={"login": login},
        headers=tokens.auth_headers(token),
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        items = data.get("data", [])
        return items[0] if items else None


async def _fetch_twitch_stream(
    session: aiohttp.ClientSession,
    tokens: _TwitchTokenManager,
    login: str,
) -> Optional[dict]:
    token = await tokens.get_token(session)
    for attempt in range(2):
        async with session.get(
            "https://api.twitch.tv/helix/streams",
            params={"user_login": login},
            headers=tokens.auth_headers(token),
        ) as resp:
            if resp.status == 401 and attempt == 0:
                token = await tokens.refresh_token(session)
                continue
            resp.raise_for_status()
            data = await resp.json()
            items = data.get("data", [])
            return items[0] if items else None
    return None


def _build_twitch_live_embed(stream: dict) -> discord.Embed:
    login = stream["user_login"]
    display_name = stream["user_name"]
    title = stream["title"]
    game = stream.get("game_name", "Unknown")
    viewers = stream.get("viewer_count", 0)
    thumbnail = stream.get("thumbnail_url", "").replace("{width}", "1280").replace("{height}", "720")

    embed = discord.Embed(
        title=f"🔴 {display_name} is live on Twitch!",
        description=title,
        url=f"https://twitch.tv/{login}",
        color=TWITCH_COLOUR,
    )
    embed.add_field(name="Game", value=game, inline=True)
    embed.add_field(name="Viewers", value=f"{viewers:,}", inline=True)
    if thumbnail:
        embed.set_image(url=thumbnail)
    embed.set_footer(text="Twitch", icon_url=TWITCH_ICON_URL)
    return embed
```

- [ ] **Step 4: Implement `_poll_twitch` in StreamingCog**

Replace the stub `_poll_twitch` method in `StreamingCog`:

```python
    async def _poll_twitch(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        stream = await _fetch_twitch_stream(session, self._twitch_tokens, sub.channel_id)
        is_now_live = stream is not None

        if is_now_live and not sub.is_live:
            channel = guild.get_channel(sub.discord_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                embed = _build_twitch_live_embed(stream)
                content = f"<@&{sub.ping_role_id}>" if sub.ping_role_id else None
                await channel.send(content=content, embed=embed)
            from ..storage import utcnow
            return replace(sub, is_live=True, last_live_at=utcnow())

        if not is_now_live and sub.is_live:
            return replace(sub, is_live=False)

        return sub
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_cogs_streaming.py -k "twitch" -v --maxWorkers=2
```

Expected: all Twitch tests PASS.

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add Twitch stream polling and embed builder"
```

---

## Task 4: YouTube channel resolution

**Files:**
- Modify: `axitools/cogs/streaming.py`
- Modify: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write failing YouTube resolution tests**

Append to `tests/test_cogs_streaming.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cogs_streaming.py -k "resolve_youtube" -v --maxWorkers=2
```

Expected: `ImportError` — function not defined yet.

- [ ] **Step 3: Add `_resolve_youtube_channel` to streaming.py**

In `axitools/cogs/streaming.py`, add after `_build_twitch_live_embed`:

```python
async def _resolve_youtube_channel(
    session: aiohttp.ClientSession,
    channel_input: str,
    api_key: str,
) -> Optional[tuple[str, str]]:
    """Resolve channel input to (channel_id, display_name). Returns None if not found."""
    # Strip protocol/domain
    cleaned = channel_input.strip()
    for prefix in ("https://", "http://", "www.", "youtube.com/", "youtu.be/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]

    # Extract UC... ID from /channel/UCxxx path
    if cleaned.startswith("channel/") or "/channel/" in cleaned:
        part = cleaned.split("channel/")[-1].split("/")[0].split("?")[0]
        if part.startswith("UC"):
            return await _fetch_youtube_channel_by_id(session, part, api_key)

    # Bare UC... ID
    if cleaned.startswith("UC") and "/" not in cleaned and "?" not in cleaned:
        return await _fetch_youtube_channel_by_id(session, cleaned, api_key)

    # Handle: @handle or handle (with or without leading @)
    handle = cleaned.lstrip("@").split("?")[0].split("/")[0]
    return await _fetch_youtube_channel_by_handle(session, handle, api_key)


async def _fetch_youtube_channel_by_id(
    session: aiohttp.ClientSession, channel_id: str, api_key: str
) -> Optional[tuple[str, str]]:
    async with session.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "id,snippet", "id": channel_id, "key": api_key},
    ) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        return item["id"], item["snippet"]["title"]


async def _fetch_youtube_channel_by_handle(
    session: aiohttp.ClientSession, handle: str, api_key: str
) -> Optional[tuple[str, str]]:
    async with session.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "id,snippet", "forHandle": handle, "key": api_key},
    ) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        return item["id"], item["snippet"]["title"]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_streaming.py -k "resolve_youtube" -v --maxWorkers=2
```

Expected: all 4 YouTube resolution tests PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add YouTube channel resolution with handle and UC ID support"
```

---

## Task 5: YouTube RSS polling, video classification, and embeds

**Files:**
- Modify: `axitools/cogs/streaming.py`
- Modify: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write failing YouTube polling tests**

Append to `tests/test_cogs_streaming.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cogs_streaming.py -k "youtube" -v --maxWorkers=2
```

Expected: `ImportError` — functions not defined yet.

- [ ] **Step 3: Add YouTube helpers and embed builders to streaming.py**

In `axitools/cogs/streaming.py`, add after `_fetch_youtube_channel_by_handle`:

```python
def _youtube_video_id(entry_id: str) -> Optional[str]:
    if entry_id.startswith("yt:video:"):
        return entry_id[len("yt:video:"):]
    return None


async def _fetch_youtube_rss(
    session: aiohttp.ClientSession, channel_id: str
) -> List[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    async with session.get(url) as resp:
        if resp.status != 200:
            return []
        text = await resp.text()
    parsed = feedparser.parse(text)
    return list(parsed.entries)


async def _fetch_youtube_video_details(
    session: aiohttp.ClientSession, video_id: str, api_key: str
) -> Optional[dict]:
    async with session.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,liveStreamingDetails", "id": video_id, "key": api_key},
    ) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        items = data.get("items", [])
        return items[0] if items else None


def _build_youtube_live_embed(details: dict) -> discord.Embed:
    video_id = details["id"]
    snippet = details["snippet"]
    title = snippet["title"]
    channel_name = snippet["channelTitle"]

    embed = discord.Embed(
        title=f"🔴 {channel_name} is live on YouTube!",
        description=title,
        url=f"https://youtube.com/watch?v={video_id}",
        color=YOUTUBE_COLOUR,
    )
    embed.set_image(url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")
    embed.set_footer(text="YouTube", icon_url=YOUTUBE_ICON_URL)
    return embed


def _build_youtube_video_embed(details: dict, *, is_vod: bool = False) -> discord.Embed:
    video_id = details["id"]
    snippet = details["snippet"]
    title = snippet["title"]
    channel_name = snippet["channelTitle"]
    published_at = snippet.get("publishedAt", "")

    label = "posted a new VOD" if is_vod else "posted a new video"
    embed = discord.Embed(
        title=f"📺 {channel_name} {label}",
        description=f"[{title}](https://youtube.com/watch?v={video_id})",
        url=f"https://youtube.com/watch?v={video_id}",
        color=YOUTUBE_COLOUR,
    )
    if published_at:
        embed.add_field(name="Published", value=published_at[:10], inline=True)
    embed.set_image(url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")
    embed.set_footer(text="YouTube", icon_url=YOUTUBE_ICON_URL)
    return embed
```

- [ ] **Step 4: Implement `_poll_youtube` in StreamingCog**

Replace the stub `_poll_youtube` method in `StreamingCog`:

```python
    async def _poll_youtube(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        from ..storage import utcnow

        # Check if a tracked live stream has ended
        if sub.is_live and sub.last_vod_id:
            video_id = _youtube_video_id(sub.last_vod_id)
            if video_id and YOUTUBE_API_KEY:
                details = await _fetch_youtube_video_details(session, video_id, YOUTUBE_API_KEY)
                if details:
                    broadcast_content = details["snippet"].get("liveBroadcastContent", "none")
                    ended = details.get("liveStreamingDetails", {}).get("actualEndTime")
                    if broadcast_content != "live" and ended:
                        channel = guild.get_channel(sub.discord_channel_id)
                        if channel and isinstance(channel, discord.TextChannel):
                            embed = _build_youtube_video_embed(details, is_vod=True)
                            content = f"<@&{sub.ping_role_id}>" if sub.ping_role_id else None
                            await channel.send(content=content, embed=embed)
                        sub = replace(sub, is_live=False)

        # Check RSS for new entries
        entries = await _fetch_youtube_rss(session, sub.channel_id)
        if not entries:
            return sub

        latest_entry = entries[0]
        latest_id = latest_entry.get("id")
        if not latest_id or latest_id == sub.last_vod_id:
            return sub

        video_id = _youtube_video_id(latest_id)
        if not video_id:
            return replace(sub, last_vod_id=latest_id)

        details = None
        if YOUTUBE_API_KEY:
            details = await _fetch_youtube_video_details(session, video_id, YOUTUBE_API_KEY)

        channel = guild.get_channel(sub.discord_channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            is_live = details and details["snippet"].get("liveBroadcastContent") == "live" if details else False
            embed = _build_youtube_live_embed(details) if is_live else _build_youtube_video_embed(
                details or {"id": video_id, "snippet": {"title": latest_entry.get("title", ""), "channelTitle": sub.channel_display_name, "publishedAt": ""}},
                is_vod=False,
            )
            content = f"<@&{sub.ping_role_id}>" if sub.ping_role_id else None
            await channel.send(content=content, embed=embed)
            return replace(sub, last_vod_id=latest_id, is_live=bool(is_live))

        return replace(sub, last_vod_id=latest_id)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_cogs_streaming.py -k "youtube" -v --maxWorkers=2
```

Expected: all YouTube tests PASS.

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add YouTube RSS polling, video classification, and embed builders"
```

---

## Task 6: /stream add command

**Files:**
- Modify: `axitools/cogs/streaming.py`
- Modify: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write failing command tests**

Append to `tests/test_cogs_streaming.py`:

```python
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
        await cog._stream_add(interaction, "test", "twitch", "nobody", discord_channel)
        await session.close()

    subs = bot.storage.get_stream_subscriptions(123)
    assert len(subs) == 0
    # stream_add defers then uses followup.send for all messages
    interaction.followup.send.assert_called()
    call_text = str(interaction.followup.send.call_args)
    assert "not found" in call_text.lower() or "could not" in call_text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cogs_streaming.py -k "stream_add" -v --maxWorkers=2
```

Expected: `AttributeError` — `stream_add` method not defined.

- [ ] **Step 3: Add `/stream add` command to streaming.py**

In `axitools/cogs/streaming.py`, add to `StreamingCog` (before the closing of the class). The `@app_commands.command()` decorator makes this appear as `/stream add` automatically because the cog inherits from `commands.GroupCog, name="stream"`:

```python
    @app_commands.command(name="add", description="Subscribe to a YouTube channel or Twitch streamer")
    @app_commands.describe(
        name="A short label for this subscription (e.g. arenanet)",
        platform="The streaming platform",
        channel="Channel name, @handle, or URL",
        discord_channel="The Discord channel to post notifications in",
    )
    @app_commands.choices(platform=[
        app_commands.Choice(name="Twitch", value="twitch"),
        app_commands.Choice(name="YouTube", value="youtube"),
    ])
    async def stream_add(
        self,
        interaction: discord.Interaction,
        name: str,
        platform: app_commands.Choice[str],
        channel: str,
        discord_channel: discord.TextChannel,
    ) -> None:
        await self._stream_add(interaction, name, platform.value, channel, discord_channel)

    async def _stream_add(
        self,
        interaction: discord.Interaction,
        name: str,
        platform: str,
        channel: str,
        discord_channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        session = await self._get_session()

        existing = self.bot.storage.find_stream_subscription(interaction.guild.id, name)
        if existing:
            await interaction.followup.send(
                f"A subscription named **{name}** already exists. Use `/stream update` to modify it.",
                ephemeral=True,
            )
            return

        if platform == "twitch":
            if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
                await interaction.followup.send(
                    "Twitch credentials are not configured on this bot.", ephemeral=True
                )
                return
            user = await _fetch_twitch_user(session, self._twitch_tokens, channel.strip())
            if not user:
                await interaction.followup.send(
                    f"Could not find Twitch channel **{channel}**. Check the channel name and try again.",
                    ephemeral=True,
                )
                return
            login = user["login"]
            display_name = user["display_name"]
            # Prime: check current live state so we don't immediately notify
            stream = await _fetch_twitch_stream(session, self._twitch_tokens, login)
            is_live = stream is not None
            sub = StreamSubscription(
                name=name,
                platform="twitch",
                channel_id=login,
                channel_display_name=display_name,
                discord_channel_id=discord_channel.id,
                is_live=is_live,
            )

        elif platform == "youtube":
            if not YOUTUBE_API_KEY:
                await interaction.followup.send(
                    "YouTube API key is not configured on this bot.", ephemeral=True
                )
                return
            result = await _resolve_youtube_channel(session, channel.strip(), YOUTUBE_API_KEY)
            if not result:
                await interaction.followup.send(
                    f"Could not find YouTube channel **{channel}**. Provide a `@handle`, channel URL, or `UC...` channel ID.",
                    ephemeral=True,
                )
                return
            channel_id, display_name = result
            # Prime: get latest video ID so we don't post old content
            entries = await _fetch_youtube_rss(session, channel_id)
            last_vod_id = entries[0].get("id") if entries else None
            sub = StreamSubscription(
                name=name,
                platform="youtube",
                channel_id=channel_id,
                channel_display_name=display_name,
                discord_channel_id=discord_channel.id,
                last_vod_id=last_vod_id,
                is_live=False,
            )
        else:
            await interaction.followup.send(f"Unknown platform: {platform}", ephemeral=True)
            return

        self.bot.storage.upsert_stream_subscription(interaction.guild.id, sub)
        platform_label = "Twitch" if platform == "twitch" else "YouTube"
        await interaction.followup.send(
            f"✓ Subscribed to **{display_name}** on {platform_label}. Notifications will post in {discord_channel.mention}.",
            ephemeral=True,
        )
```

No standalone command function needed — the `@app_commands.command(name="add")` decorator on the cog method handles registration automatically via `commands.GroupCog`.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_streaming.py -k "stream_add" -v --maxWorkers=2
```

Expected: all `/stream add` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add /stream add command with channel validation and priming"
```

---

## Task 7: /stream list, remove, update commands

**Files:**
- Modify: `axitools/cogs/streaming.py`
- Modify: `tests/test_cogs_streaming.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cogs_streaming.py`:

```python
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
    await cog.stream_list(interaction)

    interaction.response.send_message.assert_called_once()
    call_args = interaction.response.send_message.call_args
    # Message should include an embed
    embed = call_args.kwargs.get("embed") or (call_args.args[0] if call_args.args else None)
    assert embed is not None or "arenanet" in str(call_args).lower()


@pytest.mark.asyncio
async def test_stream_list_empty_guild(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog.stream_list(interaction)

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
    await cog.stream_remove(interaction, "todelete")

    assert bot.storage.get_stream_subscriptions(123) == []


@pytest.mark.asyncio
async def test_stream_remove_unknown_sends_error(tmp_path):
    from axitools.cogs.streaming import StreamingCog

    bot = _make_bot(tmp_path)
    cog = StreamingCog.__new__(StreamingCog)
    cog.bot = bot

    interaction = _make_interaction()
    await cog.stream_remove(interaction, "doesnotexist")

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
    await cog.stream_update(interaction, "arenanet", discord_channel=new_channel, ping_role=None)

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
    await cog.stream_update(interaction, "arenanet", discord_channel=None, ping_role=role)

    updated = bot.storage.find_stream_subscription(123, "arenanet")
    assert updated.ping_role_id == 5555
    assert updated.discord_channel_id == 789  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cogs_streaming.py -k "stream_list or stream_remove or stream_update" -v --maxWorkers=2
```

Expected: `AttributeError` — methods not defined.

- [ ] **Step 3: Add list, remove, update commands to StreamingCog**

Add these decorated methods to `StreamingCog` in `axitools/cogs/streaming.py` (before the class closing). Because the cog inherits from `commands.GroupCog, name="stream"`, the `@app_commands.command()` decorator automatically registers these as `/stream list`, `/stream remove`, and `/stream update`:

```python
    @app_commands.command(name="list", description="List all stream subscriptions for this server")
    async def stream_list(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        subs = self.bot.storage.get_stream_subscriptions(interaction.guild.id)
        if not subs:
            await interaction.response.send_message(
                "No stream subscriptions configured. Use `/stream add` to add one.", ephemeral=True
            )
            return
        embed = discord.Embed(title="Stream Subscriptions", color=0x5865F2)
        for sub in subs:
            platform_label = "Twitch 🟣" if sub.platform == "twitch" else "YouTube 🔴"
            channel_mention = f"<#{sub.discord_channel_id}>"
            ping = f" | Ping: <@&{sub.ping_role_id}>" if sub.ping_role_id else ""
            status = " | 🔴 Live" if sub.is_live else ""
            embed.add_field(
                name=f"{sub.name} ({platform_label})",
                value=f"{sub.channel_display_name} → {channel_mention}{ping}{status}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a stream subscription")
    @app_commands.describe(name="The subscription name to remove")
    async def stream_remove(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        deleted = self.bot.storage.delete_stream_subscription(interaction.guild.id, name)
        if not deleted:
            await interaction.response.send_message(
                f"No subscription named **{name}** found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✓ Removed subscription **{name}**.", ephemeral=True
        )

    @app_commands.command(name="update", description="Update the channel or ping role for a subscription")
    @app_commands.describe(
        name="The subscription name to update",
        discord_channel="New Discord channel for notifications (optional)",
        ping_role="Role to ping on new notifications (optional)",
    )
    async def stream_update(
        self,
        interaction: discord.Interaction,
        name: str,
        discord_channel: Optional[discord.TextChannel] = None,
        ping_role: Optional[discord.Role] = None,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        sub = self.bot.storage.find_stream_subscription(interaction.guild.id, name)
        if not sub:
            await interaction.response.send_message(
                f"No subscription named **{name}** found.", ephemeral=True
            )
            return
        updated = replace(
            sub,
            discord_channel_id=discord_channel.id if discord_channel else sub.discord_channel_id,
            ping_role_id=ping_role.id if ping_role else sub.ping_role_id,
        )
        self.bot.storage.upsert_stream_subscription(interaction.guild.id, updated)
        await interaction.response.send_message(
            f"✓ Updated subscription **{name}**.", ephemeral=True
        )
```

No standalone command functions needed — `commands.GroupCog` handles registration for all `@app_commands.command()` decorated methods.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_streaming.py -v --maxWorkers=2
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/streaming.py tests/test_cogs_streaming.py
git commit -m "feat: add /stream list, remove, and update commands"
```

---

## Task 8: Register cog in bot.py + smoke test

**Files:**
- Modify: `axitools/bot.py`

- [ ] **Step 1: Add the cog to bot.py**

In `axitools/bot.py`, inside `setup_hook`, add after the last `load_extension` call:

```python
        await self.load_extension("axitools.cogs.streaming")
```

- [ ] **Step 2: Run the full test suite**

```bash
python -m pytest tests/ -v --maxWorkers=2
```

Expected: all tests PASS. The existing bot sanity test (`test_bot_sanity.py`) imports the bot and should still pass.

- [ ] **Step 3: Verify the cog loads at import time**

```bash
python -c "from axitools.cogs.streaming import StreamingCog, _TwitchTokenManager; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add axitools/bot.py
git commit -m "feat: register streaming cog in bot setup"
```

---

## Environment Variables to Configure

Before running in production, set:

| Variable | Where to get it |
|---|---|
| `TWITCH_CLIENT_ID` | dev.twitch.tv → Your Applications → Client ID |
| `TWITCH_CLIENT_SECRET` | dev.twitch.tv → Your Applications → New Secret |
| `YOUTUBE_API_KEY` | console.cloud.google.com → APIs & Services → Credentials → API Key (enable YouTube Data API v3) |

Add these to your `.env` file (same place as `DISCORD_TOKEN`).
