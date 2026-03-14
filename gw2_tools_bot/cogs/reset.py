"""Reset timer command for GW2 Tools."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR

LOGGER = logging.getLogger(__name__)


def _next_wvw_reset() -> int:
    """Return the Unix timestamp for the next WvW reset (Saturday 00:00 UTC)."""
    now = datetime.now(timezone.utc)
    # Saturday is weekday 5
    days_until_saturday = (5 - now.weekday()) % 7
    next_saturday = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_saturday)
    if next_saturday <= now:
        next_saturday += timedelta(weeks=1)
    return int(next_saturday.timestamp())


class ResetCog(commands.Cog):
    """Show upcoming GW2 reset times."""

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="reset", description="Show the next WvW weekly reset time.")
    async def reset_command(self, interaction: discord.Interaction) -> None:
        wvw_ts = _next_wvw_reset()

        embed = discord.Embed(
            title="WvW Weekly Reset",
            colour=BRAND_COLOUR,
        )
        embed.set_thumbnail(
            url="https://wiki.guildwars2.com/images/thumb/9/97/"
            "Tyria_%28world%29_map_2.jpg/240px-Tyria_%28world%29_map_2.jpg"
        )
        embed.add_field(
            name="Next Reset",
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
