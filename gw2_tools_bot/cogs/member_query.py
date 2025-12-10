from __future__ import annotations

import csv
import io
import json
import logging
import shlex
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..http_utils import read_response_text
from ..storage import ApiKeyRecord

LOGGER = logging.getLogger(__name__)


class MemberQueryCog(commands.Cog):
    """Admin member lookup using a simple DSL."""

    memberquery = app_commands.Group(
        name="memberquery",
        description=(
            "Admin search to group members by GW2 guild, Discord role, account, character, or Discord name."
        ),
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

    # ------------------------------------------------------------------
    # DSL handling
    # ------------------------------------------------------------------
    def _parse_member_query(self, query: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
        tokens = shlex.split(query)
        filters: List[Tuple[str, str]] = []
        group_by: Optional[str] = None
        allowed_filters = {"guild", "role", "account", "character", "discord"}
        allowed_groups = {"guild", "role", "account", "discord"}

        for token in tokens:
            if ":" not in token:
                raise ValueError(
                    "Each filter must use the form `type:value` (e.g. guild:EWW or role:@Raid)."
                )
            key, value = token.split(":", 1)
            key_clean = key.strip().lower()
            value_clean = value.strip()
            if not key_clean or not value_clean:
                raise ValueError("Filters must include both a type and a value.")

            if key_clean == "group":
                if value_clean.lower() not in allowed_groups:
                    raise ValueError(
                        "Unsupported group. Choose from guild, role, account, or discord."
                    )
                group_by = value_clean.lower()
                continue

            if key_clean not in allowed_filters:
                raise ValueError(
                    "Unsupported filter. Use guild, role, account, character, or discord."
                )

            filters.append((key_clean, value_clean))

        if not filters:
            raise ValueError("Provide at least one filter such as guild:EWW or role:@Role.")

        return filters, group_by

    def _match_member_filters(
        self,
        filters: Sequence[Tuple[str, str]],
        member: discord.Member,
        record: ApiKeyRecord,
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
                matched_guilds.extend(label_matches or [guild_labels.get(id_matches[0], id_matches[0])])
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
                if needle not in record.account_name.casefold():
                    return False, matched_guilds, matched_roles
            elif filter_type == "character":
                if not any(needle in character.casefold() for character in record.characters):
                    return False, matched_guilds, matched_roles
            elif filter_type == "discord":
                display = f"{member.display_name} ({member.name})".casefold()
                if needle not in display and needle not in str(member.id):
                    return False, matched_guilds, matched_roles

        return True, sorted(set(matched_guilds)), sorted(set(matched_roles))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @memberquery.command(
        name="query",
        description=(
            "Admin search to group members by GW2 guild, Discord role, account, character, or Discord name."
        ),
    )
    @app_commands.describe(
        query="Filters like guild:EWW role:@Raid group:guild",
        as_csv="Export the results to a CSV attachment",
    )
    async def member_query(
        self, interaction: discord.Interaction, query: str, as_csv: bool = False
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

        try:
            filters, group_by = self._parse_member_query(query)
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="Member query",
                description=str(exc),
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

        guild_ids = {
            guild_id for _, _, record in results for guild_id in record.guild_ids if guild_id
        }
        guild_details = await self._fetch_guild_details(guild_ids)

        matched: List[Tuple[discord.Member, ApiKeyRecord, List[str], List[str]]] = []
        for _, user_id, record in results:
            member = interaction.guild.get_member(user_id)
            if not member:
                continue
            guild_labels = {
                gid: guild_details.get(gid, gid)
                for gid in record.guild_ids
                if gid
            }
            ok, matched_guilds, matched_roles = self._match_member_filters(
                filters, member, record, guild_labels
            )
            if ok:
                matched.append((member, record, matched_guilds or list(guild_labels.values()), matched_roles))

        if not matched:
            await self._send_embed(
                interaction,
                title="Member query",
                description="No members matched the provided filters.",
                use_followup=True,
            )
            return

        grouped: Dict[str, List[Tuple[discord.Member, ApiKeyRecord, List[str], List[str]]]] = {}
        for member, record, matched_guilds, matched_roles in matched:
            if group_by == "guild":
                keys = matched_guilds or ["No guilds"]
            elif group_by == "role":
                keys = matched_roles or [role.mention for role in member.roles if not role.is_default()]
                if not keys:
                    keys = ["No roles"]
            elif group_by == "account":
                keys = [record.account_name or "Unknown account"]
            elif group_by == "discord":
                keys = [member.display_name]
            else:
                keys = ["Matches"]

            for key in keys:
                grouped.setdefault(key, []).append((member, record, matched_guilds, matched_roles))

        embed = self._embed(
            title="Member query results",
            description=self._format_list(
                [
                    f"Filters: {', '.join(f'{k}:{v}' for k, v in filters)}",
                    f"Group by: {group_by or 'none'}",
                ],
                placeholder="Filters applied",
            ),
        )

        for group, entries in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].casefold())):
            preview_lines: List[str] = []
            for member, record, matched_guilds, matched_roles in entries[:10]:
                guilds_label = matched_guilds or ["No guilds"]
                preview_lines.append(
                    " • ".join(
                        [
                            member.mention,
                            f"Account: {record.account_name or 'Unknown'}",
                            f"Guilds: {', '.join(guilds_label)}",
                            f"Roles: {', '.join(matched_roles) if matched_roles else 'None recorded'}",
                        ]
                    )
                )

            embed.add_field(
                name=f"{group} ({len(entries)})",
                value=self._format_list(preview_lines, placeholder="No entries"),
                inline=False,
            )

        files = None
        if as_csv:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "Discord ID",
                    "Discord Name",
                    "Account Name",
                    "API Key Name",
                    "Guild IDs",
                    "Guild Names",
                    "Roles",
                    "Characters",
                ]
            )
            for member, record, _, _ in matched:
                guild_labels = [guild_details.get(gid, gid) for gid in record.guild_ids]
                roles = [role.name for role in member.roles if not role.is_default()]
                writer.writerow(
                    [
                        member.id,
                        f"{member.display_name} ({member.name})",
                        record.account_name,
                        record.name,
                        "; ".join(record.guild_ids),
                        "; ".join(guild_labels),
                        "; ".join(roles),
                        "; ".join(record.characters),
                    ]
                )

            buffer.seek(0)
            files = [
                discord.File(
                    fp=io.BytesIO(buffer.getvalue().encode("utf-8")),
                    filename="member_query.csv",
                )
            ]

        await interaction.followup.send(embed=embed, files=files, ephemeral=True)

    @memberquery.command(
        name="help",
        description="Explain the /memberquery DSL for admin searches.",
    )
    async def member_query_help(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        embed = self._embed(
            title="Member query help",
            description=(
                "Build filters with `type:value` pairs separated by spaces. "
                "Use quotes if values contain spaces."
            ),
        )

        embed.add_field(
            name="Common examples",
            value="\n".join(
                [
                    "`guild:EWW role:@Raid group:guild` — members in EWW with the Raid role, grouped by guild.",
                    "`character:\"my thief\"` — members with a character matching that name.",
                    "`discord:@User role:1234567890` — members matching a mention/ID and role ID.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="Filters",
            value=self._format_list(
                [
                    "`guild:<name|tag|id>` — matches GW2 guild name/tag/ID on saved keys.",
                    "`role:<@mention|id|name>` — matches any non-@everyone Discord role held.",
                    "`account:<text>` — matches GW2 account name from the API key.",
                    "`character:<text>` — matches any stored character name.",
                    "`discord:<name|id>` — matches Discord display name/username or ID.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="Grouping",
            value=self._format_list(
                [
                    "Add `group:guild`, `group:role`, `group:account`, or `group:discord` to cluster results.",
                    "Omit grouping to show everything under a single section.",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="CSV export",
            value=self._format_list(
                [
                    "Set `as_csv:true` to attach a CSV of all matches (Discord IDs, roles, guilds, characters).",
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

    await bot.add_cog(MemberQueryCog(bot), override=True)
