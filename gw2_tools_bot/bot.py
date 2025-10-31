"""Bot setup for GW2 Tools."""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from .storage import DEFAULT_STORAGE_ROOT, GuildConfig, StorageManager

LOGGER = logging.getLogger(__name__)


class GW2ToolsBot(commands.Bot):
    """Discord bot implementation for GW2 Tools."""

    def __init__(self, *, storage_root=DEFAULT_STORAGE_ROOT) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("gw2!"),
            intents=intents,
            application_id=None,
        )
        self.storage = StorageManager(storage_root)
        self.tree.on_error = self.on_app_command_error

    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        """Load cogs on startup."""

        await self.load_extension("gw2_tools_bot.cogs.config")
        await self.load_extension("gw2_tools_bot.cogs.builds")

    # ------------------------------------------------------------------
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        LOGGER.exception("App command error: %s", error)
        if interaction.response.is_done():
            await interaction.followup.send(
                "An unexpected error occurred while processing the command.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "An unexpected error occurred while processing the command.", ephemeral=True
            )

    # ------------------------------------------------------------------
    def get_config(self, guild_id: int) -> GuildConfig:
        return self.storage.get_config(guild_id)

    def save_config(self, guild_id: int, config: GuildConfig) -> None:
        self.storage.save_config(guild_id, config)

    # ------------------------------------------------------------------
    def is_authorised(self, guild: discord.Guild, member: discord.Member) -> bool:
        config = self.get_config(guild.id)
        if not config.moderator_role_ids:
            return member.guild_permissions.administrator
        role_ids = {role.id for role in member.roles}
        return bool(role_ids.intersection(config.moderator_role_ids)) or member.guild_permissions.administrator

    async def ensure_authorised(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return False
        if not self.is_authorised(interaction.guild, interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return False
        return True


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def create_bot() -> GW2ToolsBot:
    logging.basicConfig(level=logging.INFO)
    return GW2ToolsBot()


def run() -> None:
    """Entry point for running the bot via ``python -m gw2_tools_bot``."""

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set the DISCORD_TOKEN environment variable before running the bot.")
    bot = create_bot()
    bot.run(token)


__all__ = ["GW2ToolsBot", "create_bot", "run"]
