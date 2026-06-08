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
from ..storage import StreamSubscription, utcnow

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


async def _resolve_youtube_channel(
    session: aiohttp.ClientSession,
    channel_input: str,
    api_key: str,
) -> Optional[tuple[str, str]]:
    """Resolve channel input to (channel_id, display_name). Returns None if not found."""
    # Strip protocol/domain
    cleaned = channel_input.strip()
    for prefix in ("https://", "http://", "www.", "m.youtube.com/", "youtube.com/", "youtu.be/"):
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
            return replace(sub, is_live=True, last_live_at=utcnow())

        if not is_now_live and sub.is_live:
            return replace(sub, is_live=False)

        return sub

    async def _poll_youtube(
        self, guild: discord.Guild, sub: StreamSubscription, session: aiohttp.ClientSession
    ) -> StreamSubscription:
        # Check if a tracked live stream has ended
        if sub.is_live and sub.last_vod_id:
            video_id = _youtube_video_id(sub.last_vod_id)
            if not YOUTUBE_API_KEY:
                LOGGER.warning(
                    "YouTube subscription '%s' in guild %s is marked live but YOUTUBE_API_KEY is not set; "
                    "cannot detect stream end",
                    sub.name,
                    getattr(sub, "channel_id", "?"),
                )
            elif video_id:
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
            is_live = bool(details and details["snippet"].get("liveBroadcastContent") == "live")
            embed = _build_youtube_live_embed(details) if is_live else _build_youtube_video_embed(
                details or {"id": video_id, "snippet": {"title": latest_entry.get("title", ""), "channelTitle": sub.channel_display_name, "publishedAt": ""}},
                is_vod=False,
            )
            content = f"<@&{sub.ping_role_id}>" if sub.ping_role_id else None
            await channel.send(content=content, embed=embed)
            return replace(sub, last_vod_id=latest_id, is_live=bool(is_live))

        return replace(sub, last_vod_id=latest_id)

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

    @app_commands.command(name="list", description="List all stream subscriptions for this server")
    async def stream_list(self, interaction: discord.Interaction) -> None:
        await self._stream_list(interaction)

    async def _stream_list(self, interaction: discord.Interaction) -> None:
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
        await self._stream_remove(interaction, name)

    async def _stream_remove(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        deleted = self.bot.storage.delete_stream_subscription(interaction.guild.id, name)
        if not deleted:
            await interaction.response.send_message(
                f"Subscription **{name}** not found.", ephemeral=True
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
        await self._stream_update(interaction, name, discord_channel=discord_channel, ping_role=ping_role)

    async def _stream_update(
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


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(StreamingCog(bot))
