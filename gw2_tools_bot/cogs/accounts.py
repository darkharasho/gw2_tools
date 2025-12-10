from __future__ import annotations

import json
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot
from ..http_utils import read_response_text
from ..storage import ApiKeyRecord, utcnow

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

    async def _validate_api_key(
        self, api_key: str
    ) -> Tuple[List[str], List[str], Dict[str, str], str]:
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
        missing = self.REQUIRED_PERMISSIONS.difference(permissions_set)
        if missing:
            raise ValueError(
                "API key is missing required permissions: "
                + ", ".join(sorted(missing))
            )

        account = await self._fetch_json(
            "https://api.guildwars2.com/v2/account", api_key=api_key
        )
        account_name = account.get("name", "Unknown account")
        guild_ids_payload = account.get("guilds") or []
        guild_ids = sorted(
            {
                guild_id.strip()
                for guild_id in guild_ids_payload
                if isinstance(guild_id, str) and guild_id.strip()
            }
        )
        guild_details = await self._fetch_guild_details(guild_ids, api_key=api_key)
        return permissions, guild_ids, guild_details, account_name

    # ------------------------------------------------------------------
    # Role syncing
    # ------------------------------------------------------------------
    async def _sync_roles(
        self, guild: discord.Guild, member: discord.Member
    ) -> Tuple[List[discord.Role], List[discord.Role], Optional[str]]:
        config = self.bot.get_config(guild.id)
        if not config.guild_role_ids:
            return [], [], None

        guild_memberships: set[str] = set()
        for record in self.bot.storage.get_user_api_keys(guild.id, member.id):
            guild_memberships.update(record.guild_ids)

        desired_role_ids = {
            role_id for guild_id, role_id in config.guild_role_ids.items() if guild_id in guild_memberships
        }

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
        roles_to_remove = [role for role in mapped_roles if role.id not in desired_role_ids]

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
        lines = []
        for guild_id in guild_ids[:10]:
            if not isinstance(guild_id, str):
                continue
            label = details.get(guild_id, guild_id)
            lines.append(f"`{guild_id}` - {label}")

        message = "\n".join(lines) or "No details available for the matched guilds."
        await self._send_embed(
            interaction,
            title="Guild search results",
            description=message,
            use_followup=True,
        )

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
        cleaned_guild_id = guild_id.strip()
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
    @app_commands.describe(guild_id="Guild Wars 2 guild ID to remove")
    async def remove_guild_role(self, interaction: discord.Interaction, guild_id: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        removed = config.guild_role_ids.pop(guild_id, None)
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        if removed:
            await self._send_embed(
                interaction,
                title="Guild role mapping removed",
                description=f"Removed mapping for guild `{guild_id}`.",
            )
        else:
            await self._send_embed(
                interaction,
                title="Guild role mapping",
                description=f"No mapping found for guild `{guild_id}`.",
                colour=discord.Colour.red(),
            )

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

        lines = []
        for guild_id, role_id in config.guild_role_ids.items():
            role = interaction.guild.get_role(role_id) if interaction.guild else None
            role_label = role.mention if role else f"role ID {role_id}"
            lines.append(f"`{guild_id}` → {role_label}")

        await self._send_embed(
            interaction,
            title="Guild role mappings",
            description="\n".join(lines),
        )

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
        records = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        current_lower = current.lower()
        matches: List[app_commands.Choice[str]] = []
        for record in records:
            if current_lower in record.name.lower():
                matches.append(app_commands.Choice(name=record.name, value=record.name))
            if len(matches) >= 25:
                break
        return matches

    @api_keys.command(name="add", description="Add a new Guild Wars 2 API key.")
    @app_commands.describe(name="Friendly name for this key", key="Your Guild Wars 2 API key")
    async def add_api_key(self, interaction: discord.Interaction, name: str, key: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._send_embed(
                interaction,
                title="API key",
                description="This command can only be used in a server.",
                colour=discord.Colour.red(),
            )
            return

        name_clean = name.strip()
        key_clean = key.strip()
        if not name_clean or not key_clean:
            await self._send_embed(
                interaction,
                title="API key",
                description="Please provide both a name and API key.",
                colour=discord.Colour.red(),
            )
            return

        existing_keys = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        if self._find_existing_name(existing_keys, name_clean):
            await self._send_embed(
                interaction,
                title="API key",
                description="You already have a key with that name. Use /apikey update to change it.",
                colour=discord.Colour.red(),
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            permissions, guild_ids, guild_details, account_name = await self._validate_api_key(key_clean)
        except ValueError as exc:
            await self._send_embed(
                interaction,
                title="API key validation failed",
                description=str(exc),
                colour=discord.Colour.red(),
                use_followup=True,
            )
            return

        record = ApiKeyRecord(
            name=name_clean,
            key=key_clean,
            permissions=permissions,
            guild_ids=guild_ids,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.bot.storage.upsert_api_key(interaction.guild.id, interaction.user.id, record)

        added, removed, error = await self._sync_roles(interaction.guild, interaction.user)

        summary_lines = [
            f"Saved API key for {account_name} with permissions: {', '.join(sorted(permissions))}.",
        ]
        if guild_ids:
            names = [guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
            summary_lines.append("Guild memberships: " + ", ".join(names))
        else:
            summary_lines.append("No guild memberships were found on this account.")

        if added:
            summary_lines.append("Roles added: " + ", ".join(role.mention for role in added))
        if removed:
            summary_lines.append("Roles removed: " + ", ".join(role.mention for role in removed))
        if not added and not removed:
            summary_lines.append(
                "No configured guild role mappings matched your guild memberships. Ask a moderator to set them with /guildroles set."
            )
        if error:
            summary_lines.append(error)

        await self._send_embed(
            interaction,
            title="API key saved",
            description="\n".join(summary_lines),
            use_followup=True,
        )

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

        deleted = self.bot.storage.delete_api_key(
            interaction.guild.id, interaction.user.id, name_clean
        )
        added, removed, error = await self._sync_roles(interaction.guild, interaction.user)

        summary = [
            f"Deleted `{record.name}` ({self._mask_key(record.key)}).",
        ]
        if removed:
            summary.append("Roles removed: " + ", ".join(role.mention for role in removed))
        if added:
            summary.append("Roles added: " + ", ".join(role.mention for role in added))
        if error:
            summary.append(error)
        if not deleted:
            summary.append("No changes were made because the key could not be removed.")

        await self._send_embed(
            interaction,
            title="API key removed" if deleted else "API key",
            description="\n".join(summary),
        )

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

        lines = []
        for record in records:
            roles = [guild_id for guild_id in record.guild_ids]
            lines.append(
                f"`{record.name}` ({self._mask_key(record.key)}) — guilds: {', '.join(roles) if roles else 'none'}"
            )
        await self._send_embed(
            interaction,
            title="Your API keys",
            description="\n".join(lines),
        )


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

        try:
            permissions, guild_ids, guild_details, account_name = await self.cog._validate_api_key(key_clean)
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
            permissions=permissions,
            guild_ids=guild_ids,
            created_at=self.record.created_at,
            updated_at=utcnow(),
        )
        self.cog.bot.storage.upsert_api_key(guild.id, user.id, updated_record)

        added, removed, error = await self.cog._sync_roles(guild, user)

        summary = [
            f"Updated API key {self.cog._mask_key(self.record.key)} → {self.cog._mask_key(key_clean)} for {account_name}.",
        ]
        if guild_ids:
            names = [guild_details.get(guild_id, guild_id) for guild_id in guild_ids]
            summary.append("Guild memberships: " + ", ".join(names))
        if added:
            summary.append("Roles added: " + ", ".join(role.mention for role in added))
        if removed:
            summary.append("Roles removed: " + ", ".join(role.mention for role in removed))
        if error:
            summary.append(error)

        embed = self.cog._embed(title="API key updated", description="\n".join(summary))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AccountsCog(bot))

