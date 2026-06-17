"""Configuration cog for AxiTools."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..api.server import generate_app_key, hash_app_key
from ..bot import AxiToolsBot
from ..config_status import ConfigStatus, StatusField
from ..storage import GuildConfig


def _discord_ts(iso: Optional[str]) -> str:
    """Render a stored ISO timestamp as a relative Discord timestamp."""
    if not iso:
        return "never"
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return iso
    return f"<t:{int(dt.timestamp())}:R>"


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


class ArcDpsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "ConfigView", default_channel: Optional[discord.abc.GuildChannel]):
        super().__init__(
            placeholder="Select the channel for ArcDPS update notifications",
            channel_types=(
                discord.ChannelType.text,
                discord.ChannelType.news,
            ),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values:
            channel = self.values[0]
            self.config_view.config.arcdps_channel_id = channel.id
            message = f"ArcDPS updates channel set to {channel.mention}."
        else:
            self.config_view.config.arcdps_channel_id = None
            message = "ArcDPS updates disabled."
        self.config_view.persist()
        await interaction.response.send_message(message, ephemeral=True)


class UpdateNotesChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self, view: "ConfigView", default_channel: Optional[discord.abc.GuildChannel]
    ) -> None:
        super().__init__(
            placeholder="Select the channel for game update notes notifications",
            channel_types=(
                discord.ChannelType.text,
                discord.ChannelType.news,
            ),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values:
            channel = self.values[0]
            self.config_view.config.update_notes_channel_id = channel.id
            message = f"Game update notes channel set to {channel.mention}."
        else:
            self.config_view.config.update_notes_channel_id = None
            message = "Game update notes notifications disabled."
        self.config_view.persist()
        await interaction.response.send_message(message, ephemeral=True)


class GameNewsChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self, view: "discord.ui.View", default_channel: Optional[discord.abc.GuildChannel]
    ) -> None:
        super().__init__(
            placeholder="Select the channel for GW2 + GW3 news",
            channel_types=(discord.ChannelType.text, discord.ChannelType.news),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values:
            channel = self.values[0]
            self.config_view.config.game_news_channel_id = channel.id
            message = f"Game news channel set to {channel.mention}."
        else:
            self.config_view.config.game_news_channel_id = None
            message = "Game news notifications disabled."
        self.config_view.persist()
        await interaction.response.send_message(message, ephemeral=True)


class MoreChannelsView(discord.ui.View):
    """Secondary config page for channels that overflow the primary view's
    5-action-row limit."""

    def __init__(
        self,
        parent_view: "ConfigView",
        default_channel: Optional[discord.abc.GuildChannel],
    ) -> None:
        super().__init__(timeout=300)
        # Reuse the parent's config + persist so edits save to the same place.
        self.config = parent_view.config
        self.persist = parent_view.persist
        self.add_item(GameNewsChannelSelect(self, default_channel))
        self.add_item(CloseButton())


class ResetRolesButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.danger, label="Reset to admins")

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.config.moderator_role_ids = []
        self.view.persist()
        await interaction.response.send_message(
            "Moderator roles reset to server administrators only.", ephemeral=True
        )


class MoreChannelsButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="More channels…")

    async def callback(self, interaction: discord.Interaction) -> None:
        config = self.view.config
        default_channel = (
            interaction.guild.get_channel(config.game_news_channel_id)
            if config.game_news_channel_id
            else None
        )
        await interaction.response.send_message(
            "Additional channels:",
            view=MoreChannelsView(self.view, default_channel),
            ephemeral=True,
        )


class CloseButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Close")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Configuration closed.", view=None)
        self.view.stop()


class ConfigView(discord.ui.View):
    def __init__(self, bot: AxiToolsBot, guild: discord.Guild, config: GuildConfig):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.config = config

        default_roles = [role for role_id in config.moderator_role_ids if (role := guild.get_role(role_id))]
        default_channel = guild.get_channel(config.build_channel_id) if config.build_channel_id else None
        default_arcdps_channel = (
            guild.get_channel(config.arcdps_channel_id) if config.arcdps_channel_id else None
        )
        default_update_notes_channel = (
            guild.get_channel(config.update_notes_channel_id)
            if config.update_notes_channel_id
            else None
        )

        self.add_item(ModeratorRoleSelect(self, default_roles))
        self.add_item(BuildChannelSelect(self, default_channel))
        self.add_item(ArcDpsChannelSelect(self, default_arcdps_channel))
        self.add_item(UpdateNotesChannelSelect(self, default_update_notes_channel))
        self.add_item(ResetRolesButton())
        self.add_item(MoreChannelsButton())
        self.add_item(CloseButton())

    def persist(self) -> None:
        self.bot.save_config(self.guild.id, self.config)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


STATE_EMOJI = {"ok": "✅", "warn": "⚠️", "missing": "❌"}


class StatusView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)

    @discord.ui.button(label="Open Config", style=discord.ButtonStyle.primary)
    async def open_config(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Use `/config setup` to update your server settings.", ephemeral=True
        )


class ConfigCog(commands.GroupCog, name="config", group_extras={"category": "Server Setup"}):
    """Manage server configuration for AxiTools."""

    apikey = app_commands.Group(
        name="apikey",
        description="Manage the AxiVale API key for this server.",
    )

    def __init__(self, bot: AxiToolsBot) -> None:
        super().__init__()
        self.bot = bot

    @staticmethod
    async def _check_manage_guild(interaction: discord.Interaction) -> bool:
        """Gate apikey commands behind Manage Server, replying ephemerally on failure."""
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used inside a server.", ephemeral=True
            )
            return False
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Unable to resolve your server membership.", ephemeral=True
            )
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the **Manage Server** permission to manage the AxiVale API key.",
                ephemeral=True,
            )
            return False
        return True

    @apikey.command(name="generate", description="Generate a new AxiVale API key for this server.")
    @app_commands.describe(name="A label to tell this key apart (e.g. laptop, work-pc).")
    async def apikey_generate(
        self, interaction: discord.Interaction, name: Optional[str] = None
    ) -> None:
        if not await self._check_manage_guild(interaction):
            return

        key = generate_app_key()
        info = self.bot.storage.add_app_key(
            interaction.guild.id, hash_app_key(key), interaction.user.id, name or "default"
        )
        total = len(self.bot.storage.list_app_keys(interaction.guild.id))
        note = (
            f"\nThis server now has **{total}** keys — existing keys stay valid. "
            "Use `/config apikey list` to review or `/config apikey revoke` to remove one."
            if total > 1
            else ""
        )
        await interaction.response.send_message(
            f"AxiVale API key **{info.label}** for **{interaction.guild.name}**:\n"
            f"```\n{key}\n```\n"
            "Paste this into AxiVale → Settings → AxiTools to connect this server.\n"
            f"⚠️ This key is only shown once.{note}",
            ephemeral=True,
        )

    @apikey.command(name="list", description="List this server's AxiVale API keys.")
    async def apikey_list(self, interaction: discord.Interaction) -> None:
        if not await self._check_manage_guild(interaction):
            return

        keys = self.bot.storage.list_app_keys(interaction.guild.id)
        if not keys:
            await interaction.response.send_message(
                "No AxiVale API keys on file for this server. "
                "Generate one with `/config apikey generate`.",
                ephemeral=True,
            )
            return

        lines = [
            f"• **{k.label}** — added {_discord_ts(k.created_at)} by <@{k.created_by}> "
            f"· last used {_discord_ts(k.last_used_at)}"
            for k in keys
        ]
        await interaction.response.send_message(
            f"**AxiVale API keys for {interaction.guild.name}** ({len(keys)}):\n"
            + "\n".join(lines)
            + "\n\nRevoke one with `/config apikey revoke name:<label>`.",
            ephemeral=True,
        )

    @apikey.command(name="revoke", description="Revoke an AxiVale API key by name (or all).")
    @app_commands.describe(
        name="The key label to revoke. Leave empty to revoke ALL keys for this server."
    )
    async def apikey_revoke(
        self, interaction: discord.Interaction, name: Optional[str] = None
    ) -> None:
        if not await self._check_manage_guild(interaction):
            return

        if name:
            removed = self.bot.storage.revoke_app_key(interaction.guild.id, name)
            message = (
                f"Revoked the AxiVale key **{name}**."
                if removed
                else f"No AxiVale key named **{name}** on this server. See `/config apikey list`."
            )
        else:
            removed = self.bot.storage.revoke_app_key(interaction.guild.id)
            message = (
                f"Revoked **all {removed}** AxiVale key(s) for this server."
                if removed
                else "No AxiVale API keys on file for this server."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="setup", description="Open AxiTools settings for this server.")
    async def config_command(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Unable to resolve your server membership.", ephemeral=True)
            return
        if not self.bot.is_authorised(
            interaction.guild,
            interaction.user,
            permissions=getattr(interaction, "permissions", None),
        ):
            await interaction.response.send_message("You do not have permission to configure AxiTools.", ephemeral=True)
            return

        config = self.bot.get_config(interaction.guild.id)
        view = ConfigView(self.bot, interaction.guild, config)

        await interaction.response.send_message(
            "Use the selectors below to update the AxiTools configuration.",
            view=view,
            ephemeral=True,
        )

    def _is_first_run(self, guild_id: int) -> bool:
        """Return True if no meaningful config has been set for this guild."""
        config = self.bot.get_config(guild_id)
        return not any([
            config.build_channel_id,
            config.arcdps_channel_id,
            config.update_notes_channel_id,
            config.moderator_role_ids,
            config.comp_schedules,
        ])

    def _build_status_embed(self, guild: discord.Guild) -> discord.Embed:
        """Build a rich embed showing config status across all feature cogs."""
        cog_names = [
            "BuildsCog",
            "RssFeedsCog",
            "ArcDpsUpdatesCog",
            "UpdateNotesCog",
            "GameNewsCog",
            "SelectCog",
            "CompCog",
            "AccountSelfCog",
            "AuditCog",
        ]

        statuses: list[ConfigStatus] = []
        for name in cog_names:
            cog = self.bot.get_cog(name)
            if cog is not None and hasattr(cog, "get_config_status"):
                statuses.append(cog.get_config_status(guild.id))

        any_missing = any(
            f.state == "missing"
            for s in statuses
            for f in s.fields
        )
        colour = discord.Colour.red() if any_missing else discord.Colour.green()

        embed = discord.Embed(
            title="AxiTools Configuration Status",
            colour=colour,
        )

        for status in statuses:
            lines = []
            for field in status.fields:
                emoji = STATE_EMOJI.get(field.state, "•")
                lines.append(f"{emoji} **{field.label}:** {field.value}")
            value = "\n".join(lines) or "No fields reported."
            embed.add_field(name=status.title, value=value, inline=False)

        return embed

    @app_commands.command(name="status", description="View AxiTools configuration status for this server.")
    async def status_command(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Unable to resolve your server membership.", ephemeral=True)
            return
        if not self.bot.is_authorised(
            interaction.guild,
            interaction.user,
            permissions=getattr(interaction, "permissions", None),
        ):
            await interaction.response.send_message("You do not have permission to view AxiTools status.", ephemeral=True)
            return

        embed = self._build_status_embed(interaction.guild)

        if self._is_first_run(interaction.guild.id):
            embed.description = (
                "Looks like AxiTools hasn't been configured yet. "
                "Use `/config setup` to get started!"
            )

        await interaction.response.send_message(embed=embed, view=StatusView(), ephemeral=True)

    def get_config_status(self, guild_id: int) -> ConfigStatus:
        config = self.bot.get_config(guild_id)
        fields = []
        if config.moderator_role_ids:
            fields.append(StatusField(
                label="Moderator Roles",
                value=f"{len(config.moderator_role_ids)} role(s) configured",
                state="ok",
            ))
        else:
            fields.append(StatusField(
                label="Moderator Roles",
                value="Defaults to server administrators",
                state="warn",
            ))
        return ConfigStatus(
            title="Bot Configuration",
            fields=fields,
            setup_command="/config setup",
        )


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(ConfigCog(bot))
