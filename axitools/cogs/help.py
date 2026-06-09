"""Help command for AxiTools."""
from __future__ import annotations

from collections import defaultdict
import logging
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR


LOGGER = logging.getLogger(__name__)


DEFAULT_CATEGORY = "Other"


def _walk_extras(command: app_commands.Command, key: str):
    """Return the nearest ``extras[key]`` value from the command or its parents."""
    node = command
    while node is not None:
        value = getattr(node, "extras", {}).get(key)
        if value is not None:
            return value
        node = getattr(node, "parent", None)
    return None


def _is_public(command: app_commands.Command) -> bool:
    return bool(_walk_extras(command, "public"))


def _category(command: app_commands.Command) -> str:
    return _walk_extras(command, "category") or DEFAULT_CATEGORY


def _collect_commands(
    commands_list: Iterable[app_commands.Command | app_commands.Group],
) -> list[app_commands.Command]:
    collected: list[app_commands.Command] = []
    for command in commands_list:
        if isinstance(command, app_commands.Group):
            collected.extend(_collect_commands(command.commands))
        else:
            collected.append(command)
    return collected


class HelpCog(commands.Cog):
    """Provide a permissions-aware help command."""

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show available AxiTools commands.", extras={"public": True, "category": "General"})
    async def help_command(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        is_authorised = False
        if guild and member:
            is_authorised = self.bot.is_authorised(
                guild,
                member,
            )
        
        LOGGER.info("Help command invoked by %s (auth=%s)", interaction.user, is_authorised)

        # Fetch global commands
        commands_list = self.bot.tree.get_commands(guild=None)
        # Fetch guild-specific commands if in a guild
        if guild:
            commands_list.extend(self.bot.tree.get_commands(guild=guild))

        command_entries = _collect_commands(commands_list)
        LOGGER.info("Collected %d commands for help display", len(command_entries))

        lines_by_category: dict[str, list[str]] = defaultdict(list)

        for command in command_entries:
            if not is_authorised and not _is_public(command):
                continue

            lines_by_category[_category(command)].append(
                f"/{command.qualified_name} — {command.description or 'No description provided.'}"
            )

        if not lines_by_category:
            await interaction.response.send_message(
                "No commands are available for your current permissions.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="AxiTools commands",
            description=(
                "Commands you can access based on your permissions."
                if is_authorised
                else "Commands you can access based on your permissions. "
                "Some additional commands require moderator permissions."
            ),
            colour=BRAND_COLOUR,
        )
        embed.set_footer(text="Guild Wars 2 Tools")

        for category in sorted(lines_by_category.keys()):
            entries = "\n".join(sorted(lines_by_category[category]))
            embed.add_field(name=category, value=entries, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(HelpCog(bot))
