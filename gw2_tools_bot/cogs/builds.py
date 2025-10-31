"""Build management cog for GW2 Tools."""
from __future__ import annotations

from datetime import datetime
import re
from pathlib import Path
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..storage import ISOFORMAT, BuildRecord, utcnow
from ..utils import build_embed, get_icon_and_color, resolve_profession
from .. import constants

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "build"


class BuildAddModal(discord.ui.Modal):
    """Modal used to gather inputs for creating a build."""

    def __init__(self, cog: "BuildsCog", *, default_class: Optional[str] = None) -> None:
        super().__init__(title="Add Guild Wars 2 build")
        self.cog = cog

        self.name_input = discord.ui.TextInput(label="Build name", max_length=100)
        self.class_input = discord.ui.TextInput(
            label="Class or specialization",
            placeholder="e.g. Guardian, Tempest, or Luminary",
            max_length=50,
        )
        if default_class:
            self.class_input.default = default_class
        self.chat_code_input = discord.ui.TextInput(label="Chat code", max_length=200)
        self.url_input = discord.ui.TextInput(
            label="Reference URL",
            required=False,
            placeholder="Optional link to a website or video",
            max_length=200,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder="Optional notes about the build",
            max_length=1000,
        )

        for item in (
            self.name_input,
            self.class_input,
            self.chat_code_input,
            self.url_input,
            self.description_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_add_submission(
            interaction=interaction,
            name=self.name_input.value,
            class_option=self.class_input.value,
            chat_code=self.chat_code_input.value,
            url=self.url_input.value,
            description=self.description_input.value,
        )


class BuildEditModal(discord.ui.Modal):
    """Modal used to gather inputs for updating a build."""

    def __init__(self, cog: "BuildsCog", record: BuildRecord) -> None:
        super().__init__(title=f"Edit {record.name}")
        self.cog = cog
        self.original_id = record.build_id

        selection = record.specialization or record.profession

        self.name_input = discord.ui.TextInput(
            label="Build name",
            default=record.name,
            max_length=100,
        )
        self.class_input = discord.ui.TextInput(
            label="Class or specialization",
            default=selection,
            placeholder="e.g. Guardian, Tempest, or Luminary",
            max_length=50,
        )
        self.chat_code_input = discord.ui.TextInput(
            label="Chat code",
            default=record.chat_code,
            max_length=200,
        )
        self.url_input = discord.ui.TextInput(
            label="Reference URL",
            default=record.url or "",
            required=False,
            placeholder="Optional link to a website or video",
            max_length=200,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            default=record.description or "",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder="Optional notes about the build",
            max_length=1000,
        )

        for item in (
            self.name_input,
            self.class_input,
            self.chat_code_input,
            self.url_input,
            self.description_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_edit_submission(
            interaction=interaction,
            original_id=self.original_id,
            name=self.name_input.value,
            class_option=self.class_input.value,
            chat_code=self.chat_code_input.value,
            url=self.url_input.value,
            description=self.description_input.value,
        )


class BuildDeleteModal(discord.ui.Modal):
    """Modal that confirms deletion by asking for the build name."""

    def __init__(self, cog: "BuildsCog", record: BuildRecord) -> None:
        super().__init__(title=f"Delete {record.name}?")
        self.cog = cog
        self.build_id = record.build_id

        self.confirm_input = discord.ui.TextInput(
            label="Type the build name to confirm",
            placeholder=record.name,
            max_length=100,
        )
        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_delete_submission(
            interaction=interaction,
            build_id=self.build_id,
            confirmation=self.confirm_input.value,
        )


class BuildsCog(commands.GroupCog, name="builds"):
    """Manage Guild Wars 2 build posts."""

    description = "Manage Guild Wars 2 builds."

    def __init__(self, bot: GW2ToolsBot) -> None:
        super().__init__()
        self.bot = bot

    # ------------------------------------------------------------------
    def _normalise_class_selection(self, selection: str) -> str:
        candidate = selection.strip()
        for choice in constants.CLASS_CHOICES:
            if candidate.lower() == choice.lower():
                return choice
        raise ValueError(f"Unknown class selection: {selection}")

    async def handle_add_submission(
        self,
        *,
        interaction: discord.Interaction,
        name: str,
        class_option: str,
        chat_code: str,
        url: str,
        description: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        channel = await self._get_build_channel(interaction.guild)
        if not channel:
            await interaction.response.send_message(
                "No build channel has been configured. Ask a moderator to run /config.",
                ephemeral=True,
            )
            return

        name_value = name.strip()
        chat_code_value = chat_code.strip()
        if not name_value:
            await interaction.response.send_message("Build name cannot be empty.", ephemeral=True)
            return
        if not chat_code_value:
            await interaction.response.send_message("Chat code cannot be empty.", ephemeral=True)
            return

        try:
            normalized_selection = self._normalise_class_selection(class_option)
            profession, specialization = resolve_profession(normalized_selection)
        except ValueError:
            await interaction.response.send_message("Unknown class or specialization.", ephemeral=True)
            return

        build_id = slugify(name_value)
        existing = self.bot.storage.find_build(interaction.guild.id, build_id)
        if existing:
            await interaction.response.send_message(
                "A build with that name already exists. Try renaming it or edit the existing build.",
                ephemeral=True,
            )
            return

        url_value = url.strip()
        description_value = description.strip()

        record = BuildRecord(
            build_id=build_id,
            name=name_value,
            profession=profession,
            specialization=specialization,
            url=url_value or None,
            chat_code=chat_code_value,
            description=description_value or None,
            created_by=interaction.user.id,
            created_at=utcnow(),
            updated_by=interaction.user.id,
            updated_at=utcnow(),
        )

        record = await self._create_post(
            channel=channel,
            record=record,
            selection=normalized_selection,
            author=interaction.user,
        )
        self.bot.storage.upsert_build(interaction.guild.id, record)
        await interaction.response.send_message(f"Build **{name_value}** created.", ephemeral=True)

    async def handle_edit_submission(
        self,
        *,
        interaction: discord.Interaction,
        original_id: str,
        name: str,
        class_option: str,
        chat_code: str,
        url: str,
        description: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        record = self.bot.storage.find_build(interaction.guild.id, original_id)
        if not record:
            await interaction.response.send_message("Build not found.", ephemeral=True)
            return

        name_value = name.strip()
        chat_code_value = chat_code.strip()
        if not name_value:
            await interaction.response.send_message("Build name cannot be empty.", ephemeral=True)
            return
        if not chat_code_value:
            await interaction.response.send_message("Chat code cannot be empty.", ephemeral=True)
            return

        try:
            normalized_selection = self._normalise_class_selection(class_option)
            profession, specialization = resolve_profession(normalized_selection)
        except ValueError:
            await interaction.response.send_message("Unknown class or specialization.", ephemeral=True)
            return

        candidate_id = slugify(name_value)
        if candidate_id != original_id:
            conflict = self.bot.storage.find_build(interaction.guild.id, candidate_id)
            if conflict:
                await interaction.response.send_message(
                    "Another build already uses that name. Choose a different name.",
                    ephemeral=True,
                )
                return

        channel = await self._get_build_channel(interaction.guild)
        if not channel:
            await interaction.response.send_message(
                "No build channel has been configured. Ask a moderator to run /config.",
                ephemeral=True,
            )
            return

        url_value = url.strip()
        description_value = description.strip()

        original_build_id = record.build_id

        record.name = name_value
        record.build_id = candidate_id
        record.profession = profession
        record.specialization = specialization
        record.url = url_value or None
        record.chat_code = chat_code_value
        record.description = description_value or None
        record.updated_by = interaction.user.id
        record.updated_at = utcnow()

        await self._update_post(
            channel=channel,
            record=record,
            selection=normalized_selection,
            author=interaction.user,
        )

        if record.build_id != original_build_id:
            self.bot.storage.delete_build(interaction.guild.id, original_build_id)
        self.bot.storage.upsert_build(interaction.guild.id, record)
        await interaction.response.send_message(f"Build **{record.name}** updated.", ephemeral=True)

    # ------------------------------------------------------------------
    async def handle_delete_submission(
        self,
        *,
        interaction: discord.Interaction,
        build_id: str,
        confirmation: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        record = self.bot.storage.find_build(interaction.guild.id, build_id)
        if not record:
            await interaction.response.send_message("Build not found.", ephemeral=True)
            return

        if confirmation.strip().lower() != record.name.strip().lower():
            await interaction.response.send_message("Confirmation text does not match the build name.", ephemeral=True)
            return

        await self._delete_existing_post(record, interaction.guild)

        removed = self.bot.storage.delete_build(interaction.guild.id, build_id)
        if removed:
            await interaction.response.send_message("Build deleted.", ephemeral=True)
        else:
            await interaction.response.send_message("Build could not be deleted.", ephemeral=True)

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

    async def _resolve_user_display(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member:
            return member.display_name
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
        if member:
            return member.display_name
        user = self.bot.get_user(user_id)
        if user:
            return user.name
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return f"User {user_id}"
        return user.name if user else f"User {user_id}"

    def _format_timestamp(self, timestamp: str) -> str:
        try:
            parsed = datetime.strptime(timestamp, ISOFORMAT)
        except ValueError:
            return timestamp
        return parsed.strftime("%m/%d/%y")

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
        updated_by = await self._resolve_user_display(channel.guild, record.updated_by)
        updated_on = self._format_timestamp(record.updated_at)
        embed = build_embed(
            record,
            icon_attachment_name=filename,
            color=color,
            updated_by=updated_by,
            updated_on=updated_on,
        )

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
        updated_by = await self._resolve_user_display(channel.guild, record.updated_by)
        updated_on = self._format_timestamp(record.updated_at)
        embed = build_embed(
            record,
            icon_attachment_name=filename,
            color=color,
            updated_by=updated_by,
            updated_on=updated_on,
        )

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

    async def _class_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        del interaction  # Unused for now but kept for future guild-specific overrides.
        current_lower = current.lower()
        matches = [
            choice for choice in constants.CLASS_CHOICES if current_lower in choice.lower()
        ]
        return [app_commands.Choice(name=choice, value=choice) for choice in matches[:25]]

    # ------------------------------------------------------------------
    @app_commands.command(name="add", description="Add a new Guild Wars 2 build")
    @app_commands.describe(class_option="Select a class or specialization")
    @app_commands.autocomplete(class_option=_class_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        class_option: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        modal = BuildAddModal(self, default_class=class_option)
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    @app_commands.command(name="edit", description="Edit an existing build")
    @app_commands.describe(build="Select the build to edit")
    @app_commands.autocomplete(build=_build_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction,
        build: str,
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

        modal = BuildEditModal(self, record)
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    @app_commands.command(name="delete", description="Delete a build")
    @app_commands.describe(build="Select the build to delete")
    @app_commands.autocomplete(build=_build_autocomplete)
    async def delete(
        self,
        interaction: discord.Interaction,
        build: str,
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

        modal = BuildDeleteModal(self, record)
        await interaction.response.send_modal(modal)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(BuildsCog(bot))
