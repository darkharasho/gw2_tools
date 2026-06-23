"""RSS feed subscription and notification cog."""
from __future__ import annotations

import calendar
import hashlib
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR
from ..config_status import ConfigStatus, StatusField
from ..rendering import clean_html, html_to_discord_markdown, truncate_embed_field
from ..storage import RssFeedConfig, TrackedRelease
from ._paginated_select import PaginatedSelectView

LOGGER = logging.getLogger(__name__)


def _entry_identifier(entry: feedparser.FeedParserDict) -> Optional[str]:
    for key in ("id", "guid", "link", "title"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


_GITHUB_RELEASES_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/releases(?:\.atom)?/?$",
    re.IGNORECASE,
)


def _parse_github_repo(url: str) -> Optional[Tuple[str, str]]:
    if not url:
        return None
    match = _GITHUB_RELEASES_RE.match(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _github_tag_from_entry(entry: feedparser.FeedParserDict) -> Optional[str]:
    link = entry.get("link")
    if link and "/releases/tag/" in link:
        return link.rsplit("/releases/tag/", 1)[1].strip("/") or None
    entry_id = entry.get("id")
    if entry_id and "/" in str(entry_id):
        candidate = str(entry_id).rsplit("/", 1)[1].strip()
        if candidate:
            return candidate
    return None


def _release_is_complete(release: "feedparser.FeedParserDict") -> bool:
    if release.get("draft"):
        return False
    has_assets = bool(release.get("assets"))
    body = (release.get("body") or "").strip()
    return has_assets or bool(body)


def _release_content_hash(release: "feedparser.FeedParserDict") -> str:
    name = str(release.get("name") or "")
    body = str(release.get("body") or "")
    asset_names = sorted(
        str(asset.get("name") or "")
        for asset in (release.get("assets") or [])
        if isinstance(asset, dict)
    )
    payload = "\x00".join([name, body, *asset_names])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _within_grace_window(
    first_posted_at: Optional[str], now: datetime, hours: int = 2
) -> bool:
    if not first_posted_at:
        return False
    try:
        posted = datetime.fromisoformat(first_posted_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = now - posted
    return 0 <= delta.total_seconds() <= hours * 3600


def _append_seen_id(seen: List[str], entry_id: str, cap: int = 50) -> List[str]:
    result = [item for item in seen if item != entry_id]
    result.append(entry_id)
    if len(result) > cap:
        result = result[-cap:]
    return result


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
                rendered = html_to_discord_markdown(str(value))
                if rendered:
                    return truncate_embed_field(rendered, max_length) if len(rendered) > max_length else rendered
                return clean_html(str(value), max_length=max_length)

    summary = entry.get("summary") or entry.get("description")
    if summary:
        rendered = html_to_discord_markdown(str(summary))
        if rendered:
            return truncate_embed_field(rendered, max_length) if len(rendered) > max_length else rendered
        return clean_html(str(summary), max_length=max_length)
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


def _convert_iso8601(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class RssFeedsCog(commands.GroupCog, name="rss", group_extras={"category": "Announcements"}):
    """Manage RSS feed subscriptions and push updates to Discord channels."""

    CHECK_INTERVAL_MINUTES = 10
    EDIT_GRACE_HOURS = 2
    EMBED_COLOR = BRAND_COLOUR
    GITHUB_API_BASE = "https://api.github.com"

    def __init__(self, bot: AxiToolsBot) -> None:
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

    async def _fetch_github_release(
        self, owner: str, repo: str, tag: str
    ) -> Optional[dict]:
        session = await self._get_session()
        url = f"{self.GITHUB_API_BASE}/repos/{owner}/{repo}/releases/tags/{quote(tag, safe='')}"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("AXITOOLS_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, ValueError):
            LOGGER.warning("Failed to fetch GitHub release %s/%s@%s", owner, repo, tag, exc_info=True)
            return None

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
            embed.description = f"{description}\n\u200B"

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
        metadata_lines: List[str] = []

        if published_text:
            metadata_lines.append(f"**Published:** {published_text}")

        author_name = entry.get("author")
        if author_name:
            metadata_lines.append(f"**Author:** {author_name}")

        tags = entry.get("tags")
        if isinstance(tags, list):
            tag_names = [
                tag.get("label") or tag.get("term")
                for tag in tags
                if isinstance(tag, dict) and (tag.get("label") or tag.get("term"))
            ]
            if tag_names:
                joined = ", ".join(tag_names)
                metadata_lines.append(f"**Tags:** {joined[:800]}")

        if metadata_lines:
            metadata_lines.append("\u200B")
            embed.add_field(
                name="Details",
                value="\n".join(metadata_lines)[:1024],
                inline=False,
            )

        embed.set_footer(text=f"RSS feed: {feed_config.name}")
        return embed

    def _build_github_release_embed(
        self, feed_config: RssFeedConfig, release: dict
    ) -> discord.Embed:
        title = release.get("name") or release.get("tag_name") or "New release"
        url = release.get("html_url") or feed_config.url
        embed = discord.Embed(title=title, url=url, color=self.EMBED_COLOR)

        # Prominent "which repository" header: owner/repo with the owner avatar,
        # derived from the release URL (falls back to the feed name).
        repo_match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url or "")
        owner = repo_match.group(1) if repo_match else None
        if repo_match:
            embed.set_author(
                name=f"{owner}/{repo_match.group(2)}",
                url=f"https://github.com/{owner}/{repo_match.group(2)}",
                icon_url=f"https://github.com/{owner}.png",
            )
        else:
            embed.set_author(name=feed_config.name)

        # Thumbnail: per-feed override (e.g. the app icon) if set, otherwise the
        # GitHub owner avatar so every release embed still carries an image.
        thumbnail = feed_config.thumbnail_url or (
            f"https://github.com/{owner}.png" if owner else None
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        body = (release.get("body") or "").strip()
        if body:
            embed.description = truncate_embed_field(body, 1800)

        published = release.get("published_at")
        if published:
            parsed = _convert_iso8601(published)
            if parsed:
                embed.timestamp = parsed

        assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
        if assets:
            lines = []
            for asset in assets[:10]:
                name = asset.get("name") or "download"
                link = asset.get("browser_download_url")
                lines.append(f"[{name}]({link})" if link else name)
            embed.add_field(name="Downloads", value="\n".join(lines)[:1024], inline=False)

        if release.get("prerelease"):
            embed.set_footer(text=f"{feed_config.name} · pre-release")
        else:
            embed.set_footer(text=f"{feed_config.name} · release")
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
            field_value_parts = [
                f"**URL:** [Open feed]({feed.url})",
                f"**Channel:** {channel_display}",
            ]
            if feed.last_entry_published_at:
                field_value_parts.append(f"**Last post:** {feed.last_entry_published_at}")
            field_value = "\n".join(field_value_parts) + "\n\u200B"
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

    async def _process_github_feed(
        self,
        guild: "discord.Guild",
        feed_config: RssFeedConfig,
        parsed_feed: "feedparser.FeedParserDict",
        owner: str,
        repo: str,
    ) -> Optional[RssFeedConfig]:
        entries = list(getattr(parsed_feed, "entries", []) or [])
        if not entries:
            return None

        # First poll for this GitHub feed (freshly added, or migrated from the
        # legacy atom path where only last_entry_id was stored): adopt the
        # current entries as the baseline WITHOUT announcing, exactly like
        # priming a newly added feed. Without this, the first poll after an
        # upgrade would re-announce every repo's recent release history.
        if not feed_config.seen_entry_ids and not feed_config.tracked_releases:
            primed: List[str] = []
            for entry in entries:
                eid = _entry_identifier(entry)
                if eid:
                    primed = _append_seen_id(primed, eid)
            if not primed:
                return None
            return replace(feed_config, seen_entry_ids=primed)

        channel = await self._resolve_channel(guild, feed_config.channel_id)
        if not channel:
            LOGGER.warning(
                "Configured RSS channel %s for guild %s is not accessible",
                feed_config.channel_id, guild.id,
            )
            return None

        seen = list(feed_config.seen_entry_ids)
        tracked = dict(feed_config.tracked_releases)
        changed = False
        now = datetime.now(timezone.utc)

        # Oldest -> newest so posts arrive in chronological order.
        for entry in reversed(entries):
            entry_id = _entry_identifier(entry)
            if not entry_id:
                continue
            existing = tracked.get(entry_id)
            if existing and existing.finalized:
                continue
            if existing and existing.message_id:
                if not _within_grace_window(existing.first_posted_at, now, self.EDIT_GRACE_HOURS):
                    if not existing.finalized:
                        tracked[entry_id] = replace(existing, finalized=True)
                        changed = True
                    continue
                tag = _github_tag_from_entry(entry)
                if not tag:
                    continue
                release = await self._fetch_github_release(owner, repo, tag)
                if not release or not _release_is_complete(release):
                    continue
                new_hash = _release_content_hash(release)
                if new_hash == existing.content_hash:
                    continue
                try:
                    message = await channel.fetch_message(existing.message_id)
                    await message.edit(embed=self._build_github_release_embed(feed_config, release))
                except discord.NotFound:
                    tracked[entry_id] = replace(existing, finalized=True)
                    changed = True
                    continue
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Failed to edit GitHub release message for %s", tag)
                    continue
                tracked[entry_id] = replace(existing, content_hash=new_hash)
                changed = True
                continue
            if existing and not existing.message_id:
                # Seen before but not yet postable. Keep checking only within
                # the grace window; give up (finalize) afterwards so a release
                # that never completes is not re-fetched from the API forever.
                if not _within_grace_window(existing.first_posted_at, now, self.EDIT_GRACE_HOURS):
                    tracked[entry_id] = replace(existing, finalized=True)
                    changed = True
                    continue
                tag = _github_tag_from_entry(entry)
                if not tag:
                    continue
                release = await self._fetch_github_release(owner, repo, tag)
                if not release or not _release_is_complete(release):
                    continue
                embed = self._build_github_release_embed(feed_config, release)
                try:
                    message = await channel.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Failed to post GitHub release '%s' to channel %s in guild %s",
                        tag, feed_config.channel_id, guild.id,
                    )
                    continue
                tracked[entry_id] = replace(
                    existing,
                    message_id=getattr(message, "id", None),
                    content_hash=_release_content_hash(release),
                    first_posted_at=now.isoformat(),  # restart clock for the edit window
                )
                seen = _append_seen_id(seen, entry_id)
                changed = True
                continue
            if entry_id in seen and not existing:
                continue

            tag = _github_tag_from_entry(entry)
            if not tag:
                continue
            release = await self._fetch_github_release(owner, repo, tag)
            if not release:
                continue  # transient fetch failure — retry next poll, record nothing
            if not _release_is_complete(release):
                # First sighting of an incomplete release: record first-seen so we
                # can bound re-fetching and eventually give up.
                tracked[entry_id] = TrackedRelease(
                    entry_id=entry_id,
                    message_id=None,
                    content_hash=None,
                    first_posted_at=now.isoformat(),
                    finalized=False,
                )
                changed = True
                continue

            embed = self._build_github_release_embed(feed_config, release)
            try:
                message = await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post GitHub release '%s' to channel %s in guild %s",
                    tag, feed_config.channel_id, guild.id,
                )
                continue

            tracked[entry_id] = TrackedRelease(
                entry_id=entry_id,
                message_id=getattr(message, "id", None),
                content_hash=_release_content_hash(release),
                first_posted_at=now.isoformat(),
                finalized=False,
            )
            seen = _append_seen_id(seen, entry_id)
            changed = True

        if not changed:
            return None
        return replace(feed_config, seen_entry_ids=seen, tracked_releases=tracked)

    async def _process_feed(self, guild: discord.Guild, feed_config: RssFeedConfig) -> Optional[RssFeedConfig]:
        parsed = await self._fetch_feed(feed_config.url)
        if not parsed or not parsed.entries:
            return None

        repo = _parse_github_repo(feed_config.url)
        if repo:
            return await self._process_github_feed(guild, feed_config, parsed, repo[0], repo[1])

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
        if not await self.bot.ensure_authorised(interaction):
            return
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
        thumbnail="Optional image URL for the embed thumbnail (e.g. the app icon). Defaults to the GitHub owner avatar.",
    )
    async def set_feed(
        self,
        interaction: discord.Interaction,
        name: str,
        url: str,
        channel: discord.TextChannel,
        thumbnail: Optional[str] = None,
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
                thumbnail_url=thumbnail if thumbnail is not None else existing.thumbnail_url,
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
                thumbnail_url=thumbnail,
            )
            self.bot.storage.upsert_rss_feed(guild.id, new_feed)
            message = (
                f"RSS feed **{name}** added and will post new updates in {channel.mention}."
            )

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="delete", description="Delete an RSS feed subscription.")
    async def delete_feed(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        feeds = self.bot.storage.get_rss_feeds(guild.id)
        if not feeds:
            await interaction.response.send_message(
                "There are no RSS feeds configured for this server.", ephemeral=True
            )
            return

        view = self._build_feed_select_view(
            interaction.user,
            guild,
            feeds,
            placeholder="Select an RSS feed to delete",
            action=self._execute_delete_feed,
        )
        await interaction.response.send_message(
            "Select the RSS feed you would like to delete.",
            view=view,
            ephemeral=True,
        )

    def _build_feed_select_view(
        self,
        invoker: discord.abc.User,
        guild: discord.Guild,
        feeds: Sequence[RssFeedConfig],
        *,
        placeholder: str,
        action,
    ) -> PaginatedSelectView:
        """Build a paginated dropdown of feeds wired to ``action``.

        ``action`` is invoked as ``await action(interaction, feed_config, selector=select)``
        when the user picks a feed.
        """
        sorted_feeds = sorted(feeds, key=lambda feed: feed.name.lower())
        feed_lookup: Dict[str, RssFeedConfig] = {feed.name: feed for feed in sorted_feeds}

        options: List[Tuple[str, str]] = []
        descriptions: Dict[str, str] = {}
        for feed in sorted_feeds:
            options.append((feed.name, feed.name[:100]))

            channel = guild.get_channel(feed.channel_id)
            if isinstance(channel, discord.TextChannel):
                channel_name = f"#{channel.name}"
            else:
                channel_name = f"ID {feed.channel_id}"

            description = " • ".join([str(feed.url), f"Channel: {channel_name}"])[:100]
            if description:
                descriptions[feed.name] = description

        async def on_select(value: str, interaction: discord.Interaction) -> None:
            feed_config = feed_lookup.get(value)
            select = view._select  # noqa: F821 - bound below
            if not feed_config:
                await interaction.response.send_message(
                    "The selected RSS feed could not be found. Please close this menu and try again.",
                    ephemeral=True,
                )
                return
            await action(interaction, feed_config, selector=select)

        view = PaginatedSelectView(
            options=options,
            page_size=PaginatedSelectView.PAGE_SIZE,
            on_select=on_select,
            descriptions=descriptions,
            placeholder=placeholder,
            invoker_id=invoker.id,
        )
        return view

    async def _execute_delete_feed(
        self,
        interaction: discord.Interaction,
        feed_config: RssFeedConfig,
        *,
        selector: Optional[discord.ui.Select] = None,
    ) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "This action must be used within a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        removed = self.bot.storage.delete_rss_feed(guild.id, feed_config.name)
        if not removed:
            await interaction.followup.send(
                "That RSS feed could not be removed. It may have already been deleted.",
                ephemeral=True,
            )
            return

        if selector and selector.view:
            selector.view.disable()
            try:
                await interaction.message.edit(view=selector.view)
            except discord.HTTPException:  # pragma: no cover - best effort UI tidy-up
                LOGGER.debug("Failed to disable RSS feed delete selector for guild %s", guild.id)

        await interaction.followup.send(
            f"RSS feed **{feed_config.name}** has been removed.", ephemeral=True
        )

    async def _execute_test_feed(
        self,
        interaction: discord.Interaction,
        feed_config: RssFeedConfig,
        *,
        selector: Optional[discord.ui.Select] = None,
    ) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "This action must be used within a server.", ephemeral=True
            )
            return

        channel = await self._resolve_channel(guild, feed_config.channel_id)
        if not channel:
            await interaction.response.send_message(
                "The configured channel for this feed is not accessible. Please update the feed first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

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

        if selector and selector.view:
            selector.view.disable()
            try:
                await interaction.message.edit(view=selector.view)
            except discord.HTTPException:  # pragma: no cover - best effort UI tidy-up
                LOGGER.debug("Failed to disable RSS feed test selector for guild %s", guild.id)

        await interaction.followup.send(
            (
                f"Posted the latest entry from **{feed_config.name}** to {channel.mention}."
                f" [View message]({message.jump_url})"
            ),
            ephemeral=True,
        )

    async def run_test_feed(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        feeds = self.bot.storage.get_rss_feeds(guild.id)
        if not feeds:
            await interaction.response.send_message(
                "No RSS feeds are configured for this server.",
                ephemeral=True,
            )
            return

        view = self._build_feed_select_view(
            interaction.user,
            guild,
            feeds,
            placeholder="Select an RSS feed to test",
            action=self._execute_test_feed,
        )
        message = (
            "Choose an RSS feed below to post its latest entry to the configured channel."
        )
        if len(feeds) > PaginatedSelectView.PAGE_SIZE:
            message += (
                "\nUse the navigation buttons to browse all feeds before making a selection."
            )

        await interaction.response.send_message(
            message,
            view=view,
            ephemeral=True,
        )


    def get_config_status(self, guild_id: int) -> ConfigStatus:
        feeds = self.bot.storage.get_rss_feeds(guild_id)
        n = len(feeds)
        if n > 0:
            field = StatusField(
                label="RSS Feeds",
                value=f"{n} feed{'s' if n != 1 else ''} configured",
                state="ok",
            )
        else:
            field = StatusField(
                label="RSS Feeds",
                value="None configured — use /rss set",
                state="missing",
            )
        return ConfigStatus(
            title="RSS Feeds",
            fields=[field],
            setup_command="/rss set",
        )


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(RssFeedsCog(bot))
