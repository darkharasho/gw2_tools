"""Guild Wars 2 game update notes monitoring cog."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..storage import UpdateNotesStatus

LOGGER = logging.getLogger(__name__)


# The forum deploys aggressive bot protection and will return HTTP 403 responses
# for atypical clients.  Using a mainstream browser signature ensures the
# scraper receives the regular HTML response instead of an access denied page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Restrict accepted encodings to formats supported by the default aiohttp
    # installation so we do not trigger zstandard responses that require
    # optional dependencies to decode.
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://en-forum.guildwars2.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-GPC": "1",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
DEV_TRACKER_URL = "https://en-forum.guildwars2.com/discover/6/"
FORUM_BASE_URL = "https://en-forum.guildwars2.com/"
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)


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
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_bootstrapped = False
        self._poll_updates.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_updates.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=FETCH_TIMEOUT,
                headers=DEFAULT_HEADERS,
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
            self._session_bootstrapped = False
        return self._session

    async def _bootstrap_session(self) -> None:
        session = await self._get_session()
        if self._session_bootstrapped:
            return

        try:
            async with session.get(FORUM_BASE_URL) as response:
                # Some forum mitigations only lift after an initial navigation
                # to the base domain which sets cookies and challenges the
                # client.  Do not raise for status here so we can retry later
                # if the warm-up request failed with a transient error.
                if response.status < 400:
                    self._session_bootstrapped = True
        except aiohttp.ClientError:
            LOGGER.debug("Forum session bootstrap failed", exc_info=True)

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
                embed = self._build_embed(entry, body)
                try:
                    await channel.send(embed=embed)
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
        await self._bootstrap_session()
        session = await self._get_session()
        try:
            async with session.get(
                DEV_TRACKER_URL,
            ) as response:
                response.raise_for_status()
                html = await response.text()
        except aiohttp.ClientError:
            LOGGER.warning("Failed to fetch Guild Wars 2 dev tracker", exc_info=True)
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

        await self._bootstrap_session()
        session = await self._get_session()
        try:
            async with session.get(
                entry.url,
            ) as response:
                response.raise_for_status()
                html = await response.text()
        except aiohttp.ClientError:
            LOGGER.warning("Failed to fetch game update notes content from %s", entry.url)
            return None

        soup = BeautifulSoup(html, "html.parser")
        wrapper = soup.select_one(f"#comment-{entry.comment_id}_wrap")
        if not wrapper:
            return None
        content = wrapper.select_one('[data-role="commentContent"]')
        if not content:
            return None

        text = content.get_text("\n", strip=True)
        return self._normalise_text(text)

    def _build_embed(
        self, entry: PatchNotesEntry, body: Optional[str]
    ) -> discord.Embed:
        description = body or entry.summary or "New Guild Wars 2 game update notes are available."
        description = self._truncate(description, 4000)
        embed = discord.Embed(
            title=entry.title,
            url=entry.url,
            description=description,
            color=self.EMBED_COLOR,
        )
        parsed_timestamp = self._parse_timestamp(entry.published_at)
        if parsed_timestamp:
            embed.timestamp = parsed_timestamp
        embed.set_footer(text="Guild Wars 2 Forums – Dev Tracker")
        return embed

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
            embed = self._build_embed(entry, body)
            await channel.send(embed=embed)
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
