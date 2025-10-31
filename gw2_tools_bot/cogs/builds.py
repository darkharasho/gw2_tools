"""Build management cog for GW2 Tools."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..storage import BuildRecord, utcnow
from ..utils import build_embed, get_icon_and_color, resolve_profession
from .. import constants

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "build"


class BuildsCog(commands.GroupCog, name="builds"):
    """Manage Guild Wars 2 build posts."""

    description = "Manage Guild Wars 2 builds."

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    async def _get_build_channel(self, guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        config = self.bot.get_config(guild.id)
        if not config.build_channel_id:
            return None
        channel = guild.get_channel(config.build_channel_id)
        if channel:
            return channel
        try:
            channel = await guild.fetch_channel(config.build_channel_id)
        except discord.HTTPException:
            return None
        return channel

    async def _delete_existing_post(self, record: BuildRecord, guild: discord.Guild) -> None:
        if not record.channel_id or not record.message_id:
            return
        channel = guild.get_channel(record.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(record.channel_id)
            except discord.HTTPException:
                return
        if isinstance(channel, discord.ForumChannel) and record.thread_id:
            thread = channel.get_thread(record.thread_id)
            if not thread:
                try:
                    thread = await channel.fetch_thread(record.thread_id)
                except discord.HTTPException:
                    thread = None
            if thread:
                try:
                    await thread.delete()
                except discord.HTTPException:
                    pass
        elif isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                message = await channel.fetch_message(record.message_id)
            except discord.HTTPException:
                message = None
            if message:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

    async def _ensure_channel(self, interaction: discord.Interaction) -> Optional[discord.abc.GuildChannel]:
        channel = await self._get_build_channel(interaction.guild)
        if not channel:
            await interaction.response.send_message(
                "No build channel has been configured. Ask a moderator to run /config.",
                ephemeral=True,
            )
            return None
        return channel

    # ------------------------------------------------------------------
    async def _create_post(
        self,
        *,
        channel: discord.abc.GuildChannel,
        record: BuildRecord,
        selection: str,
        author: discord.abc.User,
    ) -> BuildRecord:
        icon_path, color = get_icon_and_color(selection)
        icon_file = Path(icon_path)
        filename = icon_file.name
        file = discord.File(icon_file, filename=filename)
        embed = build_embed(record, icon_attachment_name=filename, color=color)

        if isinstance(channel, discord.ForumChannel):
            result = await channel.create_thread(name=record.name, embed=embed, file=file)
            thread = getattr(result, "thread", None) or result
            message = getattr(result, "message", None)
            if message is None:
                try:
                    message = await thread.fetch_message(thread.id)
                except discord.HTTPException:
                    message = None
            record.thread_id = thread.id
            record.channel_id = channel.id
            record.message_id = message.id if message else thread.id
        else:
            message = await channel.send(embed=embed, file=file)
            record.message_id = message.id
            record.channel_id = channel.id
        return record

    async def _update_post(
        self,
        *,
        channel: discord.abc.GuildChannel,
        record: BuildRecord,
        selection: str,
        author: discord.abc.User,
    ) -> None:
        icon_path, color = get_icon_and_color(selection)
        filename = Path(icon_path).name
        file = discord.File(icon_path, filename=filename)
        embed = build_embed(record, icon_attachment_name=filename, color=color)

        if isinstance(channel, discord.ForumChannel):
            if record.channel_id and record.channel_id != channel.id:
                await self._delete_existing_post(record, channel.guild)
                record.thread_id = None
                record.message_id = None
            thread: Optional[discord.Thread] = None
            if record.thread_id:
                thread = channel.get_thread(record.thread_id)
                if not thread:
                    try:
                        thread = await channel.fetch_thread(record.thread_id)
                    except discord.HTTPException:
                        thread = None
            if not thread:
                await self._create_post(channel=channel, record=record, selection=selection, author=author)
                return
            await thread.edit(name=record.name)
            message = None
            if record.message_id:
                try:
                    message = await thread.fetch_message(record.message_id)
                except discord.HTTPException:
                    message = None
            if not message:
                try:
                    message = await thread.fetch_message(thread.id)
                except discord.HTTPException:
                    message = None
            if message:
                await message.edit(embeds=[embed], attachments=[file])
            record.thread_id = thread.id
            record.message_id = message.id if message else thread.id
            record.channel_id = channel.id
        else:
            if record.channel_id and record.channel_id != channel.id:
                await self._delete_existing_post(record, channel.guild)
                record.message_id = None
            if not record.message_id:
                new_record = await self._create_post(channel=channel, record=record, selection=selection, author=author)
                record.message_id = new_record.message_id
                record.channel_id = new_record.channel_id
                return
            try:
                message = await channel.fetch_message(record.message_id)
            except discord.HTTPException:
                new_record = await self._create_post(channel=channel, record=record, selection=selection, author=author)
                record.message_id = new_record.message_id
                record.channel_id = new_record.channel_id
                return
            await message.edit(embeds=[embed], attachments=[file])
            record.channel_id = channel.id

    # ------------------------------------------------------------------
    async def _build_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        builds = self.bot.storage.get_builds(interaction.guild.id)
        current_lower = current.lower()
        matches = [
            build for build in builds if current_lower in build.name.lower() or current_lower in build.build_id
        ]
        return [app_commands.Choice(name=build.name, value=build.build_id) for build in matches[:25]]

    async def _class_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        current_lower = current.lower()
        matches = [choice for choice in constants.CLASS_CHOICES if current_lower in choice.lower()]
        if not matches:
            matches = constants.CLASS_CHOICES
        return [app_commands.Choice(name=choice, value=choice) for choice in matches[:25]]

    # ------------------------------------------------------------------
    @app_commands.command(name="add", description="Add a new Guild Wars 2 build")
    @app_commands.describe(
        name="Name of the build",
        class_option="Base profession or elite specialization",
        url="Optional external URL for reference",
        chat_code="In-game chat code",
        description="Optional description for the build",
    )
    @app_commands.autocomplete(class_option=_class_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        class_option: str,
        chat_code: str,
        url: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return
        channel = await self._ensure_channel(interaction)
        if not channel:
            return

        build_id = slugify(name)
        existing = self.bot.storage.find_build(interaction.guild.id, build_id)
        if existing:
            await interaction.response.send_message(
                "A build with that name already exists. Try renaming it or edit the existing build.",
                ephemeral=True,
            )
            return

        try:
            profession, specialization = resolve_profession(class_option)
        except ValueError:
            await interaction.response.send_message("Unknown class or specialization.", ephemeral=True)
            return
        record = BuildRecord(
            build_id=build_id,
            name=name,
            profession=profession,
            specialization=specialization,
            url=url,
            chat_code=chat_code,
            description=description,
            created_by=interaction.user.id,
            created_at=utcnow(),
            updated_by=interaction.user.id,
            updated_at=utcnow(),
        )

        record = await self._create_post(channel=channel, record=record, selection=class_option, author=interaction.user)
        self.bot.storage.upsert_build(interaction.guild.id, record)
        await interaction.response.send_message(f"Build **{name}** created.", ephemeral=True)

    # ------------------------------------------------------------------
    @app_commands.command(name="edit", description="Edit an existing build")
    @app_commands.describe(
        build="Select the build to edit",
        name="Updated name for the build",
        class_option="Base profession or elite specialization",
        url="Optional external URL",
        chat_code="In-game chat code",
        description="Optional description",
    )
    @app_commands.autocomplete(build=_build_autocomplete)
    @app_commands.autocomplete(class_option=_class_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction,
        build: str,
        name: Optional[str] = None,
        class_option: Optional[str] = None,
        url: Optional[str] = None,
        chat_code: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return
        channel = await self._ensure_channel(interaction)
        if not channel:
            return

        record = self.bot.storage.find_build(interaction.guild.id, build)
        if not record:
            await interaction.response.send_message("Build not found.", ephemeral=True)
            return

        original_id = record.build_id

        if name:
            candidate_id = slugify(name)
            if candidate_id != original_id:
                conflict = self.bot.storage.find_build(interaction.guild.id, candidate_id)
                if conflict:
                    await interaction.response.send_message(
                        "Another build already uses that name. Choose a different name.",
                        ephemeral=True,
                    )
                    return
            record.name = name
            record.build_id = candidate_id
        if class_option:
            try:
                profession, specialization = resolve_profession(class_option)
            except ValueError:
                await interaction.response.send_message("Unknown class or specialization.", ephemeral=True)
                return
            record.profession = profession
            record.specialization = specialization
        if url is not None:
            record.url = url
        if chat_code is not None:
            record.chat_code = chat_code
        if description is not None:
            record.description = description

        record.updated_by = interaction.user.id
        record.updated_at = utcnow()

        selection = record.specialization or record.profession
        if class_option:
            selection = class_option
        await self._update_post(channel=channel, record=record, selection=selection, author=interaction.user)

        if record.build_id != original_id:
            self.bot.storage.delete_build(interaction.guild.id, original_id)
        self.bot.storage.upsert_build(interaction.guild.id, record)
        await interaction.response.send_message(f"Build **{record.name}** updated.", ephemeral=True)

    # ------------------------------------------------------------------
    @app_commands.command(name="delete", description="Delete a build")
    @app_commands.describe(build="Select the build to delete", confirm="Confirm deletion")
    @app_commands.autocomplete(build=_build_autocomplete)
    async def delete(
        self,
        interaction: discord.Interaction,
        build: str,
        confirm: bool,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        record = self.bot.storage.find_build(interaction.guild.id, build)
        if not record:
            await interaction.response.send_message("Build not found.", ephemeral=True)
            return
        if not confirm:
            await interaction.response.send_message(
                "Deletion not confirmed. Pass `confirm=true` to delete the build.",
                ephemeral=True,
            )
            return

        if interaction.guild:
            await self._delete_existing_post(record, interaction.guild)

        removed = self.bot.storage.delete_build(interaction.guild.id, build)
        if removed:
            await interaction.response.send_message("Build deleted.", ephemeral=True)
        else:
            await interaction.response.send_message("Build could not be deleted.", ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(BuildsCog(bot))
