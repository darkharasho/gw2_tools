"""RSS feed subscription and notification cog."""
from __future__ import annotations

import logging
import re
from dataclasses import replace
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


class RssFeedsCog(commands.GroupCog, name="rss"):
    """Manage RSS feed subscriptions and push updates to Discord channels."""

    CHECK_INTERVAL_MINUTES = 10

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

        feed_title = parsed_feed.feed.get("title", feed_config.name)
        last_processed: Optional[Tuple[str, Optional[str]]] = None

        for entry_id, entry in entries:
            title = entry.get("title") or "New update"
            link = entry.get("link")
            summary = entry.get("summary") or entry.get("description") or ""
            cleaned_summary = _clean_summary(summary) if summary else None

            embed = discord.Embed(title=title, url=link, color=discord.Color.blurple())
            embed.set_author(name=feed_title)
            if cleaned_summary:
                embed.description = cleaned_summary

            published_text = entry.get("published") or entry.get("updated")
            if published_text:
                embed.add_field(name="Published", value=published_text, inline=False)

            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post RSS entry '%s' to channel %s in guild %s",
                    title,
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

        lines = [f"**{feed.name}** → <#{feed.channel_id}>" for feed in feeds]
        message = "Configured RSS feeds:\n" + "\n".join(lines)
        await interaction.response.send_message(message, ephemeral=True)

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


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(RssFeedsCog(bot))

