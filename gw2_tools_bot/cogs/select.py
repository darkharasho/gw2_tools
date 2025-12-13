from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..http_utils import read_response_text
from ..storage import normalise_guild_id

LOGGER = logging.getLogger(__name__)


class SelectCog(commands.Cog):
    """Admin member lookup with selectable filters and grouping."""

    select = app_commands.Group(
        name="select",
        description=(
            "SQL-style admin search to group members by guild, role, account, character, or Discord name."
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
            elif isinstance(name, str):
                details[guild_id] = name
        return details

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
    class _GuildExprNode:
        def evaluate(
            self, guild_labels: Mapping[str, str]
        ) -> Tuple[bool, List[str]]:
            raise NotImplementedError

    class _GuildExprTerm(_GuildExprNode):
        def __init__(self, value: str) -> None:
            self.value = value

        def evaluate(
            self, guild_labels: Mapping[str, str]
        ) -> Tuple[bool, List[str]]:
            matches = SelectCog._match_guild_term(self.value, guild_labels)
            return bool(matches), matches

    class _GuildExprAnd(_GuildExprNode):
        def __init__(self, left: "SelectCog._GuildExprNode", right: "SelectCog._GuildExprNode") -> None:
            self.left = left
            self.right = right

        def evaluate(
            self, guild_labels: Mapping[str, str]
        ) -> Tuple[bool, List[str]]:
            left_ok, left_matches = self.left.evaluate(guild_labels)
            if not left_ok:
                return False, []
            right_ok, right_matches = self.right.evaluate(guild_labels)
            if not right_ok:
                return False, []
            return True, sorted(set(left_matches + right_matches))

    class _GuildExprOr(_GuildExprNode):
        def __init__(self, left: "SelectCog._GuildExprNode", right: "SelectCog._GuildExprNode") -> None:
            self.left = left
            self.right = right

        def evaluate(
            self, guild_labels: Mapping[str, str]
        ) -> Tuple[bool, List[str]]:
            left_ok, left_matches = self.left.evaluate(guild_labels)
            right_ok, right_matches = self.right.evaluate(guild_labels)

            if left_ok and right_ok:
                return True, sorted(set(left_matches + right_matches))
            if left_ok:
                return True, left_matches
            if right_ok:
                return True, right_matches
            return False, []

    def _parse_guild_expression(self, text: str) -> Optional[_GuildExprNode]:
        tokens = self._tokenize_guild_expression(text)
        if not tokens:
            return None

        def parse_expression(index: int = 0) -> Tuple["SelectCog._GuildExprNode", int]:
            node, index = parse_term(index)
            while index < len(tokens) and tokens[index].lower() == "or":
                right, index = parse_term(index + 1)
                node = self._GuildExprOr(node, right)
            return node, index

        def parse_term(index: int) -> Tuple["SelectCog._GuildExprNode", int]:
            node, index = parse_factor(index)
            while index < len(tokens) and tokens[index].lower() == "and":
                right, index = parse_factor(index + 1)
                node = self._GuildExprAnd(node, right)
            return node, index

        def parse_factor(index: int) -> Tuple["SelectCog._GuildExprNode", int]:
            if tokens[index] == "(":
                node, index = parse_expression(index + 1)
                if index >= len(tokens) or tokens[index] != ")":
                    raise ValueError("Mismatched parentheses in guild filter")
                return node, index + 1
            if tokens[index] == ")":
                raise ValueError("Unexpected closing parenthesis in guild filter")
            if tokens[index].lower() in {"and", "or"}:
                raise ValueError("Expected a guild before an AND/OR operator")
            return self._GuildExprTerm(tokens[index]), index + 1

        node, position = parse_expression()
        if position != len(tokens):
            raise ValueError("Unexpected token in guild filter. Use AND/OR between guilds.")
        return node

    @staticmethod
    def _tokenize_guild_expression(text: str) -> List[str]:
        import shlex

        cleaned = text.strip()
        if not cleaned:
            return []

        raw_tokens = shlex.split(cleaned)
        tokens: List[str] = []
        for token in raw_tokens:
            parts = re.findall(r"\(|\)|[^()]+", token)
            for part in parts:
                stripped = part.strip()
                if not stripped:
                    continue
                upper = stripped.upper()
                if upper in {"AND", "OR"}:
                    tokens.append(upper.lower())
                elif stripped in {"&", "&&"}:
                    tokens.append("and")
                elif stripped in {"|", "||"}:
                    tokens.append("or")
                elif stripped in {"(", ")"}:
                    tokens.append(stripped)
                else:
                    tokens.append(stripped)
        return tokens

    @staticmethod
    def _match_guild_term(needle: str, guild_labels: Mapping[str, str]) -> List[str]:
        normalized_needle = normalise_guild_id(needle)
        needle_lower = needle.casefold()
        label_matches = [
            label
            for label in guild_labels.values()
            if needle_lower in label.casefold() or needle_lower in label.lower()
        ]
        id_matches = [
            gid
            for gid in guild_labels
            if needle_lower in gid.lower() or normalise_guild_id(gid) == normalized_needle
        ]
        if not label_matches and not id_matches:
            return []
        if label_matches:
            return sorted(set(label_matches))
        match_id = id_matches[0]
        return [guild_labels.get(match_id, match_id)]

    @staticmethod
    def _split_guild_input(current: str) -> Tuple[str, str]:
        """Split guild input into a stable prefix and a trailing partial token."""

        match = re.search(r"[^()\s]+$", current)
        if match:
            return current[: match.start()], match.group(0)
        return current, ""

    @staticmethod
    def _append_guild_token(prefix: str, token: str) -> str:
        """Join the DSL-friendly prefix with the next token using tidy spacing."""

        base = prefix.rstrip()
        if not base:
            return token
        if base.endswith("("):
            return f"{base}{token}"
        if token == ")":
            return f"{base}{token}"
        return f"{base} {token}"

    def _build_filters(
        self,
        *,
        guild: Optional[str],
        role: Optional[discord.Role],
        account: Optional[str],
        character: Optional[str],
        discord_member: Optional[discord.Member],
    ) -> Tuple[Optional[_GuildExprNode], List[Tuple[str, str]]]:
        guild_expression = self._parse_guild_expression(guild) if guild else None

        filters: List[Tuple[str, str]] = []
        if role:
            filters.append(("role", str(role.id)))
        if account:
            filters.append(("account", account))
        if character:
            filters.append(("character", character))
        if discord_member:
            filters.append(("discord", str(discord_member.id)))
        return guild_expression, filters

    def _match_member_filters(
        self,
        guild_expression: Optional[_GuildExprNode],
        filters: Sequence[Tuple[str, str]],
        member: discord.Member,
        account_names: Sequence[str],
        characters: Sequence[str],
        character_keys: Sequence[str],
        guild_labels: Dict[str, str],
    ) -> Tuple[bool, List[str], List[str]]:
        matched_guilds: List[str] = []
        matched_roles: List[str] = []

        if guild_expression:
            ok, matched_guilds = guild_expression.evaluate(guild_labels)
            if not ok:
                return False, [], []

        for filter_type, raw_value in filters:
            needle = raw_value.casefold()
            if filter_type == "role":
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

        prefix, partial = self._split_guild_input(current or "")
        choices: List[app_commands.Choice[str]] = []
        seen: set[str] = set()
        current_lower = partial.lower()

        def add_dsl_choices() -> None:
            keyword_tokens = ["AND", "OR", "(", ")"]
            for token in keyword_tokens:
                if current_lower and current_lower not in token.lower():
                    continue
                value = self._append_guild_token(prefix, token)
                if not value or len(value) > 100:
                    continue
                display = f"{token} (keyword)"
                choices.append(app_commands.Choice(name=display[:100], value=value))
                if len(choices) >= 25:
                    return

        add_dsl_choices()

        async def add_choices(guild_ids: Iterable[str]) -> None:
            nonlocal choices
            try:
                details = await self._fetch_guild_details(list(guild_ids))
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
                value = self._append_guild_token(prefix, label)
                if not value or len(value) > 100:
                    continue
                seen.add(guild_id)
                choices.append(app_commands.Choice(name=display[:100], value=value))
                if len(choices) >= 25:
                    break

        # Prefer configured guild IDs first.
        await add_choices(configured_ids)

        # Fallback to GW2 API search for unmatched input so admins can target
        # guilds that are not yet configured via /guildroles.
        if len(choices) < 25 and partial.strip():
            try:
                search_results = await self._fetch_json(
                    "https://api.guildwars2.com/v2/guild/search",
                    params={"name": partial.strip()},
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

    @select.command(
        name="query",
        description=(
            "SQL-style admin member search by GW2 guild, Discord role, account, character, or Discord name."
        ),
    )
    @app_commands.describe(
        guild="SQL-style AND/OR GW2 guild filter (e.g. 'EWW AND DUI' or 'EWW OR DUI')",
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
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_embed(
                interaction,
                title="Select",
                description="This command can only be used in a server.",
                colour=BRAND_COLOUR,
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        normalized_guild_map = {
            normalise_guild_id(gid): role_id
            for gid, role_id in config.guild_role_ids.items()
            if normalise_guild_id(gid)
        }
        allowed_guild_ids = set(normalized_guild_map.keys())
        guild = (guild.strip() or None) if isinstance(guild, str) else guild
        account = (account.strip() or None) if isinstance(account, str) else account
        character = (character.strip() or None) if isinstance(character, str) else character

        # Only consider the character filter when a value was actually supplied.
        character_provided = bool(character)
        character_key = self._character_key(character) if character_provided else None
        character_label = character

        try:
            guild_expression, filters = self._build_filters(
                guild=guild,
                role=role,
                account=account,
                character=character_key,
                discord_member=discord_member,
            )
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="Select",
                description=f"Invalid guild filter: {exc}",
                colour=BRAND_COLOUR,
            )
            return
        # Only surface character lists when the character filter was provided.
        # Suppress character details when only counts are requested.
        show_characters = character_provided and not count_only
        if group_by:
            group_by = group_by.lower()
        allowed_groups = {"guild", "role", "account", "discord"}
        if group_by and group_by not in allowed_groups:
            await self._send_embed(
                interaction,
                title="Select",
                description="Unsupported group. Choose guild, role, account, or discord.",
                colour=BRAND_COLOUR,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        if not results:
            await self._send_embed(
                interaction,
                title="Select",
                description="No stored API keys were found for this server.",
                use_followup=True,
            )
            return

        # Opportunistically backfill missing character lists so autocompletes
        # and grouping have richer data to work with.
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

        if character_provided and character_key:
            canonical_character = None
            for bundle in bundles.values():
                entries = bundle.get("character_map", {}).get(character_key, [])
                if entries:
                    canonical_character = entries[0][0]
                    break

            if not canonical_character:
                await self._send_embed(
                    interaction,
                    title="Select",
                    description=(
                        "No stored characters matched that name. Try selecting a name "
                        "from the autocomplete list or refresh your API keys with `/apikey update`."
                    ),
                    colour=BRAND_COLOUR,
                    use_followup=True,
                )
                return

            character_label = canonical_character

        if not bundles:
            await self._send_embed(
                interaction,
                title="Select",
                description="No stored API keys were found for this server.",
                use_followup=True,
            )
            return

        guild_ids_for_lookup = allowed_guild_ids or {
            guild_id for bundle in bundles.values() for guild_id in bundle["guild_ids"]
        }
        if guild:
            guild_ids_for_lookup = set(guild_ids_for_lookup)
            for token in self._tokenize_guild_expression(guild):
                if token in {"and", "or", "(", ")"}:
                    continue
                normalized_token = normalise_guild_id(token)
                if normalized_token:
                    guild_ids_for_lookup.add(normalized_token)
        guild_details = await self._fetch_guild_details(guild_ids_for_lookup)

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
            ok, matched_guilds, matched_roles = self._match_member_filters(
                guild_expression,
                filters,
                member,
                bundle["account_names"],
                bundle["characters"],
                bundle["character_keys"],
                guild_labels,
            )
            if ok:
                matched_character_entries: List[Tuple[str, Optional[str]]] = []
                if character_provided and character_key:
                    needle = character_key
                    matched_character_entries = list(
                        bundle.get("character_map", {}).get(needle, [])
                    )
                    if not matched_character_entries:
                        continue
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
                    )
                )

        if not matched:
            await self._send_embed(
                interaction,
                title="Select",
                description="No members matched the provided filters.",
                use_followup=True,
            )
            return

        single_target = (
            not count_only
            and not group_by
            and len(matched) == 1
            and (account or character_provided or discord_member)
        )

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
        ) in matched:
            if group_by == "guild":
                keys = matched_guilds or ["No guilds"]
            elif group_by == "role":
                keys = mapped_role_mentions or matched_roles
                if not keys:
                    keys = ["No mapped roles"]
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
                    )
                )

        filters_label: List[str] = []
        if guild:
            filters_label.append(f"Guild filter: {guild}")
        else:
            filters_label.append("Guild: All mapped")
        if role:
            filters_label.append(f"Role: {role.name}")
        if account:
            filters_label.append(f"Account: {account}")
        if character_provided:
            filters_label.append(f"Character: {character_label}")
        if discord_member:
            filters_label.append(f"Discord: {discord_member.display_name}")
        if not any([guild, role, account, character_provided, discord_member]):
            filters_label = ["None (all)"]

        summary_embed = self._embed(
            title="Select results",
            description="",
        )
        summary_embed.add_field(
            name="Filters",
            value=self._trim_field("```\n" + "\n".join(filters_label) + "\n```"),
            inline=False,
        )
        summary_embed.add_field(
            name="Group by",
            value=group_by.capitalize() if group_by else "None",
            inline=False,
        )
        summary_embed.add_field(
            name="Matches",
            value=str(len(matched)),
            inline=False,
        )

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
                matched_guilds,
                _,
                _,
                guild_ids,
            ) in matched:
                guild_labels = [guild_details.get(gid, gid) for gid in guild_ids]
                roles = [role.name for role in member.roles if not role.is_default()]
                characters_for_csv = (
                    [name for name, _ in character_entries]
                    if character_provided
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
            for group, entries in sorted(
                grouped.items(), key=lambda item: (-len(item[1]), item[0].casefold())
            ):
                display_group = group
                if (
                    group_by == "role"
                    and group.startswith("<@&")
                    and group.endswith(">")
                ):
                    try:
                        role_id = int(group.strip("<@&>"))
                    except ValueError:
                        role_id = None
                    role_obj = interaction.guild.get_role(role_id) if role_id else None
                    display_group = role_obj.name if role_obj else "Role"

                if group_by and display_group:
                    match_blocks.append(f"**{display_group}**")

                for (
                    member,
                    account_names,
                    _characters,
                    character_entries,
                    matched_guilds,
                    matched_roles,
                    mapped_role_mentions,
                    guild_ids,
                ) in entries:
                    guilds_label = matched_guilds or [
                        self._friendly_guild_label(gid, guild_details)
                        for gid in guild_ids
                        if gid
                    ]
                    role_labels = self._role_labels(
                        mapped_role_mentions or matched_roles, interaction.guild
                    )

                    block_lines = [
                        member.mention,
                        f"Roles: {', '.join(role_labels) if role_labels else 'No mapped roles'}",
                        "```",
                        *[f"- {label}" for label in guilds_label or ["No guilds"]],
                        "```",
                    ]

                    if show_characters and character_entries:
                        block_lines.append("Characters:")
                        block_lines.append("```")
                        block_lines.extend(
                            [
                                f"- {name}" + (f" — {acct}" if acct else "")
                                for name, acct in character_entries
                            ]
                            or ["- None"]
                        )
                        block_lines.append("```")

                    match_blocks.append("\n".join(block_lines))
                match_blocks.append("")

        # Build paginated embeds of match blocks to stay within Discord limits.
        match_embeds: List[discord.Embed] = []
        if match_blocks:
            current: List[str] = []
            current_len = 0
            for block in match_blocks:
                # Separate entries with a blank line for readability.
                block_len = len(block) + (2 if current else 0)
                if current and current_len + block_len > 3800:
                    embed = self._embed(title="Matches", description="\n\n".join(current))
                    match_embeds.append(embed)
                    current = []
                    current_len = 0

                if block:
                    current.append(block)
                    current_len += block_len

            if current:
                embed = self._embed(title="Matches", description="\n\n".join(current))
                match_embeds.append(embed)

        await self._safe_followup(
            interaction,
            embeds=[summary_embed, *match_embeds] if match_embeds else [summary_embed],
            files=files,
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
            description="Use SQL-style filters and grouping. All filters are optional.",
        )

        embed.add_field(
            name="Common examples",
            value="\n".join(
                [
                    "Set **Guild** to `EWW AND DUI` and **Group by** to `Guild` to see members in both guilds.",
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
                    "**Guild** — autocomplete mapped GW2 guilds configured via `/guildroles` and combine them with AND/OR.",
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
