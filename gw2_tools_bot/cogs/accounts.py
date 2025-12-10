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
    memberquery = app_commands.Group(
        name="memberquery",
        description="Admin search to group members by GW2 guild, Discord role, account, character, or Discord name.",
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:  # pragma: no cover - discord.py lifecycle
        """Register application command groups when the cog loads."""
        added: List[str] = []
        registered = {command.name for command in self.bot.tree.get_commands()}
        for group in (self.guild_roles, self.api_keys, self.guild_lookup, self.memberquery):
            if group.name not in registered:
                self.bot.tree.add_command(group)
                added.append(group.name)
            else:
                # Ensure stale registrations do not block reloads when extensions are
                # reloaded before tree entries are cleared.
                self.bot.tree.add_command(group, override=True)

        if added:
            try:
                await self.bot.tree.sync()
                for guild in self.bot.guilds:
                    await self.bot.tree.sync(guild=guild)
            except Exception:
                LOGGER.exception("Failed to sync commands after adding: %s", ", ".join(added))

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
    def _normalise_guild_id(guild_id: str) -> str:
        return normalise_guild_id(guild_id)

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

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept-Encoding": "gzip, deflate, br"},
                auto_decompress=False,
            )
        return self._session

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        for group in (self.guild_roles, self.api_keys, self.guild_lookup, self.memberquery):
            try:
                self.bot.tree.remove_command(group.name, type=discord.AppCommandType.chat_input)
            except Exception:
                LOGGER.debug("Failed to remove command group %s during unload", group.name, exc_info=True)

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
        try:
            guild_details = await self._fetch_guild_details(record.guild_ids, api_key=record.key)
        except ValueError as exc:
            guild_details = {}
            error = str(exc)

        guild_labels = [guild_details.get(guild_id, guild_id) for guild_id in record.guild_ids]

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
        guild_details = await self._fetch_guild_details(guild_ids)

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
                colour=discord.Colour.red(),
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

        details = await self._fetch_guild_details([gid for gid in guild_ids if isinstance(gid, str)])
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
                colour=discord.Colour.red(),
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

        details = await self._fetch_guild_details(guild_ids)
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
                id_matches = [
                    gid for gid in guild_labels if needle in gid.lower()
                ]
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

    @api_keys.command(name="add", description="Add a new Guild Wars 2 API key.")
    @app_commands.describe(key="Your Guild Wars 2 API key")
    async def add_api_key(self, interaction: discord.Interaction, key: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            return

        key_clean = key.strip()
        if not key_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide an API key.",
                colour=discord.Colour.red(),
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
            description: str, *, colour: discord.Colour = discord.Colour.blurple()
        ) -> discord.Embed:
            embed = self._embed(
                title="API key verification",
                description=description,
                colour=colour,
            )
            embed.add_field(name="Verification steps", value=_steps_value(), inline=False)
            return embed

        async def _refresh_progress(description: str, *, colour: discord.Colour = discord.Colour.blurple()) -> None:
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
                "You have already saved this API key. Use `/apikey update` if you need to refresh it.",
                colour=discord.Colour.red(),
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
            await _refresh_progress(str(exc), colour=discord.Colour.red())
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
                colour=discord.Colour.red(),
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

        embed = self._embed(
            title="API key saved",
            description="Verification completed. Your key was stored and roles were synced.",
        )
        embed.add_field(name="Verification steps", value=_steps_value(), inline=False)
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

        guild_labels = [guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
        embed.add_field(
            name="Guild memberships",
            value=self._format_list(guild_labels, placeholder="No guilds found"),
            inline=False,
        )

        embed.add_field(
            name="Characters",
            value=self._format_list(characters, placeholder="No characters found"),
            inline=False,
        )

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

        embed.add_field(
            name="Role sync",
            value=self._format_list(role_lines, placeholder="No role changes"),
            inline=False,
        )

        embed.add_field(name="API key", value=f"```{key_clean}```", inline=False)

        await interaction.edit_original_response(embed=embed)

    @api_keys.command(name="update", description="Update or rename a stored API key.")
    @app_commands.describe(name="Existing key name")
    async def update_api_key(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            return

        name_clean = name.strip()
        if not name_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide a key name to update.",
                colour=discord.Colour.red(),
            )
            return

        existing_keys = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        record = self._find_existing_name(existing_keys, name_clean)
        if not record:
            await self._send_embed(
                interaction,
                title="API key",
                description="No stored key found with that name. Use /apikey list to see saved keys.",
                colour=discord.Colour.red(),
            )
            return

        await interaction.response.send_modal(UpdateApiKeyModal(self, interaction, record, existing_keys))

    @update_api_key.autocomplete("name")
    async def update_api_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._autocomplete_key_names(interaction, current)


    @api_keys.command(name="remove", description="Delete a stored API key.")
    @app_commands.describe(name="Name of the key to delete")
    async def remove_api_key(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            return

        name_clean = name.strip()
        if not name_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide the name of the key to delete.",
                colour=discord.Colour.red(),
            )
            return

        record = self.bot.storage.find_api_key(interaction.guild.id, interaction.user.id, name_clean)
        if not record:
            await self._send_embed(
                interaction,
                title="API key",
                description="No stored key found with that name. Use /apikey list to see saved keys.",
                colour=discord.Colour.red(),
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
        guild_detail_map = await self._fetch_guild_details(lost_guilds) if lost_guilds else {}
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
                colour=discord.Colour.red(),
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
            did_match, matched_guilds, matched_roles = self._match_member_filters(
                filters, member, record, guild_labels
            )
            if did_match:
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
            files = [discord.File(fp=io.BytesIO(buffer.getvalue().encode("utf-8")), filename="member_query.csv")]

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


class UpdateApiKeyModal(discord.ui.Modal, title="Update API key"):
    """Modal that captures a new API key and optional name."""

    new_key: discord.ui.TextInput[discord.ui.Modal]
    new_name: discord.ui.TextInput[discord.ui.Modal]

    def __init__(
        self,
        cog: AccountsCog,
        interaction: discord.Interaction,
        record: ApiKeyRecord,
        existing_records: Sequence[ApiKeyRecord],
    ) -> None:
        super().__init__()
        self.cog = cog
        self.record = record
        self.existing_records = existing_records

        self.new_key = discord.ui.TextInput(
            label="New API key",
            placeholder="Enter the new Guild Wars 2 API key",
            required=True,
            style=discord.TextStyle.paragraph,
            min_length=5,
        )
        self.new_name = discord.ui.TextInput(
            label="New name (optional)",
            placeholder="Leave blank to keep current name",
            default=record.name,
            required=False,
            max_length=100,
        )
        self.add_item(self.new_key)
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - Discord callback
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            embed = self.cog._embed(
                title="API key",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        key_clean = self.new_key.value.strip()
        name_clean = (self.new_name.value or self.record.name).strip()
        if not name_clean:
            embed = self.cog._embed(
                title="API key",
                description="Please provide a valid name.",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if name_clean.lower() != self.record.name.lower() and self.cog._find_existing_name(
            self.existing_records, name_clean
        ):
            embed = self.cog._embed(
                title="API key",
                description="You already have a key with that new name. Choose another.",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        duplicate_key = next(
            (
                existing
                for existing in self.existing_records
                if existing.key == key_clean and existing.name.lower() != self.record.name.lower()
            ),
            None,
        )
        if duplicate_key:
            embed = self.cog._embed(
                title="API key",
                description="You already have another saved API key with this value.",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            (
                permissions,
                guild_ids,
                guild_details,
                account_name,
                _,
                characters,
            ) = await self.cog._validate_api_key(key_clean)
        except ValueError as exc:
            embed = self.cog._embed(
                title="API key validation failed",
                description=str(exc),
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        updated_record = ApiKeyRecord(
            name=name_clean,
            key=key_clean,
            account_name=account_name,
            permissions=permissions,
            guild_ids=guild_ids,
            characters=characters,
            created_at=self.record.created_at,
            updated_at=utcnow(),
        )
        self.cog.bot.storage.upsert_api_key(guild.id, user.id, updated_record)

        added, removed, error = await self.cog._sync_roles(guild, user)

        embed = self.cog._embed(
            title="API key updated",
            description="Your key details were refreshed and roles resynced.",
        )
        embed.add_field(
            name="Account",
            value=self.cog._format_list([f"Key name: `{name_clean}`", f"Account name: {account_name}"]),
            inline=True,
        )
        embed.add_field(
            name="Permissions",
            value=self.cog._format_list(sorted(permissions), placeholder="No permissions"),
            inline=True,
        )

        guild_labels = [guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
        embed.add_field(
            name="Guild memberships",
            value=self.cog._format_list(guild_labels, placeholder="No guilds found"),
            inline=False,
        )

        embed.add_field(
            name="Characters",
            value=self.cog._format_list(characters, placeholder="No characters found"),
            inline=False,
        )

        role_lines: List[str] = []
        if added:
            role_lines.append("Added: " + ", ".join(role.mention for role in added))
        if removed:
            role_lines.append("Removed: " + ", ".join(role.mention for role in removed))
        if error:
            role_lines.append(error)

        embed.add_field(
            name="Role sync",
            value=self.cog._format_list(role_lines, placeholder="No role changes"),
            inline=False,
        )

        embed.add_field(
            name="API key",
            value=f"```{self.cog._mask_key(self.record.key)} → {self.cog._mask_key(key_clean)}```",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AccountsCog(bot), override=True)

