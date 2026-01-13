"""Help command for GW2 Tools."""
from __future__ import annotations

from collections import defaultdict
<<<<<<< HEAD
=======
import logging
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR


<<<<<<< HEAD
PUBLIC_COMMANDS = {
    "apikey add",
=======
LOGGER = logging.getLogger(__name__)


PUBLIC_COMMANDS = {
    "apikey add",
    "apikey refresh",
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    "apikey remove",
    "apikey help",
    "apikey list",
    "gw2guild search",
<<<<<<< HEAD
=======
    "help",
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
}


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

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show available GW2 Tools commands.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        is_authorised = False
        if guild and member:
            is_authorised = self.bot.is_authorised(
                guild,
                member,
<<<<<<< HEAD
                permissions=getattr(interaction, "permissions", None),
            )

        commands_list = self.bot.tree.get_commands(guild=guild)
        command_entries = _collect_commands(commands_list)
=======
            )
        
        LOGGER.info("Help command invoked by %s (auth=%s)", interaction.user, is_authorised)

        # Fetch global commands
        commands_list = self.bot.tree.get_commands(guild=None)
        # Fetch guild-specific commands if in a guild
        if guild:
            commands_list.extend(self.bot.tree.get_commands(guild=guild))

        command_entries = _collect_commands(commands_list)
        LOGGER.info("Collected %d commands for help display", len(command_entries))

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        lines_by_group: dict[str, list[str]] = defaultdict(list)

        for command in command_entries:
            qualified_name = command.qualified_name
<<<<<<< HEAD
            if not is_authorised and qualified_name not in PUBLIC_COMMANDS:
                continue
=======
            # Case-insensitive check for public commands
            is_public = qualified_name.lower() in {cmd.lower() for cmd in PUBLIC_COMMANDS}
            
            if not is_authorised and not is_public:
                continue

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
            group_name = qualified_name.split(" ", 1)[0]
            lines_by_group[group_name].append(
                f"/{qualified_name} — {command.description or 'No description provided.'}"
            )

        if not lines_by_group:
            await interaction.response.send_message(
                "No commands are available for your current permissions.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="GW2 Tools commands",
            description=(
                "Commands you can access based on your permissions."
                if is_authorised
                else "Commands you can access based on your permissions. "
                "Some additional commands require moderator permissions."
            ),
            colour=BRAND_COLOUR,
        )
        embed.set_footer(text="Guild Wars 2 Tools")

        for group_name in sorted(lines_by_group.keys()):
            entries = "\n".join(sorted(lines_by_group[group_name]))
            embed.add_field(name=f"/{group_name}", value=entries, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(HelpCog(bot))
