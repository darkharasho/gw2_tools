"""Developer/test commands, only registered outside production."""
from __future__ import annotations

import os

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import AxiToolsBot

PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}


class DevCog(commands.GroupCog, name="dev", group_extras={"category": "Dev"}):
    """Developer-only test triggers."""

    def __init__(self, bot: AxiToolsBot) -> None:
        super().__init__()
        self.bot = bot

    @app_commands.command(name="arcdps", description="Send a test ArcDPS notification.")
    async def arcdps(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("ArcDpsUpdatesCog")
        await cog.run_force_notification(interaction)

    @app_commands.command(
        name="updatenotes",
        description="Send the latest game update notes notification.",
    )
    async def updatenotes(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("UpdateNotesCog")
        await cog.run_force_notification(interaction)

    @app_commands.command(
        name="rsstest",
        description="Post the latest entry from a configured RSS feed to its channel.",
    )
    async def rsstest(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("RssFeedsCog")
        await cog.run_test_feed(interaction)


async def setup(bot: AxiToolsBot) -> None:
    if not PRODUCTION:
        await bot.add_cog(DevCog(bot))
