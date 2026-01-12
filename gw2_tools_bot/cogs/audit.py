"""Audit logging and query tooling for GW2 Tools."""
from __future__ import annotations

import asyncio
import json
import logging
import textwrap
import re
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

DISCORD_EVENT_TITLES = {
    "member_join": "Member joined",
    "member_leave": "Member left",
    "member_kick": "Member kicked",
    "member_ban": "Member banned",
    "member_unban": "Member unbanned",
    "member_role_update": "Member roles updated",
    "message_delete": "Message deleted",
    "message_edit": "Message edited",
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


def _format_channel_label(channel: discord.abc.GuildChannel | discord.Thread) -> str:
    name = getattr(channel, "name", "unknown")
    if isinstance(channel, discord.CategoryChannel):
        return name
    return f"#{name}"


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


class AuditCog(commands.Cog):
    """Audit logging and query commands."""

    audit = app_commands.Group(
        name="audit", description="Configure and query audit logging."
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session = aiohttp.ClientSession(timeout=GW2_LOG_FETCH_TIMEOUT)
        self._poll_gw2_logs.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_gw2_logs.cancel()
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
    @app_commands.describe(user="Discord username, mention, or ID to search for.")
    async def audit_query_command(
        self, interaction: discord.Interaction, user: str
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        user_id = self._parse_user_id(user)
        store = self.bot.storage.get_audit_store(interaction.guild.id)
        rows = store.query_discord_events(
            user_id=user_id, user_query=user, limit=AUDIT_QUERY_LIMIT
        )
        if not rows:
            await interaction.response.send_message(
                "No Discord audit entries found for that user.",
                ephemeral=True,
            )
            return

        table = self._format_table(
            headers=["Timestamp", "Event", "Actor", "Target", "Details"],
            rows=[self._format_discord_table_row(row) for row in rows],
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
            details={"Details": "Member joined the server."},
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        actor = None
        event_type = "member_leave"
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.kick, member.id
        )
        if entry:
            actor = entry.user
            event_type = "member_kick"
        details = "Member left the server." if event_type == "member_leave" else "Member was kicked."
        await self._log_discord_event(
            guild,
            event_type=event_type,
            actor=actor,
            target=member,
            details={"Details": details},
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.ban, user.id
        )
        if entry:
            actor = entry.user
        details = "Member was banned."
        await self._log_discord_event(
            guild,
            event_type="member_ban",
            actor=actor,
            target=user,
            details={"Details": details},
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.unban, user.id
        )
        if entry:
            actor = entry.user
        details = "Member was unbanned."
        await self._log_discord_event(
            guild,
            event_type="member_unban",
            actor=actor,
            target=user,
            details={"Details": details},
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        author = message.author if isinstance(message.author, discord.abc.User) else None
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
            target=author,
            details=details,
        )

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None:
            return
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
        await self._log_discord_event(
            after.guild,
            event_type="message_edit",
            actor=author,
            target=author,
            details=details,
        )

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
        details: Mapping[str, str],
    ) -> None:
        channel_id = self._audit_channel_id(guild)
        if not channel_id:
            return

        created_at = utcnow()
        store = self.bot.storage.get_audit_store(guild.id)
        details_text = "\n".join(f"{key}: {value}" for key, value in details.items())
        store.add_discord_event(
            created_at=created_at,
            event_type=event_type,
            actor_id=actor.id if actor else None,
            actor_name=_display_user(actor),
            target_id=target.id if target else None,
            target_name=_display_user(target),
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
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Failed to send audit log message to channel %s for guild %s",
                channel_id,
                guild.id,
            )

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
    def _format_discord_table_row(row: Mapping[str, Any]) -> list[str]:
        created_at = AuditCog._format_timestamp(row["created_at"])
        event_type = row["event_type"]
        actor = AuditCog._format_user_label(row["actor_name"])
        target = AuditCog._format_user_label(row["target_name"])
        details = AuditCog._normalise_table_cell(row["details"] or "")
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
        try:
            payload = json.loads(details)
        except json.JSONDecodeError:
            summary = details
        else:
            summary = AuditCog._summarise_gw2_payload(payload)
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
    def _format_user_label(value: Optional[str]) -> str:
        if not value:
            return "Unknown"
        match = re.search(r"<@!?\d+>\s*\(([^)]+)\)", value)
        if match:
            username = match.group(1)
            return AuditCog._normalise_table_cell(f"@{username} ({username})")
        cleaned = AuditCog._normalise_table_cell(value)
        if cleaned.startswith("<@") and cleaned.endswith(">"):
            return "Unknown"
        return cleaned

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



async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AuditCog(bot))
