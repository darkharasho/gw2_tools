"""Multi-source game news feed (Guild Wars 2 + Guild Wars 3)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import urljoin

import discord
import feedparser
import requests
from bs4 import BeautifulSoup
from discord.ext import commands, tasks

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR
from ..config_status import ConfigStatus, StatusField
from ..storage import GameNewsStatus
from .rss import (
    _convert_struct_time,
    _entry_identifier,
    _extract_entry_description,
    _extract_entry_thumbnail,
)

LOGGER = logging.getLogger(__name__)

GW2_FEED_URL = "https://www.guildwars2.com/en/feed/"
GW3_NEWS_PAGE_URL = "https://www.guildwars3.com/en/news/"
GW3_BASE_URL = "https://www.guildwars3.com"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# Upper bound on remembered entry ids per source. Far larger than any source's
# index (GW2 feed ~10 items, GW3 index a handful), so trimming never drops an
# id that is still live on the page — which would otherwise re-post it.
SEEN_IDS_LIMIT = 200

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class GameNewsEntry:
    """A news article from any source, normalized for posting."""

    source_key: str
    entry_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    published_at: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class NewsSource:
    """Describes one news source: its dedup key, footer label, and logo asset."""

    key: str
    label: str
    logo_asset: str


class GameNewsCog(commands.Cog):
    """Poll GW2 (RSS) and GW3 (scrape) news and post new articles."""

    CHECK_INTERVAL_MINUTES = 15

    SOURCES: List[NewsSource] = [
        NewsSource(key="gw2", label="Guild Wars 2 – News", logo_asset="gw2_logo.png"),
        NewsSource(key="gw3", label="Guild Wars 3 – News", logo_asset="gw3_logo.png"),
    ]

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)
        self._poll_news.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_news.cancel()
        self._session.close()

    # ---- shared helpers ---------------------------------------------------

    def _select_new_entries(
        self,
        entries: Sequence[GameNewsEntry],
        seen_ids: Sequence[str],
    ) -> List[GameNewsEntry]:
        """Return entries whose id has never been seen, oldest-first.

        Dedup is set membership, not position relative to a single boundary id.
        A source that reorders its index (e.g. pins an older article above the
        newest) therefore cannot resurface an already-posted entry, and a
        boundary scrolling off the index is a non-event. Works identically for
        sources with dates (GW2) and without (GW3).
        """
        seen = set(seen_ids)
        # ``entries`` arrive newest-first; reverse so we post oldest-first.
        return [entry for entry in reversed(list(entries)) if entry.entry_id not in seen]

    def _remember(self, status: GameNewsStatus, source_key: str, entry_id: str) -> None:
        """Record ``entry_id`` as seen for ``source_key`` (bounded, newest-last)."""
        seen = status.seen_entry_ids.setdefault(source_key, [])
        if entry_id in seen:
            return
        seen.append(entry_id)
        if len(seen) > SEEN_IDS_LIMIT:
            del seen[:-SEEN_IDS_LIMIT]

    def _mark_all_seen(
        self, status: GameNewsStatus, source_key: str, entries: Sequence[GameNewsEntry]
    ) -> None:
        """Record every current entry id as seen (oldest-first, bounded).

        Used to seed silently on first run and after a forced post, so the poll
        loop never floods a channel with the existing backlog.
        """
        for entry in reversed(list(entries)):  # oldest-first -> newest at the tail
            self._remember(status, source_key, entry.entry_id)

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        candidate = value
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            LOGGER.debug("Unable to parse game news timestamp: %s", value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    def _parse_gw3_html(self, html: str) -> List[GameNewsEntry]:
        soup = BeautifulSoup(html, "html.parser")
        entries: List[GameNewsEntry] = []
        for card in soup.select("article.news-article"):
            try:
                entry = self._parse_gw3_card(card)
            except Exception:  # pragma: no cover - defensive per-card guard
                LOGGER.debug("Failed to parse a GW3 news card", exc_info=True)
                continue
            if entry:
                entries.append(entry)
        return entries

    def _parse_gw3_card(self, card) -> Optional[GameNewsEntry]:
        slug: Optional[str] = None
        card_id = card.get("id") or ""
        if card_id.startswith("article-"):
            slug = card_id[len("article-"):].strip()

        anchor = card.find_parent("a")
        href = anchor.get("href") if anchor else None
        if not slug and href:
            slug = href.rstrip("/").split("/")[-1].strip()
        if not slug:
            return None

        heading = card.select_one("h2.title") or card.find("h2")
        title = heading.get_text(strip=True) if heading else ""
        if not title:
            return None

        if href:
            url = urljoin(GW3_NEWS_PAGE_URL, href)
        else:
            url = f"{GW3_BASE_URL}/en/news/{slug}"

        image = card.find("img")
        image_url = image.get("src") if image and image.get("src") else None

        return GameNewsEntry(
            source_key="gw3",
            entry_id=slug,
            title=title,
            url=url,
            image_url=image_url,
            published_at=None,
            summary=None,
        )

    def _first_image_from_entry(self, entry) -> Optional[str]:
        """Pull the first <img src> from the entry's content/summary HTML.

        The GW2 feed embeds images inline in content:encoded (no media tags),
        so _extract_entry_thumbnail returns None and we fall back to this.
        """
        html_candidates: List[str] = []
        contents = entry.get("content")
        if isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict) and item.get("value"):
                    html_candidates.append(str(item["value"]))
        summary = entry.get("summary") or entry.get("description")
        if summary:
            html_candidates.append(str(summary))

        for html in html_candidates:
            soup = BeautifulSoup(html, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("http"):
                    return src
        return None

    def _parse_gw2_feed(self, raw: str) -> List[GameNewsEntry]:
        parsed = feedparser.parse(raw)
        entries: List[GameNewsEntry] = []
        for entry in parsed.entries:
            entry_id = _entry_identifier(entry)
            link = entry.get("link")
            if not entry_id or not link:
                continue
            title = entry.get("title") or "Guild Wars 2 News"
            published = _convert_struct_time(entry.get("published_parsed"))
            published_at = published.isoformat() if published else None
            summary = _extract_entry_description(entry)
            image = _extract_entry_thumbnail(entry) or self._first_image_from_entry(entry)
            entries.append(
                GameNewsEntry(
                    source_key="gw2",
                    entry_id=entry_id,
                    title=str(title),
                    url=str(link),
                    image_url=image,
                    published_at=published_at,
                    summary=summary,
                )
            )
        return entries

    def _build_embed(self, source: NewsSource, entry: GameNewsEntry) -> discord.Embed:
        embed = discord.Embed(
            title=self._truncate(entry.title, 256),
            url=entry.url,
            color=BRAND_COLOUR,
        )
        if entry.summary:
            embed.description = self._truncate(entry.summary, 4000)
        if entry.image_url:
            embed.set_image(url=entry.image_url)
        timestamp = self._parse_timestamp(entry.published_at)
        if timestamp:
            embed.timestamp = timestamp
        if (ASSETS_DIR / source.logo_asset).exists():
            embed.set_thumbnail(url=f"attachment://{source.logo_asset}")
        embed.set_footer(text=source.label)
        return embed

    def _build_file(self, source: NewsSource) -> Optional[discord.File]:
        path = ASSETS_DIR / source.logo_asset
        if not path.exists():
            return None
        return discord.File(str(path), filename=source.logo_asset)

    async def _send_entry(
        self, channel: discord.abc.Messageable, source: NewsSource, entry: GameNewsEntry
    ) -> None:
        embed = self._build_embed(source, entry)
        file = self._build_file(source)
        if file is not None:
            await channel.send(embed=embed, file=file)
        else:
            await channel.send(embed=embed)

    async def _fetch_url(self, url: str, retries: int = 3) -> Optional[str]:
        last_error: Optional[BaseException] = None
        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(self._session.get, url, timeout=30)
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except requests.RequestException as error:
                last_error = error
                LOGGER.warning(
                    "Failed to fetch %s (attempt %s/%s)", url, attempt + 1, retries,
                    exc_info=True,
                )
                if attempt + 1 < retries:
                    await asyncio.sleep(min(5, 2 ** attempt))
        if last_error is not None:
            LOGGER.warning("Giving up on %s after repeated failures", url, exc_info=last_error)
        return None

    async def _fetch_entries(self, source_key: str) -> List[GameNewsEntry]:
        if source_key == "gw2":
            raw = await self._fetch_url(GW2_FEED_URL)
            return self._parse_gw2_feed(raw) if raw else []
        if source_key == "gw3":
            html = await self._fetch_url(GW3_NEWS_PAGE_URL)
            return self._parse_gw3_html(html) if html else []
        return []

    async def _resolve_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel is not None:
            return None
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Unable to fetch game news channel %s for guild %s", channel_id, guild.id
            )
            return None
        return fetched if isinstance(fetched, discord.TextChannel) else None

    async def _process_guild(
        self, guild: discord.Guild, source_entries: Dict[str, List[GameNewsEntry]]
    ) -> None:
        config = self.bot.get_config(guild.id)
        channel_id = config.game_news_channel_id
        if not channel_id:
            return

        status = self.bot.storage.get_game_news_status(guild.id) or GameNewsStatus()
        channel: Optional[discord.TextChannel] = None
        changed = False

        for source in self.SOURCES:
            entries = source_entries.get(source.key) or []
            if not entries:
                continue

            if source.key not in status.seen_entry_ids:
                # First time we've seen this source for this guild: record the
                # existing backlog as seen and post nothing.
                self._mark_all_seen(status, source.key, entries)
                changed = True
                continue

            new_entries = self._select_new_entries(entries, status.seen_entry_ids[source.key])
            if not new_entries:
                continue

            if channel is None:
                channel = await self._resolve_channel(guild, channel_id)
            if not channel:
                break

            for entry in new_entries:
                try:
                    await self._send_entry(channel, source, entry)
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Failed to post game news in channel %s for guild %s",
                        channel_id, guild.id,
                    )
                    break
                self._remember(status, source.key, entry.entry_id)
                changed = True

        if changed:
            self.bot.storage.save_game_news_status(guild.id, status)

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poll_news(self) -> None:
        if not self.bot.guilds:
            return
        source_entries: Dict[str, List[GameNewsEntry]] = {
            source.key: await self._fetch_entries(source.key) for source in self.SOURCES
        }
        if not any(source_entries.values()):
            return
        for guild in self.bot.guilds:
            await self._process_guild(guild, source_entries)

    @_poll_news.before_loop
    async def _before_poll_news(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def run_force_notification(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)
        channel_id = config.game_news_channel_id
        if not channel_id:
            await interaction.response.send_message(
                "Game news notifications are disabled for this server.", ephemeral=True
            )
            return

        channel = await self._resolve_channel(interaction.guild, channel_id)
        if not channel:
            await interaction.response.send_message(
                "Unable to locate the configured game news channel.", ephemeral=True
            )
            return

        posted = 0
        status = self.bot.storage.get_game_news_status(interaction.guild.id) or GameNewsStatus()
        for source in self.SOURCES:
            entries = await self._fetch_entries(source.key)
            if not entries:
                continue
            entry = entries[0]
            await self._send_entry(channel, source, entry)
            # Remember the whole current backlog so the poll loop neither
            # re-posts this entry nor floods with the rest of the index.
            self._mark_all_seen(status, source.key, entries)
            posted += 1
        if posted:
            self.bot.storage.save_game_news_status(interaction.guild.id, status)
            await interaction.response.send_message(
                f"Posted the latest game news ({posted} source(s)) in {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Unable to fetch any game news right now.", ephemeral=True
            )

    def get_config_status(self, guild_id: int) -> ConfigStatus:
        config = self.bot.get_config(guild_id)
        if config.game_news_channel_id:
            field = StatusField(
                label="Game News Channel",
                value=f"<#{config.game_news_channel_id}>",
                state="ok",
            )
        else:
            field = StatusField(
                label="Game News Channel",
                value="Not configured — use /config setup",
                state="missing",
            )
        return ConfigStatus(
            title="Game News (GW2 + GW3)",
            fields=[field],
            setup_command="/config setup",
        )


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(GameNewsCog(bot))
