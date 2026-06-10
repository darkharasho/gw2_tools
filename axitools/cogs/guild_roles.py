from __future__ import annotations

import asyncio
import csv
import logging
import re
from collections import defaultdict
from io import StringIO
from typing import Dict, List, Optional, Sequence, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR
from ..storage import ApiKeyRecord, GuildConfig
from ._accounts_shared import AccountsSharedMixin

LOGGER = logging.getLogger(__name__)


class GuildRolesCog(AccountsSharedMixin, commands.Cog):
    """Configure and audit Guild Wars 2 guild to Discord role mappings."""

    guild_roles = app_commands.Group(
        name="guildroles",
        description="Configure Guild Wars 2 guild to role mappings.",
        extras={"category": "Moderation"},
    )
    guild_role_allowlist = app_commands.Group(
        name="whitelist",
        description="Manage preferred guild role allowlist entries.",
        parent=guild_roles,
    )

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot
        self._init_shared_state()
        self._audit_key_cache: Dict[Tuple[int, str], str] = {}
        self._audit_key_cache_loaded: Set[int] = set()

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self._close_session()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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
            rank = entry.get("rank")
            members.append(
                {
                    "name": name.strip(),
                    "wvw_member": bool(entry.get("wvw_member")),
                    "rank": rank.strip() if isinstance(rank, str) else "",
                }
            )

        return members

    def _find_mapped_guild(self, config: GuildConfig, role_id: int) -> Optional[str]:
        for guild_id, configured_role_id in config.guild_role_ids.items():
            if configured_role_id == role_id:
                return guild_id
        return None

    def _has_guild_permission(self, record: ApiKeyRecord) -> bool:
        return "guilds" in {value.lower() for value in record.permissions}

    def _ensure_audit_key_cache_loaded(self, guild_id: int) -> None:
        if guild_id in self._audit_key_cache_loaded:
            return
        cached_entries = self.bot.storage.get_audit_key_cache(guild_id)
        for gw2_guild_id, api_key in cached_entries.items():
            self._audit_key_cache[(guild_id, gw2_guild_id)] = api_key
        self._audit_key_cache_loaded.add(guild_id)

    def _persist_audit_key_cache(self, guild_id: int) -> None:
        entries = {
            gw2_guild_id: api_key
            for (cache_guild_id, gw2_guild_id), api_key in self._audit_key_cache.items()
            if cache_guild_id == guild_id
        }
        self.bot.storage.save_audit_key_cache(guild_id, entries)

    async def _fetch_guild_members_for_audit(
        self, guild: discord.Guild, guild_id: str
    ) -> List[Dict[str, object]]:
        self._ensure_audit_key_cache_loaded(guild.id)
        candidates = [
            record
            for _, _, record in self.bot.storage.query_api_keys(
                guild_id=guild.id, gw2_guild_id=guild_id
            )
            if self._has_guild_permission(record)
        ]
        if not candidates:
            raise ValueError(
                "No stored API keys with the guilds permission were found for that guild. "
                "Ask a guild leader to add their API key with /apikey add."
            )

        cache_key = (guild.id, self._normalise_guild_id(guild_id))
        cached_key = self._audit_key_cache.get(cache_key)
        last_error: Optional[ValueError] = None
        if cached_key:
            cached_record = next(
                (record for record in candidates if record.key == cached_key),
                None,
            )
            if cached_record:
                try:
                    return await self._fetch_guild_members(
                        guild_id, api_key=cached_record.key
                    )
                except ValueError as exc:
                    last_error = exc
                    self._audit_key_cache.pop(cache_key, None)
                    self._persist_audit_key_cache(guild.id)
            else:
                self._audit_key_cache.pop(cache_key, None)
                self._persist_audit_key_cache(guild.id)

        for record in candidates:
            if record.key == cached_key:
                continue
            try:
                members = await self._fetch_guild_members(guild_id, api_key=record.key)
                self._audit_key_cache[cache_key] = record.key
                self._persist_audit_key_cache(guild.id)
                return members
            except ValueError as exc:
                last_error = exc
                continue

        if last_error:
            raise ValueError(
                "Stored API keys could not access the guild roster. "
                "Ask a guild leader to add their API key with /apikey add."
            ) from last_error
        raise ValueError(
            "Stored API keys could not access the guild roster. "
            "Ask a guild leader to add their API key with /apikey add."
        )

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

    # ------------------------------------------------------------------
    # Guild role audit
    # ------------------------------------------------------------------
    @guild_roles.command(
        name="audit", description="Audit Discord role assignments against live guild membership data."
    )
    @app_commands.describe(
        role="Discord role mapped to a Guild Wars 2 guild",
        csv_output="Attach a CSV export",
        full_roster="Include the full guild roster in the output",
        ephemeral="Send the audit response privately",
    )
    async def audit_guild_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        csv_output: bool = False,
        full_roster: bool = False,
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
        alliance_guild_id = (
            self._normalise_guild_id(config.alliance_guild_id)
            if config.alliance_guild_id
            else None
        )
        show_full_roster = full_roster

        await interaction.response.defer(ephemeral=ephemeral, thinking=True)

        chunk_task: Optional[asyncio.Task] = None
        if interaction.guild and not interaction.guild.chunked:
            chunk_task = asyncio.create_task(interaction.guild.chunk(cache=True))

        try:
            members = await self._fetch_guild_members_for_audit(
                interaction.guild, guild_id
            )
            wvw_members_source = members
            if alliance_guild_id and alliance_guild_id != guild_id:
                wvw_members_source = await self._fetch_guild_members_for_audit(
                    interaction.guild, alliance_guild_id
                )
        except ValueError as exc:
            if chunk_task:
                try:
                    await chunk_task
                except Exception:
                    LOGGER.exception("Failed to chunk guild members before audit")
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
        alliance_label = None
        if alliance_guild_id:
            alliance_lookup = await self._cached_guild_labels([alliance_guild_id])
            alliance_label = alliance_lookup.get(alliance_guild_id, alliance_guild_id)

        if chunk_task:
            try:
                await chunk_task
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
        for entry in wvw_members_source:
            name = str(entry["name"])
            normalized_name = self._normalise_account_name(name)
            is_wvw_member = bool(entry.get("wvw_member"))
            member_wvw_lookup[normalized_name] = is_wvw_member
            if is_wvw_member:
                wvw_members[normalized_name] = name

        discrepancy_rows: List[Sequence[str]] = []
        csv_rows: List[Sequence[str]] = []

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

        target_guild_tag = guild_tag_for_id(guild_id)
        missing_role_label = self._strip_emoji(role.name) or "role"
        alliance_member_lookup = (
            {self._normalise_account_name(str(entry["name"])) for entry in wvw_members_source}
            if alliance_guild_id
            else set()
        )

        for normalized_name, original_name in guild_member_lookup.items():
            records = account_records.get(normalized_name, [])
            selected_member: Optional[discord.Member] = None
            selected_record: Optional[ApiKeyRecord] = None
            if records:
                for user_id, record in records:
                    member = interaction.guild.get_member(user_id)
                    if member:
                        selected_member = member
                        selected_record = record
                        break
                if not selected_record:
                    selected_record = records[0][1]
                    selected_member = (
                        interaction.guild.get_member(records[0][0])
                        if records
                        else None
                    )

            display_name = (
                self._strip_emoji(selected_member.display_name)
                if selected_member
                else "--"
            )
            roles = (
                ", ".join(
                    sorted(
                        self._strip_emoji(role.name)
                        for role in selected_member.roles
                        if role.name and not role.is_default()
                    )
                )
                if selected_member
                else "--"
            )
            guild_tags = (
                ", ".join(guild_tags_for_member(selected_member)) or target_guild_tag
                if selected_member
                else target_guild_tag
            )
            account_label = self._strip_emoji(
                selected_record.account_name if selected_record and selected_record.account_name else original_name
            )

            issues: List[str] = []
            if not records:
                issues.append("No API key")
            else:
                has_role = selected_member is not None and role in selected_member.roles
                if not has_role:
                    issues.append(f"Missing {missing_role_label} role")

            if alliance_guild_id:
                if normalized_name not in alliance_member_lookup:
                    issues.append("Not in alliance guild")
                elif not member_wvw_lookup.get(normalized_name, False):
                    issues.append("Not WvW member")
            elif not member_wvw_lookup.get(normalized_name, False):
                issues.append("Not WvW member")

            if issues or show_full_roster:
                combined_issues = "; ".join(issues) if issues else "None"
                discrepancy_rows.append(
                    (display_name, account_label, guild_tags, combined_issues)
                )
                csv_rows.append(
                    (
                        self._strip_emoji(selected_member.name) if selected_member else "--",
                        account_label,
                        guild_tags,
                        combined_issues,
                        roles,
                    )
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

            display_name = self._strip_emoji(member.display_name)
            roles = ", ".join(
                sorted(
                    self._strip_emoji(role.name)
                    for role in member.roles
                    if role.name and not role.is_default()
                )
            )
            guild_tags = ", ".join(guild_tags_for_member(member)) or "--"

            if not account_names:
                discrepancy_rows.append((display_name, "--", guild_tags, "No API key"))
                csv_rows.append(
                    (
                        self._strip_emoji(member.name),
                        "--",
                        guild_tags,
                        "No API key",
                        roles,
                    )
                )
                continue

            for account_name in sorted(account_names):
                normalised = self._normalise_account_name(account_name)
                if normalised in guild_member_lookup:
                    continue
                clean_account = self._strip_emoji(account_name)
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

        guild_label = guild_labels.get(guild_id, guild_id)

        summary_lines = [
            "**Guild membership audit**",
            f"Guild: {guild_label}",
            f"Guild ID: `{guild_id}`",
            f"Role: {role.mention}",
        ]
        if alliance_label:
            summary_lines.append(f"Alliance WvW guild: {alliance_label}")

        report_table = self._format_table(
            ["Discord", "GW2 account", "Guilds", "Issue"],
            discrepancy_rows,
            placeholder="None",
            code_block=False,
        )

        report_buffer = StringIO(report_table)

        files: List[discord.File] = [
            discord.File(fp=StringIO(report_buffer.getvalue()), filename="guild_audit.txt")
        ]
        if csv_output:
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["Discord username", "GW2 account", "Guilds", "Issue", "Roles"])
            writer.writerows(csv_rows)
            files.append(discord.File(fp=StringIO(buffer.getvalue()), filename="guild_audit.csv"))

        content = "\n".join(summary_lines)
        await interaction.followup.send(content=content, files=files, ephemeral=ephemeral)

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

    @guild_roles.command(
        name="setalliance",
        description="Set the alliance guild used for WvW membership checks in role audits.",
    )
    @app_commands.describe(guild_id="Guild Wars 2 guild ID to use for alliance WvW checks")
    async def set_alliance_guild(
        self, interaction: discord.Interaction, guild_id: str
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        cleaned_guild_id = self._normalise_guild_id(guild_id)
        if not cleaned_guild_id:
            await self._send_embed(
                interaction,
                title="Alliance guild",
                description="Please provide a valid guild ID.",
                colour=BRAND_COLOUR,
            )
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        config.alliance_guild_id = cleaned_guild_id
        details = await self._cached_guild_labels([cleaned_guild_id])
        alliance_label = details.get(cleaned_guild_id, cleaned_guild_id)
        config.alliance_guild_name = alliance_label
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        await self._send_embed(
            interaction,
            title="Alliance guild saved",
            description=f"WvW membership checks will use **{alliance_label}**.",
        )

    @guild_roles.command(
        name="clearalliance",
        description="Clear the alliance guild used for WvW membership checks in role audits.",
    )
    async def clear_alliance_guild(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        config.alliance_guild_id = None
        config.alliance_guild_name = None
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        await self._send_embed(
            interaction,
            title="Alliance guild cleared",
            description="WvW membership checks will use the audited guild roster.",
        )

    @guild_role_allowlist.command(
        name="add",
        description="Allow a role to be chosen as a preferred guild role.",
    )
    @app_commands.describe(role="Discord role to allow for preferred selection")
    async def add_preferred_guild_role_allowlist(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        if role.id in config.preferred_guild_role_allowlist:
            await self._send_embed(
                interaction,
                title="Preferred role allowlist",
                description=f"{role.mention} is already available for preferred selection.",
            )
            return

        config.preferred_guild_role_allowlist.append(role.id)
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        await self._send_embed(
            interaction,
            title="Preferred role allowlist updated",
            description=f"{role.mention} can now be selected as a preferred guild role.",
        )

    @guild_role_allowlist.command(
        name="remove",
        description="Remove a role from the preferred guild role allowlist.",
    )
    @app_commands.describe(role="Discord role to remove from preferred selection")
    async def remove_preferred_guild_role_allowlist(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        if role.id not in config.preferred_guild_role_allowlist:
            await self._send_embed(
                interaction,
                title="Preferred role allowlist",
                description=f"{role.mention} is not currently allowed for preferred selection.",
            )
            return

        config.preferred_guild_role_allowlist = [
            role_id
            for role_id in config.preferred_guild_role_allowlist
            if role_id != role.id
        ]
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        self.bot.storage.clear_preferred_guild_role_for_role(interaction.guild.id, role.id)
        await self._send_embed(
            interaction,
            title="Preferred role allowlist updated",
            description=f"{role.mention} can no longer be selected as a preferred guild role.",
        )

    @guild_role_allowlist.command(
        name="list",
        description="List roles allowed to be selected as preferred guild roles.",
    )
    async def list_preferred_guild_role_allowlist(
        self, interaction: discord.Interaction
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        if not interaction.guild:
            await self._send_embed(
                interaction,
                title="Preferred role allowlist",
                description="This command can only be used in a server.",
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        if not config.preferred_guild_role_allowlist:
            await self._send_embed(
                interaction,
                title="Preferred role allowlist",
                description="No roles are currently available for preferred selection.",
            )
            return

        role_labels: List[str] = []
        for role_id in config.preferred_guild_role_allowlist:
            role = interaction.guild.get_role(role_id)
            role_labels.append(role.mention if role else f"role ID {role_id}")

        await self._send_embed(
            interaction,
            title="Preferred role allowlist",
            description=self._format_list(role_labels),
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


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(GuildRolesCog(bot))
