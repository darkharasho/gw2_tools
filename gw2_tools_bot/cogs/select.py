from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..constants import WVW_SERVER_NAMES
from ..http_utils import read_response_text
from ..storage import ISOFORMAT, ApiKeyRecord, normalise_guild_id

LOGGER = logging.getLogger(__name__)


@dataclass
class _FilterSet:
    guilds: List[str]
    roles: List[discord.Role]
    accounts: List[str]
    character_keys: List[str]
    character_labels: List[str]
    discord_members: List[discord.Member]
    filters: List[Tuple[str, str]]

    @property
    def character_provided(self) -> bool:
        return bool(self.character_keys)


class SelectCog(commands.Cog):
    """Admin member lookup with selectable filters and grouping."""

    select = app_commands.Group(
        name="select",
        description=(
            "Admin search to group members by guild, role, account, character, or Discord name."
        ),
        # Force an explicit default permission set so Discord refreshes any
        # previously cached administrator-only defaults and surfaces the
        # command to authorised moderator roles governed by runtime checks.
        default_permissions=discord.Permissions(),
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        super().__init__()
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        if self._session and not self._session.closed:
            await self._session.close()

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
    def _format_characters_block(names: Sequence[str]) -> str:
        if not names:
            return "```None```"

        # Distribute names across up to three columns for readability.
        columns = min(3, max(1, len(names)))
        rows = (len(names) + columns - 1) // columns
        grid: List[List[str]] = [[] for _ in range(rows)]
        for idx, name in enumerate(names):
            grid[idx % rows].append(name)

        col_widths = [0] * columns
        for col in range(columns):
            for row in grid:
                if col < len(row):
                    col_widths[col] = max(col_widths[col], len(row[col]))

        lines: List[str] = []
        for row in grid:
            padded = [
                value.ljust(col_widths[col_idx])
                for col_idx, value in enumerate(row)
            ]
            lines.append("  ".join(padded).rstrip())

        return "```\n" + "\n".join(lines) + "\n```"

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
            padded_cells = [
                f" {cell.ljust(widths[idx])} " for idx, cell in enumerate(row)
            ]
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

    @staticmethod
    def _trim_field(value: str, limit: int = 1024) -> str:
        if len(value) <= limit:
            return value

        suffix = "\n… truncated …"
        if len(suffix) >= limit:
            return value[:limit]

        allowed = limit - len(suffix)
        return value[:allowed].rstrip() + suffix

    @staticmethod
    def _character_key(name: str) -> str:
        """Case-insensitive key for character names."""

        return name.strip().casefold()

    @staticmethod
    def _option_names(interaction: discord.Interaction) -> set[str]:
        """Return provided option names for the current subcommand."""

        data = interaction.data or {}
        options = data.get("options") or []
        if options and isinstance(options[0], dict) and options[0].get("options"):
            options = options[0]["options"]

        names: set[str] = set()
        for option in options:
            if isinstance(option, dict):
                name = option.get("name")
                if isinstance(name, str):
                    names.add(name)
        return names

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

    async def _send_message(
        self,
        interaction: discord.Interaction,
        *,
        content: str,
        ephemeral: bool = True,
        use_followup: bool = False,
    ) -> None:
        if use_followup or interaction.response.is_done():
            await interaction.followup.send(content=content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, ephemeral=ephemeral)

    async def _safe_followup(
        self,
        interaction: discord.Interaction,
        *,
        embeds: Sequence[discord.Embed],
        files: Optional[Sequence[discord.File]] = None,
        content: Optional[str] = None,
    ) -> None:
        """Send a followup only if the interaction is still active."""

        try:
            await interaction.followup.send(
                content=content,
                embeds=list(embeds),
                files=list(files) if files is not None else [],
                ephemeral=True,
            )
        except discord.NotFound:
            LOGGER.warning("Interaction expired before results could be sent")

    async def _safe_followup_messages(
        self,
        interaction: discord.Interaction,
        *,
        contents: Sequence[str],
        files: Optional[Sequence[discord.File]] = None,
    ) -> None:
        """Send one or more followup messages, retrying only if interaction is active."""

        if not contents:
            return

        try:
            await interaction.followup.send(
                content=contents[0],
                files=list(files) if files is not None else [],
                ephemeral=True,
            )
            for content in contents[1:]:
                await interaction.followup.send(content=content, ephemeral=True)
        except discord.NotFound:
            LOGGER.warning("Interaction expired before results could be sent")
    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept-Encoding": "gzip, deflate, br"},
                auto_decompress=False,
            )
        return self._session

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

    @staticmethod
    def _parse_timestamp(value: str) -> Optional[datetime]:
        try:
            parsed = datetime.strptime(value, ISOFORMAT)
        except (TypeError, ValueError):
            return None
        return parsed.replace(tzinfo=timezone.utc)

    async def _cached_guild_labels(self, guild_ids: Iterable[str]) -> Dict[str, str]:
        normalized = [
            normalise_guild_id(guild_id)
            for guild_id in guild_ids
            if isinstance(guild_id, str) and normalise_guild_id(guild_id)
        ]
        if not normalized:
            return {}

        cached = self.bot.storage.get_guild_details(normalized)
        labels: Dict[str, str] = {}
        stale: List[str] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        for guild_id in normalized:
            detail = cached.get(guild_id)
            if not detail:
                stale.append(guild_id)
                continue
            label, updated_at = detail
            labels[guild_id] = label
            updated = self._parse_timestamp(updated_at)
            if not updated or updated < cutoff:
                stale.append(guild_id)

        if stale:
            try:
                refreshed = await self._fetch_guild_details(stale)
                labels.update(refreshed)
            except ValueError:
                LOGGER.warning("Guild lookup failed while warming cache", exc_info=True)

        return labels

    async def _fetch_account_summary(
        self, api_key: str
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        payload = await self._fetch_json(
            "https://api.guildwars2.com/v2/account", api_key=api_key
        )
        account_name = payload.get("name")
        account_name_value = account_name if isinstance(account_name, str) else None
        team_id = payload.get("wvw_team_id")
        team_value = team_id if isinstance(team_id, int) else None
        world_id = payload.get("world")
        world_value = world_id if isinstance(world_id, int) else None
        wvw_guild_id = payload.get("wvw_guild_id")
        wvw_guild_value = wvw_guild_id if isinstance(wvw_guild_id, str) else None
        return account_name_value, team_value or world_value, wvw_guild_value

    async def _build_gw2_account_embed(
        self,
        *,
        title: str,
        record: "ApiKeyRecord",
        account_label: Optional[str] = None,
    ) -> discord.Embed:
        account_name = account_label or record.account_name or "Unknown account"
        wvw_team_label = "Unassigned"
        wvw_guild_label = "None"

        try:
            fetched_name, world_id, wvw_guild_id = await self._fetch_account_summary(
                record.key
            )
            if fetched_name:
                account_name = fetched_name
            if world_id:
                wvw_team_label = WVW_SERVER_NAMES.get(world_id, "Unknown")
            if wvw_guild_id:
                wvw_guild_detail = await self._cached_guild_labels([wvw_guild_id])
                wvw_guild_label = wvw_guild_detail.get(wvw_guild_id, wvw_guild_id)
        except ValueError:
            LOGGER.warning(
                "Failed to fetch account details for select lookup.",
                exc_info=True,
            )

        guild_details = await self._cached_guild_labels(record.guild_ids)
        guild_labels = [
            guild_details.get(guild_id, guild_id) for guild_id in record.guild_ids
        ]
        guild_value = (
            "\n".join(f"- {label}" for label in guild_labels) if guild_labels else "None"
        )

        embed = self._embed(title=title)
        embed.add_field(
            name="Username",
            value=self._trim_field(f"```\n{account_name}\n```"),
            inline=False,
        )
        embed.add_field(
            name="WvW Team",
            value=self._trim_field(f"```\n{wvw_team_label}\n```"),
            inline=True,
        )
        embed.add_field(
            name="WvW Guild",
            value=self._trim_field(f"```\n{wvw_guild_label}\n```"),
            inline=True,
        )
        embed.add_field(
            name="Guilds",
            value=self._trim_field(f"```\n{guild_value}\n```"),
            inline=False,
        )
        return embed

    def _friendly_guild_label(
        self, guild_id: str, guild_details: Mapping[str, str]
    ) -> str:
        label = guild_details.get(guild_id)
        if label:
            return label
        if not guild_id:
            return "Unknown guild"
        shortened = (
            f"{guild_id[:4]}…{guild_id[-4:]}" if len(guild_id) > 8 else guild_id
        )
        return f"Guild {shortened}"

    def _role_labels(
        self, role_strings: Sequence[str], guild: discord.Guild
    ) -> List[str]:
        labels: List[str] = []
        for value in role_strings:
            role_obj: Optional[discord.Role] = None
            if value.isdigit():
                role_obj = guild.get_role(int(value))
            elif value.startswith("<@&") and value.endswith(">"):
                try:
                    role_obj = guild.get_role(int(value.strip("<@&>")))
                except ValueError:
                    role_obj = None
            if role_obj:
                labels.append(role_obj.name)
            else:
                labels.append(value)

        return sorted({label.casefold(): label for label in labels}.values())

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
        # Keep the original casing of the first occurrence while enforcing
        # case-insensitive uniqueness.
        return sorted({value.casefold(): value for value in names}.values())

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------
    def _prepare_filter_set(
        self,
        *,
        guilds: Sequence[Optional[str]],
        roles: Sequence[Optional[discord.Role]],
        accounts: Sequence[Optional[str]],
        characters: Sequence[Optional[str]],
        discord_members: Sequence[Optional[discord.Member]],
    ) -> _FilterSet:
        guild_values: List[str] = []
        for value in guilds:
            if isinstance(value, str):
                value = value.strip() or None
            if not value:
                continue
            normalized = normalise_guild_id(value)
            if normalized and normalized not in guild_values:
                guild_values.append(normalized)

        role_values: List[discord.Role] = []
        for role in roles:
            if role and role not in role_values:
                role_values.append(role)

        account_values: List[str] = []
        for value in accounts:
            if isinstance(value, str):
                value = value.strip()
            if value and value not in account_values:
                account_values.append(value)

        character_keys: List[str] = []
        character_labels: List[str] = []
        for value in characters:
            if isinstance(value, str):
                value = value.strip()
            if not value:
                continue
            key = self._character_key(value)
            if key and key not in character_keys:
                character_keys.append(key)
                character_labels.append(value)

        discord_values: List[discord.Member] = []
        for member in discord_members:
            if member and member not in discord_values:
                discord_values.append(member)

        filters: List[Tuple[str, str]] = []
        for gid in guild_values:
            filters.append(("guild", gid))
        for role in role_values:
            filters.append(("role", str(role.id)))
        for account in account_values:
            filters.append(("account", account))
        for key in character_keys:
            filters.append(("character", key))
        for member in discord_values:
            filters.append(("discord", str(member.id)))

        return _FilterSet(
            guilds=guild_values,
            roles=role_values,
            accounts=account_values,
            character_keys=character_keys,
            character_labels=character_labels,
            discord_members=discord_values,
            filters=filters,
        )

    def _match_member_filters(
        self,
        filters: Sequence[Tuple[str, str]],
        member: discord.Member,
        account_names: Sequence[str],
        characters: Sequence[str],
        character_keys: Sequence[str],
        guild_labels: Dict[str, str],
    ) -> Tuple[bool, List[str], List[str]]:
        matched_guilds: List[str] = []
        matched_roles: List[str] = []

        for filter_type, raw_value in filters:
            needle = raw_value.casefold()
            if filter_type == "guild":
                normalized_needle = normalise_guild_id(raw_value)
                label_matches = [
                    label
                    for label in guild_labels.values()
                    if needle in label.casefold() or needle in label.lower()
                ]
                id_matches = [
                    gid
                    for gid in guild_labels
                    if needle in gid.lower()
                    or normalise_guild_id(gid) == normalized_needle
                ]
                if not label_matches and not id_matches:
                    return False, matched_guilds, matched_roles
                matched_guilds.extend(
                    label_matches or [guild_labels.get(id_matches[0], id_matches[0])]
                )
            elif filter_type == "role":
                role_id = None
                if raw_value.isdigit():
                    role_id = int(raw_value)
                elif raw_value.startswith("<@&") and raw_value.endswith(">"):
                    try:
                        role_id = int(raw_value.strip("<@&>"))
                    except ValueError:
                        role_id = None
                roles = [role for role in member.roles if not role.is_default()]
                role_matches = [
                    role
                    for role in roles
                    if (role_id and role.id == role_id)
                    or needle in role.name.casefold()
                ]
                if not role_matches:
                    return False, matched_guilds, matched_roles
                matched_roles.extend(role.mention for role in role_matches)
            elif filter_type == "account":
                if not any(needle in name.casefold() for name in account_names):
                    return False, matched_guilds, matched_roles
            elif filter_type == "character":
                if not any(needle == key for key in character_keys):
                    return False, matched_guilds, matched_roles
            elif filter_type == "discord":
                display = f"{member.display_name} ({member.name})".casefold()
                if needle not in display and needle not in str(member.id):
                    return False, matched_guilds, matched_roles

        return True, sorted(set(matched_guilds)), sorted(set(matched_roles))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def _guild_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        config = self.bot.get_config(interaction.guild.id)
        configured_ids = [
            normalise_guild_id(gid)
            for gid in config.guild_role_ids.keys()
            if normalise_guild_id(gid)
        ]

        choices: List[app_commands.Choice[str]] = []
        seen: set[str] = set()
        current_lower = current.lower()

        async def add_choices(guild_ids: Iterable[str]) -> None:
            nonlocal choices
            try:
                details = await self._cached_guild_labels(list(guild_ids))
            except ValueError:
                LOGGER.warning("Guild lookup failed during autocomplete", exc_info=True)
                details = {}

            for guild_id in guild_ids:
                if guild_id in seen or not guild_id:
                    continue
                label = details.get(guild_id, guild_id)
                display = f"{label} ({guild_id})"
                if current_lower and current_lower not in display.lower():
                    continue
                seen.add(guild_id)
                choices.append(app_commands.Choice(name=display[:100], value=guild_id))
                if len(choices) >= 25:
                    break

        # Prefer configured guild IDs first.
        await add_choices(configured_ids)

        # Fallback to GW2 API search for unmatched input so admins can target
        # guilds that are not yet configured via /guildroles.
        if len(choices) < 25 and current.strip():
            try:
                search_results = await self._fetch_json(
                    "https://api.guildwars2.com/v2/guild/search",
                    params={"name": current.strip()},
                )
                if isinstance(search_results, list):
                    api_ids = [
                        normalise_guild_id(gid)
                        for gid in search_results
                        if isinstance(gid, str) and normalise_guild_id(gid)
                    ]
                    await add_choices(api_ids)
            except ValueError:
                LOGGER.warning("Guild search failed during autocomplete", exc_info=True)

        return choices[:25]

    async def _account_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        seen = []
        for _, _, record in results:
            if record.account_name and record.account_name not in seen:
                if current.lower() in record.account_name.lower():
                    seen.append(record.account_name)
        return [
            app_commands.Choice(name=value[:100], value=value) for value in seen[:25]
        ]

    async def _character_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        current_lower = current.casefold()
        character_map: dict[str, str] = {}

        async def add_characters_from_record(record: "ApiKeyRecord") -> None:
            for name in record.characters:
                key = self._character_key(name)
                if not key or key in character_map:
                    continue
                if current_lower and current_lower not in key:
                    continue
                character_map[key] = name

        # Prefer stored character lists, but if none are present we will
        # opportunistically backfill a few records to populate the autocomplete.
        for _, _, record in results:
            await add_characters_from_record(record)

        if not character_map:
            fetched = 0
            for guild_id, user_id, record in results:
                if fetched >= 3:
                    break
                if record.characters:
                    continue
                try:
                    characters = await self._fetch_character_names(record.key)
                except ValueError:
                    continue
                record.characters = characters
                self.bot.storage.upsert_api_key(guild_id, user_id, record)
                fetched += 1
                await add_characters_from_record(record)

        def sort_key(name: str) -> tuple[int, int, str]:
            lower = name.casefold()
            if current_lower and lower.startswith(current_lower):
                return (0, len(lower), lower)
            position = lower.find(current_lower) if current_lower else -1
            return (1, position if position >= 0 else 9999, lower)

        choices = [
            app_commands.Choice(name=value[:100], value=value)
            for value in sorted(character_map.values(), key=sort_key)[:25]
        ]
        return choices

    async def _run_query(
        self,
        interaction: discord.Interaction,
        *,
        filter_sets: Sequence[_FilterSet],
        group_by: Optional[str],
        as_csv: bool,
        count_only: bool,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_message(
                interaction,
                content="Select: This command can only be used in a server.",
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        normalized_guild_map = {
            normalise_guild_id(gid): role_id
            for gid, role_id in config.guild_role_ids.items()
            if normalise_guild_id(gid)
        }
        allowed_guild_ids = set(normalized_guild_map.keys())

        # Drop completely empty filter sets when at least one set has filters so
        # that a blank set does not match everyone and short-circuit OR queries
        # that rely on the populated sets.
        populated_filter_sets = [fs for fs in filter_sets if fs.filters]
        if populated_filter_sets:
            filter_sets = populated_filter_sets

        show_characters = any(fs.character_provided for fs in filter_sets) and not count_only
        if group_by:
            group_by = group_by.lower()
        allowed_groups = {"guild", "role", "account", "discord"}
        if group_by and group_by not in allowed_groups:
            await self._send_message(
                interaction,
                content="Select: Unsupported group. Choose guild, role, account, or discord.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        if not results:
            await self._send_message(
                interaction,
                content="Select: No stored API keys were found for this server.",
                use_followup=True,
            )
            return

        for guild_id, user_id, record in results:
            if record.characters:
                continue
            try:
                characters = await self._fetch_character_names(record.key)
            except ValueError:
                continue
            record.characters = characters
            self.bot.storage.upsert_api_key(guild_id, user_id, record)

        bundles: Dict[int, Dict[str, object]] = {}
        for _, user_id, record in results:
            member = interaction.guild.get_member(user_id)
            if not member:
                continue

            bundle = bundles.setdefault(
                user_id,
                {
                    "member": member,
                    "account_names": [],
                    "characters": [],
                    "character_entries": [],
                    "character_keys": set(),
                    "character_map": {},
                    "guild_ids": set(),
                },
            )
            if record.account_name and record.account_name not in bundle["account_names"]:
                bundle["account_names"].append(record.account_name)
            for character in record.characters:
                key = self._character_key(character)
                if not key:
                    continue
                if character not in bundle["characters"]:
                    bundle["characters"].append(character)
                if key not in bundle["character_keys"]:
                    bundle["character_keys"].add(key)
                entry = (character, record.account_name)
                if entry not in bundle["character_entries"]:
                    bundle["character_entries"].append(entry)
                character_bucket = bundle["character_map"].setdefault(key, [])
                if entry not in character_bucket:
                    character_bucket.append(entry)
            for gid in record.guild_ids:
                normalized_gid = normalise_guild_id(gid) if gid else ""
                if normalized_gid:
                    bundle["guild_ids"].add(normalized_gid)

        for fset in filter_sets:
            if not fset.character_keys:
                continue
            canonical_labels: List[str] = []
            for key, label in zip(fset.character_keys, fset.character_labels):
                canonical_character = None
                for bundle in bundles.values():
                    entries = bundle.get("character_map", {}).get(key, [])
                    if entries:
                        canonical_character = entries[0][0]
                        break
                if not canonical_character:
                    await self._send_message(
                        interaction,
                        content=(
                            "Select: No stored characters matched that name. Try selecting a name "
                            "from the autocomplete list or refresh your API keys with `/apikey update`."
                        ),
                        use_followup=True,
                    )
                    return
                canonical_labels.append(canonical_character)
            if canonical_labels:
                fset.character_labels = canonical_labels

        if not bundles:
            await self._send_message(
                interaction,
                content="Select: No stored API keys were found for this server.",
                use_followup=True,
            )
            return

        guild_ids_for_lookup = allowed_guild_ids or {
            guild_id for bundle in bundles.values() for guild_id in bundle["guild_ids"]
        }
        for fset in filter_sets:
            for gid in fset.guilds:
                if gid:
                    guild_ids_for_lookup.add(gid)
        guild_details = await self._cached_guild_labels(guild_ids_for_lookup)

        matched: List[
            Tuple[
                discord.Member,
                List[str],
                List[str],
                List[Tuple[str, Optional[str]]],
                List[str],
                List[str],
                List[str],
                List[str],
                _FilterSet,
            ]
        ] = []
        for bundle in bundles.values():
            member = bundle["member"]
            guild_labels = {
                gid: self._friendly_guild_label(gid, guild_details)
                for gid in bundle["guild_ids"]
                if gid
            }
            mapped_role_mentions: List[str] = []
            for gid in bundle["guild_ids"]:
                role_id = normalized_guild_map.get(gid)
                if role_id:
                    role_obj = interaction.guild.get_role(role_id)
                    if role_obj:
                        mapped_role_mentions.append(role_obj.mention)
            mapped_role_mentions = sorted(set(mapped_role_mentions))

            for fset in filter_sets:
                ok, matched_guilds, matched_roles = self._match_member_filters(
                    fset.filters,
                    member,
                    bundle["account_names"],
                    bundle["characters"],
                    bundle["character_keys"],
                    guild_labels,
                )
                if not ok:
                    continue

                if fset.character_provided:
                    entries: List[Tuple[str, Optional[str]]] = []
                    for key in fset.character_keys:
                        entries.extend(bundle.get("character_map", {}).get(key, []))
                    if not entries:
                        continue
                    deduped_entries: List[Tuple[str, Optional[str]]] = []
                    seen_entries: set[Tuple[str, Optional[str]]] = set()
                    for entry in entries:
                        if entry not in seen_entries:
                            seen_entries.add(entry)
                            deduped_entries.append(entry)
                    matched_character_entries = deduped_entries
                else:
                    matched_character_entries = list(bundle.get("character_entries", []))

                matched.append(
                    (
                        member,
                        bundle["account_names"],
                        bundle["characters"],
                        matched_character_entries,
                        matched_guilds or list(guild_labels.values()),
                        matched_roles,
                        mapped_role_mentions,
                        sorted(bundle["guild_ids"]),
                        fset,
                    )
                )
                break

        if not matched:
            await self._send_message(
                interaction,
                content="Select: No members matched the provided filters.",
                use_followup=True,
            )
            return

        grouped: Dict[
            str,
            List[
                Tuple[
                    discord.Member,
                    List[str],
                    List[str],
                    List[Tuple[str, Optional[str]]],
                    List[str],
                    List[str],
                    List[str],
                    List[str],
                    _FilterSet,
                ]
            ],
        ] = {}
        for (
            member,
            account_names,
            characters,
            character_entries,
            matched_guilds,
            matched_roles,
            mapped_role_mentions,
            guild_ids,
            filter_set,
        ) in matched:
            if group_by == "guild":
                keys = matched_guilds or ["No guilds"]
            elif group_by == "role":
                role_names = [
                    role.name for role in member.roles if not role.is_default()
                ]
                keys = role_names or ["No roles"]
            elif group_by == "account":
                keys = [
                    ", ".join(account_names)
                    if account_names
                    else "Unknown account"
                ]
            elif group_by == "discord":
                keys = [member.display_name]
            else:
                keys = ["Matches"]

            for key in keys:
                grouped.setdefault(key, []).append(
                    (
                        member,
                        account_names,
                        characters,
                        character_entries,
                        matched_guilds,
                        matched_roles,
                        mapped_role_mentions,
                        guild_ids,
                        filter_set,
                    )
                )

        filters_label: List[str] = []
        for idx, fset in enumerate(filter_sets, start=1):
            parts: List[str] = []
            if fset.guilds:
                parts.append(f"Guild: {', '.join(fset.guilds)}")
            else:
                parts.append("Guild: All mapped")
            if fset.roles:
                parts.append("Role: " + ", ".join(role.name for role in fset.roles))
            if fset.accounts:
                parts.append("Account: " + ", ".join(fset.accounts))
            if fset.character_provided:
                parts.append("Character: " + ", ".join(fset.character_labels))
            if fset.discord_members:
                parts.append(
                    "Discord: "
                    + ", ".join(member.display_name for member in fset.discord_members)
                )
            if not any(
                [
                    fset.guilds,
                    fset.roles,
                    fset.accounts,
                    fset.character_provided,
                    fset.discord_members,
                ]
            ):
                parts = ["None (all)"]

            prefix = "Filters" if len(filter_sets) == 1 else f"Filters set {idx}"
            filters_label.append(f"{prefix}: {', '.join(parts)}")

        summary_lines = ["Select results", "Filters:", *filters_label]
        summary_lines.extend(
            [
                f"Group by: {group_by.capitalize() if group_by else 'None'}",
                f"Matches: {len(matched)}",
            ]
        )
        summary_content = "\n".join(summary_lines)

        files: List[discord.File] = []
        if as_csv:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "Discord ID",
                    "Discord Name",
                    "Account Name",
                    "Guild IDs",
                    "Guild Names",
                    "Roles",
                    "Characters",
                ]
            )

            for (
                member,
                account_names,
                characters,
                character_entries,
                _matched_guilds,
                _,
                _,
                guild_ids,
                filter_set,
            ) in matched:
                guild_labels = [guild_details.get(gid, gid) for gid in guild_ids]
                roles = [role.name for role in member.roles if not role.is_default()]
                characters_for_csv = (
                    [name for name, _ in character_entries]
                    if filter_set.character_provided
                    else characters
                )
                writer.writerow(
                    [
                        member.id,
                        f"{member.display_name} ({member.name})",
                        "; ".join(account_names),
                        "; ".join(guild_ids),
                        "; ".join(guild_labels or ["No guilds"]),
                        "; ".join(roles),
                        "; ".join(characters_for_csv),
                    ]
                )

            buffer.seek(0)
            files = [
                discord.File(
                    fp=io.BytesIO(buffer.getvalue().encode("utf-8")),
                    filename="select_query.csv",
                )
            ]

        match_blocks: List[str] = []

        if not count_only:
            headers = ["Discord", "Account", "Roles", "Guilds"]
            if show_characters:
                headers.append("Characters")

            for group, entries in sorted(
                grouped.items(), key=lambda item: (-len(item[1]), item[0].casefold())
            ):
                group_rows: List[List[str]] = []
                for (
                    member,
                    account_names,
                    _characters,
                    character_entries,
                    matched_guilds,
                    matched_roles,
                    mapped_role_mentions,
                    guild_ids,
                    filter_set,
                ) in entries:
                    all_guild_labels = [
                        self._friendly_guild_label(gid, guild_details)
                        for gid in guild_ids
                        if gid
                    ]
                    guilds_label = sorted(
                        {
                            label.casefold(): label
                            for label in matched_guilds + all_guild_labels
                            if label
                        }.values()
                    )
                    role_labels = [
                        role.name for role in member.roles if not role.is_default()
                    ]
                    characters_label = "; ".join(
                        [
                            f"{name} ({acct})" if acct else name
                            for name, acct in character_entries
                        ]
                    )

                    row = [
                        f"{member.display_name} ({member.name})",
                        ", ".join(account_names) if account_names else "Unknown",
                        ", ".join(role_labels) if role_labels else "No roles",
                        ", ".join(guilds_label) if guilds_label else "No guilds",
                    ]
                    if show_characters:
                        row.append(characters_label or "None")
                    group_rows.append(row)

                table_title = group if group_by else "Matches"
                match_blocks.append(table_title)
                match_blocks.append(
                    self._format_table(
                        headers=headers,
                        rows=group_rows,
                        code_block=False,
                    )
                )

        output_buffer = io.StringIO()
        output_buffer.write(summary_content)
        output_buffer.write("\n\n")
        if match_blocks:
            output_buffer.write("\n\n".join(match_blocks))
            output_buffer.write("\n")
        output_buffer.seek(0)

        output_files = [
            discord.File(fp=output_buffer, filename="select_results.txt"),
            *files,
        ]

        await self._safe_followup_messages(
            interaction,
            contents=["Select results attached."],
            files=output_files,
        )

    @select.command(
        name="member",
        description="Show a detailed lookup for a Discord member.",
    )
    @app_commands.describe(member="Discord member to look up")
    async def member_lookup(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_message(
                interaction,
                content="Select: This command can only be used in a server.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        records = self.bot.storage.get_user_api_keys(interaction.guild.id, member.id)
        if not records:
            await self._send_message(
                interaction,
                content="Select: No stored API keys were found for that member.",
                use_followup=True,
            )
            return

        embeds: List[discord.Embed] = []
        for record in records:
            embed = await self._build_gw2_account_embed(
                title=f"{member.display_name} | {member.name}",
                record=record,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embeds.append(embed)

        for idx in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[idx : idx + 10], ephemeral=True)

    @select.command(
        name="gw2_member",
        description="Look up a Guild Wars 2 account by account name.",
    )
    @app_commands.describe(account_name="Guild Wars 2 account name (ex: Name.1234)")
    async def gw2_member_lookup(
        self, interaction: discord.Interaction, account_name: str
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_message(
                interaction,
                content="Select: This command can only be used in a server.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        query = account_name.strip().casefold()
        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        exact_match: Optional[ApiKeyRecord] = None
        partial_match: Optional[ApiKeyRecord] = None
        for _, _, record in results:
            if not record.account_name:
                continue
            candidate = record.account_name.casefold()
            if candidate == query:
                exact_match = record
                break
            if query and query in candidate and partial_match is None:
                partial_match = record

        record = exact_match or partial_match
        if not record:
            await self._send_message(
                interaction,
                content="Select: No stored API keys matched that account name.",
                use_followup=True,
            )
            return

        title = f"{record.account_name or account_name} | GW2 account"
        embed = await self._build_gw2_account_embed(
            title=title,
            record=record,
            account_label=record.account_name or account_name,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @select.command(
        name="gw2_character",
        description="Look up a Guild Wars 2 account by character name.",
    )
    @app_commands.describe(character_name="Guild Wars 2 character name")
    async def gw2_character_lookup(
        self, interaction: discord.Interaction, character_name: str
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_message(
                interaction,
                content="Select: This command can only be used in a server.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        query = character_name.strip()
        query_key = query.casefold()
        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        matched_record: Optional[ApiKeyRecord] = None
        matched_character = query

        for guild_id, user_id, record in results:
            character_names = record.characters or []
            for name in character_names:
                if name.casefold() == query_key:
                    matched_record = record
                    matched_character = name
                    break
            if matched_record:
                break

            if record.characters:
                continue

            try:
                characters = await self._fetch_character_names(record.key)
            except ValueError:
                continue
            record.characters = characters
            self.bot.storage.upsert_api_key(guild_id, user_id, record)
            for name in characters:
                if name.casefold() == query_key:
                    matched_record = record
                    matched_character = name
                    break
            if matched_record:
                break

        if not matched_record:
            await self._send_message(
                interaction,
                content="Select: No stored API keys matched that character name.",
                use_followup=True,
            )
            return

        title = f"{matched_character} | {matched_record.account_name or 'GW2 account'}"
        embed = await self._build_gw2_account_embed(
            title=title,
            record=matched_record,
            account_label=matched_record.account_name or "Unknown account",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @select.command(
        name="query",
        description=(
            "Admin member search by GW2 guild, Discord role, account, character, or Discord name."
        ),
    )
    @app_commands.describe(
        guild="Match a GW2 guild name/tag/ID (autocomplete)",
        role="Match members with this Discord role",
        account="Match a stored GW2 account name",
        character="Match a stored GW2 character name",
        discord_member="Match a specific Discord member",
        group_by="Group results by this field",
        as_csv="Export the results to a CSV attachment",
        count_only="Return only counts instead of detailed entries",
    )
    @app_commands.autocomplete(
        guild=_guild_autocomplete,
        account=_account_autocomplete,
        character=_character_autocomplete,
    )
    @app_commands.choices(
        group_by=[
            app_commands.Choice(name="Guild", value="guild"),
            app_commands.Choice(name="Role", value="role"),
            app_commands.Choice(name="Account", value="account"),
            app_commands.Choice(name="Discord", value="discord"),
        ]
    )
    async def member_query(
        self,
        interaction: discord.Interaction,
        guild: Optional[str] = None,
        role: Optional[discord.Role] = None,
        account: Optional[str] = None,
        character: Optional[str] = None,
        discord_member: Optional[discord.Member] = None,
        group_by: Optional[str] = None,
        as_csv: bool = False,
        count_only: bool = False,
    ) -> None:
        filter_set = self._prepare_filter_set(
            guilds=[guild],
            roles=[role],
            accounts=[account],
            characters=[character],
            discord_members=[discord_member],
        )
        await self._run_query(
            interaction,
            filter_sets=[filter_set],
            group_by=group_by,
            as_csv=as_csv,
            count_only=count_only,
        )

    @select.command(
        name="and",
        description=(
            "Match members where two values of the same filter type must all be true."
        ),
    )
    @app_commands.describe(
        guild_one="First GW2 guild to match",
        guild_two="Second GW2 guild to match",
        role_one="First Discord role to match",
        role_two="Second Discord role to match",
        account_one="First GW2 account name to match",
        account_two="Second GW2 account name to match",
        character_one="First GW2 character name to match",
        character_two="Second GW2 character name to match",
        discord_member_one="First Discord member to match",
        discord_member_two="Second Discord member to match",
        group_by="Group results by this field",
        as_csv="Export the results to a CSV attachment",
        count_only="Return only counts instead of detailed entries",
    )
    @app_commands.autocomplete(
        guild_one=_guild_autocomplete,
        guild_two=_guild_autocomplete,
        account_one=_account_autocomplete,
        account_two=_account_autocomplete,
        character_one=_character_autocomplete,
        character_two=_character_autocomplete,
    )
    @app_commands.choices(
        group_by=[
            app_commands.Choice(name="Guild", value="guild"),
            app_commands.Choice(name="Role", value="role"),
            app_commands.Choice(name="Account", value="account"),
            app_commands.Choice(name="Discord", value="discord"),
        ]
    )
    async def member_query_and(
        self,
        interaction: discord.Interaction,
        guild_one: Optional[str] = None,
        guild_two: Optional[str] = None,
        role_one: Optional[discord.Role] = None,
        role_two: Optional[discord.Role] = None,
        account_one: Optional[str] = None,
        account_two: Optional[str] = None,
        character_one: Optional[str] = None,
        character_two: Optional[str] = None,
        discord_member_one: Optional[discord.Member] = None,
        discord_member_two: Optional[discord.Member] = None,
        group_by: Optional[str] = None,
        as_csv: bool = False,
        count_only: bool = False,
    ) -> None:
        filter_set = self._prepare_filter_set(
            guilds=[guild_one, guild_two],
            roles=[role_one, role_two],
            accounts=[account_one, account_two],
            characters=[character_one, character_two],
            discord_members=[discord_member_one, discord_member_two],
        )
        await self._run_query(
            interaction,
            filter_sets=[filter_set],
            group_by=group_by,
            as_csv=as_csv,
            count_only=count_only,
        )

    @select.command(
        name="or",
        description=(
            "Match members where either set of provided filters can succeed."
        ),
    )
    @app_commands.describe(
        guild_one="First GW2 guild to match",
        guild_two="Second GW2 guild to match",
        role_one="First Discord role to match",
        role_two="Second Discord role to match",
        account_one="First GW2 account name to match",
        account_two="Second GW2 account name to match",
        character_one="First GW2 character name to match",
        character_two="Second GW2 character name to match",
        discord_member_one="First Discord member to match",
        discord_member_two="Second Discord member to match",
        group_by="Group results by this field",
        as_csv="Export the results to a CSV attachment",
        count_only="Return only counts instead of detailed entries",
    )
    @app_commands.autocomplete(
        guild_one=_guild_autocomplete,
        guild_two=_guild_autocomplete,
        account_one=_account_autocomplete,
        account_two=_account_autocomplete,
        character_one=_character_autocomplete,
        character_two=_character_autocomplete,
    )
    @app_commands.choices(
        group_by=[
            app_commands.Choice(name="Guild", value="guild"),
            app_commands.Choice(name="Role", value="role"),
            app_commands.Choice(name="Account", value="account"),
            app_commands.Choice(name="Discord", value="discord"),
        ]
    )
    async def member_query_or(
        self,
        interaction: discord.Interaction,
        guild_one: Optional[str] = None,
        guild_two: Optional[str] = None,
        role_one: Optional[discord.Role] = None,
        role_two: Optional[discord.Role] = None,
        account_one: Optional[str] = None,
        account_two: Optional[str] = None,
        character_one: Optional[str] = None,
        character_two: Optional[str] = None,
        discord_member_one: Optional[discord.Member] = None,
        discord_member_two: Optional[discord.Member] = None,
        group_by: Optional[str] = None,
        as_csv: bool = False,
        count_only: bool = False,
    ) -> None:
        filter_set_one = self._prepare_filter_set(
            guilds=[guild_one],
            roles=[role_one],
            accounts=[account_one],
            characters=[character_one],
            discord_members=[discord_member_one],
        )
        filter_set_two = self._prepare_filter_set(
            guilds=[guild_two],
            roles=[role_two],
            accounts=[account_two],
            characters=[character_two],
            discord_members=[discord_member_two],
        )
        await self._run_query(
            interaction,
            filter_sets=[filter_set_one, filter_set_two],
            group_by=group_by,
            as_csv=as_csv,
            count_only=count_only,
        )

    @select.command(
        name="help",
        description="Explain the select filters and grouping options.",
    )
    async def member_query_help(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        embed = self._embed(
            title="Select help",
            description=(
                "Use `/select query` for a single set of optional filters, `/select and` "
                "to require two values of the same type, or `/select or` to try "
                "alternate filters. All filters are optional. Use `/select member`, "
                "`/select gw2_member`, or `/select gw2_character` to see detailed embeds."
            ),
        )

        embed.add_field(
            name="Common examples",
            value="\n".join(
                [
                    "Use `/select and` with two **Guild** values to require members in both guilds.",
                    "Choose a **Role** to find everyone holding that Discord role.",
                    "Combine **Account** and **Character** filters to narrow to specific players.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="Filters",
            value=self._format_list(
                [
                    "**Guild** — autocomplete mapped GW2 guilds configured via `/guildroles`.",
                    "**Role** — pick any non-@everyone Discord role to match holders.",
                    "**Account** — autocomplete stored GW2 account names.",
                    "**Character** — autocomplete stored character names.",
                    "**Discord member** — select a specific member by name/mention.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="Grouping",
            value=self._format_list(
                [
                    "Choose Guild, Role, Account, or Discord to group matches.",
                    "Leave blank to show everything under a single section.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="CSV export",
            value=self._format_list(
                [
                    "Enable **As CSV** to attach a CSV of all matches (Discord IDs, roles, guilds, characters).",
                    "Enable **Count only** to return totals without member details.",
                ]
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    for stale_cog in ("MemberQueryCog", "SelectCog"):
        if bot.get_cog(stale_cog):
            LOGGER.info("Removing stale %s during cog load", stale_cog)
            bot.remove_cog(stale_cog)

    for legacy in ("memberquery", "select"):
        existing = bot.tree.get_command(legacy)
        if existing:
            LOGGER.info("Replacing existing %s command during cog load", legacy)
            bot.tree.remove_command(legacy, type=discord.AppCommandType.chat_input)

    cog = SelectCog(bot)
    await bot.add_cog(cog, override=True)
    # Explicitly (re)attach the group to the command tree so it registers even if
    # stale state lingered from prior runs.
    bot.tree.add_command(cog.select, override=True)

    # Force an immediate resync when the bot is already connected so the renamed
    # command propagates without requiring a restart.
    if bot.user:
        try:
            await bot.tree.sync()
            for guild in bot.guilds:
                await bot.tree.sync(guild=guild)
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to sync select commands during cog load")
