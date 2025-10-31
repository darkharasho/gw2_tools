"""Configuration cog for GW2 Tools."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..storage import GuildConfig


class ModeratorRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view: "ConfigView", default_roles: list[discord.Role]):
        super().__init__(
            placeholder="Select moderator roles (leave empty for admins only)",
            min_values=0,
            max_values=25,
            default_values=default_roles or None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        roles = [role.id for role in self.values]
        self.config_view.config.moderator_role_ids = roles
        self.config_view.persist()
        await interaction.response.send_message(
            "Moderator roles updated.", ephemeral=True
        )


class BuildChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "ConfigView", default_channel: Optional[discord.abc.GuildChannel]):
        super().__init__(
            placeholder="Select the channel to post builds",
            channel_types=(
                discord.ChannelType.text,
                discord.ChannelType.news,
                discord.ChannelType.forum,
            ),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values:
            channel = self.values[0]
            self.config_view.config.build_channel_id = channel.id
            await interaction.response.send_message(
                f"Build channel set to {channel.mention}.", ephemeral=True
            )
        else:
            self.config_view.config.build_channel_id = None
            await interaction.response.send_message(
                "Build channel cleared.", ephemeral=True
            )
        self.config_view.persist()


class ResetRolesButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.danger, label="Reset to admins")

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.config.moderator_role_ids = []
        self.view.persist()
        await interaction.response.send_message(
            "Moderator roles reset to server administrators only.", ephemeral=True
        )


class CloseButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Close")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Configuration closed.", view=None)
        self.view.stop()


class ConfigView(discord.ui.View):
    def __init__(self, bot: GW2ToolsBot, guild: discord.Guild, config: GuildConfig):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.config = config

        default_roles = [role for role_id in config.moderator_role_ids if (role := guild.get_role(role_id))]
        default_channel = guild.get_channel(config.build_channel_id) if config.build_channel_id else None

        self.add_item(ModeratorRoleSelect(self, default_roles))
        self.add_item(BuildChannelSelect(self, default_channel))
        self.add_item(ResetRolesButton())
        self.add_item(CloseButton())

    def persist(self) -> None:
        self.bot.save_config(self.guild.id, self.config)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class ConfigCog(commands.Cog):
    """Manage server configuration for GW2 Tools."""

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="config", description="Configure GW2 Tools settings for this server.")
    async def config_command(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Unable to resolve your server membership.", ephemeral=True)
            return
        if not self.bot.is_authorised(interaction.guild, interaction.user):
            await interaction.response.send_message("You do not have permission to configure GW2 Tools.", ephemeral=True)
            return

        config = self.bot.get_config(interaction.guild.id)
        view = ConfigView(self.bot, interaction.guild, config)

        await interaction.response.send_message(
            "Use the selectors below to update the GW2 Tools configuration.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(ConfigCog(bot))
