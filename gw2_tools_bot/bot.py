"""Bot setup for GW2 Tools."""
from __future__ import annotations

import logging
import os
from typing import Set

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

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
        self._global_sync_done = False
        self._synced_guilds: Set[int] = set()

    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        """Load cogs on startup."""

        await self.load_extension("gw2_tools_bot.cogs.config")
        await self.load_extension("gw2_tools_bot.cogs.accounts")
        await self.load_extension("gw2_tools_bot.cogs.builds")
        await self.load_extension("gw2_tools_bot.cogs.arcdps")
        await self.load_extension("gw2_tools_bot.cogs.update_notes")
        await self.load_extension("gw2_tools_bot.cogs.rss")
        await self.load_extension("gw2_tools_bot.cogs.comps")

    async def on_ready(self) -> None:
        await self._sync_global_commands()
        for guild in self.guilds:
            await self._sync_guild_commands(guild)
        LOGGER.info("GW2 Tools is ready. Logged in as %s (%s)", self.user, getattr(self.user, "id", "unknown"))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._sync_global_commands()
        await self._sync_guild_commands(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self._sync_global_commands()
        await self._sync_guild_commands(guild)

    async def _sync_global_commands(self) -> None:
        if self._global_sync_done:
            return
        try:
            synced = await self.tree.sync()
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to sync global application commands")
        else:
            LOGGER.info("Synced %s global application commands", len(synced))
            self._global_sync_done = True

    async def _sync_guild_commands(self, guild: discord.Guild) -> None:
        if guild.id in self._synced_guilds:
            return
        try:
            synced = await self.tree.sync(guild=guild)
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to sync application commands for guild %s", guild.id)
        else:
            LOGGER.info("Synced %s application commands for guild %s", len(synced), guild.id)
            self._synced_guilds.add(guild.id)

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
    def is_authorised(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        permissions: discord.Permissions | None = None,
    ) -> bool:
        config = self.get_config(guild.id)

        if permissions is None:
            try:
                permissions = member.guild_permissions
            except AttributeError:
                LOGGER.warning(
                    "Unable to resolve guild permissions for member %s in guild %s; assuming none",
                    getattr(member, "id", "unknown"),
                    getattr(guild, "id", "unknown"),
                )
                permissions = discord.Permissions.none()

        if permissions.administrator:
            return True

        if not config.moderator_role_ids:
            return False

        role_ids = set(getattr(member, "_roles", ()))
        role_ids.update(role.id for role in getattr(member, "roles", []) if role is not None)

        return bool(role_ids.intersection(config.moderator_role_ids))

    async def ensure_authorised(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return False
        if not self.is_authorised(
            interaction.guild,
            interaction.user,
            permissions=getattr(interaction, "permissions", None),
        ):
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
