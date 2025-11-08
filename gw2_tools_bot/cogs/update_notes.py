"""Guild Wars 2 game update notes monitoring cog."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import discord
import requests
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks
from markdownify import markdownify as html_to_markdown

from ..bot import GW2ToolsBot
from ..storage import UpdateNotesStatus

LOGGER = logging.getLogger(__name__)


GAME_UPDATE_NOTES_PAGE_URL = "https://wiki.guildwars2.com/wiki/Game_updates"
EMBED_THUMBNAIL_URL = (
    "https://wiki.guildwars2.com/images/thumb/c/cd/"
    "Visions_of_Eternity_logo.png/244px-Visions_of_Eternity_logo.png"
)
REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class PatchNotesEntry:
    """Structured representation of a game update notes entry."""

    entry_id: str
    title: str
    url: str
    published_at: Optional[str]
    summary: str
    content: str
    legacy_entry_ids: Sequence[str] = ()


class UpdateNotesCog(commands.Cog):
    """Poll the official wiki page for new game update notes."""

    CHECK_INTERVAL_MINUTES = 15
    EMBED_COLOR = discord.Color.dark_gold()
    PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)
        self._poll_updates.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_updates.cancel()
        self._session.close()

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

            new_entries = self._resolve_new_entries(
                entries, status.last_entry_id, status.last_entry_published_at
            )
            if not new_entries:
                continue

            channel = await self._resolve_channel(guild, channel_id)
            if not channel:
                continue

            for entry in new_entries:
                body = entry.content or entry.summary
                embeds = self._build_embeds(entry, body)
                try:
                    for embed in embeds:
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
        html = await self._fetch_url(GAME_UPDATE_NOTES_PAGE_URL)
        if html is None:
            LOGGER.warning("Failed to fetch Guild Wars 2 game update notes page")
            return []

        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#mw-content-text")
        if content is None:
            LOGGER.warning("Unable to locate game update notes content wrapper")
            return []

        entries: List[PatchNotesEntry] = []
        for heading in content.select("h2"):
            entry = self._parse_page_entry(heading)
            if entry:
                entries.append(entry)
        return entries

    def _parse_page_entry(self, heading) -> Optional[PatchNotesEntry]:
        if heading.find_parent(class_="navbox"):
            return None

        headline = heading.find("span", class_="mw-headline")
        if not headline:
            return None

        title = headline.get_text(strip=True)
        if not title:
            return None
        if not title.lower().startswith("update -"):
            return None

        anchor = headline.get("id")
        if not anchor:
            return None
        if not anchor.startswith("Update_-_"):
            return None

        entry_id = anchor
        url = f"{GAME_UPDATE_NOTES_PAGE_URL}#{anchor}"
        published_at = self._parse_heading_timestamp(title)

        section_elements = []
        for sibling in heading.find_next_siblings():
            if getattr(sibling, "name", None) == "h2":
                break
            section_elements.append(sibling)

        if not section_elements:
            summary = "New Guild Wars 2 game update notes are available."
            content_markdown = summary
        else:
            fragment = BeautifulSoup(
                "<div>" + "".join(str(el) for el in section_elements) + "</div>",
                "html.parser",
            )
            for unwanted in fragment.select("span.mw-editsection"):
                unwanted.decompose()
            for link in fragment.select("a[href]"):
                href = link.get("href", "")
                if href.startswith("/wiki/"):
                    link["href"] = f"https://wiki.guildwars2.com{href}"
                elif href.startswith("//"):
                    link["href"] = f"https:{href}"
            rendered = self._render_comment_content(fragment)
            content_markdown = rendered or "New Guild Wars 2 game update notes are available."
            summary = content_markdown

        return PatchNotesEntry(
            entry_id=entry_id,
            title=title,
            url=url,
            published_at=published_at,
            summary=summary,
            content=content_markdown,
            legacy_entry_ids=(url,),
        )

    def _parse_heading_timestamp(self, title: str) -> Optional[str]:
        match = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", title)
        if not match:
            return None
        date_text = match.group(1)
        try:
            parsed = datetime.strptime(date_text, "%B %d, %Y")
        except ValueError:
            LOGGER.debug("Unable to parse heading date: %s", title)
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()

    def _resolve_new_entries(
        self,
        entries: Sequence[PatchNotesEntry],
        last_entry_id: Optional[str],
        last_entry_published_at: Optional[str],
    ) -> List[PatchNotesEntry]:
        if not entries:
            return []

        collected: List[PatchNotesEntry] = []
        cutoff = self._parse_timestamp(last_entry_published_at)
        for entry in entries:
            if last_entry_id and (
                entry.entry_id == last_entry_id
                or last_entry_id in entry.legacy_entry_ids
            ):
                break

            entry_timestamp = self._parse_timestamp(entry.published_at)
            if cutoff and entry_timestamp and entry_timestamp <= cutoff:
                break

            collected.append(entry)

        return list(reversed(collected))

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

    def _build_embeds(
        self, entry: PatchNotesEntry, body: Optional[str]
    ) -> List[discord.Embed]:
        description = (
            body
            or entry.summary
            or "New Guild Wars 2 game update notes are available."
        )
        truncated_description = self._truncate(description, 4000)
        if not truncated_description:
            truncated_description = "New Guild Wars 2 game update notes are available."

        embeds: List[discord.Embed] = []
        parsed_timestamp = self._parse_timestamp(entry.published_at)
        embed = discord.Embed(
            title=entry.title,
            url=entry.url,
            description=truncated_description,
            color=self.EMBED_COLOR,
        )
        if parsed_timestamp:
            embed.timestamp = parsed_timestamp
        embed.set_thumbnail(url=EMBED_THUMBNAIL_URL)
        if truncated_description != description:
            embed.set_footer(text="Guild Wars 2 Wiki – Game Updates (truncated)")
        else:
            embed.set_footer(text="Guild Wars 2 Wiki – Game Updates")
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
            LOGGER.debug("Unable to parse game update notes timestamp: %s", value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

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
                    "Failed to fetch %s (attempt %s/%s)",
                    url,
                    attempt + 1,
                    retries,
                    exc_info=True,
                )
                if attempt + 1 < retries:
                    await asyncio.sleep(min(5, 2**attempt))
        if last_error is not None:
            LOGGER.warning("Giving up on %s after repeated failures", url, exc_info=last_error)
        return None

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
            body = entry.content or entry.summary
            embeds = self._build_embeds(entry, body)
            for embed in embeds:
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
