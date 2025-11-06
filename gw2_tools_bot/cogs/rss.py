"""RSS feed subscription and notification cog."""
from __future__ import annotations

import calendar
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from html import unescape
from typing import Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..storage import RssFeedConfig

LOGGER = logging.getLogger(__name__)


SUMMARY_REGEX = re.compile(r"<[^>]+>")


def _clean_summary(summary: str, *, max_length: int = 400) -> str:
    """Return a sanitised version of an RSS entry summary."""

    text = SUMMARY_REGEX.sub("", summary)
    text = unescape(text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _entry_identifier(entry: feedparser.FeedParserDict) -> Optional[str]:
    for key in ("id", "guid", "link", "title"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


def _resolve_new_entries(
    entries: Sequence[feedparser.FeedParserDict],
    last_entry_id: Optional[str],
) -> List[Tuple[str, feedparser.FeedParserDict]]:
    """Return new entries ordered from oldest to newest."""

    collected: List[Tuple[str, feedparser.FeedParserDict]] = []
    for entry in reversed(entries):
        entry_id = _entry_identifier(entry)
        if not entry_id:
            continue
        if last_entry_id and entry_id == last_entry_id:
            collected.clear()
            continue
        collected.append((entry_id, entry))
    return collected


def _resolve_feed_icon(parsed_feed: feedparser.FeedParserDict) -> Optional[str]:
    feed = getattr(parsed_feed, "feed", {}) or {}
    icon_candidates: Sequence[Optional[str]] = (
        feed.get("icon"),
        feed.get("logo"),
        (feed.get("image") or {}).get("href") if isinstance(feed.get("image"), dict) else None,
        (feed.get("image") or {}).get("url") if isinstance(feed.get("image"), dict) else None,
    )
    for candidate in icon_candidates:
        if candidate:
            return str(candidate)
    return None


def _extract_entry_description(entry: feedparser.FeedParserDict, *, max_length: int = 1800) -> Optional[str]:
    contents = entry.get("content")
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if value:
                return _clean_summary(str(value), max_length=max_length)

    summary = entry.get("summary") or entry.get("description")
    if summary:
        return _clean_summary(str(summary), max_length=max_length)
    return None


def _extract_entry_thumbnail(entry: feedparser.FeedParserDict) -> Optional[str]:
    media_thumbnail = entry.get("media_thumbnail")
    if isinstance(media_thumbnail, list):
        for thumb in media_thumbnail:
            if isinstance(thumb, dict):
                href = thumb.get("url") or thumb.get("href")
                if href:
                    return str(href)
    media_content = entry.get("media_content")
    if isinstance(media_content, list):
        for item in media_content:
            if isinstance(item, dict):
                url = item.get("url")
                if url:
                    return str(url)
    image = entry.get("image")
    if isinstance(image, dict):
        for key in ("href", "url"):
            value = image.get(key)
            if value:
                return str(value)
    return None


def _convert_struct_time(struct_time: Optional[Tuple[int, ...]]) -> Optional[datetime]:
    if not struct_time:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


class RssFeedsCog(commands.GroupCog, name="rss"):
    """Manage RSS feed subscriptions and push updates to Discord channels."""

    CHECK_INTERVAL_MINUTES = 10
    EMBED_COLOR = discord.Color.blurple()

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._feed_poll.start()

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._feed_poll.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _fetch_feed(self, url: str) -> Optional[feedparser.FeedParserDict]:
        session = await self._get_session()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()
                payload = await response.read()
        except aiohttp.ClientError:
            LOGGER.warning("Failed to fetch RSS feed %s", url, exc_info=True)
            return None

        parsed = feedparser.parse(payload)
        if parsed.bozo:
            LOGGER.warning("Parsing RSS feed %s resulted in bozo exception: %s", url, parsed.bozo_exception)
        return parsed

    async def _prime_feed(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        parsed = await self._fetch_feed(url)
        if not parsed or not parsed.entries:
            return None, None
        latest_entry = parsed.entries[0]
        entry_id = _entry_identifier(latest_entry)
        published = latest_entry.get("published") or latest_entry.get("updated")
        return entry_id, published

    async def _resolve_channel(self, guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel is not None:
            return None
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    def _build_entry_embed(
        self,
        feed_config: RssFeedConfig,
        entry: feedparser.FeedParserDict,
        parsed_feed: feedparser.FeedParserDict,
    ) -> discord.Embed:
        feed = getattr(parsed_feed, "feed", {}) or {}
        feed_title = feed.get("title") or feed_config.name
        feed_link = feed.get("link") or feed_config.url
        feed_icon = _resolve_feed_icon(parsed_feed)

        entry_title = entry.get("title") or "New update"
        entry_link = entry.get("link") or feed_config.url

        embed = discord.Embed(title=entry_title, url=entry_link, color=self.EMBED_COLOR)

        description = _extract_entry_description(entry)
        if description:
            embed.description = description

        author_kwargs = {"name": feed_title}
        if feed_link:
            author_kwargs["url"] = feed_link
        if feed_icon:
            author_kwargs["icon_url"] = feed_icon
        embed.set_author(**author_kwargs)

        thumbnail_url = _extract_entry_thumbnail(entry)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        published_text = entry.get("published") or entry.get("updated")
        published_at = _convert_struct_time(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        if published_at:
            embed.timestamp = published_at
        if published_text:
            embed.add_field(name="Published", value=published_text, inline=False)

        author_name = entry.get("author")
        if author_name:
            embed.add_field(name="Author", value=str(author_name), inline=True)

        tags = entry.get("tags")
        if isinstance(tags, list):
            tag_names = [
                tag.get("label") or tag.get("term")
                for tag in tags
                if isinstance(tag, dict) and (tag.get("label") or tag.get("term"))
            ]
            if tag_names:
                joined = ", ".join(tag_names)
                embed.add_field(name="Tags", value=joined[:1024], inline=True)

        embed.set_footer(text=f"RSS feed: {feed_config.name}")
        return embed

    def _build_feed_list_embeds(
        self, guild: discord.Guild, feeds: Sequence[RssFeedConfig]
    ) -> List[discord.Embed]:
        embeds: List[discord.Embed] = []
        page = 1

        def new_embed(page_number: int) -> discord.Embed:
            title = "Configured RSS feeds" if page_number == 1 else f"Configured RSS feeds (page {page_number})"
            return discord.Embed(title=title, color=self.EMBED_COLOR)

        current_embed = new_embed(page)
        current_length = len(current_embed.title or "")
        field_count = 0

        def append_embed() -> None:
            nonlocal current_embed, current_length, field_count, page
            if field_count == 0:
                return
            current_embed.set_footer(text=f"Total feeds: {len(feeds)}")
            embeds.append(current_embed)
            page += 1
            current_embed = new_embed(page)
            current_length = len(current_embed.title or "")
            field_count = 0

        for feed in feeds:
            channel = guild.get_channel(feed.channel_id)
            if isinstance(channel, discord.TextChannel):
                channel_display = channel.mention
            else:
                channel_display = f"<#{feed.channel_id}>"

            field_name = feed.name
            field_value_parts = [f"[Open feed]({feed.url})", f"Channel: {channel_display}"]
            if feed.last_entry_published_at:
                field_value_parts.append(f"Last post: {feed.last_entry_published_at}")
            field_value = "\n".join(field_value_parts)
            if len(field_value) > 1024:
                field_value = field_value[:1021] + "…"

            projected_length = current_length + len(field_name) + len(field_value)
            if field_count >= 25 or projected_length > 5500:
                append_embed()

            current_embed.add_field(name=field_name, value=field_value, inline=False)
            current_length += len(field_name) + len(field_value)
            field_count += 1

        append_embed()
        if not embeds:
            current_embed.set_footer(text=f"Total feeds: {len(feeds)}")
            embeds.append(current_embed)
        return embeds

    async def _post_entries(
        self,
        guild: discord.Guild,
        feed_config: RssFeedConfig,
        entries: Iterable[Tuple[str, feedparser.FeedParserDict]],
        parsed_feed: feedparser.FeedParserDict,
    ) -> Optional[RssFeedConfig]:
        channel = await self._resolve_channel(guild, feed_config.channel_id)
        if not channel:
            LOGGER.warning(
                "Configured RSS channel %s for guild %s is not accessible", feed_config.channel_id, guild.id
            )
            return None

        last_processed: Optional[Tuple[str, Optional[str]]] = None

        for entry_id, entry in entries:
            entry_title = entry.get("title") or "New update"
            embed = self._build_entry_embed(feed_config, entry, parsed_feed)
            published_text = entry.get("published") or entry.get("updated")

            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post RSS entry '%s' to channel %s in guild %s",
                    entry_title,
                    feed_config.channel_id,
                    guild.id,
                )
                return None

            last_processed = (entry_id, published_text)

        if not last_processed:
            return None

        entry_id, published_at = last_processed
        return replace(feed_config, last_entry_id=entry_id, last_entry_published_at=published_at)

    async def _process_feed(self, guild: discord.Guild, feed_config: RssFeedConfig) -> Optional[RssFeedConfig]:
        parsed = await self._fetch_feed(feed_config.url)
        if not parsed or not parsed.entries:
            return None

        new_entries = _resolve_new_entries(parsed.entries, feed_config.last_entry_id)
        if not new_entries:
            return None

        return await self._post_entries(guild, feed_config, new_entries, parsed)

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _feed_poll(self) -> None:
        if not self.bot.guilds:
            return

        for guild in self.bot.guilds:
            feeds = self.bot.storage.get_rss_feeds(guild.id)
            if not feeds:
                continue

            updated = False
            current_feeds = list(feeds)
            for index, feed_config in enumerate(current_feeds):
                try:
                    updated_feed = await self._process_feed(guild, feed_config)
                except Exception:  # pragma: no cover - defensive logging
                    LOGGER.exception(
                        "Unexpected error while polling RSS feed '%s' for guild %s",
                        feed_config.name,
                        guild.id,
                    )
                    continue

                if updated_feed:
                    current_feeds[index] = updated_feed
                    updated = True

            if updated:
                self.bot.storage.save_rss_feeds(guild.id, current_feeds)

    @_feed_poll.before_loop
    async def _before_poll(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    @app_commands.command(name="list", description="List configured RSS feeds.")
    async def list_feeds(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        feeds = self.bot.storage.get_rss_feeds(interaction.guild.id)
        if not feeds:
            await interaction.response.send_message("No RSS feeds are configured for this server.", ephemeral=True)
            return
        embeds = self._build_feed_list_embeds(interaction.guild, feeds)
        await interaction.response.send_message(embed=embeds[0], ephemeral=True)
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set", description="Create or update an RSS feed subscription.")
    @app_commands.describe(
        name="Unique name for the feed.",
        url="URL to the RSS or Atom feed.",
        channel="Channel where updates should be posted.",
    )
    async def set_feed(
        self,
        interaction: discord.Interaction,
        name: str,
        url: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        guild = interaction.guild
        assert guild is not None  # for type checkers

        existing = self.bot.storage.find_rss_feed(guild.id, name)
        baseline_entry_id: Optional[str] = None
        baseline_published: Optional[str] = None

        try:
            baseline_entry_id, baseline_published = await self._prime_feed(url)
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Unexpected error priming RSS feed %s", url)
            await interaction.response.send_message(
                "Failed to validate the RSS feed. Please try again later.", ephemeral=True
            )
            return

        if existing:
            updated_feed = replace(
                existing,
                url=url,
                channel_id=channel.id,
                last_entry_id=baseline_entry_id or existing.last_entry_id,
                last_entry_published_at=baseline_published or existing.last_entry_published_at,
            )
            self.bot.storage.upsert_rss_feed(guild.id, updated_feed)
            message = f"RSS feed **{name}** updated to post in {channel.mention}."
        else:
            new_feed = RssFeedConfig(
                name=name,
                url=url,
                channel_id=channel.id,
                last_entry_id=baseline_entry_id,
                last_entry_published_at=baseline_published,
            )
            self.bot.storage.upsert_rss_feed(guild.id, new_feed)
            message = (
                f"RSS feed **{name}** added and will post new updates in {channel.mention}."
            )

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="delete", description="Delete an RSS feed subscription.")
    @app_commands.describe(name="Name of the feed to delete.")
    async def delete_feed(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        removed = self.bot.storage.delete_rss_feed(guild.id, name)
        if not removed:
            await interaction.response.send_message(
                f"RSS feed **{name}** was not found in this server's configuration.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"RSS feed **{name}** has been removed.", ephemeral=True
        )

    @app_commands.command(name="test", description="Post the latest entry from an RSS feed to its configured channel.")
    @app_commands.describe(name="Name of the feed to test.")
    async def test_feed(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        feed_config = self.bot.storage.find_rss_feed(guild.id, name)
        if not feed_config:
            await interaction.response.send_message(
                f"RSS feed **{name}** is not configured for this server.", ephemeral=True
            )
            return

        channel = await self._resolve_channel(guild, feed_config.channel_id)
        if not channel:
            await interaction.response.send_message(
                "The configured channel for this feed is not accessible. Please update the feed first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        parsed = await self._fetch_feed(feed_config.url)
        if not parsed or not parsed.entries:
            await interaction.followup.send(
                "Unable to retrieve any entries from the RSS feed at this time.", ephemeral=True
            )
            return

        entry = parsed.entries[0]
        embed = self._build_entry_embed(feed_config, entry, parsed)

        try:
            message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Failed to send manual RSS test entry for '%s' to channel %s in guild %s",
                feed_config.name,
                feed_config.channel_id,
                guild.id,
            )
            await interaction.followup.send(
                "Failed to post the test entry to the configured channel. Please check my permissions and try again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                f"Posted the latest entry from **{feed_config.name}** to {channel.mention}."
                f" [View message]({message.jump_url})"
            ),
            ephemeral=True,
        )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(RssFeedsCog(bot))

