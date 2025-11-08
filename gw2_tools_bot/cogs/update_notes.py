"""Guild Wars 2 game update notes monitoring cog."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import cloudscraper
import discord
import requests
from bs4 import BeautifulSoup
from cloudscraper.exceptions import CloudflareChallengeError
from discord import app_commands
from discord.ext import commands, tasks
from markdownify import markdownify as html_to_markdown

from ..bot import GW2ToolsBot
from ..storage import UpdateNotesStatus

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from cloudscraper import CloudScraper

LOGGER = logging.getLogger(__name__)


FORUM_BASE_URL = "https://en-forum.guildwars2.com/"
DEV_TRACKER_URL = f"{FORUM_BASE_URL}discover/6/"
EMBED_THUMBNAIL_URL = (
    "https://wiki.guildwars2.com/images/thumb/c/cd/"
    "Visions_of_Eternity_logo.png/244px-Visions_of_Eternity_logo.png"
)
SCRAPER_BROWSER_SIGNATURE = {
    "browser": "chrome",
    "platform": "windows",
    "desktop": True,
    "mobile": False,
}

SCRAPER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Priority": "u=0, i",
    "Referer": FORUM_BASE_URL,
    "Sec-CH-UA": '"Not A(Brand";v="99", "Google Chrome";v="124", "Chromium";v="124"',
    "Sec-CH-UA-Arch": '"x86"',
    "Sec-CH-UA-Bitness": '"64"',
    "Sec-CH-UA-Full-Version": '"124.0.6367.45"',
    "Sec-CH-UA-Full-Version-List": '"Not A(Brand";v="99.0.0.0", "Google Chrome";v="124.0.6367.45", "Chromium";v="124.0.6367.45"',
    "Sec-CH-UA-Model": '""',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-CH-UA-Platform-Version": '"15.0.0"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class PatchNotesEntry:
    """Structured representation of a dev tracker entry."""

    entry_id: str
    title: str
    url: str
    comment_id: Optional[str]
    published_at: Optional[str]
    summary: str


class UpdateNotesCog(commands.Cog):
    """Poll the official forum dev tracker for new game update notes."""

    CHECK_INTERVAL_MINUTES = 15
    EMBED_COLOR = discord.Color.dark_gold()
    PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._scraper: Optional["CloudScraper"] = None
        self._scraper_lock = asyncio.Lock()
        self._poll_updates.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_updates.cancel()
        if self._scraper is not None:
            self.bot.loop.create_task(self._close_scraper())
        self._scraper = None

    async def _close_scraper(self) -> None:
        async with self._scraper_lock:
            if self._scraper is not None:
                await asyncio.to_thread(self._scraper.close)
                self._scraper = None

    async def _get_scraper(self) -> "CloudScraper":
        async with self._scraper_lock:
            if self._scraper is None:
                self._scraper = await asyncio.to_thread(
                    cloudscraper.create_scraper,
                    browser=SCRAPER_BROWSER_SIGNATURE,
                )
                self._scraper.headers.update(SCRAPER_HEADERS)
                await asyncio.to_thread(self._prime_scraper, self._scraper)
            return self._scraper

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poll_updates(self) -> None:
        if not self.bot.guilds:
            return

        entries = await self._fetch_entries()
        if not entries:
            return

        for guild in self.bot.guilds:
            config = self.bot.get_config(guild.id)
            channel_id = config.update_notes_channel_id
            if not channel_id:
                continue

            status = self.bot.storage.get_update_notes_status(guild.id)
            if status is None or not status.last_entry_id:
                latest = entries[0]
                self.bot.storage.save_update_notes_status(
                    guild.id,
                    UpdateNotesStatus(
                        last_entry_id=latest.entry_id,
                        last_entry_published_at=latest.published_at,
                    ),
                )
                continue

            new_entries = self._resolve_new_entries(entries, status.last_entry_id)
            if not new_entries:
                continue

            channel = await self._resolve_channel(guild, channel_id)
            if not channel:
                continue

            for entry in new_entries:
                body = await self._fetch_entry_body(entry)
                embeds = self._build_embeds(entry, body)
                try:
                    if len(embeds) == 1:
                        await channel.send(embed=embeds[0])
                    else:
                        await channel.send(embeds=embeds)
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Failed to post game update notes in channel %s for guild %s",
                        channel_id,
                        guild.id,
                    )
                    break
                status.last_entry_id = entry.entry_id
                status.last_entry_published_at = entry.published_at
                self.bot.storage.save_update_notes_status(guild.id, status)

    @_poll_updates.before_loop
    async def _before_poll_updates(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def _fetch_entries(self) -> List[PatchNotesEntry]:
        html = await self._fetch_html(DEV_TRACKER_URL)
        if html is None:
            LOGGER.warning("Failed to fetch Guild Wars 2 dev tracker")
            return []

        soup = BeautifulSoup(html, "html.parser")
        entries: List[PatchNotesEntry] = []
        for item in soup.select("li.ipsStreamItem"):
            entry = self._parse_entry(item)
            if not entry:
                continue
            entries.append(entry)
        return entries

    def _parse_entry(self, item) -> Optional[PatchNotesEntry]:
        title_anchor = item.select_one(".ipsStreamItem_title a")
        if not title_anchor:
            return None

        title = title_anchor.get_text(strip=True)
        if "game update notes" not in title.lower():
            return None

        url = title_anchor.get("href")
        if not url:
            return None

        entry_id, comment_id = self._extract_entry_identifier(url, item)
        published_at = None
        timestamp = item.select_one("time")
        if timestamp and timestamp.get("datetime"):
            published_at = timestamp.get("datetime")

        summary_element = item.select_one(".ipsStreamItem_snippet")
        summary = self._normalise_text(
            summary_element.get_text("\n", strip=True) if summary_element else ""
        )

        return PatchNotesEntry(
            entry_id=entry_id,
            title=title,
            url=url,
            comment_id=comment_id,
            published_at=published_at,
            summary=summary,
        )

    def _extract_entry_identifier(
        self, url: str, item
    ) -> tuple[str, Optional[str]]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        comment_values = query.get("comment")
        comment_id: Optional[str] = None
        if comment_values:
            comment_id = str(comment_values[0])

        entry_id = comment_id or item.get("data-timestamp") or url
        return str(entry_id), comment_id

    def _resolve_new_entries(
        self, entries: Sequence[PatchNotesEntry], last_entry_id: Optional[str]
    ) -> List[PatchNotesEntry]:
        if not entries:
            return []

        collected: List[PatchNotesEntry] = []
        for entry in reversed(entries):
            if last_entry_id and entry.entry_id == last_entry_id:
                collected.clear()
                continue
            collected.append(entry)
        return collected

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
                "Unable to fetch game update notes channel %s for guild %s",
                channel_id,
                guild.id,
            )
            return None

        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    async def _fetch_entry_body(self, entry: PatchNotesEntry) -> Optional[str]:
        if not entry.comment_id:
            return None

        html = await self._fetch_html(entry.url)
        if html is None:
            LOGGER.warning("Failed to fetch game update notes content from %s", entry.url)
            return None

        soup = BeautifulSoup(html, "html.parser")
        wrapper = soup.select_one(f"#comment-{entry.comment_id}_wrap")
        if not wrapper:
            return None
        content = wrapper.select_one('[data-role="commentContent"]')
        if not content:
            return None

        return self._render_comment_content(content)

    def _build_embeds(
        self, entry: PatchNotesEntry, body: Optional[str]
    ) -> List[discord.Embed]:
        description = (
            body
            or entry.summary
            or "New Guild Wars 2 game update notes are available."
        )
        segments = self._chunk_text(description, 4000)
        if not segments:
            segments = ["New Guild Wars 2 game update notes are available."]

        embeds: List[discord.Embed] = []
        parsed_timestamp = self._parse_timestamp(entry.published_at)
        for index, segment in enumerate(segments):
            title = entry.title if index == 0 else f"{entry.title} (cont. {index})"
            embed = discord.Embed(
                title=title,
                url=entry.url,
                description=segment,
                color=self.EMBED_COLOR,
            )
            if index == 0:
                if parsed_timestamp:
                    embed.timestamp = parsed_timestamp
                embed.set_thumbnail(url=EMBED_THUMBNAIL_URL)
            embed.set_footer(text="Guild Wars 2 Forums – Dev Tracker")
            embeds.append(embed)
        return embeds

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        candidate = value
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            LOGGER.debug("Unable to parse dev tracker timestamp: %s", value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _normalise_text(self, text: str) -> str:
        cleaned_lines: List[str] = []
        previous_blank = True
        for raw_line in text.replace("\r", "\n").split("\n"):
            line = " ".join(raw_line.replace("\xa0", " ").split())
            if not line:
                if not previous_blank:
                    cleaned_lines.append("")
                previous_blank = True
                continue
            cleaned_lines.append(line)
            previous_blank = False
        return "\n".join(cleaned_lines).strip()

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    def _render_comment_content(self, content) -> str:
        for element in content.select("script, style"):
            element.decompose()

        markdown = html_to_markdown(
            str(content), heading_style="ATX", bullets="-*+", strip=["img"]
        )
        return self._clean_markdown(markdown)

    def _clean_markdown(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines()]
        cleaned: List[str] = []
        blank = False
        for line in lines:
            if line.strip():
                cleaned.append(line)
                blank = False
                continue
            if not blank:
                cleaned.append("")
            blank = True
        result = "\n".join(cleaned).strip()
        if not result:
            return result
        return self._ensure_bullet_prefix(result)

    def _ensure_bullet_prefix(self, text: str) -> str:
        bullet_pattern = re.compile(r"^(\s*)[\*\+]\s+")
        adjusted: List[str] = []
        for line in text.split("\n"):
            match = bullet_pattern.match(line)
            if match:
                indent = match.group(1)
                remainder = line[match.end() :]
                adjusted.append(f"{indent}- {remainder}")
            else:
                adjusted.append(line)
        return "\n".join(adjusted)

    def _chunk_text(self, text: str, limit: int, max_chunks: int = 10) -> List[str]:
        remaining = text.strip()
        if not remaining:
            return []

        chunks: List[str] = []
        while remaining and len(chunks) < max_chunks:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            cut = self._find_chunk_cut(remaining, limit)
            chunk = remaining[:cut].rstrip()
            if not chunk:
                chunk = remaining[:limit].rstrip()
            chunks.append(chunk)
            remaining = remaining[cut:].lstrip("\n ")

        if remaining and chunks:
            chunks[-1] = self._truncate(chunks[-1], limit)

        return chunks

    def _find_chunk_cut(self, text: str, limit: int) -> int:
        for separator in ("\n\n", "\n", " "):
            index = text.rfind(separator, 0, limit)
            if index > 0:
                return index + len(separator)
        return limit

    async def _fetch_html(self, url: str, retries: int = 3) -> Optional[str]:
        last_error: Optional[BaseException] = None
        for attempt in range(retries):
            scraper = await self._get_scraper()
            try:
                return await asyncio.to_thread(self._request_text, scraper, url)
            except (CloudflareChallengeError, requests.RequestException) as error:
                last_error = error
                LOGGER.warning("Failed to fetch %s (attempt %s/%s)", url, attempt + 1, retries, exc_info=True)
                await self._refresh_scraper()
                if attempt + 1 < retries:
                    await asyncio.sleep(min(5, 2 ** attempt))
        if last_error is not None:
            LOGGER.warning("Giving up on %s after repeated failures", url, exc_info=last_error)
        return None

    async def _refresh_scraper(self) -> None:
        async with self._scraper_lock:
            if self._scraper is not None:
                await asyncio.to_thread(self._scraper.close)
                self._scraper = None

    @staticmethod
    def _prime_scraper(scraper: "CloudScraper") -> None:
        try:
            response = scraper.get(FORUM_BASE_URL, timeout=30)
            response.raise_for_status()
        except requests.RequestException:
            LOGGER.debug("Unable to warm up dev tracker scraper", exc_info=True)

    @staticmethod
    def _request_text(scraper: "CloudScraper", url: str) -> str:
        response = scraper.get(url, timeout=30)
        if response.status_code == 403:
            UpdateNotesCog._prime_scraper(scraper)
            response = scraper.get(url, timeout=30)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    if not PRODUCTION:

        @app_commands.command(
            name="update_notes_force_notification",
            description="Send the latest game update notes notification.",
        )
        async def force_notification(self, interaction: discord.Interaction) -> None:
            if not interaction.guild:
                await interaction.response.send_message(
                    "This command must be used inside a server.", ephemeral=True
                )
                return

            if not await self.bot.ensure_authorised(interaction):
                return

            config = self.bot.get_config(interaction.guild.id)
            channel_id = config.update_notes_channel_id
            if not channel_id:
                await interaction.response.send_message(
                    "Game update notes notifications are disabled for this server.",
                    ephemeral=True,
                )
                return

            entries = await self._fetch_entries()
            if not entries:
                await interaction.response.send_message(
                    "Unable to fetch the latest game update notes.", ephemeral=True
                )
                return

            channel = await self._resolve_channel(interaction.guild, channel_id)
            if not channel:
                await interaction.response.send_message(
                    "Unable to locate the configured game update notes channel.",
                    ephemeral=True,
                )
                return

            entry = entries[0]
            body = await self._fetch_entry_body(entry)
            embeds = self._build_embeds(entry, body)
            if len(embeds) == 1:
                await channel.send(embed=embeds[0])
            else:
                await channel.send(embeds=embeds)
            status = self.bot.storage.get_update_notes_status(interaction.guild.id) or UpdateNotesStatus()
            status.last_entry_id = entry.entry_id
            status.last_entry_published_at = entry.published_at
            self.bot.storage.save_update_notes_status(interaction.guild.id, status)
            await interaction.response.send_message(
                f"Posted the latest game update notes in {channel.mention}.",
                ephemeral=True,
            )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(UpdateNotesCog(bot))
