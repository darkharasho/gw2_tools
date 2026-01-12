"""Audit logging and query tooling for GW2 Tools."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
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
            details=_format_actor_target(None, member),
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
        details = _format_actor_target(actor, member)
        await self._log_discord_event(
            guild,
            event_type=event_type,
            actor=actor,
            target=member,
            details=details,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.ban, user.id
        )
        if entry:
            actor = entry.user
        details = _format_actor_target(actor, user)
        await self._log_discord_event(
            guild,
            event_type="member_ban",
            actor=actor,
            target=user,
            details=details,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = None
        entry = await self._find_audit_entry(
            guild, discord.AuditLogAction.unban, user.id
        )
        if entry:
            actor = entry.user
        details = _format_actor_target(actor, user)
        await self._log_discord_event(
            guild,
            event_type="member_unban",
            actor=actor,
            target=user,
            details=details,
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        author = message.author if isinstance(message.author, discord.abc.User) else None
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
            target=author,
            details=details,
        )

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None:
            return
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
        await self._log_discord_event(
            after.guild,
            event_type="message_edit",
            actor=author,
            target=author,
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
        details: str,
    ) -> None:
        channel_id = self._audit_channel_id(guild)
        if not channel_id:
            return

        created_at = utcnow()
        store = self.bot.storage.get_audit_store(guild.id)
        store.add_discord_event(
            created_at=created_at,
            event_type=event_type,
            actor_id=actor.id if actor else None,
            actor_name=_display_user(actor),
            target_id=target.id if target else None,
            target_name=_display_user(target),
            details=details,
        )

        title = DISCORD_EVENT_TITLES.get(event_type, event_type.replace("_", " ").title())
        body = f"**{title}**\n{details}".strip()
        await self._send_audit_message(guild, channel_id, body)

    async def _send_audit_message(
        self, guild: discord.Guild, channel_id: int, content: str
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

        safe_content = _truncate(content, AUDIT_CHANNEL_MESSAGE_LIMIT)
        try:
            await channel.send(safe_content)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Failed to send audit log message to channel %s for guild %s",
                channel_id,
                guild.id,
            )

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
        try:
            payload = json.loads(details)
        except json.JSONDecodeError:
            summary = details
        else:
            summary = AuditCog._summarise_gw2_payload(payload)
        return _truncate(
            f"{created_at} | {event_type} | user: {user} | {summary}",
            1000,
        )

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


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AuditCog(bot))
