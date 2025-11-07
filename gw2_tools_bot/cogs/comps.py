"""Guild composition scheduling and signup management."""
from __future__ import annotations

import asyncio
import calendar
import io
import logging
import os
import re
from datetime import datetime, time as time_cls, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import constants
from ..bot import GW2ToolsBot
from ..storage import CompClassConfig, CompConfig, GuildConfig

LOGGER = logging.getLogger(__name__)

WIKI_ICON_PATH = constants.MEDIA_PATH / "gw2wikiicons"
SELECT_CUSTOM_ID_PREFIX = "gw2tools:comp:signup"

DAY_LOOKUP: Dict[str, int] = {}
for index, name in enumerate(calendar.day_name):
    DAY_LOOKUP[name.lower()] = index
    DAY_LOOKUP[name[:3].lower()] = index


def _get_day_name(index: Optional[int]) -> str:
    if index is None or not 0 <= index <= 6:
        return "Not scheduled"
    return calendar.day_name[index]


def _parse_day(value: str) -> Optional[int]:
    key = value.strip().lower()
    if not key:
        return None
    return DAY_LOOKUP.get(key)


def _parse_time(value: str) -> Optional[time_cls]:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.time()


def _resolve_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone: {value}") from None


def _icon_path_for_class(name: str) -> Optional[str]:
    candidates: List[str] = []
    stripped = re.sub(r"[^A-Za-z]", "", name)
    if stripped:
        candidates.append(stripped)
    tokens = re.split(r"\s+", name.replace("-", " "))
    for token in reversed(tokens):
        token_clean = re.sub(r"[^A-Za-z]", "", token)
        if token_clean:
            candidates.append(token_clean)
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = WIKI_ICON_PATH / f"{candidate}.png"
        if path.exists():
            return str(path)
    return None


def _emoji_name_for_class(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]", "", name)
    if len(cleaned) < 2:
        cleaned = "Class"
    return cleaned[:32]


def _load_icon_bytes(name: str) -> Optional[bytes]:
    icon_path = _icon_path_for_class(name)
    if not icon_path:
        return None
    try:
        with Image.open(icon_path) as source:
            image = source.convert("RGBA")
    except (FileNotFoundError, OSError):
        return None

    image.thumbnail((96, 96))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _format_signups(guild: discord.Guild, signups: Sequence[int]) -> str:
    if not signups:
        return "\u200b"

    lines: List[str] = []
    for user_id in signups[:15]:
        member = guild.get_member(user_id)
        if member:
            lines.append(f"• {discord.utils.escape_markdown(member.display_name)}")
        else:
            lines.append(f"• <@{user_id}>")
    remaining = len(signups) - len(lines)
    if remaining > 0:
        lines.append(f"…and {remaining} more")
    return "\n".join(lines)


def _sanitize_signups(config: CompConfig) -> None:
    valid_names = {entry.name for entry in config.classes}
    config.signups = {name: users for name, users in config.signups.items() if name in valid_names}
    for entry in config.classes:
        config.signups.setdefault(entry.name, [])


OVERVIEW_TOKEN_RE = re.compile(r"(?<!<):([0-9A-Za-z][0-9A-Za-z _-]{0,30}):")


