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


def _next_daily_reset() -> int:
    """Return the Unix timestamp for the next daily reset (00:00 UTC)."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour == 0 and now.minute == 0 and now.second == 0:
        return int(now.timestamp())
    return int(tomorrow.timestamp())


def _next_weekly_reset() -> int:
    """Return the Unix timestamp for the next weekly reset (Monday 07:30 UTC)."""
    now = datetime.now(timezone.utc)
    # Monday is weekday 0
    days_until_monday = (0 - now.weekday()) % 7
    next_monday = now.replace(hour=7, minute=30, second=0, microsecond=0) + timedelta(days=days_until_monday)
    if next_monday <= now:
        next_monday += timedelta(weeks=1)
    return int(next_monday.timestamp())


class ResetCog(commands.Cog):
    """Show upcoming GW2 reset times."""

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="reset", description="Show upcoming Guild Wars 2 reset times.")
    async def reset_command(self, interaction: discord.Interaction) -> None:
        daily_ts = _next_daily_reset()
        weekly_ts = _next_weekly_reset()

        embed = discord.Embed(
            title="Guild Wars 2 Reset Times",
            colour=BRAND_COLOUR,
        )
        embed.add_field(
            name="Daily Reset",
            value=(
                f"<t:{daily_ts}:F>\n"
                f"<t:{daily_ts}:t> — <t:{daily_ts}:R>"
            ),
            inline=False,
        )
        embed.add_field(
            name="Weekly Reset",
            value=(
                f"<t:{weekly_ts}:F>\n"
                f"<t:{weekly_ts}:t> — <t:{weekly_ts}:R>"
            ),
            inline=False,
        )
        embed.set_footer(text="Guild Wars 2 Tools")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(ResetCog(bot))
