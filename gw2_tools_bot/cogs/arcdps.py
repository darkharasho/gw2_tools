"""ArcDPS release monitoring cog."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..storage import ArcDpsStatus

LOGGER = logging.getLogger(__name__)


class ArcDpsUpdatesCog(commands.Cog):
    """Check for ArcDPS updates and notify configured channels."""

    CHECK_INTERVAL_MINUTES = 15
    RELEASE_URL = "https://www.deltaconnected.com/arcdps/x64/"
    PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self.arcdps_check.start()

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self.arcdps_check.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def arcdps_check(self) -> None:
        if not self.bot.guilds:
            return

        latest_release = await self._fetch_latest_release_time()
        if not latest_release:
            return

        for guild in self.bot.guilds:
            config = self.bot.get_config(guild.id)
            channel_id = config.arcdps_channel_id
            if not channel_id:
                continue

            stored_status = self.bot.storage.get_arcdps_status(guild.id)
            if stored_status is None:
                self.bot.storage.save_arcdps_status(
                    guild.id, ArcDpsStatus(last_updated_at=latest_release.isoformat())
                )
                continue

            try:
                stored_timestamp = datetime.fromisoformat(stored_status.last_updated_at)
            except ValueError:
                stored_timestamp = None

            if stored_timestamp and latest_release <= stored_timestamp:
                continue

            channel = await self._resolve_notification_channel(guild, channel_id)
            if not channel:
                continue

            embed = self._build_embed(guild, latest_release)
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post ArcDPS update in channel %s for guild %s", channel_id, guild.id
                )
                continue

            self.bot.storage.save_arcdps_status(
                guild.id, ArcDpsStatus(last_updated_at=latest_release.isoformat())
            )

    @arcdps_check.before_loop
    async def before_arcdps_check(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def _fetch_latest_release_time(self) -> Optional[datetime]:
        session = await self._get_session()
        try:
            async with session.get(self.RELEASE_URL) as response:
                response.raise_for_status()
                html = await response.text()
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

    def _build_embed(self, guild: discord.Guild, release_time: datetime) -> discord.Embed:
        timestamp = int(release_time.timestamp())
        embed = discord.Embed(
            title="ArcDPS Update Available",
            description="A new ArcDPS release has been detected.",
            colour=discord.Colour.from_rgb(255, 219, 90),
            url=self.RELEASE_URL,
        )
        embed.add_field(name="Updated", value=f"<t:{timestamp}:R>")
        embed.add_field(name="Release time", value=f"<t:{timestamp}:F>")
        embed.add_field(
            name="Download",
            value=f"[Get the latest build]({self.RELEASE_URL})",
            inline=False,
        )
        if guild.icon:
            embed.set_author(name=f"{guild.name} - ArcDPS Releases", icon_url=guild.icon.url)
        else:
            embed.set_author(name=f"{guild.name} - ArcDPS Releases")
        embed.set_footer(text="ArcDPS release monitor")
        return embed

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

    @app_commands.command(name="arcdps_force_notification", description="Send a test ArcDPS notification.")
    async def force_notification(self, interaction: discord.Interaction) -> None:
        """Allow developers to trigger a notification in non-production environments."""

        if self.PRODUCTION:
            await interaction.response.send_message(
                "This command is disabled in production environments.",
                ephemeral=True,
            )
            return

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
        embed = self._build_embed(interaction.guild, release_time)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Failed to send the ArcDPS notification. Check bot permissions.",
                ephemeral=True,
            )
            return

        self.bot.storage.save_arcdps_status(
            interaction.guild.id, ArcDpsStatus(last_updated_at=release_time.isoformat())
        )

        await interaction.response.send_message(
            f"Sent a test ArcDPS notification to {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(ArcDpsUpdatesCog(bot))
