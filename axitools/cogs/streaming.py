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
            data={
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


class StreamingCog(commands.GroupCog, name="stream"):
    """Notify Discord channels when YouTube channels or Twitch streamers go live."""

    POLL_INTERVAL_MINUTES = 5

    def __init__(self, bot: AxiToolsBot) -> None:
        super().__init__()
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._twitch_tokens = _TwitchTokenManager(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
        self._poll_loop.start()

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_loop.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

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
    async def _before_poll(self) -> None:  # pragma: no cover - discord.py lifecycle
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

    async def _poll_youtube(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        return sub  # implemented in Task 5


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(StreamingCog(bot))
