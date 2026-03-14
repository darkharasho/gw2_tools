"""Reset timer command for GW2 Tools."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR

LOGGER = logging.getLogger(__name__)

# NA alliance worlds are 11xxx, EU alliance worlds are 12xxx.
_EU_WORLD_PREFIX = 12


def _is_eu_world(world_id: Optional[int]) -> bool:
    """Return True if the alliance world ID belongs to the EU region."""
    if world_id is None:
        return False
    return world_id // 1000 == _EU_WORLD_PREFIX


def _next_wvw_reset(*, eu: bool) -> int:
    """Return the Unix timestamp for the next WvW reset.

    EU resets Friday 18:00 UTC, NA resets Saturday 02:00 UTC.
    """
    now = datetime.now(timezone.utc)
    if eu:
        # Friday is weekday 4
        target_weekday = 4
        target_hour = 18
        target_minute = 0
    else:
        # Saturday is weekday 5
        target_weekday = 5
        target_hour = 2
        target_minute = 0

    days_ahead = (target_weekday - now.weekday()) % 7
    next_reset = now.replace(
        hour=target_hour, minute=target_minute, second=0, microsecond=0,
    ) + timedelta(days=days_ahead)
    if next_reset <= now:
        next_reset += timedelta(weeks=1)
    return int(next_reset.timestamp())


class ResetCog(commands.Cog):
    """Show upcoming WvW reset time."""

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="reset", description="Show the next WvW weekly reset time.")
    async def reset_command(self, interaction: discord.Interaction) -> None:
        eu = False
        if interaction.guild:
            config = self.bot.get_config(interaction.guild.id)
            eu = _is_eu_world(config.alliance_server_id)

        region = "EU" if eu else "NA"
        wvw_ts = _next_wvw_reset(eu=eu)

        embed = discord.Embed(
            title="WvW Weekly Reset",
            colour=BRAND_COLOUR,
        )
        embed.set_thumbnail(
            url="https://wiki.guildwars2.com/images/thumb/9/97/"
            "Tyria_%28world%29_map_2.jpg/240px-Tyria_%28world%29_map_2.jpg"
        )
        embed.add_field(
            name=f"Next Reset ({region})",
            value=(
                f"<t:{wvw_ts}:F>\n"
                f"<t:{wvw_ts}:t> — <t:{wvw_ts}:R>"
            ),
            inline=False,
        )
        embed.set_footer(text="Guild Wars 2 Tools")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(ResetCog(bot))
