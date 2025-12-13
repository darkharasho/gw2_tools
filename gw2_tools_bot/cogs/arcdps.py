"""ArcDPS release monitoring cog."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Union

import aiohttp
import discord
from bs4 import BeautifulSoup, NavigableString, Tag
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..storage import ArcDpsStatus
from ..http_utils import read_response_text

LOGGER = logging.getLogger(__name__)


ARCDPS_IMAGE_FILENAME = "arcdps.png"
ARCDPS_IMAGE_PATH = Path(__file__).resolve().parents[2] / "media" / ARCDPS_IMAGE_FILENAME


class ArcDpsUpdatesCog(commands.Cog):
    """Check for ArcDPS updates and notify configured channels."""

    CHECK_INTERVAL_MINUTES = 15
    RELEASE_URL = "https://www.deltaconnected.com/arcdps/x64/"
    CHANGELOG_URL = "https://www.deltaconnected.com/arcdps/"
    PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self.arcdps_check.start()

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept-Encoding": "gzip, deflate, br"},
                auto_decompress=False,
            )
        return self._session

    def _store_status(
        self,
        guild_id: int,
        *,
        last_checked_at: datetime,
        last_updated_at: Optional[Union[datetime, str]],
    ) -> None:
        if isinstance(last_updated_at, datetime):
            updated_value = last_updated_at.isoformat()
        else:
            updated_value = last_updated_at

        self.bot.storage.save_arcdps_status(
            guild_id,
            ArcDpsStatus(
                last_checked_at=last_checked_at.isoformat(),
                last_updated_at=updated_value,
            ),
        )

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self.arcdps_check.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    def _parse_iso_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            LOGGER.warning("Stored ArcDPS timestamp '%s' is not valid ISO format", value)
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def arcdps_check(self) -> None:
        if not self.bot.guilds:
            return

        latest_release = await self._fetch_latest_release_time()
        if not latest_release:
            return

        changes_info: Optional[Tuple[Optional[str], List[str]]] = None

        for guild in self.bot.guilds:
            config = self.bot.get_config(guild.id)
            channel_id = config.arcdps_channel_id
            if not channel_id:
                continue

            now = datetime.now(timezone.utc)
            stored_status = self.bot.storage.get_arcdps_status(guild.id)
            if stored_status is None:
                self._store_status(guild.id, last_checked_at=now, last_updated_at=latest_release)
                continue

            stored_timestamp = self._parse_iso_timestamp(stored_status.last_updated_at)
            if stored_timestamp and latest_release <= stored_timestamp:
                self._store_status(
                    guild.id,
                    last_checked_at=now,
                    last_updated_at=stored_status.last_updated_at,
                )
                continue

            channel = await self._resolve_notification_channel(guild, channel_id)
            if not channel:
                self._store_status(
                    guild.id,
                    last_checked_at=now,
                    last_updated_at=stored_status.last_updated_at,
                )
                continue

            if changes_info is None:
                changes_info = await self._fetch_latest_changes()

            embed, thumbnail = self._build_embed(latest_release, changes_info)
            try:
                send_kwargs = {"embed": embed}
                if thumbnail:
                    send_kwargs["file"] = thumbnail
                await channel.send(**send_kwargs)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post ArcDPS update in channel %s for guild %s", channel_id, guild.id
                )
                self._store_status(
                    guild.id,
                    last_checked_at=now,
                    last_updated_at=stored_status.last_updated_at,
                )
                continue

            self._store_status(guild.id, last_checked_at=now, last_updated_at=latest_release)

    @arcdps_check.before_loop
    async def before_arcdps_check(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def _fetch_latest_release_time(self) -> Optional[datetime]:
        session = await self._get_session()
        try:
            async with session.get(self.RELEASE_URL) as response:
                response.raise_for_status()
                html = await read_response_text(response)
        except aiohttp.ClientError:
            LOGGER.warning("Failed to fetch ArcDPS release page", exc_info=True)
            return None

        soup = BeautifulSoup(html, "html.parser")
        row = soup.body.find("tr", attrs={"class": "odd"}) if soup.body else None
        if not row:
            LOGGER.warning("Unable to parse ArcDPS release information from %s", self.RELEASE_URL)
            return None

        last_modified = row.find("td", attrs={"class": "indexcollastmod"})
        if not last_modified or not last_modified.text:
            LOGGER.warning("ArcDPS release page did not contain a last modified value")
            return None

        try:
            parsed = datetime.strptime(last_modified.text.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            LOGGER.warning("Unexpected ArcDPS release timestamp format: %s", last_modified.text)
            return None

        return parsed.replace(tzinfo=timezone.utc)

    async def _fetch_latest_changes(self) -> Tuple[Optional[str], List[str]]:
        session = await self._get_session()
        try:
            async with session.get(self.CHANGELOG_URL) as response:
                response.raise_for_status()
                html = await read_response_text(response)
        except aiohttp.ClientError:
            LOGGER.warning("Failed to fetch ArcDPS changelog page", exc_info=True)
            return None, []

        soup = BeautifulSoup(html, "html.parser")
        header = soup.find("b", string=lambda s: s and s.strip().lower() == "changes")
        if not header:
            LOGGER.warning("Unable to locate ArcDPS changelog section")
            return None, []

        latest_date: Optional[str] = None
        entries: List[str] = []

        for node in header.next_siblings:
            if isinstance(node, NavigableString):
                text = str(node)
            elif isinstance(node, Tag):
                if node.name == "br":
                    continue
                if node.name == "b":
                    break
                text = node.get_text(separator=" ", strip=True)
            else:
                continue

            text = text.replace("\xa0", " ").strip()
            if not text or ":" not in text:
                continue

            date_part, _, description = text.partition(":")
            date_part = date_part.strip()
            description = description.strip()
            if not description:
                continue

            if latest_date is None:
                latest_date = date_part
            if date_part != latest_date:
                break

            entries.append(description)

        return latest_date, entries

    def _format_changelog_date(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None

        sanitized = value.replace(" ", "")
        try:
            parsed = datetime.strptime(sanitized, "%b.%d.%Y")
        except ValueError:
            return value

        return parsed.strftime("%B %d, %Y")

    def _build_embed(
        self,
        release_time: datetime,
        changes: Optional[Tuple[Optional[str], List[str]]],
    ) -> Tuple[discord.Embed, Optional[discord.File]]:
        timestamp = int(release_time.timestamp())
        embed = discord.Embed(
            title="ArcDPS Update",
            colour=BRAND_COLOUR,
            url=self.RELEASE_URL,
        )

        change_date, change_entries = changes if changes else (None, [])
        description_lines: List[str] = []

        if change_entries:
            formatted_date = self._format_changelog_date(change_date)
            header = (
                f"Changes for {formatted_date}"
                if formatted_date
                else "Latest Changes"
            )
            description_lines.append(f"**{header}**")

            bullets: List[str] = []
            running_length = sum(len(line) + 1 for line in description_lines)
            for entry in change_entries:
                clean_entry = entry.replace("\xa0", " ").strip()
                if not clean_entry:
                    continue
                bullet = f"• {clean_entry}"
                projected = running_length + len(bullet) + 1
                if projected > 4096:
                    bullets.append("• …")
                    break
                bullets.append(bullet)
                running_length = projected

            description_lines.extend(bullets)

        if description_lines:
            embed.description = "\n".join(description_lines)

        embed.add_field(name="Updated", value=f"<t:{timestamp}:R>")
        embed.add_field(name="Release time", value=f"<t:{timestamp}:F>")
        embed.add_field(
            name="Download",
            value=f"[Get the latest build]({self.RELEASE_URL})",
            inline=False,
        )

        thumbnail = self._attach_thumbnail(embed)
        return embed, thumbnail

    def _attach_thumbnail(self, embed: discord.Embed) -> Optional[discord.File]:
        if not ARCDPS_IMAGE_PATH.exists():
            LOGGER.warning("ArcDPS thumbnail missing at %s", ARCDPS_IMAGE_PATH)
            return None

        embed.set_thumbnail(url=f"attachment://{ARCDPS_IMAGE_FILENAME}")
        return discord.File(ARCDPS_IMAGE_PATH, filename=ARCDPS_IMAGE_FILENAME)

    async def _resolve_notification_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOGGER.warning(
                    "Unable to fetch ArcDPS updates channel %s for guild %s", channel_id, guild.id
                )
                return None

        if not isinstance(channel, discord.TextChannel):
            LOGGER.warning(
                "Configured ArcDPS channel %s in guild %s is not a text-based channel", channel_id, guild.id
            )
            return None

        return channel

    if not PRODUCTION:

        @app_commands.command(
            name="arcdps_force_notification",
            description="Send a test ArcDPS notification.",
        )
        async def force_notification(self, interaction: discord.Interaction) -> None:
            """Allow developers to trigger a notification in non-production environments."""

            if not interaction.guild:
                await interaction.response.send_message(
                    "This command must be used inside a server.", ephemeral=True
                )
                return

            if not await self.bot.ensure_authorised(interaction):
                return

            config = self.bot.get_config(interaction.guild.id)
            channel_id = config.arcdps_channel_id
            if not channel_id:
                await interaction.response.send_message(
                    "ArcDPS notifications are disabled for this server.",
                    ephemeral=True,
                )
                return

            channel = await self._resolve_notification_channel(interaction.guild, channel_id)
            if not channel:
                await interaction.response.send_message(
                    "Unable to locate the configured ArcDPS channel.",
                    ephemeral=True,
                )
                return

            release_time = datetime.now(timezone.utc)
            changes_info = await self._fetch_latest_changes()
            embed, thumbnail = self._build_embed(release_time, changes_info)

            try:
                send_kwargs = {"embed": embed}
                if thumbnail:
                    send_kwargs["file"] = thumbnail
                await channel.send(**send_kwargs)
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message(
                    "Failed to send the ArcDPS notification. Check bot permissions.",
                    ephemeral=True,
                )
                return

            now = datetime.now(timezone.utc)
            self._store_status(
                interaction.guild.id, last_checked_at=now, last_updated_at=release_time
            )

            await interaction.response.send_message(
                f"Sent a test ArcDPS notification to {channel.mention}.",
                ephemeral=True,
            )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(ArcDpsUpdatesCog(bot))
