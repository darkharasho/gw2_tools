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
            await interaction.response.send_message("Please provide a guild name to search for.", ephemeral=True)
            return

        try:
            guild_ids = await self._fetch_json(
                "https://api.guildwars2.com/v2/guild/search", params={"name": query}
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if not isinstance(guild_ids, list) or not guild_ids:
            await interaction.response.send_message(
                f"No guilds found matching `{query}`.", ephemeral=True
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
        await interaction.response.send_message(message, ephemeral=True)

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
            await interaction.response.send_message("Please provide a valid guild ID.", ephemeral=True)
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        config.guild_role_ids[cleaned_guild_id] = role.id
        self.bot.save_config(interaction.guild.id, config)  # type: ignore[union-attr]
        await interaction.response.send_message(
            f"Members of `{cleaned_guild_id}` will receive the {role.mention} role when their API key is verified.",
            ephemeral=True,
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
            await interaction.response.send_message(
                f"Removed mapping for guild `{guild_id}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No mapping found for guild `{guild_id}`.", ephemeral=True
            )

    @guild_roles.command(name="list", description="List all configured guild role mappings.")
    async def list_guild_roles(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)  # type: ignore[union-attr]
        if not config.guild_role_ids:
            await interaction.response.send_message(
                "No guild role mappings configured. Use /guildroles set to add one.",
                ephemeral=True,
            )
            return

        lines = []
        for guild_id, role_id in config.guild_role_ids.items():
            role = interaction.guild.get_role(role_id) if interaction.guild else None
            role_label = role.mention if role else f"role ID {role_id}"
            lines.append(f"`{guild_id}` -> {role_label}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

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

    @api_keys.command(name="add", description="Add a new Guild Wars 2 API key.")
    @app_commands.describe(name="Friendly name for this key", key="Your Guild Wars 2 API key")
    async def add_api_key(self, interaction: discord.Interaction, name: str, key: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        name_clean = name.strip()
        key_clean = key.strip()
        if not name_clean or not key_clean:
            await interaction.response.send_message("Please provide both a name and API key.", ephemeral=True)
            return

        existing_keys = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        if self._find_existing_name(existing_keys, name_clean):
            await interaction.response.send_message(
                "You already have a key with that name. Use /apikey update to change it.",
                ephemeral=True,
            )
            return

        try:
            permissions, guild_ids, guild_details, account_name = await self._validate_api_key(key_clean)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
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

        await interaction.response.send_message("\n".join(summary_lines), ephemeral=True)

    @api_keys.command(name="update", description="Update or rename a stored API key.")
    @app_commands.describe(
        name="Existing key name",
        key="New Guild Wars 2 API key",
        new_name="Optional new name for this key",
    )
    async def update_api_key(
        self, interaction: discord.Interaction, name: str, key: str, new_name: Optional[str] = None
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        name_clean = name.strip()
        key_clean = key.strip()
        if not name_clean or not key_clean:
            await interaction.response.send_message("Please provide a key name and API key.", ephemeral=True)
            return

        existing_keys = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        record = self._find_existing_name(existing_keys, name_clean)
        if not record:
            await interaction.response.send_message(
                "No stored key found with that name. Use /apikey list to see saved keys.",
                ephemeral=True,
            )
            return

        new_name_clean = new_name.strip() if new_name else record.name
        if not new_name_clean:
            await interaction.response.send_message("Please provide a valid new name.", ephemeral=True)
            return

        if new_name_clean.lower() != record.name.lower() and self._find_existing_name(
            existing_keys, new_name_clean
        ):
            await interaction.response.send_message(
                "You already have a key with that new name. Choose another.", ephemeral=True
            )
            return

        try:
            permissions, guild_ids, guild_details, account_name = await self._validate_api_key(key_clean)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        updated_record = ApiKeyRecord(
            name=new_name_clean,
            key=key_clean,
            permissions=permissions,
            guild_ids=guild_ids,
            created_at=record.created_at,
            updated_at=utcnow(),
        )
        self.bot.storage.upsert_api_key(interaction.guild.id, interaction.user.id, updated_record)

        added, removed, error = await self._sync_roles(interaction.guild, interaction.user)

        summary = [
            f"Updated API key {self._mask_key(record.key)} → {self._mask_key(key_clean)} for {account_name}.",
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

        await interaction.response.send_message("\n".join(summary), ephemeral=True)

    @api_keys.command(name="remove", description="Delete a stored API key.")
    @app_commands.describe(name="Name of the key to delete")
    async def remove_api_key(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        name_clean = name.strip()
        if not name_clean:
            await interaction.response.send_message("Please provide the name of the key to delete.", ephemeral=True)
            return

        record = self.bot.storage.find_api_key(interaction.guild.id, interaction.user.id, name_clean)
        if not record:
            await interaction.response.send_message(
                "No stored key found with that name. Use /apikey list to see saved keys.",
                ephemeral=True,
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

        await interaction.response.send_message("\n".join(summary), ephemeral=True)

    @api_keys.command(name="list", description="List your saved API keys.")
    async def list_api_keys(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        records = self.bot.storage.get_user_api_keys(interaction.guild.id, interaction.user.id)
        if not records:
            await interaction.response.send_message(
                "You have no saved keys. Use /apikey add to register one.",
                ephemeral=True,
            )
            return

        lines = []
        for record in records:
            roles = [guild_id for guild_id in record.guild_ids]
            lines.append(
                f"`{record.name}` ({self._mask_key(record.key)}) — guilds: {', '.join(roles) if roles else 'none'}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AccountsCog(bot))

