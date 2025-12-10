from __future__ import annotations

import csv
import io
import json
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..http_utils import read_response_text

LOGGER = logging.getLogger(__name__)


class MemberQueryCog(commands.Cog):
    """Admin member lookup with selectable filters and grouping."""

    memberquery = app_commands.Group(
        name="memberquery",
        description=(
            "Admin search to group members by GW2 guild, Discord role, account, character, or Discord name."
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
        colour: discord.Colour = discord.Colour.blurple(),
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
        colour: discord.Colour = discord.Colour.blurple(),
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
        embed: discord.Embed,
        files: Optional[Sequence[discord.File]] = None,
    ) -> None:
        """Send a followup only if the interaction is still active."""

        try:
            await interaction.followup.send(
                embed=embed, files=list(files) if files is not None else [], ephemeral=True
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
    def _build_filters(
        self,
        *,
        guild: Optional[str],
        role: Optional[discord.Role],
        account: Optional[str],
        character: Optional[str],
        discord_member: Optional[discord.Member],
    ) -> List[Tuple[str, str]]:
        filters: List[Tuple[str, str]] = []
        if guild:
            filters.append(("guild", guild))
        if role:
            filters.append(("role", str(role.id)))
        if account:
            filters.append(("account", account))
        if character:
            filters.append(("character", character))
        if discord_member:
            filters.append(("discord", str(discord_member.id)))
        return filters

    def _match_member_filters(
        self,
        filters: Sequence[Tuple[str, str]],
        member: discord.Member,
        account_names: Sequence[str],
        characters: Sequence[str],
        guild_labels: Dict[str, str],
    ) -> Tuple[bool, List[str], List[str]]:
        matched_guilds: List[str] = []
        matched_roles: List[str] = []

        for filter_type, raw_value in filters:
            needle = raw_value.casefold()
            if filter_type == "guild":
                label_matches = [
                    label
                    for label in guild_labels.values()
                    if needle in label.casefold() or needle in label.lower()
                ]
                id_matches = [gid for gid in guild_labels if needle in gid.lower()]
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
                if not any(needle == character.casefold() for character in characters):
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
        guild_ids = list(config.guild_role_ids.keys())
        if not guild_ids:
            return []

        try:
            details = await self._fetch_guild_details(guild_ids)
        except ValueError:
            LOGGER.warning("Guild lookup failed during autocomplete", exc_info=True)
            details = {}
        choices: List[app_commands.Choice[str]] = []
        current_lower = current.lower()
        for guild_id in guild_ids:
            label = details.get(guild_id, guild_id)
            display = f"{label} ({guild_id})"
            if current_lower in display.lower():
                choices.append(app_commands.Choice(name=display[:100], value=guild_id))
            if len(choices) >= 25:
                break
        return choices

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
        characters: List[str] = []
        for _, _, record in results:
            for name in record.characters:
                if name not in characters and current.lower() in name.lower():
                    characters.append(name)
        return [
            app_commands.Choice(name=value[:100], value=value) for value in characters[:25]
        ]

    @memberquery.command(
        name="query",
        description=(
            "Admin search to group members by GW2 guild, Discord role, account, character, or Discord name."
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
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_embed(
                interaction,
                title="Member query",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        allowed_guild_ids = set(config.guild_role_ids.keys())
        if guild and guild not in allowed_guild_ids:
            await self._send_embed(
                interaction,
                title="Member query",
                description=(
                    "Guild filters must use IDs configured via /guildroles set. "
                    "Choose a mapped guild from the autocomplete list."
                ),
                colour=discord.Colour.red(),
            )
            return

        guild = (guild.strip() or None) if isinstance(guild, str) else guild
        account = (account.strip() or None) if isinstance(account, str) else account
        character = (character.strip() or None) if isinstance(character, str) else character

        option_names = self._option_names(interaction)
        character_provided = "character" in option_names and bool(character)

        filters = self._build_filters(
            guild=guild,
            role=role,
            account=account,
            character=character,
            discord_member=discord_member,
        )
        # Only surface character lists when the character filter was provided.
        show_characters = character_provided
        if group_by:
            group_by = group_by.lower()
        allowed_groups = {"guild", "role", "account", "discord"}
        if group_by and group_by not in allowed_groups:
            await self._send_embed(
                interaction,
                title="Member query",
                description="Unsupported group. Choose guild, role, account, or discord.",
                colour=discord.Colour.red(),
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        results = self.bot.storage.query_api_keys(guild_id=interaction.guild.id)
        if not results:
            await self._send_embed(
                interaction,
                title="Member query",
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
                    "guild_ids": set(),
                },
            )
            if record.account_name and record.account_name not in bundle["account_names"]:
                bundle["account_names"].append(record.account_name)
            for character in record.characters:
                if character not in bundle["characters"]:
                    bundle["characters"].append(character)
                entry = (character, record.account_name)
                if entry not in bundle["character_entries"]:
                    bundle["character_entries"].append(entry)
            for gid in record.guild_ids:
                if gid:
                    bundle["guild_ids"].add(gid)

        if not bundles:
            await self._send_embed(
                interaction,
                title="Member query",
                description="No stored API keys were found for this server.",
                use_followup=True,
            )
            return

        guild_ids_for_lookup = allowed_guild_ids or {
            guild_id for bundle in bundles.values() for guild_id in bundle["guild_ids"]
        }
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
            configured_guilds = [
                gid for gid in bundle["guild_ids"] if gid in allowed_guild_ids
            ]
            guild_labels = {
                gid: guild_details.get(gid, gid)
                for gid in (configured_guilds or bundle["guild_ids"])
                if gid
            }
            mapped_role_mentions: List[str] = []
            for gid in bundle["guild_ids"]:
                role_id = config.guild_role_ids.get(gid)
                if role_id:
                    role_obj = interaction.guild.get_role(role_id)
                    if role_obj:
                        mapped_role_mentions.append(role_obj.mention)
            mapped_role_mentions = sorted(set(mapped_role_mentions))
            ok, matched_guilds, matched_roles = self._match_member_filters(
                filters,
                member,
                bundle["account_names"],
                bundle["characters"],
                guild_labels,
            )
            if ok:
                matched_character_entries: List[Tuple[str, Optional[str]]] = []
                if character_provided and character:
                    needle = character.casefold()
                    for name, account_name in bundle.get("character_entries", []):
                        if needle == name.casefold():
                            matched_character_entries.append((name, account_name))
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
                title="Member query",
                description="No members matched the provided filters.",
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
            guild_label = guild_details.get(guild, guild)
            filters_label.append(f"Guild: {guild_label}\n```{guild}```")
        else:
            filters_label.append("Guild: All mapped\n```All mapped```")
        if role:
            filters_label.append(f"Role: {role.name}\n```{role.id}```")
        if account:
            filters_label.append(f"Account\n```{account}```")
        if character_provided:
            filters_label.append(f"Character\n```{character}```")
        if discord_member:
            filters_label.append(
                f"Discord\n```{discord_member.display_name} ({discord_member.id})```"
            )
        if not any([guild, role, account, character, discord_member]):
            filters_label = ["None (all)\n```All members```"]

        embed = self._embed(
            title="Member query results",
            description="",
        )
        embed.add_field(
            name="Filters",
            value=self._trim_field("\n".join(filters_label)),
            inline=False,
        )
        embed.add_field(
            name="Group by",
            value=group_by.capitalize() if group_by else "None",
            inline=False,
        )

        for group, entries in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0].casefold())
        ):
            display_group = group
            if group_by == "role" and group.startswith("<@&") and group.endswith(">"):
                try:
                    role_id = int(group.strip("<@&>"))
                except ValueError:
                    role_id = None
                role_obj = interaction.guild.get_role(role_id) if role_id else None
                display_group = role_obj.name if role_obj else group

            preview_lines: List[str] = []
            for (
                member,
                account_names,
                characters,
                character_entries,
                matched_guilds,
                matched_roles,
                mapped_role_mentions,
                guild_ids,
            ) in entries[:10]:
                guilds_label = matched_guilds or ["No guilds"]
                roles_label = mapped_role_mentions or matched_roles or ["None mapped"]
                detail_lines = [
                    f"• {member.mention}",
                    f"  • Accounts: {', '.join(account_names) or 'Unknown'}",
                    f"  • Guilds: {', '.join(guilds_label)}",
                    f"  • Roles: {', '.join(roles_label)}",
                ]
                if show_characters:
                    character_lines = [
                        f"• {name} — {account_name or 'Unknown account'}"
                        for name, account_name in character_entries
                    ]
                    detail_lines.append(
                        "  • Characters:\n"
                        + self._trim_field(
                            "\n".join(character_lines) or "• None",
                        )
                    )
                preview_lines.append("\n".join(detail_lines))

            field_value = "\n".join(preview_lines) if preview_lines else "• No entries"
            embed.add_field(
                name=f"{display_group} ({len(entries)})",
                value=self._trim_field(field_value),
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
                    filename="member_query.csv",
                )
            ]

        if files:
            await self._safe_followup(interaction, embed=embed, files=files)
        else:
            await self._safe_followup(interaction, embed=embed)

    @memberquery.command(
        name="help",
        description="Explain the member query filters and grouping options.",
    )
    async def member_query_help(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        embed = self._embed(
            title="Member query help",
            description="Use the options to select filters and grouping. All filters are optional.",
        )

        embed.add_field(
            name="Common examples",
            value="\n".join(
                [
                    "Set **Guild** to `EWW` and **Group by** to `Guild` to see members in that guild.",
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
                ]
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    existing = bot.tree.get_command("memberquery")
    if existing:
        LOGGER.info("Replacing existing memberquery command during cog load")
        bot.tree.remove_command("memberquery", type=discord.AppCommandType.chat_input)

    cog = MemberQueryCog(bot)
    await bot.add_cog(cog, override=True)
    # Explicitly (re)attach the group to the command tree so it registers even if
    # stale state lingered from prior runs.
    bot.tree.add_command(cog.memberquery, override=True)
