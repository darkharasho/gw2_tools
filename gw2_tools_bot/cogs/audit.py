"""Audit logging and query tooling for GW2 Tools."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Iterable, Mapping, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..http_utils import read_response_text
from ..storage import normalise_guild_id, utcnow

LOGGER = logging.getLogger(__name__)

GW2_GUILD_LOG_URL = "https://api.guildwars2.com/v2/guild/{guild_id}/log"
GW2_LOG_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)
AUDIT_CHANNEL_MESSAGE_LIMIT = 1900
AUDIT_QUERY_LIMIT = 25
GW2_QUERY_LIMIT = 25
AUDIT_RETENTION_DAYS = 30

DISCORD_EVENT_TITLES = {
    "member_join": "Member joined",
    "member_leave": "Member left",
    "member_kick": "Member kicked",
    "member_ban": "Member banned",
    "member_unban": "Member unbanned",
    "member_role_update": "Member roles updated",
    "member_server_mute": "Member server muted",
    "member_server_unmute": "Member server unmuted",
    "member_server_deaf": "Member server deafened",
    "member_server_undeaf": "Member server undeafened",
    "message_delete": "Message deleted",
    "message_edit": "Message edited",
    "role_create": "Role created",
    "role_update": "Role updated",
    "role_delete": "Role deleted",
    "guild_update": "Server updated",
    "emoji_update": "Emojis updated",
    "channel_create": "Channel created",
    "channel_update": "Channel updated",
    "channel_delete": "Channel deleted",
}


def _truncate(value: str, max_length: int = 300) -> str:
    if value is None:
        return ""
    value = str(value)
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def _escape_text(value: Optional[str]) -> str:
    if not value:
        return ""
    cleaned = value.replace("`", "'")
    cleaned = discord.utils.escape_markdown(cleaned)
    cleaned = discord.utils.escape_mentions(cleaned)
    return cleaned


<<<<<<< HEAD
def _display_user(user: Optional[discord.abc.User]) -> Optional[str]:
    if user is None:
        return None
    if isinstance(user, discord.Member):
        if user.display_name and user.display_name != user.name:
            return f"{user.display_name} ({user})"
    return str(user)


def _format_actor_target(
    actor: Optional[discord.abc.User], target: Optional[discord.abc.User]
) -> str:
    parts = []
    if actor is not None:
        parts.append(f"Actor: {_escape_text(_display_user(actor))} ({actor.id})")
    if target is not None:
        parts.append(f"Target: {_escape_text(_display_user(target))} ({target.id})")
    return "\n".join(parts)
=======
def _format_channel_label(channel: discord.abc.GuildChannel | discord.Thread) -> str:
    name = getattr(channel, "name", "unknown")
    if isinstance(channel, discord.CategoryChannel):
        return name
    return getattr(channel, "mention", f"#{name}")


def _format_multiline_value(value: str) -> str:
    if not value:
        return "None"
    return f"```\n{value}\n```"


def _display_user(user: Optional[discord.abc.User]) -> Optional[str]:
    if user is None:
        return None
    mention = getattr(user, "mention", str(user))
    username = getattr(user, "name", str(user))
    return f"{mention} ({username})"


def _format_user_field(user: Optional[discord.abc.User], *, fallback: str) -> str:
    if user is None:
        return fallback
    return _display_user(user) or fallback
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a


class AuditCog(commands.Cog):
    """Audit logging and query commands."""

    audit = app_commands.Group(
        name="audit", description="Configure and query audit logging."
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session = aiohttp.ClientSession(timeout=GW2_LOG_FETCH_TIMEOUT)
        self._poll_gw2_logs.start()
<<<<<<< HEAD

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_gw2_logs.cancel()
=======
        self._purge_audit_logs.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_gw2_logs.cancel()
        self._purge_audit_logs.cancel()
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        if not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------
    @audit.command(
        name="channel",
        description="Set the audit log channel (leave blank to disable).",
    )
    @app_commands.describe(channel="Channel for audit logging.")
    async def audit_channel_command(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        config = self.bot.get_config(interaction.guild.id)
        if channel is None:
            config.audit_channel_id = None
            message = "Audit logging disabled."
        else:
            config.audit_channel_id = channel.id
            message = f"Audit log channel set to {channel.mention}."
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message(message, ephemeral=True)

    @audit.command(
        name="gw2_key",
        description="Set the admin GW2 API key for guild log syncing.",
    )
    @app_commands.describe(api_key="Guild Wars 2 API key with guild log access.")
    async def audit_gw2_key_command(
        self, interaction: discord.Interaction, api_key: Optional[str]
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        config = self.bot.get_config(interaction.guild.id)
        cleaned_key = api_key.strip() if api_key else ""
        config.audit_gw2_admin_api_key = cleaned_key or None
        self.bot.save_config(interaction.guild.id, config)
        message = (
            "Guild Wars 2 admin API key saved."
            if cleaned_key
            else "Guild Wars 2 admin API key cleared."
        )
        await interaction.response.send_message(message, ephemeral=True)

    @audit.command(
        name="gw2_guild",
        description="Set the Guild Wars 2 guild ID to audit.",
    )
    @app_commands.describe(guild_id="Guild Wars 2 guild UUID (leave blank to clear).")
    async def audit_gw2_guild_command(
        self, interaction: discord.Interaction, guild_id: Optional[str]
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        config = self.bot.get_config(interaction.guild.id)
        cleaned = normalise_guild_id(guild_id or "")
        config.audit_gw2_guild_id = cleaned or None
        self.bot.save_config(interaction.guild.id, config)
        message = (
            f"Guild Wars 2 audit guild set to `{cleaned}`."
            if cleaned
            else "Guild Wars 2 audit guild cleared."
        )
        await interaction.response.send_message(message, ephemeral=True)

    # ------------------------------------------------------------------
    # Query commands
    # ------------------------------------------------------------------
    @audit.command(
        name="query",
        description="Query Discord audit entries for a user.",
    )
<<<<<<< HEAD
    @app_commands.describe(user="Discord username, mention, or ID to search for.")
    async def audit_query_command(
        self, interaction: discord.Interaction, user: str
=======
    @app_commands.describe(user="Discord user to search for.")
    async def audit_query_command(
        self, interaction: discord.Interaction, user: discord.User
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

<<<<<<< HEAD
        user_id = self._parse_user_id(user)
        store = self.bot.storage.get_audit_store(interaction.guild.id)
        rows = store.query_discord_events(
            user_id=user_id, user_query=user, limit=AUDIT_QUERY_LIMIT
=======
        user_id = user.id
        store = self.bot.storage.get_audit_store(interaction.guild.id)
        rows = store.query_discord_events(
            user_id=user_id, user_query=str(user), limit=AUDIT_QUERY_LIMIT
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        )
        if not rows:
            await interaction.response.send_message(
                "No Discord audit entries found for that user.",
                ephemeral=True,
            )
            return

<<<<<<< HEAD
        lines = []
        for row in rows:
            formatted = self._format_discord_row(row)
            if formatted:
                lines.append(formatted)
        await self._send_chunked(
            interaction,
            lines,
            header="**Discord audit results**",
        )
=======
        table = self._format_table(
            headers=["Timestamp", "Event", "Actor", "Target", "Details"],
            rows=[
                self._format_discord_table_row(row, guild=interaction.guild)
                for row in rows
            ],
            max_widths=[26, 20, 30, 30, 80],
            row_divider=True,
        )
        buffer = StringIO()
        buffer.write("Discord audit results\n")
        buffer.write(table)
        buffer.write("\n")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="discord_audit.txt")
        await interaction.response.send_message(file=file, ephemeral=True)
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a

    @audit.command(
        name="gw2_query",
        description="Query GW2 guild log entries for a user.",
    )
    @app_commands.describe(user="Guild Wars 2 account name to search for.")
    async def audit_gw2_query_command(
        self, interaction: discord.Interaction, user: str
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        store = self.bot.storage.get_audit_store(interaction.guild.id)
        rows = store.query_gw2_events(user_query=user, limit=GW2_QUERY_LIMIT)
        if not rows:
            await interaction.response.send_message(
                "No Guild Wars 2 audit entries found for that user.",
                ephemeral=True,
            )
            return

<<<<<<< HEAD
        lines = []
        for row in rows:
            formatted = self._format_gw2_row(row)
            if formatted:
                lines.append(formatted)
        await self._send_chunked(
            interaction,
            lines,
            header="**Guild Wars 2 audit results**",
        )
=======
        table = self._format_table(
            headers=["Timestamp", "Event", "User", "Summary"],
            rows=[self._format_gw2_table_row(row) for row in rows],
            max_widths=[26, 20, 30, 90],
        )
        buffer = StringIO()
        buffer.write("Guild Wars 2 audit results\n")
        buffer.write(table)
        buffer.write("\n")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="gw2_audit.txt")
        await interaction.response.send_message(file=file, ephemeral=True)
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._log_discord_event(
            member.guild,
            event_type="member_join",
            actor=None,
            target=member,
<<<<<<< HEAD
            details=_format_actor_target(None, member),
=======
            details={"Details": "Member joined the server."},
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        actor = None
        event_type = "member_leave"
<<<<<<< HEAD
=======
        reason = None
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.kick, member.id
        )
        if entry:
            actor = entry.user
            event_type = "member_kick"
<<<<<<< HEAD
        details = _format_actor_target(actor, member)
=======
            reason = entry.reason
        if event_type == "member_leave":
            details_map = {"Details": "Member left the server."}
        else:
            details_map = {"Details": "Member was kicked."}
            if reason:
                details_map["Reason"] = _format_multiline_value(
                    _truncate(_escape_text(reason))
                )
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        await self._log_discord_event(
            guild,
            event_type=event_type,
            actor=actor,
            target=member,
<<<<<<< HEAD
            details=details,
=======
            details=details_map,
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.ban, user.id
        )
        if entry:
            actor = entry.user
<<<<<<< HEAD
        details = _format_actor_target(actor, user)
=======
        details = "Member was banned."
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        await self._log_discord_event(
            guild,
            event_type="member_ban",
            actor=actor,
            target=user,
<<<<<<< HEAD
            details=details,
=======
            details={"Details": details},
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.unban, user.id
        )
        if entry:
            actor = entry.user
<<<<<<< HEAD
        details = _format_actor_target(actor, user)
=======
        details = "Member was unbanned."
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        await self._log_discord_event(
            guild,
            event_type="member_unban",
            actor=actor,
            target=user,
<<<<<<< HEAD
            details=details,
=======
            details={"Details": details},
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        author = message.author if isinstance(message.author, discord.abc.User) else None
<<<<<<< HEAD
        details_parts = []
        if author is not None:
            details_parts.append(_format_actor_target(author, author))
        details_parts.append(f"Channel: {message.channel.mention}")
        if message.content:
            details_parts.append(
                f"Content: `{_truncate(_escape_text(message.content))}`"
            )
        details = "\n".join(part for part in details_parts if part)
        await self._log_discord_event(
            message.guild,
            event_type="message_delete",
            actor=author,
=======
        actor = None
        if author:
            actor = await self._find_audit_entry_user(
                message.guild,
                discord.AuditLogAction.message_delete,
                author.id,
            )
        details: dict[str, str] = {
            "Channel": _format_channel_label(message.channel),
        }
        if message.content:
            details["Content"] = _format_multiline_value(
                _truncate(_escape_text(message.content))
            )
        else:
            details["Content"] = "Unavailable (message content intent missing or not cached)."
        if message.attachments:
            details["Attachments"] = str(len(message.attachments))
        await self._log_discord_event(
            message.guild,
            event_type="message_delete",
            actor=actor or author,
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
            target=author,
            details=details,
        )

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None:
            return
<<<<<<< HEAD
        if before.content == after.content:
            return
        author = after.author if isinstance(after.author, discord.abc.User) else None
        details_parts = []
        if author is not None:
            details_parts.append(_format_actor_target(author, author))
        details_parts.append(f"Channel: {after.channel.mention}")
        if before.content:
            details_parts.append(
                f"Before: `{_truncate(_escape_text(before.content))}`"
            )
        if after.content:
            details_parts.append(
                f"After: `{_truncate(_escape_text(after.content))}`"
            )
        details = "\n".join(part for part in details_parts if part)
=======
        content_changed = before.content != after.content
        attachments_changed = len(before.attachments) != len(after.attachments)
        embeds_changed = len(before.embeds) != len(after.embeds)
        if (
            not content_changed
            and not attachments_changed
            and not embeds_changed
            and self.bot.intents.message_content
        ):
            return
        author = after.author if isinstance(after.author, discord.abc.User) else None
        details: dict[str, str] = {
            "Channel": _format_channel_label(after.channel),
        }
        if content_changed and before.content:
            details["Before"] = _format_multiline_value(
                _truncate(_escape_text(before.content))
            )
        if content_changed and after.content:
            details["After"] = _format_multiline_value(
                _truncate(_escape_text(after.content))
            )
        if (
            not content_changed
            and not attachments_changed
            and not embeds_changed
            and not self.bot.intents.message_content
        ):
            details["Content"] = "Unavailable (message content intent missing)."
        if attachments_changed:
            details["Attachments"] = (
                f"{len(before.attachments)} -> {len(after.attachments)}"
            )
        if embeds_changed:
            details["Embeds"] = f"{len(before.embeds)} -> {len(after.embeds)}"
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        await self._log_discord_event(
            after.guild,
            event_type="message_edit",
            actor=author,
            target=author,
            details=details,
        )

<<<<<<< HEAD
=======
    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if before.guild is None:
            return
        before_roles = {role for role in before.roles if not role.is_default()}
        after_roles = {role for role in after.roles if not role.is_default()}
        added = sorted(after_roles - before_roles, key=lambda role: role.name.lower())
        removed = sorted(before_roles - after_roles, key=lambda role: role.name.lower())
        if not added and not removed:
            return
        actor = await self._find_audit_entry_user(
            after.guild,
            discord.AuditLogAction.member_role_update,
            after.id,
        )
        details: dict[str, str] = {}
        if added:
            details["Added"] = ", ".join(role.mention for role in added)
        if removed:
            details["Removed"] = ", ".join(role.mention for role in removed)
        await self._log_discord_event(
            after.guild,
            event_type="member_role_update",
            actor=actor,
            target=after,
            details=details,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.guild is None:
            return
        event_type = None
        if before.mute != after.mute:
            event_type = "member_server_mute" if after.mute else "member_server_unmute"
        elif before.deaf != after.deaf:
            event_type = "member_server_deaf" if after.deaf else "member_server_undeaf"
        if not event_type:
            return
        actor = await self._find_audit_entry_user(
            member.guild,
            discord.AuditLogAction.member_update,
            member.id,
        )
        details = {}
        if after.channel:
            details["Channel"] = _format_channel_label(after.channel)
        await self._log_discord_event(
            member.guild,
            event_type=event_type,
            actor=actor,
            target=member,
            details=details or {"Details": "Voice state updated."},
        )

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if after is None:
            return
        details: dict[str, str] = {}
        if before.name != after.name:
            details["Name"] = f"{before.name} -> {after.name}"
        if before.afk_channel != after.afk_channel:
            before_label = (
                _format_channel_label(before.afk_channel)
                if before.afk_channel
                else "None"
            )
            after_label = (
                _format_channel_label(after.afk_channel)
                if after.afk_channel
                else "None"
            )
            details["AFK Channel"] = f"{before_label} -> {after_label}"
        if before.afk_timeout != after.afk_timeout:
            details["AFK Timeout"] = f"{before.afk_timeout}s -> {after.afk_timeout}s"
        if not details:
            return
        actor = await self._find_audit_entry_user(
            after,
            discord.AuditLogAction.guild_update,
            after.id,
        )
        await self._log_discord_event(
            after,
            event_type="guild_update",
            actor=actor,
            target=None,
            details=details,
        )

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self,
        guild: discord.Guild,
        before: list[discord.Emoji],
        after: list[discord.Emoji],
    ) -> None:
        before_names = {emoji.name for emoji in before}
        after_names = {emoji.name for emoji in after}
        added = sorted(after_names - before_names)
        removed = sorted(before_names - after_names)
        if not added and not removed:
            return
        details: dict[str, str] = {}
        if added:
            details["Added"] = _format_multiline_value(", ".join(added))
        if removed:
            details["Removed"] = _format_multiline_value(", ".join(removed))
        actor = await self._find_audit_entry_any(
            guild, discord.AuditLogAction.emoji_update
        )
        await self._log_discord_event(
            guild,
            event_type="emoji_update",
            actor=actor,
            target=None,
            details=details,
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        actor = await self._find_audit_entry_user(
            role.guild, discord.AuditLogAction.role_create, role.id
        )
        await self._log_discord_event(
            role.guild,
            event_type="role_create",
            actor=actor,
            target=None,
            details={"Role": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor = await self._find_audit_entry_user(
            role.guild, discord.AuditLogAction.role_delete, role.id
        )
        await self._log_discord_event(
            role.guild,
            event_type="role_delete",
            actor=actor,
            target=None,
            details={"Role": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_update(
        self, before: discord.Role, after: discord.Role
    ) -> None:
        details: dict[str, str] = {}
        if before.name != after.name:
            details["Name"] = f"{before.name} -> {after.name}"
        if before.color != after.color:
            details["Color"] = f"{before.color} -> {after.color}"
        if not details:
            return
        actor = await self._find_audit_entry_user(
            after.guild, discord.AuditLogAction.role_update, after.id
        )
        await self._log_discord_event(
            after.guild,
            event_type="role_update",
            actor=actor,
            target=None,
            details=details,
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        actor = await self._find_audit_entry_user(
            channel.guild, discord.AuditLogAction.channel_create, channel.id
        )
        await self._log_discord_event(
            channel.guild,
            event_type="channel_create",
            actor=actor,
            target=None,
            details={"Channel": _format_channel_label(channel)},
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        actor = await self._find_audit_entry_user(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id
        )
        await self._log_discord_event(
            channel.guild,
            event_type="channel_delete",
            actor=actor,
            target=None,
            details={"Channel": f"#{channel.name}"},
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        details: dict[str, str] = {}
        if before.name != after.name:
            details["Name"] = f"{before.name} -> {after.name}"
        if not details:
            return
        actor = await self._find_audit_entry_user(
            after.guild, discord.AuditLogAction.channel_update, after.id
        )
        await self._log_discord_event(
            after.guild,
            event_type="channel_update",
            actor=actor,
            target=None,
            details=details,
        )

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    # ------------------------------------------------------------------
    # GW2 sync loop
    # ------------------------------------------------------------------
    @tasks.loop(hours=24)
    async def _poll_gw2_logs(self) -> None:
        if not self.bot.guilds:
            return

        for guild in self.bot.guilds:
            config = self.bot.get_config(guild.id)
            if not config.audit_gw2_admin_api_key or not config.audit_gw2_guild_id:
                continue
            await self._sync_gw2_guild_log(
                guild.id,
                config.audit_gw2_guild_id,
                config.audit_gw2_admin_api_key,
            )

    @_poll_gw2_logs.before_loop
    async def _before_poll_gw2_logs(self) -> None:  # pragma: no cover - lifecycle
        await self.bot.wait_until_ready()

<<<<<<< HEAD
=======
    @tasks.loop(hours=24)
    async def _purge_audit_logs(self) -> None:
        if not self.bot.guilds:
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)
        ).isoformat()
        for guild in self.bot.guilds:
            store = self.bot.storage.get_audit_store(guild.id)
            store.purge_events_before(cutoff)

    @_purge_audit_logs.before_loop
    async def _before_purge_audit_logs(self) -> None:  # pragma: no cover - lifecycle
        await self.bot.wait_until_ready()

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    async def _sync_gw2_guild_log(
        self, guild_id: int, gw2_guild_id: str, api_key: str
    ) -> None:
        store = self.bot.storage.get_audit_store(guild_id)
        last_log_id = store.get_gw2_last_log_id()
        params = {"access_token": api_key}
        if last_log_id is not None:
            params["since"] = str(last_log_id)

        url = GW2_GUILD_LOG_URL.format(guild_id=gw2_guild_id)
        try:
            async with self._session.get(url, params=params) as response:
                if response.status != 200:
                    body = await read_response_text(response)
                    LOGGER.warning(
                        "Failed to fetch GW2 guild log for %s: %s %s",
                        gw2_guild_id,
                        response.status,
                        body,
                    )
                    return
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.exception("Failed to fetch GW2 guild log for %s", gw2_guild_id)
            return
        except Exception:
            LOGGER.exception("Unexpected error fetching GW2 guild log for %s", gw2_guild_id)
            return

        if not isinstance(payload, list):
            LOGGER.warning("Unexpected GW2 guild log payload for %s: %s", gw2_guild_id, payload)
            return

        max_log_id = last_log_id
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            log_id = entry.get("id")
            if isinstance(log_id, int):
                max_log_id = max(max_log_id or log_id, log_id)
            created_at = entry.get("time") or utcnow()
            event_type = entry.get("type", "unknown")
            user = entry.get("user")
            details = json.dumps(entry, sort_keys=True)
            store.add_gw2_event(
                created_at=created_at,
                event_type=event_type,
                user=user,
                details=details,
                log_id=log_id if isinstance(log_id, int) else None,
            )

        store.set_gw2_last_log_id(max_log_id, utcnow())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _audit_channel_id(self, guild: discord.Guild) -> Optional[int]:
        config = self.bot.get_config(guild.id)
        return config.audit_channel_id

    async def _log_discord_event(
        self,
        guild: discord.Guild,
        *,
        event_type: str,
        actor: Optional[discord.abc.User],
        target: Optional[discord.abc.User],
<<<<<<< HEAD
        details: str,
=======
        details: Mapping[str, str],
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    ) -> None:
        channel_id = self._audit_channel_id(guild)
        if not channel_id:
            return

        created_at = utcnow()
        store = self.bot.storage.get_audit_store(guild.id)
<<<<<<< HEAD
=======
        details_text = "\n".join(f"{key}: {value}" for key, value in details.items())
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        store.add_discord_event(
            created_at=created_at,
            event_type=event_type,
            actor_id=actor.id if actor else None,
            actor_name=_display_user(actor),
            target_id=target.id if target else None,
            target_name=_display_user(target),
<<<<<<< HEAD
            details=details,
        )

        title = DISCORD_EVENT_TITLES.get(event_type, event_type.replace("_", " ").title())
        body = f"**{title}**\n{details}".strip()
        await self._send_audit_message(guild, channel_id, body)

    async def _send_audit_message(
        self, guild: discord.Guild, channel_id: int, content: str
=======
            details=details_text,
        )

        title = DISCORD_EVENT_TITLES.get(event_type, event_type.replace("_", " ").title())
        embed = discord.Embed(title=title, colour=BRAND_COLOUR)
        embed.add_field(
            name="Actor",
            value=_format_user_field(actor, fallback="Unknown"),
            inline=True,
        )
        embed.add_field(
            name="Target",
            value=_format_user_field(target, fallback="Unknown"),
            inline=True,
        )
        if details:
            for key, value in details.items():
                embed.add_field(name=key, value=value or "None", inline=False)
        else:
            embed.add_field(
                name="Details",
                value="No additional details.",
                inline=False,
            )
        embed.set_footer(text="Guild Wars 2 Tools")
        await self._send_audit_message(guild, channel_id, embed)

    async def _send_audit_message(
        self, guild: discord.Guild, channel_id: int, embed: discord.Embed
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    ) -> None:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to resolve audit channel %s for guild %s", channel_id, guild.id
                )
                return

<<<<<<< HEAD
        safe_content = _truncate(content, AUDIT_CHANNEL_MESSAGE_LIMIT)
        try:
            await channel.send(safe_content)
=======
        if embed.description:
            embed.description = _truncate(embed.description, AUDIT_CHANNEL_MESSAGE_LIMIT)
        for index, field in enumerate(embed.fields):
            if field.value and len(field.value) > AUDIT_CHANNEL_MESSAGE_LIMIT:
                embed.set_field_at(
                    index,
                    name=field.name,
                    value=_truncate(field.value, AUDIT_CHANNEL_MESSAGE_LIMIT),
                    inline=field.inline,
                )
        try:
            await channel.send(embed=embed)
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Failed to send audit log message to channel %s for guild %s",
                channel_id,
                guild.id,
            )

<<<<<<< HEAD
=======
    async def _find_audit_entry_user(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
    ) -> Optional[discord.abc.User]:
        entry = await self._find_audit_entry(guild, action, target_id)
        if entry:
            return entry.user
        return None

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    async def _find_audit_entry(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
    ) -> Optional[discord.AuditLogEntry]:
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if entry.target and getattr(entry.target, "id", None) != target_id:
                    continue
                if entry.created_at:
                    delta = datetime.now(timezone.utc) - entry.created_at
                    if delta > timedelta(minutes=2):
                        continue
                return entry
        except discord.Forbidden:
            return None
        except discord.HTTPException:
            return None
        return None

<<<<<<< HEAD
=======
    async def _find_audit_entry_any(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
    ) -> Optional[discord.abc.User]:
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if entry.created_at:
                    delta = datetime.now(timezone.utc) - entry.created_at
                    if delta > timedelta(minutes=2):
                        continue
                return entry.user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
    @staticmethod
    def _parse_user_id(user: str) -> Optional[int]:
        match = re.match(r"<@!?(\d+)>", user.strip())
        if match:
            return int(match.group(1))
        cleaned = user.strip()
        if cleaned.isdigit():
            return int(cleaned)
        return None

    @staticmethod
<<<<<<< HEAD
    def _format_discord_row(row: Mapping[str, Any]) -> str:
        created_at = row["created_at"]
        event_type = row["event_type"]
        actor = row["actor_name"] or "Unknown"
        target = row["target_name"] or "Unknown"
        details = row["details"] or ""
        return _truncate(
            f"{created_at} | {event_type} | actor: {actor} | target: {target} | {details}",
            1000,
        )

    @staticmethod
    def _format_gw2_row(row: Mapping[str, Any]) -> str:
        created_at = row["created_at"]
        event_type = row["event_type"]
        user = row["user"] or "Unknown"
        details = row["details"] or "{}"
        summary = ""
=======
    def _format_discord_table_row(
        row: Mapping[str, Any],
        *,
        guild: Optional[discord.Guild] = None,
    ) -> list[str]:
        created_at = AuditCog._format_timestamp(row["created_at"])
        event_type = row["event_type"]
        actor = AuditCog._format_user_label(row["actor_name"], guild=guild)
        target = AuditCog._format_user_label(row["target_name"], guild=guild)
        details_text = row["details"] or ""
        details = AuditCog._normalise_table_cell(
            AuditCog._resolve_role_mentions(
                AuditCog._resolve_channel_mentions(details_text, guild=guild),
                guild=guild,
            )
        )
        return [
            created_at,
            event_type,
            actor,
            target,
            details,
        ]

    @staticmethod
    def _format_gw2_table_row(row: Mapping[str, Any]) -> list[str]:
        created_at = AuditCog._format_timestamp(row["created_at"])
        event_type = row["event_type"]
        user = AuditCog._normalise_table_cell(row["user"] or "Unknown")
        details = row["details"] or "{}"
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a
        try:
            payload = json.loads(details)
        except json.JSONDecodeError:
            summary = details
        else:
            summary = AuditCog._summarise_gw2_payload(payload)
<<<<<<< HEAD
        return _truncate(
            f"{created_at} | {event_type} | user: {user} | {summary}",
            1000,
        )
=======
        return [
            created_at,
            event_type,
            user,
            AuditCog._normalise_table_cell(summary),
        ]

    @staticmethod
    def _truncate_cell(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        if max_length <= 1:
            return value[:max_length]
        return value[: max_length - 1] + "…"

    @staticmethod
    def _normalise_table_cell(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        cleaned = re.sub(r"\(\d{5,}\)", "", cleaned).strip()
        cleaned = re.sub(r"<@!?\d+>", "@user", cleaned)
        cleaned = cleaned.replace(" ,", ",").replace("  ", " ")
        return cleaned

    @staticmethod
    def _format_user_label(
        value: Optional[str],
        *,
        guild: Optional[discord.Guild] = None,
    ) -> str:
        if not value:
            return "Unknown"
        match = re.search(r"<@!?\d+>\s*\(([^)]+)\)", value)
        if match:
            username = match.group(1)
            return AuditCog._normalise_table_cell(f"@{username} ({username})")
        mention_match = re.search(r"<@!?(\d+)>", value)
        if mention_match and guild is not None:
            member = guild.get_member(int(mention_match.group(1)))
            if member:
                return AuditCog._normalise_table_cell(
                    f"@{member.name} ({member.name})"
                )
        cleaned = AuditCog._normalise_table_cell(value)
        if cleaned.startswith("<@") and cleaned.endswith(">"):
            return "Unknown"
        return cleaned

    @staticmethod
    def _resolve_channel_mentions(
        value: str,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> str:
        if not value:
            return ""

        def replace(match: re.Match[str]) -> str:
            channel_id = int(match.group(1))
            if guild is not None:
                channel = guild.get_channel(channel_id)
                if channel is not None:
                    return f"#{channel.name}"
            return "#deleted-channel"

        return re.sub(r"<#(\d+)>", replace, str(value))

    @staticmethod
    def _resolve_role_mentions(
        value: str,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> str:
        if not value:
            return ""

        def replace(match: re.Match[str]) -> str:
            role_id = int(match.group(1))
            if guild is not None:
                role = guild.get_role(role_id)
                if role is not None:
                    return f"@{role.name}"
            return "@deleted-role"

        return re.sub(r"<@&(\d+)>", replace, str(value))

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if isinstance(value, datetime):
            timestamp = value
        else:
            text = str(value)
            try:
                timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return text
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _format_table(
        headers: list[str],
        rows: Iterable[list[str]],
        *,
        max_widths: Optional[list[int]] = None,
        row_divider: bool = False,
    ) -> str:
        normalised_rows = [
            ["" if cell is None else str(cell) for cell in row] for row in rows
        ]
        widths = [len(header) for header in headers]
        for row in normalised_rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))
        if max_widths:
            widths = [min(width, max_widths[idx]) for idx, width in enumerate(widths)]

        def wrap_cell(value: str, width: int) -> list[str]:
            if not value:
                return [""]
            if width <= 0:
                return [value]
            return textwrap.wrap(
                value,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]

        def format_row(row: list[str]) -> list[str]:
            wrapped_cells = [
                wrap_cell(cell, widths[idx]) for idx, cell in enumerate(row)
            ]
            height = max(len(cell_lines) for cell_lines in wrapped_cells)
            lines = []
            for line_index in range(height):
                cells = []
                for idx, cell_lines in enumerate(wrapped_cells):
                    cell_value = (
                        cell_lines[line_index] if line_index < len(cell_lines) else ""
                    )
                    cells.append(cell_value.ljust(widths[idx]))
                lines.append("| " + " | ".join(cells) + " |")
            return lines

        divider = "+-" + "-+-".join("-" * width for width in widths) + "-+"
        header_row = [AuditCog._truncate_cell(header, widths[idx]) for idx, header in enumerate(headers)]
        lines = [divider, format_row(header_row)[0], divider]
        for row in normalised_rows:
            lines.extend(format_row(row))
            if row_divider:
                lines.append(divider)
        if not row_divider:
            lines.append(divider)
        return "\n".join(lines)
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a

    @staticmethod
    def _summarise_gw2_payload(payload: dict[str, Any]) -> str:
        ignore = {"id", "time", "type", "user"}
        parts = []
        for key, value in payload.items():
            if key in ignore:
                continue
            if isinstance(value, (dict, list)):
                formatted = json.dumps(value, sort_keys=True)
            else:
                formatted = str(value)
            parts.append(f"{key}={formatted}")
        return ", ".join(parts) if parts else "No extra details"

<<<<<<< HEAD
    async def _send_chunked(
        self, interaction: discord.Interaction, lines: Iterable[str], *, header: str
    ) -> None:
        chunks = []
        current = [header]
        for line in lines:
            if len("\n".join(current + [line])) > 1800:
                chunks.append("\n".join(current))
                current = [header]
            current.append(line)
        if current:
            chunks.append("\n".join(current))

        if not interaction.response.is_done():
            await interaction.response.send_message(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)
            return

        for chunk in chunks:
            await interaction.followup.send(chunk, ephemeral=True)
=======
>>>>>>> 9bb3e0141ddda61990372c60e88701b73ba51b1a


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AuditCog(bot))
