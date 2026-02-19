from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..http_utils import read_response_text
from ..storage import normalise_guild_id

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


@dataclass(frozen=True)
class _BlanketCondition:
    field: str
    operator: str
    value: str


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
    BLANKET_DEFAULT_FIELDS: Tuple[str, ...] = (
        "user",
        "api_keys",
        "account_name",
        "character_name",
    )
    BLANKET_FIELD_ALIASES: Dict[str, str] = {
        "user": "user",
        "users": "user",
        "discord_user": "user",
        "discord_name": "discord_name",
        "user_id": "user_id",
        "api_keys": "api_keys",
        "api_key": "api_keys",
        "key_name": "api_keys",
        "account": "account_name",
        "account_name": "account_name",
        "character": "character_name",
        "character_name": "character_name",
        "guild": "guild_id",
        "guild_id": "guild_id",
        "permission": "permission",
        "permissions": "permission",
    }
    BLANKET_FIELD_LABELS: Dict[str, str] = {
        "user": "User",
        "discord_name": "Discord Name",
        "user_id": "User ID",
        "api_keys": "API Key Name",
        "account_name": "Account Name",
        "character_name": "Character Names",
        "guild_id": "Guild IDs",
        "permission": "Permissions",
    }
    AI_FORBIDDEN_KEYWORDS: Tuple[str, ...] = (
        "update",
        "delete",
        "insert",
        "drop",
        "alter",
        "create",
        "truncate",
        "grant",
        "revoke",
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
    def _truncate_text(value: str, *, limit: int = 260) -> str:
        cleaned = value.strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    def _build_ai_status_embed(
        self,
        *,
        stage: str,
        detail: str,
        prompt: Optional[str] = None,
        query: Optional[str] = None,
        state: str = "running",
    ) -> discord.Embed:
        colour = BRAND_COLOUR
        if state == "done":
            colour = discord.Colour.green()
        elif state == "error":
            colour = discord.Colour.red()

        description_lines = [
            f"**Stage**: {stage}",
            f"**Detail**: {detail}",
        ]
        if prompt:
            description_lines.append(
                f"**Prompt**: `{self._truncate_text(prompt, limit=180)}`"
            )
        if query:
            description_lines.append(
                f"**Query**: `{self._truncate_text(query, limit=180)}`"
            )
        return self._embed(
            title="Select AI Status",
            description="\n".join(description_lines),
            colour=colour,
        )

    async def _edit_ai_status_embed(
        self,
        interaction: discord.Interaction,
        *,
        stage: str,
        detail: str,
        prompt: Optional[str] = None,
        query: Optional[str] = None,
        state: str = "running",
    ) -> None:
        embed = self._build_ai_status_embed(
            stage=stage,
            detail=detail,
            prompt=prompt,
            query=query,
            state=state,
        )
        try:
            await interaction.edit_original_response(embed=embed)
        except discord.NotFound:
            LOGGER.warning("Interaction expired before status update could be sent")

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

    async def _cached_guild_labels(self, guild_ids: Iterable[str]) -> Dict[str, str]:
        guild_list = list(guild_ids)
        labels = self.bot.storage.get_guild_labels(guild_list)
        missing = [gid for gid in guild_list if gid and gid not in labels]
        if missing:
            try:
                await self._fetch_guild_details(missing)
            except ValueError:
                LOGGER.warning("Guild lookup failed while warming cache", exc_info=True)
            labels.update(self.bot.storage.get_guild_labels(guild_list))
        return labels

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

    @classmethod
    def _normalise_blanket_field(cls, raw: str) -> Optional[str]:
        value = raw.strip().casefold()
        if not value:
            return None
        return cls.BLANKET_FIELD_ALIASES.get(value)

    @staticmethod
    def _strip_quotes(value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            return cleaned[1:-1]
        return cleaned

    @classmethod
    def _parse_blanket_query(
        cls, query: str
    ) -> tuple[List[str], List[_BlanketCondition]]:
        cleaned = query.strip()
        match = re.match(r"^\s*select\s+(.*)$", cleaned, flags=re.IGNORECASE)
        if not match:
            raise ValueError("Query must start with SELECT.")

        remainder = match.group(1).strip()
        where_clause = ""
        where_match = re.search(r"\s+where\s+", remainder, flags=re.IGNORECASE)
        if where_match:
            where_clause = remainder[where_match.end() :].strip()
            remainder = remainder[: where_match.start()].strip()
        if not remainder:
            raise ValueError("SELECT fields are required.")

        from_match = re.search(r"\s+from\s+", remainder, flags=re.IGNORECASE)
        select_part = remainder
        if from_match:
            select_part = remainder[: from_match.start()].strip()
            from_part = remainder[from_match.end() :].strip()
            if not from_part:
                raise ValueError("FROM clause was provided without table names.")

        raw_fields = [part.strip() for part in select_part.split(",") if part.strip()]
        if not raw_fields:
            raise ValueError("At least one SELECT field is required.")

        selected_fields: List[str] = []
        if len(raw_fields) == 1 and raw_fields[0] == "*":
            selected_fields = list(cls.BLANKET_DEFAULT_FIELDS)
        else:
            for token in raw_fields:
                normalised = cls._normalise_blanket_field(token)
                if not normalised:
                    raise ValueError(f"Unsupported SELECT field: `{token}`.")
                if normalised not in selected_fields:
                    selected_fields.append(normalised)

        conditions: List[_BlanketCondition] = []
        if where_clause:
            parts = [part.strip() for part in re.split(r"\s+and\s+", where_clause, flags=re.IGNORECASE) if part.strip()]
            for part in parts:
                cond_match = re.match(
                    r"^\s*([a-zA-Z_][\w]*)\s*(==|=|!=|~=)\s*(.+?)\s*$",
                    part,
                )
                if not cond_match:
                    raise ValueError(
                        "Invalid WHERE condition. Use syntax like `account_name == Example.1234`."
                    )
                raw_field, operator, raw_value = cond_match.groups()
                field = cls._normalise_blanket_field(raw_field)
                if not field:
                    raise ValueError(f"Unsupported WHERE field: `{raw_field}`.")
                value = cls._strip_quotes(raw_value)
                if not value:
                    raise ValueError(f"Condition value for `{raw_field}` cannot be empty.")
                normalised_operator = "==" if operator == "=" else operator
                conditions.append(
                    _BlanketCondition(field=field, operator=normalised_operator, value=value)
                )
        return selected_fields, conditions

    @staticmethod
    def _blanket_condition_matches(
        condition: _BlanketCondition, row: Mapping[str, object]
    ) -> bool:
        value = condition.value.casefold()
        raw_actual = row.get(condition.field)
        if isinstance(raw_actual, list):
            actual_values = [str(item).casefold() for item in raw_actual]
        else:
            actual_values = [str(raw_actual or "").casefold()]

        if condition.operator == "==":
            return any(item == value for item in actual_values)
        if condition.operator == "!=":
            return all(item != value for item in actual_values)
        if condition.operator == "~=":
            return any(value in item for item in actual_values)
        return False

    @staticmethod
    def _build_blanket_rows(
        guild: discord.Guild,
        records: Sequence[Tuple[int, int, "ApiKeyRecord"]],
    ) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for _, user_id, record in records:
            member = guild.get_member(user_id)
            if member is not None:
                user_label = f"{member.display_name} ({member.name})"
                discord_name = member.name
            else:
                user_label = f"Unknown user ({user_id})"
                discord_name = "Unknown"
            rows.append(
                {
                    "user": user_label,
                    "discord_name": discord_name,
                    "user_id": str(user_id),
                    "api_keys": record.name or "",
                    "account_name": record.account_name or "",
                    "character_name": list(record.characters or []),
                    "guild_id": list(record.guild_ids or []),
                    "permission": list(record.permissions or []),
                }
            )
        return rows

    @staticmethod
    def _blanket_value_for_display(value: object) -> str:
        if isinstance(value, list):
            cleaned = [str(item) for item in value if str(item).strip()]
            return ", ".join(cleaned) if cleaned else "-"
        cleaned = str(value or "").strip()
        return cleaned or "-"

    @staticmethod
    def _ai_response_text(payload: Mapping[str, object]) -> str:
        def _collect_text(value: object, *, out: List[str]) -> None:
            if isinstance(value, str):
                text = value.strip()
                if text:
                    out.append(text)
                return
            if isinstance(value, list):
                for item in value:
                    _collect_text(item, out=out)
                return
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {"text", "output_text", "content"}:
                        _collect_text(nested, out=out)

        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        if isinstance(output_text, list):
            parts: List[str] = []
            for item in output_text:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            if parts:
                return "\n".join(parts).strip()

        output = payload.get("output")
        if not isinstance(output, list):
            output = []
        chunks: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
                output_text_value = part.get("output_text")
                if isinstance(output_text_value, str) and output_text_value.strip():
                    chunks.append(output_text_value.strip())
        if chunks:
            return "\n".join(chunks).strip()

        # Compatibility fallback for Chat Completions-like payloads.
        choices = payload.get("choices")
        if isinstance(choices, list):
            choice = choices[0] if choices else None
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                    if isinstance(content, list):
                        fallback_parts: List[str] = []
                        for part in content:
                            if isinstance(part, dict):
                                text = part.get("text")
                                if isinstance(text, str) and text.strip():
                                    fallback_parts.append(text.strip())
                        if fallback_parts:
                            return "\n".join(fallback_parts).strip()
        deep_chunks: List[str] = []
        _collect_text(payload, out=deep_chunks)
        for chunk in deep_chunks:
            if chunk and "select" in chunk.casefold():
                return chunk
        return "\n".join(deep_chunks).strip()

    @staticmethod
    def _extract_select_statement(text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""

        if "```" in cleaned:
            blocks = re.findall(r"```(?:\w+)?\s*(.*?)```", cleaned, flags=re.DOTALL)
            for block in blocks:
                candidate = block.strip()
                if re.match(r"^\s*select\b", candidate, flags=re.IGNORECASE):
                    return candidate

        match = re.search(r"(select\b.+)", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return cleaned
        candidate = match.group(1).strip()
        # Keep the first statement if extra prose trails the query.
        if ";" in candidate:
            candidate = candidate.split(";", 1)[0].strip()
        return candidate

    @staticmethod
    def _heuristic_query_from_prompt(prompt: str) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            return ""
        account_match = re.search(
            r"([A-Za-z0-9_.-]+\.\d{4})",
            cleaned,
        )
        if account_match and re.search(r"\bapi\s*keys?\b", cleaned, flags=re.IGNORECASE):
            account_name = account_match.group(1)
            return f"SELECT user, api_keys WHERE account_name == {account_name}"
        if account_match:
            account_name = account_match.group(1)
            return f"SELECT user, account_name, api_keys WHERE account_name == {account_name}"
        return ""

    @classmethod
    def _is_read_only_select_query_text(cls, query: str) -> bool:
        cleaned = query.strip()
        if not cleaned:
            return False
        if not re.match(r"^\s*select\b", cleaned, flags=re.IGNORECASE):
            return False
        lowered = cleaned.casefold()
        return not any(
            re.search(rf"\b{re.escape(keyword)}\b", lowered)
            for keyword in cls.AI_FORBIDDEN_KEYWORDS
        )

    async def _ai_generate_blanket_query(self, prompt: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "AI query generation is disabled. Set `OPENAI_API_KEY` to enable `/select ai`."
            )

        model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
        system_prompt = (
            "You convert natural language into a single read-only query for this grammar: "
            "SELECT <field[, field...]> [FROM <ignored>] [WHERE <field> <op> <value> [AND ...]]. "
            "Allowed fields: user, discord_name, user_id, api_keys, account_name, character_name, guild_id, permission. "
            "Allowed operators: ==, =, !=, ~=. "
            "Return ONLY the query text, no markdown, no explanation."
        )
        payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
            "max_output_tokens": 180,
        }

        session = await self._get_session()
        try:
            async with session.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body = await read_response_text(response)
                if response.status != 200:
                    raise ValueError(
                        f"AI query generation failed ({response.status}): {body[:200]}"
                    )
        except aiohttp.ClientError as exc:
            raise ValueError(f"Failed to reach AI service: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("AI service returned an invalid response format.") from exc

        generated = self._ai_response_text(parsed)
        generated = self._extract_select_statement(generated)
        if not generated:
            generated = self._heuristic_query_from_prompt(prompt)
        if not generated:
            raise ValueError(
                "AI did not return a usable query. Try including an account name like `Name.1234`."
            )
        return generated

    async def _resolve_blanket_scope(
        self, interaction: discord.Interaction, scope: str
    ) -> tuple[bool, str]:
        is_authorised = self.bot.is_authorised(
            interaction.guild,
            interaction.user,
            permissions=getattr(interaction, "permissions", None),
        )
        scope_value = scope.lower()
        if scope_value not in {"auto", "mine", "all"}:
            scope_value = "auto"

        if scope_value == "auto":
            effective_scope = "all" if is_authorised else "mine"
        else:
            effective_scope = scope_value
        return is_authorised, effective_scope

    async def _run_blanket_query(
        self,
        interaction: discord.Interaction,
        *,
        query: str,
        scope: str,
        include_query_label: str,
        progress_callback: Optional[
            Callable[[str, str, Optional[str], str], Awaitable[None]]
        ] = None,
        send_error_followups: bool = True,
    ) -> None:
        async def _progress(
            stage: str,
            detail: str,
            query_value: Optional[str] = None,
            state: str = "running",
        ) -> None:
            if progress_callback is not None:
                await progress_callback(stage, detail, query_value, state)

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await _progress(
                "Failed",
                "This command can only be used in a server.",
                query,
                "error",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content="Select: This command can only be used in a server.",
                )
            return

        await _progress("Scope", "Resolving query scope.", query)
        is_authorised, effective_scope = await self._resolve_blanket_scope(
            interaction, scope
        )
        if effective_scope == "all" and not is_authorised:
            await _progress(
                "Denied",
                "All-record scope requested without permissions.",
                query,
                "error",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content="Select: You do not have permission to query all records. Use scope `mine` or `auto`.",
                )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        await _progress("Validate", "Validating read-only SELECT query.", query)
        if not self._is_read_only_select_query_text(query):
            await _progress(
                "Failed",
                "Rejected non-read-only query text.",
                query,
                "error",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content="Select: Only read-only SELECT queries are allowed.",
                    use_followup=True,
                )
            return
        try:
            selected_fields, conditions = self._parse_blanket_query(query)
        except ValueError as exc:
            await _progress(
                "Failed",
                f"Query parser rejected the input: {exc}",
                query,
                "error",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content=f"Select: {exc}",
                    use_followup=True,
                )
            return

        await _progress(
            "Fetch",
            "Loading records from current server scope.",
            query,
        )
        user_id_filter = interaction.user.id if effective_scope == "mine" else None
        records = self.bot.storage.query_api_keys(
            guild_id=interaction.guild.id,
            user_id=user_id_filter,
        )
        if not records:
            await _progress(
                "Failed",
                "No records were found in this scope.",
                query,
                "error",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content="Select: No stored API keys were found for this scope.",
                    use_followup=True,
                )
            return

        await _progress(
            "Filter",
            "Applying WHERE conditions to scoped records.",
            query,
        )
        rows = self._build_blanket_rows(interaction.guild, records)
        matched_rows = [
            row
            for row in rows
            if all(
                self._blanket_condition_matches(condition, row)
                for condition in conditions
            )
        ]
        if not matched_rows:
            await _progress(
                "Done",
                "No matching records found.",
                query,
                "done",
            )
            if send_error_followups:
                await self._send_message(
                    interaction,
                    content="Select: No records matched the provided blanket query.",
                    use_followup=True,
                )
            return

        await _progress(
            "Render",
            "Building text table output.",
            query,
        )
        headers = [self.BLANKET_FIELD_LABELS[field] for field in selected_fields]
        table_rows = [
            [self._blanket_value_for_display(row.get(field)) for field in selected_fields]
            for row in matched_rows
        ]
        table = self._format_table(
            headers=headers,
            rows=table_rows,
            code_block=False,
        )
        output = io.StringIO()
        output.write("Select blanket query results\n")
        output.write(f"Scope: {effective_scope}\n")
        output.write(f"Matches: {len(matched_rows)}\n")
        output.write(f"{include_query_label}: {query.strip()}\n\n")
        output.write(table)
        output.write("\n")
        output.seek(0)

        file = discord.File(fp=output, filename="select_blanket_results.txt")
        await self._safe_followup_messages(
            interaction,
            contents=["Select blanket results attached."],
            files=[file],
        )
        await _progress(
            "Completed",
            f"Query finished with {len(matched_rows)} matches.",
            query,
            "done",
        )

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
                "alternate filters. All filters are optional."
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

        embed.add_field(
            name="Blanket query",
            value=self._format_list(
                [
                    "Use `/select blanket` with SQL-like syntax: `SELECT user, api_keys WHERE account_name == Name.1234 AND character_name == 'Char Name'`.",
                    "Operators: `==`, `!=`, `~=` (contains).",
                    "Use **scope=auto** to restrict non-mod users to their own data automatically.",
                ]
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @select.command(
        name="blanket",
        description="Run a SQL-like select query against stored account/API key data.",
    )
    @app_commands.describe(
        query="Example: SELECT user, api_keys WHERE account_name == Example.1234 AND character_name == 'My Character'",
        scope="auto: mods query all, others query their own records only",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Mine only", value="mine"),
            app_commands.Choice(name="All records", value="all"),
        ]
    )
    async def blanket_query(
        self,
        interaction: discord.Interaction,
        query: str,
        scope: str = "auto",
    ) -> None:
        await self._run_blanket_query(
            interaction,
            query=query,
            scope=scope,
            include_query_label="Query",
        )

    @select.command(
        name="ai",
        description="Use AI to convert freeform text into a safe read-only select query.",
    )
    @app_commands.describe(
        prompt="Natural language request, e.g. i want to see this user's api keys",
        scope="auto: mods query all, others query their own records only",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Mine only", value="mine"),
            app_commands.Choice(name="All records", value="all"),
        ]
    )
    async def ai_query(
        self,
        interaction: discord.Interaction,
        prompt: str,
        scope: str = "auto",
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_message(
                interaction,
                content="Select: This command can only be used in a server.",
            )
            return
        if not prompt.strip():
            await self._send_message(
                interaction,
                content="Select: Please provide a prompt.",
            )
            return

        await interaction.response.send_message(
            embed=self._build_ai_status_embed(
                stage="Start",
                detail="Preparing AI query generation.",
                prompt=prompt,
            ),
            ephemeral=True,
        )
        await self._edit_ai_status_embed(
            interaction,
            stage="Generate",
            detail="Sending prompt to AI model.",
            prompt=prompt,
        )
        try:
            generated_query = await self._ai_generate_blanket_query(prompt)
        except ValueError as exc:
            await self._edit_ai_status_embed(
                interaction,
                stage="Failed",
                detail=str(exc),
                prompt=prompt,
                state="error",
            )
            return

        await self._edit_ai_status_embed(
            interaction,
            stage="Generated",
            detail="AI generated a candidate SELECT query.",
            prompt=prompt,
            query=generated_query,
        )

        async def _progress_callback(
            stage: str,
            detail: str,
            query_value: Optional[str],
            state: str,
        ) -> None:
            await self._edit_ai_status_embed(
                interaction,
                stage=stage,
                detail=detail,
                prompt=prompt,
                query=query_value or generated_query,
                state=state,
            )

        # Re-run execution through the same read-only parser and guild-scoped fetch path.
        await self._run_blanket_query(
            interaction,
            query=generated_query,
            scope=scope,
            include_query_label="AI Query",
            progress_callback=_progress_callback,
            send_error_followups=False,
        )


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