class CompSignupView(discord.ui.View):
    def __init__(
        self,
        cog: "CompCog",
        guild_id: int,
        *,
        channel: Optional[discord.abc.GuildChannel] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.add_item(CompSignupSelect(self))


class CompSignupSelect(discord.ui.Select):
    def __init__(self, view: CompSignupView):
        self.comp_view = view
        config = view.cog.bot.get_config(view.guild_id).comp
        _sanitize_signups(config)
        options: List[discord.SelectOption] = []
        guild = view.cog.bot.get_guild(view.guild_id)
        channel = view.channel
        if channel is None and guild and config.channel_id:
            channel = guild.get_channel(config.channel_id)
        for entry in config.classes:
            description = "Sign up for this class"
            if entry.required is not None:
                description = f"{entry.required} needed"
            emoji = None
            if guild:
                emoji = view.cog._get_class_emoji(
                    entry, guild=guild, channel=channel
                )
            options.append(
                discord.SelectOption(
                    label=entry.name,
                    value=entry.name,
                    description=description,
                    emoji=emoji,
                )
            )

        disabled = not options
        super().__init__(
            placeholder="Select a class to sign up (select again to remove)",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No classes configured", value="__none")],
            custom_id=f"{SELECT_CUSTOM_ID_PREFIX}:{view.guild_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        config = self.comp_view.cog.bot.get_config(self.comp_view.guild_id)
        comp_config = config.comp
        _sanitize_signups(comp_config)
        if not comp_config.classes:
            await interaction.response.send_message("No classes are configured for signups yet.", ephemeral=True)
            return

        selection = self.values[0]
        if selection == "__none":
            await interaction.response.send_message("There are no classes to sign up for yet.", ephemeral=True)
            return

        user_id = interaction.user.id
        current = None
        for name, users in comp_config.signups.items():
            if user_id in users:
                current = name
                break

        removed = False
        if current == selection:
            comp_config.signups[selection].remove(user_id)
            removed = True
        else:
            if current:
                comp_config.signups[current].remove(user_id)
            signups = comp_config.signups.setdefault(selection, [])
            if user_id not in signups:
                signups.append(user_id)

        self.comp_view.cog.bot.save_config(self.comp_view.guild_id, config)
        await self.comp_view.cog.refresh_signup_message(self.comp_view.guild_id)

        if removed:
            await interaction.response.send_message(f"Removed you from **{selection}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Signed you up for **{selection}**.", ephemeral=True)


class CompChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "CompConfigView", default_channel: Optional[discord.abc.GuildChannel]):
        super().__init__(
            placeholder="Select the channel for scheduled compositions",
            channel_types=(discord.ChannelType.text, discord.ChannelType.news),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        comp_config = self.config_view.config.comp
        if self.values:
            channel = self.values[0]
            comp_config.channel_id = channel.id
            message = f"Composition posts will be sent to {channel.mention}."
        else:
            comp_config.channel_id = None
            message = "Composition posting channel cleared."
        self.config_view.persist()
        await self.config_view.refresh_summary(interaction)
        await interaction.response.send_message(message, ephemeral=True)


class ScheduleModal(discord.ui.Modal):
    def __init__(self, view: "CompConfigView") -> None:
        super().__init__(title="Configure posting schedule")
        comp_config = view.config.comp
        default_day = _get_day_name(comp_config.post_day) if comp_config.post_day is not None else ""
        self.day_input = discord.ui.TextInput(
            label="Day of week",
            placeholder="Monday",
            default=default_day if comp_config.post_day is not None else "",
            required=False,
        )
        self.time_input = discord.ui.TextInput(
            label="Time (24h HH:MM)",
            placeholder="19:30",
            default=comp_config.post_time or "",
            required=False,
        )
        self.tz_input = discord.ui.TextInput(
            label="Timezone (IANA name)",
            placeholder="UTC",
            default=comp_config.timezone or "UTC",
            required=True,
        )
        self.config_view = view
        self.add_item(self.day_input)
        self.add_item(self.time_input)
        self.add_item(self.tz_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        day_value = str(self.day_input.value).strip()
        time_value = str(self.time_input.value).strip()
        tz_value = str(self.tz_input.value).strip() or "UTC"

        comp_config = self.config_view.config.comp

        try:
            _resolve_timezone(tz_value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if not day_value and not time_value:
            comp_config.post_day = None
            comp_config.post_time = None
            comp_config.timezone = tz_value or "UTC"
            self.config_view.persist()
            await self.config_view.refresh_summary(interaction)
            await interaction.response.send_message("Posting schedule cleared.", ephemeral=True)
            return

        if not day_value or not time_value:
            await interaction.response.send_message(
                "Please provide both a day of the week and time, or leave both blank to disable scheduling.",
                ephemeral=True,
            )
            return

        day_index = _parse_day(day_value)
        if day_index is None:
            await interaction.response.send_message(
                "Unrecognised day of the week. Try values like Monday, Tue, Friday, etc.",
                ephemeral=True,
            )
            return

        parsed_time = _parse_time(time_value)
        if not parsed_time:
            await interaction.response.send_message("Time must be provided in HH:MM 24-hour format.", ephemeral=True)
            return

        comp_config.post_day = day_index
        comp_config.post_time = parsed_time.strftime("%H:%M")
        comp_config.timezone = tz_value
        self.config_view.persist()
        await self.config_view.refresh_summary(interaction)
        await interaction.response.send_message(
            f"Schedule set to {_get_day_name(day_index)} at {comp_config.post_time} {tz_value}.",
            ephemeral=True,
        )


class OverviewModal(discord.ui.Modal):
    def __init__(self, view: "CompConfigView") -> None:
        super().__init__(title="Configure composition overview")
        comp_config = view.config.comp
        self.overview_input = discord.ui.TextInput(
            label="Overview (shown above class signups)",
            style=discord.TextStyle.paragraph,
            placeholder="Example: Tank: Firebrand, Heal: Druid, Support: Chrono",
            default=comp_config.overview,
            required=False,
            max_length=1024,
        )
        self.config_view = view
        self.add_item(self.overview_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        comp_config = self.config_view.config.comp
        comp_config.overview = str(self.overview_input.value).strip()
        self.config_view.persist()
        await self.config_view.cog.refresh_signup_message(self.config_view.guild.id)
        await self.config_view.refresh_summary(interaction)
        message = "Composition overview updated."
        if not comp_config.overview:
            message = "Composition overview cleared."
        await interaction.response.send_message(message, ephemeral=True)


class ClassesModal(discord.ui.Modal):
    def __init__(self, view: "CompConfigView") -> None:
        super().__init__(title="Configure composition classes")
        comp_config = view.config.comp
        default_value = "\n".join(
            f"{entry.name}={entry.required}" if entry.required is not None else entry.name
            for entry in comp_config.classes
        )
        self.entries = discord.ui.TextInput(
            label="Classes (one per line, optional =count)",
            style=discord.TextStyle.paragraph,
            placeholder="Firebrand=2\nMechanist\nChronomancer=3",
            default=default_value,
            required=False,
        )
        self.config_view = view
        self.add_item(self.entries)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        lines = str(self.entries.value).splitlines()
        classes: List[CompClassConfig] = []
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            if "=" in stripped:
                name_part, count_part = stripped.split("=", 1)
                name = name_part.strip()
                count_part = count_part.strip()
                if not name:
                    continue
                if not count_part:
                    required_value: Optional[int] = None
                else:
                    if not count_part.isdigit():
                        await interaction.response.send_message(
                            f"'{count_part}' is not a valid number for {name}.",
                            ephemeral=True,
                        )
                        return
                    required_value = int(count_part)
            else:
                name = stripped
                required_value = None

            if not _icon_path_for_class(name):
                await interaction.response.send_message(
                    f"No icon found for '{name}'. Check the spelling against the GW2 Wiki class names.",
                    ephemeral=True,
                )
                return

            classes.append(CompClassConfig(name=name, required=required_value))

        comp_config = self.config_view.config.comp
        comp_config.classes = classes
        _sanitize_signups(comp_config)
        channel = None
        if comp_config.channel_id:
            channel = self.config_view.guild.get_channel(comp_config.channel_id)
        await self.config_view.cog.ensure_class_emojis(
            self.config_view.guild, comp_config, channel=channel
        )
        self.config_view.persist()
        await self.config_view.cog.refresh_signup_message(self.config_view.guild.id)
        await self.config_view.refresh_summary(interaction)
        await interaction.response.send_message("Classes updated.", ephemeral=True)


class PostNowButton(discord.ui.Button["CompConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.success, label="Post now")

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not view:
            return
        comp_config = view.config.comp
        if not comp_config.channel_id:
            await interaction.response.send_message("Set a channel before posting the composition.", ephemeral=True)
            return
        if not comp_config.classes:
            await interaction.response.send_message("Configure at least one class before posting.", ephemeral=True)
            return
        await view.cog.post_composition(
            view.guild.id, reset_signups=False, force_new_message=True
        )
        await interaction.response.send_message("Composition post updated.", ephemeral=True)


class CloseButton(discord.ui.Button["CompConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Close")

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if self.view and isinstance(self.view, CompConfigView):
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(content="Composition configuration closed.", view=None)
            self.view.stop()


class CompConfigView(discord.ui.View):
    def __init__(self, cog: "CompCog", guild: discord.Guild, config: GuildConfig):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config
        comp_config = config.comp
        default_channel = guild.get_channel(comp_config.channel_id) if comp_config.channel_id else None
        self.add_item(CompChannelSelect(self, default_channel))
        self.add_item(discord.ui.Button(label="Edit schedule", style=discord.ButtonStyle.primary, custom_id="comp_schedule"))
        self.children[-1].callback = self._schedule_callback  # type: ignore[assignment]
        self.add_item(discord.ui.Button(label="Edit overview", style=discord.ButtonStyle.primary, custom_id="comp_overview"))
        self.children[-1].callback = self._overview_callback  # type: ignore[assignment]
        self.add_item(discord.ui.Button(label="Edit classes", style=discord.ButtonStyle.primary, custom_id="comp_classes"))
        self.children[-1].callback = self._classes_callback  # type: ignore[assignment]
        self.add_item(PostNowButton())
        self.add_item(CloseButton())
        self.message: Optional[discord.InteractionMessage] = None

    def persist(self) -> None:
        self.cog.bot.save_config(self.guild.id, self.config)

    async def refresh_summary(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not self.message:
            if interaction.message:
                self.message = interaction.message  # type: ignore[assignment]
            else:
                try:
                    self.message = await interaction.original_response()
                except discord.HTTPException:
                    return
        embed = self.cog.build_summary_embed(self.guild, self.config.comp)
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            LOGGER.warning("Failed to update composition configuration summary for guild %s", self.guild.id)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _schedule_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ScheduleModal(self))

    async def _overview_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(OverviewModal(self))

    async def _classes_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ClassesModal(self))


class CompCog(commands.GroupCog, name="comp"):
    """Schedule and manage guild composition signups."""

    POST_CHECK_INTERVAL_MINUTES = 1

    def __init__(self, bot: GW2ToolsBot) -> None:
        super().__init__()
        self.bot = bot
        self.poster_loop.start()
        self._view_init_task: asyncio.Task[None] | None = None
        env_home = os.getenv("GW2TOOLS_EMOJI_GUILD_ID")
        try:
            self.emoji_home_guild_id: Optional[int] = int(env_home) if env_home else None
        except ValueError:
            LOGGER.warning("Invalid GW2TOOLS_EMOJI_GUILD_ID value '%s'", env_home)
            self.emoji_home_guild_id = None
        self._emoji_creation_failures: Set[int] = set()

    async def cog_load(self) -> None:
        await super().cog_load()
        if self._view_init_task is None or self._view_init_task.done():
            self._view_init_task = self.bot.loop.create_task(self._register_all_persistent_views())

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self.poster_loop.cancel()
        if self._view_init_task is not None:
            self._view_init_task.cancel()
            self._view_init_task = None

    async def _register_all_persistent_views(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._register_persistent_view(guild.id)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:  # pragma: no cover - requires Discord
        await self._register_persistent_view(guild.id)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:  # pragma: no cover - requires Discord
        await self._register_persistent_view(guild.id)

    @app_commands.command(name="manage", description="Configure the scheduled composition post for this guild.")
    async def manage(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        view = CompConfigView(self, interaction.guild, config)
        embed = self.build_summary_embed(interaction.guild, config.comp)
        await interaction.response.send_message(
            "Use the controls below to configure the weekly composition post.",
            embed=embed,
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @tasks.loop(minutes=POST_CHECK_INTERVAL_MINUTES)
    async def poster_loop(self) -> None:  # pragma: no cover - relies on Discord
        if not self.bot.guilds:
            return
        for guild in self.bot.guilds:
            await self._maybe_post_for_guild(guild)

    @poster_loop.before_loop
    async def before_poster_loop(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def _maybe_post_for_guild(self, guild: discord.Guild) -> None:
        config = self.bot.get_config(guild.id)
        comp_config = config.comp
        if not (comp_config.channel_id and comp_config.post_day is not None and comp_config.post_time):
            return
        if not comp_config.classes:
            return

        try:
            tz = _resolve_timezone(comp_config.timezone or "UTC")
        except ValueError:
            LOGGER.warning("Invalid timezone '%s' for guild %s", comp_config.timezone, guild.id)
            return

        now = datetime.now(tz)
        if now.weekday() != comp_config.post_day:
            return

        target_time = _parse_time(comp_config.post_time)
        if not target_time:
            return

        if now.hour != target_time.hour or now.minute != target_time.minute:
            return

        last_post_at = comp_config.last_post_at
        if last_post_at:
            try:
                last_post_dt = datetime.fromisoformat(last_post_at)
            except ValueError:
                last_post_dt = None
            if last_post_dt is not None:
                last_local = last_post_dt.astimezone(tz)
                if (
                    last_local.weekday() == comp_config.post_day
                    and last_local.hour == target_time.hour
                    and last_local.minute == target_time.minute
                    and last_local.date() == now.date()
                ):
                    return

        await self.post_composition(guild.id, reset_signups=True)

    def _can_manage_emojis(self, guild: discord.Guild) -> bool:
        me = guild.me
        permissions = getattr(me, "guild_permissions", None) if me else None
        if permissions is None:
            return False
        return bool(
            getattr(permissions, "manage_emojis_and_stickers", False)
            or getattr(permissions, "manage_emojis", False)
        )

    def _has_emoji_capacity(self, guild: discord.Guild) -> bool:
        limit = getattr(guild, "emoji_limit", None)
        if limit is None:
            return True
        try:
            current = len(guild.emojis)
        except Exception:  # pragma: no cover - defensive
            return True
        return current < limit

    def _iter_emoji_host_guilds(self, primary: Optional[discord.Guild]) -> Iterable[discord.Guild]:
        seen: Set[int] = set()
        if self.emoji_home_guild_id:
            home = self.bot.get_guild(self.emoji_home_guild_id)
            if home is not None:
                seen.add(home.id)
                yield home
        if primary is not None and primary.id not in seen:
            seen.add(primary.id)
            yield primary
        for guild in self.bot.guilds:
            if guild.id in seen:
                continue
            seen.add(guild.id)
            yield guild

    def _can_use_external_emojis(
        self, guild: discord.Guild, channel: Optional[discord.abc.GuildChannel] = None
    ) -> bool:
        me = guild.me
        if me is None:
            return True
        permissions: Optional[discord.Permissions]
        try:
            if channel is not None:
                permissions = channel.permissions_for(me)
            else:
                permissions = getattr(me, "guild_permissions", None)
        except Exception:  # pragma: no cover - defensive
            permissions = None
        if permissions is None:
            return True
        return getattr(permissions, "use_external_emojis", True)

    async def ensure_class_emojis(
        self,
        guild: discord.Guild,
        comp_config: CompConfig,
        *,
        channel: Optional[discord.abc.GuildChannel] = None,
    ) -> bool:
        if not comp_config.classes:
            return False

        updated = False
        allow_external = self._can_use_external_emojis(guild, channel)
        available_by_name: Dict[str, discord.Emoji] = {}
        for emoji in self.bot.emojis:
            if not emoji.name:
                continue
            if emoji.guild_id == guild.id or allow_external:
                available_by_name[emoji.name] = emoji
        hosts = list(self._iter_emoji_host_guilds(guild))
        if not allow_external:
            hosts = [host for host in hosts if host.id == guild.id]
        for entry in comp_config.classes:
            emoji_obj: Optional[discord.Emoji] = None
            if entry.emoji_id:
                emoji_obj = self.bot.get_emoji(entry.emoji_id)
                if emoji_obj is None or (
                    emoji_obj.guild_id != guild.id and not allow_external
                ):
                    entry.emoji_id = None
                    emoji_obj = None
                    updated = True
            if emoji_obj:
                continue

            emoji_name = _emoji_name_for_class(entry.name)
            emoji_obj = available_by_name.get(emoji_name)
            if emoji_obj and (emoji_obj.guild_id == guild.id or allow_external):
                if entry.emoji_id != emoji_obj.id:
                    entry.emoji_id = emoji_obj.id
                    updated = True
                continue

            icon_bytes = _load_icon_bytes(entry.name)
            if not icon_bytes:
                continue

            created = False
            for host in hosts:
                if host.id in self._emoji_creation_failures:
                    continue
                if not self._can_manage_emojis(host):
                    continue
                if not self._has_emoji_capacity(host):
                    continue
                try:
                    new_emoji = await host.create_custom_emoji(
                        name=emoji_name,
                        image=icon_bytes,
                        reason="GW2 Tools composition class icon",
                    )
                except discord.Forbidden:
                    LOGGER.warning("Missing permissions to create emojis in guild %s", host.id)
                    self._emoji_creation_failures.add(host.id)
                    continue
                except discord.HTTPException as exc:
                    LOGGER.warning("Failed to create emoji '%s' in guild %s: %s", emoji_name, host.id, exc)
                    continue
                else:
                    entry.emoji_id = new_emoji.id
                    available_by_name[emoji_name] = new_emoji
                    updated = True
                    created = True
                    break

            if not created and entry.emoji_id is None:
                LOGGER.debug(
                    "No available emoji slot for class '%s' in guild %s", entry.name, guild.id
                )

        return updated

    def _get_class_emoji(
        self,
        entry: CompClassConfig,
        *,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.GuildChannel] = None,
    ) -> Optional[Union[discord.Emoji, discord.PartialEmoji]]:
        if guild is None:
            return None

        allow_external = self._can_use_external_emojis(guild, channel)
        if entry.emoji_id:
            emoji = self.bot.get_emoji(entry.emoji_id)
            if emoji and (emoji.guild_id == guild.id or allow_external):
                return emoji
            guild_emoji = discord.utils.get(guild.emojis, id=entry.emoji_id)
            if guild_emoji:
                return guild_emoji
            if allow_external and entry.emoji_id:
                return discord.PartialEmoji(
                    name=_emoji_name_for_class(entry.name), id=entry.emoji_id
                )
            return None

        emoji_name = _emoji_name_for_class(entry.name)
        if allow_external:
            emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
            if emoji:
                return emoji
        else:
            emoji = discord.utils.get(guild.emojis, name=emoji_name)
            if emoji:
                return emoji
        return None

    async def post_composition(
        self,
        guild_id: int,
        *,
        reset_signups: bool,
        force_new_message: bool = False,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        config = self.bot.get_config(guild_id)
        comp_config = config.comp
        if not comp_config.channel_id:
            return

        channel = guild.get_channel(comp_config.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(comp_config.channel_id)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Unable to fetch composition channel %s in guild %s", comp_config.channel_id, guild_id)
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            LOGGER.warning("Composition channel %s in guild %s is not a text channel", comp_config.channel_id, guild_id)
            return

        _sanitize_signups(comp_config)

        if reset_signups:
            comp_config.signups = {entry.name: [] for entry in comp_config.classes}

        emoji_updated = await self.ensure_class_emojis(
            guild, comp_config, channel=channel
        )
        if emoji_updated:
            self.bot.save_config(guild_id, config)

        embed = self._build_comp_embed(guild, comp_config, channel=channel)
        view = CompSignupView(self, guild_id, channel=channel)

        message = None
        if comp_config.message_id:
            try:
                message = await channel.fetch_message(comp_config.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if force_new_message and message is not None:
            try:
                await message.edit(view=None)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.debug(
                    "Failed to clear view on previous comp message %s in guild %s",
                    comp_config.message_id,
                    guild_id,
                )
            message = None

        if message is None:
            try:
                new_message = await channel.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to send composition message in guild %s", guild_id)
                return
            comp_config.message_id = new_message.id
        else:
            try:
                await message.edit(embed=embed, view=view)
                new_message = message
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to update composition message in guild %s", guild_id)
                return

        comp_config.last_post_at = datetime.now(timezone.utc).isoformat()
        self.bot.save_config(guild_id, config)
        self.bot.add_view(view, message_id=comp_config.message_id)

    async def refresh_signup_message(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        config = self.bot.get_config(guild_id)
        comp_config = config.comp
        if not (comp_config.channel_id and comp_config.message_id):
            return
        _sanitize_signups(comp_config)
        channel = guild.get_channel(comp_config.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(comp_config.channel_id)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Unable to fetch composition channel %s in guild %s", comp_config.channel_id, guild_id)
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(comp_config.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            comp_config.message_id = None
            self.bot.save_config(guild_id, config)
            return

        emoji_updated = await self.ensure_class_emojis(guild, comp_config, channel=channel)
        if emoji_updated:
            self.bot.save_config(guild_id, config)

        embed = self._build_comp_embed(guild, comp_config, channel=channel)
        view = CompSignupView(self, guild_id, channel=channel)
        try:
            await message.edit(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Failed to refresh composition message in guild %s", guild_id)
            return
        self.bot.add_view(view, message_id=message.id)

    async def _register_persistent_view(self, guild_id: int) -> None:
        config = self.bot.get_config(guild_id)
        comp_config = config.comp
        if comp_config.message_id:
            _sanitize_signups(comp_config)
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = (
                    guild.get_channel(comp_config.channel_id)
                    if comp_config.channel_id
                    else None
                )
                emoji_updated = await self.ensure_class_emojis(
                    guild, comp_config, channel=channel
                )
                if emoji_updated:
                    self.bot.save_config(guild_id, config)
            view = CompSignupView(self, guild_id, channel=channel)
            self.bot.add_view(view, message_id=comp_config.message_id)

    def _format_overview_text(
        self,
        overview: str,
        comp_config: CompConfig,
        *,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.GuildChannel] = None,
    ) -> str:
        if not overview or guild is None:
            return overview

        if channel is None and comp_config.channel_id:
            channel = guild.get_channel(comp_config.channel_id)

        emoji_lookup: Dict[str, str] = {}
        for entry in comp_config.classes:
            emoji_obj = self._get_class_emoji(entry, guild=guild, channel=channel)
            if not emoji_obj:
                continue
            emoji_text = str(emoji_obj)
            key_variants = {
                entry.name.casefold(),
                _emoji_name_for_class(entry.name).casefold(),
                re.sub(r"[^0-9a-z]", "", entry.name.casefold()),
            }
            for key in key_variants:
                if key:
                    emoji_lookup[key] = emoji_text

        if not emoji_lookup:
            return overview

        def replace(match: re.Match[str]) -> str:
            token = match.group(1)
            if not token:
                return match.group(0)
            lowered = token.casefold()
            normalized = re.sub(r"[^0-9a-z]", "", lowered)
            for candidate in (lowered, normalized):
                if candidate and candidate in emoji_lookup:
                    return emoji_lookup[candidate]
            return match.group(0)

        return OVERVIEW_TOKEN_RE.sub(replace, overview)

    def build_summary_embed(self, guild: discord.Guild, config: CompConfig) -> discord.Embed:
        embed = discord.Embed(title="Guild Composition Settings", color=discord.Color.blurple())
        schedule_text = "Posting schedule not configured."
        if config.post_day is not None and config.post_time:
            schedule_text = (
                f"Scheduled for **{_get_day_name(config.post_day)}** at **{config.post_time}** {config.timezone}."
            )
        if config.channel_id:
            channel = guild.get_channel(config.channel_id)
            if channel:
                channel_value = channel.mention
            else:
                channel_value = f"<#{config.channel_id}>"
        else:
            channel = None
            channel_value = "Not set"
        embed.add_field(name="Post Channel", value=channel_value, inline=False)
        embed.add_field(name="Schedule", value=schedule_text, inline=False)

        if config.overview:
            overview_text = self._format_overview_text(
                config.overview,
                config,
                guild=guild,
                channel=channel,
            )
            embed.add_field(name="Composition Overview", value=overview_text, inline=False)
        else:
            embed.add_field(name="Composition Overview", value="No overview set.", inline=False)

        if config.classes:
            lines = []
            for entry in config.classes:
                if entry.required is None:
                    lines.append(f"• {entry.name}")
                else:
                    lines.append(f"• {entry.name} — {entry.required}")
            embed.add_field(name="Configured Classes", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Configured Classes", value="No classes configured.", inline=False)
        return embed

    def _build_comp_embed(
        self,
        guild: discord.Guild,
        comp_config: CompConfig,
        *,
        channel: Optional[discord.abc.GuildChannel] = None,
    ) -> discord.Embed:
        if channel is None and comp_config.channel_id:
            resolved_channel = guild.get_channel(comp_config.channel_id)
            channel = resolved_channel
        embed = discord.Embed(title="Guild Composition Signup", color=discord.Color.dark_teal())
        if comp_config.post_day is not None and comp_config.post_time:
            embed.description = (
                f"Scheduled for **{_get_day_name(comp_config.post_day)}** at **{comp_config.post_time}** {comp_config.timezone}.\n"
                "Select your class using the dropdown below."
            )
        else:
            embed.description = "Select your class using the dropdown below."

        if comp_config.overview:
            overview_text = self._format_overview_text(
                comp_config.overview,
                comp_config,
                guild=guild,
                channel=channel,
            )
            embed.add_field(
                name="Composition Overview",
                value=overview_text,
                inline=False,
            )

        for entry in comp_config.classes:
            signups = comp_config.signups.get(entry.name, [])
            emoji = self._get_class_emoji(entry, guild=guild, channel=channel)
            prefix = f"{emoji} " if emoji else ""
            current_total = len(signups)
            if entry.required is not None:
                title = f"{prefix}{entry.name} ({current_total}/{entry.required})"
            else:
                title = f"{prefix}{entry.name} ({current_total})"
            embed.add_field(name=title, value=_format_signups(guild, signups), inline=True)

        embed.set_footer(text="Select the dropdown again to remove yourself from a class.")
        embed.timestamp = discord.utils.utcnow()
        return embed


async def setup(bot: GW2ToolsBot) -> None:  # pragma: no cover - discord.py lifecycle
    await bot.add_cog(CompCog(bot))
