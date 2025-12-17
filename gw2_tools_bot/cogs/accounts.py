from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
from collections import defaultdict
from io import StringIO
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..http_utils import read_response_text
from ..storage import ApiKeyRecord, normalise_guild_id, utcnow

LOGGER = logging.getLogger(__name__)


class AccountsCog(commands.Cog):
    """Manage Guild Wars 2 API keys and guild role mappings."""

    REQUIRED_PERMISSIONS = {"account", "characters", "guilds", "wvw"}

    guild_roles = app_commands.Group(
        name="guildroles", description="Configure Guild Wars 2 guild to role mappings."
    )
    api_keys = app_commands.Group(
        name="apikey", description="Manage your Guild Wars 2 API keys."
    )
    guild_lookup = app_commands.Group(
        name="gw2guild", description="Look up Guild Wars 2 guild information."
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._refresh_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._member_cache_refresher.start()
        self._refresh_task = asyncio.create_task(self._run_initial_refreshes())

    # ------------------------------------------------------------------
    # Presentation helpers
    # ------------------------------------------------------------------
    def _embed(
        self,
        *,
        title: str,
        description: Optional[str] = None,
        colour: discord.Colour = BRAND_COLOUR,
    ) -> discord.Embed:
        embed = discord.Embed(title=title, description=description or "", colour=colour)
        embed.set_footer(text="Guild Wars 2 Tools")
        return embed

    @staticmethod
    def _format_list(items: Sequence[str], *, placeholder: str = "None") -> str:
        if not items:
            return placeholder
        return "\n".join(f"• {value}" for value in items)

    @staticmethod
    def _format_table(
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        placeholder: str = "None",
        code_block: bool = True,
    ) -> str:
        if not rows:
            return placeholder

        widths = [len(header) for header in headers]
        for row in rows:
            for idx, cell in enumerate(row):
                widths[idx] = max(widths[idx], len(cell))

        def _format_row(row: Sequence[str]) -> str:
            padded_cells = [f" {cell.ljust(widths[idx])} " for idx, cell in enumerate(row)]
            return "|" + "|".join(padded_cells) + "|"

        def _divider(char: str) -> str:
            segments = (char * (width + 2) for width in widths)
            return "+" + "+".join(segments) + "+"

        header_divider = _divider("=")
        row_divider = _divider("-")

        lines = [header_divider, _format_row(headers), header_divider]
        lines.extend(_format_row(row) for row in rows)
        lines.append(row_divider)
        table = "\n".join(lines)
        return f"```\n{table}\n```" if code_block else table

    def _add_table_field_with_chunks(
        self,
        embed: discord.Embed,
        *,
        base_name: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        placeholder: str = "None",
    ) -> None:
        """Add one or more embed fields for a table while respecting Discord limits."""

        if not rows:
            embed.add_field(name=base_name, value=placeholder, inline=False)
            return

        remaining_rows = list(rows)
        part = 1
        while remaining_rows:
            chunk: List[Sequence[str]] = []
            while remaining_rows:
                candidate_row = remaining_rows[0]
                candidate_chunk = chunk + [candidate_row]
                candidate_table = self._format_table(headers, candidate_chunk)
                if len(candidate_table) > 1024:
                    break
                chunk.append(remaining_rows.pop(0))

            # Safety fallback: ensure progress even if a single row exceeds the limit.
            if not chunk:
                chunk.append(remaining_rows.pop(0))

            name = base_name if part == 1 else f"{base_name} (part {part})"
            table_value = self._format_table(headers, chunk)
            if len(table_value) > 1024:
                body = table_value[4:-4] if table_value.startswith("```\n") and table_value.endswith("```") else table_value
                allowed_body_length = 1024 - 8  # opening and closing code fences
                truncated_body = body[: allowed_body_length - 3] + "..."
                table_value = f"```\n{truncated_body}\n```" if table_value.startswith("```\n") else truncated_body[:1024]
            embed.add_field(
                name=name,
                value=table_value,
                inline=False,
            )
            part += 1

    def _table_sections(
        self,
        *,
        base_title: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        placeholder: str = "None",
        limit: int = 1800,
    ) -> List[str]:
        """Format one or more text sections containing tables under a character limit."""

        def _truncate_table(table: str, allowed_length: int) -> str:
            if allowed_length <= 0:
                return ""
            if table.startswith("```\n") and table.endswith("\n```"):
                body = table[4:-4]
                allowed_body_length = max(0, allowed_length - 8)
                if len(body) <= allowed_body_length:
                    return table
                truncated_body = body[: max(0, allowed_body_length - 3)] + "..."
                return f"```\n{truncated_body}\n```"
            return table[:allowed_length]

        if not rows:
            return [f"**{base_title}**\n{placeholder}"]

        remaining_rows = list(rows)
        sections: List[str] = []
        part = 1
        while remaining_rows:
            chunk: List[Sequence[str]] = []
            while remaining_rows:
                candidate_row = remaining_rows[0]
                candidate_chunk = chunk + [candidate_row]
                candidate_table = self._format_table(headers, candidate_chunk)
                candidate_title = base_title if part == 1 else f"{base_title} (part {part})"
                candidate_content = f"**{candidate_title}**\n{candidate_table}"
                if len(candidate_content) > limit:
                    break
                chunk.append(remaining_rows.pop(0))

            if not chunk:
                chunk.append(remaining_rows.pop(0))

            title = base_title if part == 1 else f"{base_title} (part {part})"
            table = self._format_table(headers, chunk)
            content = f"**{title}**\n{table}"
            if len(content) > limit:
                allowed_table_length = max(0, limit - len(f"**{title}**\n"))
                table = _truncate_table(table, allowed_table_length)
                content = f"**{title}**\n{table}" if table else f"**{title}**"
            sections.append(content)
            part += 1

        return sections

    @staticmethod
    def _character_summary(characters: Sequence[str]) -> str:
        count = len(characters)
        if not count:
            return "No characters found"
        if count == 1:
            return "1 character synced"
        return f"{count} characters synced"

    @staticmethod
    def _normalise_guild_id(guild_id: str) -> str:
        return normalise_guild_id(guild_id)

    @staticmethod
    def _normalise_account_name(name: str) -> str:
        return name.strip().casefold()

    @staticmethod
    def _strip_emoji(text: str) -> str:
        emoji_pattern = re.compile(
            """
            [\U0001F1E6-\U0001F1FF]  # flags (iOS)
            |[\U0001F300-\U0001F5FF]  # symbols & pictographs
            |[\U0001F600-\U0001F64F]  # emoticons
            |[\U0001F680-\U0001F6FF]  # transport & map symbols
            |[\U0001F700-\U0001F77F]
            |[\U0001F780-\U0001F7FF]
            |[\U0001F800-\U0001F8FF]
            |[\U0001F900-\U0001F9FF]
            |[\U0001FA00-\U0001FA6F]
            |[\U0001FA70-\U0001FAFF]
            |[\U00002702-\U000027B0]
            |[\U000024C2-\U0001F251]
            """,
            flags=re.UNICODE | re.VERBOSE,
        )
        return emoji_pattern.sub("", text)

    async def _send_embed(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        description: str,
        colour: discord.Colour = BRAND_COLOUR,
        ephemeral: bool = True,
        use_followup: bool = False,
    ) -> None:
        embed = self._embed(title=title, description=description, colour=colour)
        if use_followup or interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept-Encoding": "gzip, deflate, br"},
                auto_decompress=False,
            )
        return self._session

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        if self._refresh_task:
            self._refresh_task.cancel()
        self._member_cache_refresher.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    async def _fetch_json(
        self,
        url: str,
        *,
        api_key: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict | List:
        session = await self._get_session()
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                text = await read_response_text(response)
        except aiohttp.ClientError as exc:
            raise ValueError(f"Failed to reach the Guild Wars 2 API: {exc}") from exc

        if response.status != 200:
            raise ValueError(
                f"Guild Wars 2 API returned {response.status}: {text[:200]}"
            )

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError("Unexpected response format from the Guild Wars 2 API") from exc

    async def _fetch_guild_details(
        self, guild_ids: Iterable[str], *, api_key: Optional[str] = None
    ) -> Dict[str, str]:
        details: Dict[str, str] = {}
        cache_payload: Dict[str, Tuple[str, Optional[str]]] = {}
        for guild_id in guild_ids:
            if not guild_id:
                continue
            try:
                payload = await self._fetch_json(
                    f"https://api.guildwars2.com/v2/guild/{guild_id}", api_key=api_key
                )
            except ValueError:
                continue
            name = payload.get("name")
            tag = payload.get("tag")
            if isinstance(name, str) and isinstance(tag, str):
                details[guild_id] = f"{name} [{tag}]"
                cache_payload[guild_id] = (name, tag)
            elif isinstance(name, str):
                details[guild_id] = name
                cache_payload[guild_id] = (name, None)
        if cache_payload:
            self.bot.storage.upsert_guild_details(cache_payload)
        return details

    async def _cached_guild_labels(self, guild_ids: Sequence[str]) -> Dict[str, str]:
        if not guild_ids:
            return {}
        try:
            return await self._fetch_guild_details(guild_ids)
        except ValueError:
            LOGGER.warning("Guild lookup failed while fetching live labels", exc_info=True)
            return {}

    async def _run_initial_refreshes(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self._refresh_member_cache()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Initial cache refresh failed")

    async def _refresh_member_cache(self) -> None:
        for guild_id, user_id, record in self.bot.storage.all_api_keys():
            try:
                (
                    permissions,
                    guild_ids,
                    _guild_details,
                    account_name,
                    _missing,
                    characters,
                ) = await self._validate_api_key(
                    record.key, allow_missing_permissions=True
                )
            except ValueError as exc:
                LOGGER.warning(
                    "Failed to refresh API key for guild %s user %s: %s",
                    guild_id,
                    user_id,
                    exc,
                )
                continue

            refreshed = ApiKeyRecord(
                name=record.name,
                key=record.key,
                account_name=account_name,
                permissions=permissions,
                guild_ids=guild_ids,
                guild_labels=_guild_details,
                characters=characters,
                created_at=record.created_at,
                updated_at=utcnow(),
            )
            self.bot.storage.upsert_api_key(guild_id, user_id, refreshed)

    async def _fetch_guild_members(
        self, guild_id: str, *, api_key: str
    ) -> List[Dict[str, object]]:
        payload = await self._fetch_json(
            f"https://api.guildwars2.com/v2/guild/{guild_id}/members", api_key=api_key
        )

        if not isinstance(payload, list):
            raise ValueError(
                "Unexpected response from /v2/guild/:id/members. The endpoint should return a list of members."
            )

        members: List[Dict[str, object]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            members.append(
                {
                    "name": name.strip(),
                    "wvw_member": bool(entry.get("wvw_member")),
                }
            )

        return members

    def _find_mapped_guild(self, config: GuildConfig, role_id: int) -> Optional[str]:
        for guild_id, configured_role_id in config.guild_role_ids.items():
            if configured_role_id == role_id:
                return guild_id
        return None

    def _find_user_guild_key(
        self, guild: discord.Guild, user_id: int, guild_id: str
    ) -> Optional[ApiKeyRecord]:
        keys = self.bot.storage.get_user_api_keys(guild.id, user_id)
        target = self._normalise_guild_id(guild_id)
        for record in keys:
            guild_memberships = {self._normalise_guild_id(value) for value in record.guild_ids}
            permissions = {value.lower() for value in record.permissions}
            if target in guild_memberships and "guilds" in permissions:
                return record
        return None

    @tasks.loop(hours=24 * 7)
    async def _member_cache_refresher(self) -> None:
        try:
            await self._refresh_member_cache()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Scheduled member cache refresh failed")

    @_member_cache_refresher.before_loop
    async def _wait_for_member_cache_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _fetch_character_names(self, api_key: str) -> List[str]:
        payload = await self._fetch_json(
            "https://api.guildwars2.com/v2/characters", api_key=api_key
        )
        if not isinstance(payload, list):
            raise ValueError(
                "Unexpected response from /v2/characters. The endpoint should return a list of character names."
            )

        names: List[str] = []
        for name in payload:
            if isinstance(name, str):
                cleaned = name.strip()
                if cleaned:
                    names.append(cleaned)
        return sorted({value.casefold(): value for value in names}.values())

    async def _validate_api_key(
        self, api_key: str, *, allow_missing_permissions: bool = False
    ) -> Tuple[List[str], List[str], Dict[str, str], str, List[str], List[str]]:
        tokeninfo = await self._fetch_json(
            "https://api.guildwars2.com/v2/tokeninfo", api_key=api_key
        )
        permissions_payload = tokeninfo.get("permissions") or []
        permissions_set = {
            perm.strip().lower()
            for perm in permissions_payload
            if isinstance(perm, str) and perm.strip()
        }
        permissions = sorted(permissions_set)
        missing = sorted(self.REQUIRED_PERMISSIONS.difference(permissions_set))
        if missing and not allow_missing_permissions:
            raise ValueError(
                "API key is missing required permissions: " + ", ".join(missing)
            )

        account = await self._fetch_json(
            "https://api.guildwars2.com/v2/account", api_key=api_key
        )
        account_name = account.get("name", "Unknown account")
        guild_ids_payload = account.get("guilds") or []
        if not isinstance(guild_ids_payload, list):
            raise ValueError(
                "Unexpected response from /v2/account. The `guilds` field should be an array of guild ID strings."
            )
        guild_ids = sorted(
            {
                self._normalise_guild_id(guild_id)
                for guild_id in guild_ids_payload
                if isinstance(guild_id, str) and guild_id.strip()
            }
        )
        guild_details = await self._fetch_guild_details(guild_ids, api_key=api_key)
        characters = await self._fetch_character_names(api_key)
        return permissions, guild_ids, guild_details, account_name, missing, characters

    def _generate_default_name(
        self, account_name: str, existing_records: Sequence[ApiKeyRecord]
    ) -> str:
        base = account_name or "Account"
        existing = {record.name.lower() for record in existing_records}
        if base.lower() not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})".lower() in existing:
            suffix += 1
        return f"{base} ({suffix})"

    async def _resolve_record_details(
        self, record: ApiKeyRecord
    ) -> Tuple[str, List[str], Optional[str]]:
        account_name = record.account_name or ""
        error: Optional[str] = None
        guild_details: Dict[str, str] = {}

        try:
            guild_details = await self._cached_guild_labels(record.guild_ids)
        except Exception as exc:  # pragma: no cover - defensive fallback for legacy rows
            error = str(exc)

        if not isinstance(guild_details, dict):
            guild_details = {}

        if not account_name:
            try:
                account = await self._fetch_json(
                    "https://api.guildwars2.com/v2/account", api_key=record.key
                )
                account_name_raw = account.get("name")
                if isinstance(account_name_raw, str) and account_name_raw.strip():
                    account_name = account_name_raw.strip()
            except ValueError as exc:
                error = error or str(exc)

        if not account_name:
            account_name = "Unknown account"

        guild_ids = record.guild_ids or []
        guild_labels = [guild_details.get(gid, gid) for gid in guild_ids]
        return account_name, guild_labels, error

    # ------------------------------------------------------------------
    # Role syncing
    # ------------------------------------------------------------------
    async def _sync_roles(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        allowed_role_ids_to_remove: Optional[set[int]] = None,
    ) -> Tuple[List[discord.Role], List[discord.Role], Optional[str]]:
        config = self.bot.get_config(guild.id)
        if not config.guild_role_ids:
            return [], [], None

        guild_memberships: set[str] = set()
        for record in self.bot.storage.get_user_api_keys(guild.id, member.id):
            guild_memberships.update(self._normalise_guild_id(gid) for gid in record.guild_ids)

        normalized_role_map = {
            self._normalise_guild_id(guild_id): role_id
            for guild_id, role_id in config.guild_role_ids.items()
        }

        desired_role_ids = {
            role_id for guild_id, role_id in normalized_role_map.items() if guild_id in guild_memberships
        }

        me = guild.me
        if not me:
            return [], [], "I could not determine my permissions to manage roles in this server."

        permissions = me.guild_permissions
        if not permissions.manage_roles:
            return (
                [],
                [],
                "I need the **Manage Roles** permission to assign or remove Guild Wars 2 guild roles. "
                "Ask a server administrator to update my permissions.",
            )

        unmanageable_roles = [
            role
            for role_id in desired_role_ids.union(config.guild_role_ids.values())
            if (role := guild.get_role(role_id)) is not None
            if role >= me.top_role
        ]
        if unmanageable_roles:
            role_mentions = ", ".join(role.mention for role in unmanageable_roles[:5])
            return (
                [],
                [],
                "I cannot manage the configured roles because my highest role is below: "
                f"{role_mentions}. Move my role above these to enable automatic assignment.",
            )

        desired_roles = [
            role for role_id in desired_role_ids if (role := guild.get_role(role_id)) is not None
        ]
        current_roles = set(member.roles)
        mapped_roles = {
            role
            for role_id in config.guild_role_ids.values()
            if (role := guild.get_role(role_id)) is not None and role in current_roles
        }

        roles_to_add = [role for role in desired_roles if role not in current_roles]
        roles_to_remove = [
            role
            for role in mapped_roles
            if role.id not in desired_role_ids
            and (not allowed_role_ids_to_remove or role.id in allowed_role_ids_to_remove)
        ]

        added: List[discord.Role] = []
        removed: List[discord.Role] = []
        error: Optional[str] = None

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="GW2 guild role sync")
                added = roles_to_add
            except discord.Forbidden:
                error = "I do not have permission to assign one or more roles."
            except discord.HTTPException:
                error = "Failed to assign roles due to a Discord error."

        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="GW2 guild role sync")
                removed = roles_to_remove
            except discord.Forbidden:
                error = error or "I do not have permission to remove one or more roles."
            except discord.HTTPException:
                error = error or "Failed to remove roles due to a Discord error."

        return added, removed, error

    async def _build_guild_role_embeds(
        self,
        guild: Optional[discord.Guild],
        *,
        title: str,
        description: str,
        guild_ids: Sequence[str],
    ) -> List[discord.Embed]:
        embeds: List[discord.Embed] = []

        if not guild_ids:
            embeds.append(self._embed(title=title, description=description))
            return embeds

        role_map = (
            self.bot.get_config(guild.id).guild_role_ids  # type: ignore[union-attr]
            if guild
            else {}
        )
        guild_details = await self._cached_guild_labels(guild_ids)

        embed = self._embed(title=title, description=description)
        role_summary: List[str] = []
        for role_id in role_map.values():
            role = guild.get_role(role_id) if guild else None
            if role:
                role_summary.append(role.mention)

        def _add_summary(target: discord.Embed) -> None:
            if role_summary:
                target.add_field(
                    name="Configured Discord roles",
                    value=self._format_list(role_summary),
                    inline=False,
                )

        _add_summary(embed)
        for index, guild_id in enumerate(guild_ids, start=1):
            if len(embed.fields) >= 25:
                embeds.append(embed)
                embed = self._embed(title=title, description=description)
                _add_summary(embed)

            role_id = role_map.get(guild_id)
            role = guild.get_role(role_id) if guild and role_id else None
            role_label = role.mention if role else (f"role ID {role_id}" if role_id else "Not configured")
            label = guild_details.get(guild_id, guild_id)
            id_block = f"Guild ID:\n```\n{guild_id}\n```"
            value_lines = [id_block, f"Discord role: {role_label}"]

            embed.add_field(name=f"{index}. {label}", value="\n".join(value_lines), inline=False)

        embeds.append(embed)
        return embeds

    @guild_roles.command(
        name="audit", description="Audit Discord role assignments against live guild membership data."
    )
    @app_commands.describe(
        role="Discord role mapped to a Guild Wars 2 guild",
        csv_output="Attach a CSV export",
        ephemeral="Send the audit response privately",
    )
    async def audit_guild_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        csv_output: bool = False,
        ephemeral: bool = True,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_embed(
                interaction,
                title="Guild membership audit",
                description="This command can only be used in a server.",
                ephemeral=ephemeral,
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        guild_id = self._find_mapped_guild(config, role.id)
        if not guild_id:
            await self._send_embed(
                interaction,
                title="Guild membership audit",
                description=(
                    "That role is not mapped to a Guild Wars 2 guild. Use /guildroles set to map it before running"
                    " an audit."
                ),
                ephemeral=ephemeral,
            )
            return

        caller_key = self._find_user_guild_key(interaction.guild, interaction.user.id, guild_id)
        if not caller_key:
            await self._send_embed(
                interaction,
                title="Guild membership audit",
                description=(
                    "You need a stored API key with access to that guild. Use /apikey add with a key that"
                    " belongs to the guild and includes the guilds permission, then try again."
                ),
                ephemeral=ephemeral,
            )
            return

        await interaction.response.defer(ephemeral=ephemeral, thinking=True)

        try:
            members = await self._fetch_guild_members(guild_id, api_key=caller_key.key)
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="Guild membership audit",
                description=str(exc),
                colour=BRAND_COLOUR,
                use_followup=True,
                ephemeral=ephemeral,
            )
            return

        role_to_guild = {role_id: gid for gid, role_id in config.guild_role_ids.items()}
        guild_labels = self.bot.storage.get_guild_labels(role_to_guild.values())

        # Ensure we have the full member list before inspecting role holders.
        try:
            await interaction.guild.chunk(cache=True)
        except Exception:
            LOGGER.exception("Failed to chunk guild members before audit")

        def guild_tag_for_id(gid: str) -> str:
            label = guild_labels.get(gid, gid)
            match = re.search(r"\[(.+?)\]", label)
            return f"[{match.group(1)}]" if match else label

        def guild_tags_for_member(member: discord.Member) -> List[str]:
            tags: List[str] = []
            for member_role in member.roles:
                mapped_guild_id = role_to_guild.get(member_role.id)
                if not mapped_guild_id:
                    continue
                tag = guild_tag_for_id(mapped_guild_id)
                if tag not in tags:
                    tags.append(tag)
            return tags

        guild_member_lookup: Dict[str, str] = {}
        wvw_members: Dict[str, str] = {}
        member_wvw_lookup: Dict[str, bool] = {}
        for entry in members:
            name = str(entry["name"])
            normalized_name = self._normalise_account_name(name)
            guild_member_lookup[normalized_name] = name
            is_wvw_member = bool(entry.get("wvw_member"))
            member_wvw_lookup[normalized_name] = is_wvw_member
            if is_wvw_member:
                wvw_members[normalized_name] = name

        discrepancy_rows: List[Sequence[str]] = []
        csv_rows: List[Sequence[str]] = []
        processed_accounts: set[str] = set()

        account_records: Dict[str, List[Tuple[int, ApiKeyRecord]]] = defaultdict(list)
        user_records: Dict[int, List[ApiKeyRecord]] = defaultdict(list)
        for _guild_id, user_id, record in self.bot.storage.query_api_keys(
            guild_id=interaction.guild.id, gw2_guild_id=guild_id
        ):
            user_records[user_id].append(record)
            if record.account_name:
                account_records[self._normalise_account_name(record.account_name)].append(
                    (user_id, record)
                )

        for member in role.members:
            records = [
                record
                for record in user_records.get(member.id, [])
                if self._normalise_guild_id(guild_id)
                in {self._normalise_guild_id(value) for value in record.guild_ids}
            ]

            account_names = {
                record.account_name for record in records if record.account_name
            }
            normalized_accounts = {
                self._normalise_account_name(name) for name in account_names if name
            }
            processed_accounts.update(normalized_accounts)

            display_name = self._strip_emoji(member.display_name)
            roles = ", ".join(
                sorted(
                    self._strip_emoji(role.name)
                    for role in member.roles
                    if role.name and not role.is_default()
                )
            )
            guild_tags = ", ".join(guild_tags_for_member(member)) or "—"

            if not account_names:
                discrepancy_rows.append((display_name, "—", guild_tags, "No API key"))
                csv_rows.append(
                    (
                        self._strip_emoji(member.name),
                        "—",
                        guild_tags,
                        "No API key",
                        roles,
                    )
                )
                continue

            for account_name in sorted(account_names):
                normalised = self._normalise_account_name(account_name)
                in_guild = normalised in guild_member_lookup
                is_wvw = normalised in wvw_members
                clean_account = self._strip_emoji(account_name)

                if not in_guild:
                    discrepancy_rows.append(
                        (display_name, clean_account, guild_tags, "Not in guild")
                    )
                    csv_rows.append(
                        (
                            self._strip_emoji(member.name),
                            clean_account,
                            guild_tags,
                            "Not in guild",
                            roles,
                        )
                    )
                elif not is_wvw:
                    discrepancy_rows.append(
                        (display_name, clean_account, guild_tags, "Not WvW member")
                    )
                    csv_rows.append(
                        (
                            self._strip_emoji(member.name),
                            clean_account,
                            guild_tags,
                            "Not WvW member",
                            roles,
                        )
                    )

        target_guild_tag = guild_tag_for_id(guild_id)
        missing_role_label = self._strip_emoji(role.name) or "role"
        for normalized_name, original_name in guild_member_lookup.items():
            if normalized_name in processed_accounts:
                continue

            records = account_records.get(normalized_name)
            if not records:
                discrepancy_rows.append(
                    ("—", self._strip_emoji(original_name), target_guild_tag, "No API key")
                )
                csv_rows.append(
                    (
                        "—",
                        self._strip_emoji(original_name),
                        target_guild_tag,
                        "No API key",
                        "—",
                    )
                )
                continue

            for user_id, record in records:
                member = interaction.guild.get_member(user_id)
                display_name = (
                    self._strip_emoji(member.display_name) if member else "—"
                )
                roles = (
                    ", ".join(
                        sorted(
                            self._strip_emoji(role.name)
                            for role in member.roles
                            if role.name and not role.is_default()
                        )
                    )
                    if member
                    else "—"
                )
                guild_tags = (
                    ", ".join(guild_tags_for_member(member)) or target_guild_tag
                    if member
                    else target_guild_tag
                )

                has_role = member is not None and role in member.roles
                if not has_role:
                    discrepancy_rows.append(
                        (
                            display_name,
                            self._strip_emoji(record.account_name or original_name),
                            guild_tags,
                            f"Not in {missing_role_label}",
                        )
                    )
                    csv_rows.append(
                        (
                            self._strip_emoji(member.name) if member else "—",
                            self._strip_emoji(record.account_name or original_name),
                            guild_tags,
                            f"Not in {missing_role_label}",
                            roles,
                        )
                    )

                if has_role and not member_wvw_lookup.get(normalized_name, False):
                    discrepancy_rows.append(
                        (
                            display_name,
                            self._strip_emoji(record.account_name or original_name),
                            guild_tags,
                            "Not WvW member",
                        )
                    )
                    csv_rows.append(
                        (
                            self._strip_emoji(member.name) if member else "—",
                            self._strip_emoji(record.account_name or original_name),
                            guild_tags,
                            "Not WvW member",
                            roles,
                        )
                    )

        guild_label = guild_labels.get(guild_id, guild_id)

        summary_lines = [
            "**Guild membership audit**",
            "Compared live Guild Wars 2 guild membership against current Discord role assignments using your API key to avoid stale data.",
            "",
            f"Guild: {guild_label}",
            f"Guild ID: `{guild_id}`",
            f"Role: {role.mention}",
        ]

        report_table = self._format_table(
            ["Discord", "GW2 account", "Guilds", "Issue"],
            discrepancy_rows,
            placeholder="None",
            code_block=False,
        )

        report_buffer = StringIO()
        report_buffer.write(report_table)
        report_buffer.seek(0)

        files: List[discord.File] = [
            discord.File(fp=StringIO(report_buffer.read()), filename="guild_audit.txt")
        ]
        if csv_output:
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["Discord username", "GW2 account", "Guilds", "Issue", "Roles"])
            writer.writerows(csv_rows)
            buffer.seek(0)
            files.append(discord.File(fp=StringIO(buffer.read()), filename="guild_audit.csv"))

        content = "\n".join(summary_lines + ["", "Attached guild_audit.txt with audit results."])
        await interaction.followup.send(content=content, files=files, ephemeral=ephemeral)

    # ------------------------------------------------------------------
    # Guild lookup
    # ------------------------------------------------------------------
    @guild_lookup.command(name="search", description="Find a Guild Wars 2 guild ID by name.")
    @app_commands.describe(name="Full or partial guild name to search for")
    async def guild_search(self, interaction: discord.Interaction, name: str) -> None:
        query = name.strip()
        if not query:
            await self._send_embed(
                interaction,
                title="Search", 
                description="Please provide a guild name to search for.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild_ids = await self._fetch_json(
                "https://api.guildwars2.com/v2/guild/search", params={"name": query}
            )
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="Search failed",
                description=str(exc),
                colour=BRAND_COLOUR,
                use_followup=True,
            )
            return

        if not isinstance(guild_ids, list) or not guild_ids:
            await self._send_embed(
                interaction,
                title="Search",
                description=f"No guilds found matching `{query}`.",
                use_followup=True,
            )
            return

        details = await self._cached_guild_labels([gid for gid in guild_ids if isinstance(gid, str)])
        embed = self._embed(title="Guild search results", description=f"Matches for `{query}`")
        for guild_id in guild_ids[:10]:
            if not isinstance(guild_id, str):
                continue
            label = details.get(guild_id, guild_id)
            embed.add_field(name=label, value=f"```\n{guild_id}\n```", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Guild role configuration
    # ------------------------------------------------------------------
    @guild_roles.command(name="set", description="Map a Guild Wars 2 guild ID to a Discord role.")
    @app_commands.describe(guild_id="Guild Wars 2 guild ID", role="Discord role to assign")
    async def set_guild_role(
        self, interaction: discord.Interaction, guild_id: str, role: discord.Role
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        cleaned_guild_id = self._normalise_guild_id(guild_id)
        if not cleaned_guild_id:
            await self._send_embed(
                interaction,
                title="Guild role mapping",
                description="Please provide a valid guild ID.",
                colour=BRAND_COLOUR,
            )
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        config.guild_role_ids[cleaned_guild_id] = role.id
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        await self._send_embed(
            interaction,
            title="Guild role mapping saved",
            description=(
                f"Members of `{cleaned_guild_id}` will receive the {role.mention} role when their API key is verified."
            ),
        )

    @guild_roles.command(name="remove", description="Remove a guild to role mapping.")
    @app_commands.describe(
        guild_id="Guild Wars 2 guild ID to remove",
        cleanup_roles="Remove the mapped role from existing members",
    )
    async def remove_guild_role(
        self, interaction: discord.Interaction, guild_id: str, cleanup_roles: bool = False
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        existing_ids = list(config.guild_role_ids.keys())
        normalized_id = self._normalise_guild_id(guild_id)
        removed = config.guild_role_ids.pop(normalized_id, None)
        if removed is None and normalized_id != guild_id:
            removed = config.guild_role_ids.pop(guild_id, None)
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]

        if not removed:
            embeds = await self._build_guild_role_embeds(
                interaction.guild,  # type: ignore[arg-type]
                title="No mapping found",
                description=f"`{guild_id}` is not currently mapped. Choose from the options below.",
                guild_ids=existing_ids,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embeds=embeds, ephemeral=True)
            else:
                await interaction.response.send_message(embeds=embeds, ephemeral=True)
            return

        cleanup_summary = None
        role = interaction.guild.get_role(removed) if interaction.guild else None
        if cleanup_roles and role:
            removed_count = 0
            failure: Optional[str] = None
            for member in list(role.members):
                try:
                    await member.remove_roles(role, reason="GW2 guild role cleanup")
                    removed_count += 1
                except discord.Forbidden:
                    failure = "I do not have permission to remove the mapped role from all members."
                    break
                except discord.HTTPException:
                    failure = "Failed to remove the mapped role from some members due to a Discord error."
                    break

            if failure:
                cleanup_summary = failure
            else:
                cleanup_summary = f"Removed {removed_count} instance(s) of {role.mention} from members."
        elif cleanup_roles:
            cleanup_summary = "Cannot clean up roles because the mapped role no longer exists."

        description_lines = [f"Removed mapping for guild `{guild_id}`."]
        if cleanup_summary:
            description_lines.append(cleanup_summary)

        await self._send_embed(
            interaction,
            title="Guild role mapping removed",
            description="\n".join(description_lines),
        )

    @remove_guild_role.autocomplete("guild_id")
    async def remove_guild_role_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        config = self.bot.get_config(interaction.guild.id)
        guild_ids = list(config.guild_role_ids.keys())
        if not guild_ids:
            return []

        details = await self._cached_guild_labels(guild_ids)
        current_lower = current.lower()
        choices: List[app_commands.Choice[str]] = []
        for guild_id in guild_ids:
            label = details.get(guild_id, guild_id)
            if current_lower in guild_id.lower() or current_lower in label.lower():
                choices.append(app_commands.Choice(name=label, value=guild_id))
            if len(choices) >= 25:
                break
        return choices

    @guild_roles.command(name="list", description="List all configured guild role mappings.")
    async def list_guild_roles(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        if not config.guild_role_ids:
            await self._send_embed(
                interaction,
                title="Guild role mappings",
                description="No guild role mappings configured. Use /guildroles set to add one.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        embeds = await self._build_guild_role_embeds(
            interaction.guild,  # type: ignore[arg-type]
            title="Guild role mappings",
            description="Configured Guild Wars 2 guild to Discord role assignments.",
            guild_ids=list(config.guild_role_ids.keys()),
        )

        await interaction.followup.send(embeds=embeds, ephemeral=True)

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------
    def _mask_key(self, key: str) -> str:
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}…{key[-4:]}"

    def _find_existing_name(
        self, keys: Sequence[ApiKeyRecord], name: str
    ) -> Optional[ApiKeyRecord]:
        target = name.lower()
        for record in keys:
            if record.name.lower() == target:
                return record
        return None

    async def _autocomplete_key_names(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return []
        try:
            records = self.bot.storage.get_user_api_keys(
                interaction.guild.id, interaction.user.id
            )
        except Exception:
            LOGGER.exception(
                "Failed to load API key names for autocomplete in guild %s user %s",
                getattr(interaction.guild, "id", "unknown"),
                getattr(interaction.user, "id", "unknown"),
            )
            return []
        current_lower = current.lower()
        matches: List[app_commands.Choice[str]] = []
        for record in records:
            if current_lower in record.name.lower():
                matches.append(app_commands.Choice(name=record.name, value=record.name))
            if len(matches) >= 25:
                break
        return matches

    @api_keys.command(name="add", description="Add a new Guild Wars 2 API key.")
    @app_commands.describe(key="Your Guild Wars 2 API key")
    async def add_api_key(self, interaction: discord.Interaction, key: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        key_clean = key.strip()
        if not key_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide an API key.",
                colour=BRAND_COLOUR,
            )
            return

        existing_keys = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)

        steps: List[Dict[str, str]] = [
            {"label": "Key validation", "status": "⏳ Pending"},
            {"label": "Permission: account", "status": "⏳ Pending"},
            {"label": "Permission: characters", "status": "⏳ Pending"},
            {"label": "Permission: guilds", "status": "⏳ Pending"},
            {"label": "Permission: wvw", "status": "⏳ Pending"},
            {"label": "Account lookup", "status": "⏳ Pending"},
            {"label": "Guild lookup", "status": "⏳ Pending"},
            {"label": "Character lookup", "status": "⏳ Pending"},
            {"label": "Save key", "status": "⏳ Pending"},
            {"label": "Role sync", "status": "⏳ Pending"},
        ]

        def _set_status(label: str, status: str) -> None:
            for step in steps:
                if step["label"] == label:
                    step["status"] = status
                    return

        def _steps_value() -> str:
            return "\n".join(f"{step['status']} — {step['label']}" for step in steps)

        def _progress_embed(
            description: str, *, colour: discord.Colour = BRAND_COLOUR
        ) -> discord.Embed:
            embed = self._embed(
                title="API key verification",
                description=description,
                colour=colour,
            )
            embed.add_field(name="Verification steps", value=_steps_value(), inline=False)
            return embed

        async def _refresh_progress(description: str, *, colour: discord.Colour = BRAND_COLOUR) -> None:
            await interaction.edit_original_response(
                embed=_progress_embed(description, colour=colour)
            )

        await interaction.response.send_message(
            embed=_progress_embed("Starting verification and permission checks…"),
            ephemeral=True,
        )

        duplicate_key = next((record for record in existing_keys if record.key == key_clean), None)
        if duplicate_key:
            _set_status("Key validation", "❌ Duplicate")
            await _refresh_progress(
                "You have already saved this API key.",
                colour=BRAND_COLOUR,
            )
            return

        try:
            (
                permissions,
                guild_ids,
                guild_details,
                account_name,
                missing,
                characters,
            ) = await self._validate_api_key(key_clean, allow_missing_permissions=True)
        except ValueError as exc:
            _set_status("Key validation", "❌ Failed")
            await _refresh_progress(str(exc), colour=BRAND_COLOUR)
            return

        _set_status("Key validation", "✅ Success")
        await _refresh_progress("Key validated. Checking permissions…")
        for permission in sorted(self.REQUIRED_PERMISSIONS):
            label = f"Permission: {permission}"
            _set_status(label, "✅ Present" if permission in permissions else "❌ Missing")
            await _refresh_progress("Updated permission checks…")

        if missing:
            await _refresh_progress(
                "API key is missing required permissions. Please generate a key with `account`, `characters`, `guilds`, and `wvw`.",
                colour=BRAND_COLOUR,
            )
            return

        _set_status("Account lookup", "✅ Success")
        _set_status("Guild lookup", "✅ Success")
        _set_status("Character lookup", "✅ Success")
        await _refresh_progress("Account, guild, and character lookups completed. Saving key…")

        default_name = self._generate_default_name(account_name, existing_keys)

        record = ApiKeyRecord(
            name=default_name,
            key=key_clean,
            account_name=account_name,
            permissions=permissions,
            guild_ids=guild_ids,
            guild_labels=guild_details,
            characters=characters,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.bot.storage.upsert_api_key(interaction.guild.id, interaction.user.id, record)
        _set_status("Save key", "✅ Stored")
        await _refresh_progress("Key saved. Syncing roles…")

        added, removed, error = await self._sync_roles(interaction.guild, interaction.user)
        _set_status("Role sync", "✅ Completed" if not error else "⚠️ Issues")
        await _refresh_progress("Role synchronization complete.")

        role_lines: List[str] = []
        if added:
            role_lines.append("Added: " + ", ".join(role.mention for role in added))
        if removed:
            role_lines.append("Removed: " + ", ".join(role.mention for role in removed))
        if not added and not removed:
            role_lines.append(
                "No configured guild role mappings matched your guild memberships. Ask a moderator to set them with /guildroles set."
            )
        if error:
            role_lines.append(error)

        embed = self._embed(
            title="API key saved",
            description="Verification completed. Your key was stored and roles were synced.",
        )
        embed.add_field(name="Verification steps", value=_steps_value(), inline=False)
        embed.add_field(
            name="Role sync",
            value=self._format_list(role_lines, placeholder="No role changes"),
            inline=False,
        )
        embed.add_field(
            name="\u200b",
            value="__Stored API key details__",
            inline=False,
        )
        embed.add_field(
            name="Account",
            value=self._format_list([f"Key name: `{default_name}`", f"Account name: {account_name}"]),
            inline=True,
        )
        embed.add_field(
            name="Permissions",
            value=self._format_list(sorted(permissions), placeholder="No permissions"),
            inline=True,
        )

        cached_guild_details = await self._cached_guild_labels(guild_ids)
        guild_labels = [cached_guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
        embed.add_field(
            name="Guild memberships",
            value=self._format_list(guild_labels, placeholder="No guilds found"),
            inline=False,
        )

        embed.add_field(
            name="Characters",
            value=self._character_summary(characters),
            inline=False,
        )

        embed.add_field(name="API key", value=f"```{key_clean}```", inline=False)

        await interaction.edit_original_response(embed=embed)

    @api_keys.command(name="remove", description="Delete a stored API key.")
    @app_commands.describe(name="Name of the key to delete")
    async def remove_api_key(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        name_clean = name.strip()
        if not name_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide the name of the key to delete.",
                colour=BRAND_COLOUR,
            )
            return

        record = self.bot.storage.find_api_key(interaction.guild.id, interaction.user.id, name_clean)
        if not record:
            await self._send_embed(
                interaction,
                title="API key",
                description="No stored key found with that name. Use /apikey list to see saved keys.",
                colour=BRAND_COLOUR,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        config = self.bot.get_config(interaction.guild.id)
        normalized_role_map = {
            self._normalise_guild_id(guild_id): role_id
            for guild_id, role_id in config.guild_role_ids.items()
        }

        account_name, guild_labels, resolve_error = await self._resolve_record_details(record)
        deleted = self.bot.storage.delete_api_key(
            interaction.guild.id, interaction.user.id, name_clean
        )

        remaining_keys = self.bot.storage.get_user_api_keys(
            interaction.guild.id, interaction.user.id
        )
        remaining_memberships = {
            self._normalise_guild_id(guild_id)
            for key in remaining_keys
            for guild_id in key.guild_ids
            if self._normalise_guild_id(guild_id)
        }
        lost_guilds = {
            normalized
            for guild_id in record.guild_ids
            if (normalized := self._normalise_guild_id(guild_id))
            and normalized not in remaining_memberships
        }

        allowed_role_ids_to_remove = {
            role_id for guild_id, role_id in normalized_role_map.items() if guild_id in lost_guilds
        }
        added, removed, error = await self._sync_roles(
            interaction.guild,
            interaction.user,
            allowed_role_ids_to_remove=allowed_role_ids_to_remove,
        )

        embed = self._embed(
            title="API key removed" if deleted else "API key",
            description=(
                "Your key was removed and your roles were resynced."
                if deleted
                else "No changes were made because the key could not be removed."
            ),
        )

        action_lines: List[str] = []
        action_lines.append("✅ Deleted stored key." if deleted else "❌ Failed to delete the stored key.")
        role_sync_lines: List[str] = []
        guild_detail_map = (
            await self._cached_guild_labels(lost_guilds) if lost_guilds else {}
        )
        removed_ids = {role.id for role in removed}
        member_roles = set(interaction.user.roles)
        for guild_id in sorted(lost_guilds):
            role_id = normalized_role_map.get(guild_id)
            role = interaction.guild.get_role(role_id) if role_id else None
            label = guild_detail_map.get(guild_id, guild_id)
            if role and role_id in removed_ids:
                role_sync_lines.append(f"✅ Removed {role.mention} for {label}")
            elif role and role in member_roles and role_id in allowed_role_ids_to_remove:
                note = error or "I could not remove the role automatically."
                role_sync_lines.append(
                    f"⚠️ {note} Please remove {role.mention} for {label} manually."
                )
            elif role and role in member_roles:
                role_sync_lines.append(
                    f"ℹ️ Kept {role.mention} for {label} because you still have another key with that guild."
                )
            elif role:
                role_sync_lines.append(f"ℹ️ No {role.mention} assigned for {label}")
            else:
                role_sync_lines.append(f"ℹ️ No mapped Discord role for {label}")

        if removed:
            action_lines.append("✅ Roles removed: " + ", ".join(role.mention for role in removed))
        if added:
            action_lines.append("✅ Roles added: " + ", ".join(role.mention for role in added))
        if error:
            action_lines.append(f"⚠️ {error}")
        embed.add_field(name="Actions", value=self._format_list(action_lines), inline=False)

        embed.add_field(
            name="Role sync",
            value=self._format_list(
                role_sync_lines,
                placeholder=(
                    "No roles changed. Your other stored API keys still cover your guild memberships."
                    if not removed
                    else "No guild roles are configured for your memberships."
                ),
            ),
            inline=False,
        )

        embed.add_field(
            name="\u200b",
            value="__Stored API key details__",
            inline=False,
        )

        embed.add_field(
            name="Account",
            value=self._format_list(
                [f"Key name: `{record.name}`", f"Account name: {account_name}"],
                placeholder="No account details",
            ),
            inline=True,
        )
        embed.add_field(
            name="Permissions",
            value=self._format_list(record.permissions, placeholder="None recorded"),
            inline=True,
        )

        guild_field_value: str
        if guild_labels:
            guild_field_value = self._format_list(guild_labels)
        elif record.guild_ids:
            guild_field_value = self._format_list(record.guild_ids)
        else:
            guild_field_value = "None"

        embed.add_field(name="Guild memberships", value=guild_field_value, inline=False)

        embed.add_field(
            name="Characters",
            value=self._character_summary(record.characters),
            inline=False,
        )

        if resolve_error or error:
            embed.add_field(
                name="Warnings",
                value=self._format_list(
                    [value for value in (resolve_error, error) if value],
                    placeholder="None",
                ),
                inline=False,
            )

        embed.add_field(
            name="API key",
            value=f"```{self._mask_key(record.key)}```",
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @api_keys.command(name="help", description="Show help for API key commands.")
    async def api_key_help(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        embed = self._embed(
            title="API key commands",
            description=(
                "Manage your Guild Wars 2 API keys. Commands use your saved keys to sync guild roles and keep"
                " your account data up to date."
            ),
        )

        embed.add_field(
            name="/apikey add",
            value=(
                "Register a new Guild Wars 2 API key. You'll be asked to paste your key and it must include"
                " the required permissions. Verification checks your account, guilds, and characters, then"
                " syncs any configured roles."
            ),
            inline=False,
        )

        embed.add_field(
            name="/apikey refresh <name>",
            value=(
                "Revalidate a stored key. This re-reads your account, guild, and character data, updates the"
                " saved record, and resyncs your mapped Discord roles. Use this after changing guilds or when"
                " your key permissions change."
            ),
            inline=False,
        )

        embed.add_field(
            name="/apikey list",
            value=(
                "View all of your saved keys along with their recorded permissions, guild memberships, and"
                " account names."
            ),
            inline=False,
        )

        embed.add_field(
            name="/apikey remove <name>",
            value=(
                "Delete a stored key. Any Discord roles that came from that key's guild memberships will be"
                " resynced afterward."
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @remove_api_key.autocomplete("name")
    async def remove_api_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._autocomplete_key_names(interaction, current)

    @api_keys.command(name="list", description="List your saved API keys.")
    async def list_api_keys(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        records = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        if not records:
            await self._send_embed(
                interaction,
                title="API keys",
                description="You have no saved keys. Use /apikey add to register one.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        embeds: List[discord.Embed] = []
        for record in records:
            account_name, guild_labels, error = await self._resolve_record_details(record)
            embed = self._embed(
                title=record.name,
                description="Saved Guild Wars 2 API key details.",
            )
            embed.add_field(
                name="Account",
                value=self._format_list([f"Account name: {account_name}", f"Key name: `{record.name}`"]),
                inline=True,
            )
            embed.add_field(
                name="Permissions",
                value=self._format_list(record.permissions, placeholder="None"),
                inline=True,
            )

            guild_field_value: str
            if guild_labels:
                guild_field_value = self._format_list(guild_labels)
            elif record.guild_ids:
                guild_field_value = self._format_list(record.guild_ids)
            else:
                guild_field_value = "None"

            embed.add_field(
                name="Guilds",
                value=guild_field_value,
                inline=False,
            )

            if error:
                embed.add_field(name="Warnings", value=error, inline=False)

            embed.add_field(name="API key", value=f"```{record.key}```", inline=False)
            embeds.append(embed)

        # Discord limits embeds per message; send additional followups if necessary.
        first_batch = embeds[:10]
        await interaction.followup.send(embeds=first_batch, ephemeral=True)
        remaining = embeds[10:]
        while remaining:
            await interaction.followup.send(embeds=remaining[:10], ephemeral=True)
            remaining = remaining[10:]

    @api_keys.command(
        name="refresh",
        description="Refresh a stored API key and resync your roles.",
    )
    @app_commands.describe(name="Existing key name")
    async def refresh_api_key(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        name_clean = name.strip()
        if not name_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide a key name to refresh.",
                colour=BRAND_COLOUR,
            )
            return

        record = self.bot.storage.find_api_key(
            interaction.guild.id, interaction.user.id, name_clean
        )
        if not record:
            await self._send_embed(
                interaction,
                title="API key",
                description="No stored key found with that name. Use /apikey list to see saved keys.",
                colour=BRAND_COLOUR,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            (
                permissions,
                guild_ids,
                guild_details,
                account_name,
                _,
                characters,
            ) = await self._validate_api_key(record.key)
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="API key validation failed",
                description=str(exc),
                colour=BRAND_COLOUR,
            )
            return

        refreshed_record = ApiKeyRecord(
            name=record.name,
            key=record.key,
            account_name=account_name,
            permissions=permissions,
            guild_ids=guild_ids,
            guild_labels=guild_details,
            characters=characters,
            created_at=record.created_at,
            updated_at=utcnow(),
        )
        self.bot.storage.upsert_api_key(
            interaction.guild.id, interaction.user.id, refreshed_record
        )

        added, removed, error = await self._sync_roles(
            interaction.guild, interaction.user
        )

        role_lines: List[str] = []
        if added:
            role_lines.append("Added: " + ", ".join(role.mention for role in added))
        if removed:
            role_lines.append("Removed: " + ", ".join(role.mention for role in removed))
        if error:
            role_lines.append(error)

        embed = self._embed(
            title="API key refreshed",
            description="Your key details were revalidated and roles resynced.",
        )
        embed.add_field(
            name="Actions",
            value=self._format_list(
                ["Updated stored key details", "Resynced mapped Discord roles"],
                placeholder="No actions recorded",
            ),
            inline=False,
        )
        embed.add_field(
            name="Role sync",
            value=self._format_list(role_lines, placeholder="No role changes"),
            inline=False,
        )
        embed.add_field(name="\u200b", value="__Stored API key details__", inline=False)
        embed.add_field(
            name="Account",
            value=self._format_list(
                [f"Key name: `{record.name}`", f"Account name: {account_name}"]
            ),
            inline=True,
        )
        embed.add_field(
            name="Permissions",
            value=self._format_list(sorted(permissions), placeholder="No permissions"),
            inline=True,
        )

        guild_labels = [guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
        embed.add_field(
            name="Guild memberships",
            value=self._format_list(guild_labels, placeholder="No guilds found"),
            inline=False,
        )

        embed.add_field(
            name="Characters",
            value=self._character_summary(characters),
            inline=False,
        )

        embed.add_field(
            name="API key", value=f"```{self._mask_key(record.key)}```", inline=False
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @refresh_api_key.autocomplete("name")
    async def refresh_api_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._autocomplete_key_names(interaction, current)

async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AccountsCog(bot))

