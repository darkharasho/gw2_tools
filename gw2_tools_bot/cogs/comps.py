"""Guild composition scheduling and signup management."""
from __future__ import annotations

import asyncio
import calendar
import logging
import os
import re
import uuid
from datetime import datetime, time as time_cls, timezone, tzinfo, timedelta
from typing import Dict, List, Optional, Sequence, Tuple, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import constants
from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..storage import CompClassConfig, CompConfig, CompSchedule, CompPreset, GuildConfig, normalise_timezone

LOGGER = logging.getLogger(__name__)

# Common timezone abbreviations mapped to canonical IANA identifiers so users can
# supply friendly labels without memorising long region names.
TIMEZONE_ALIASES = {
    "ACDT": "Australia/Adelaide",
    "ACST": "Australia/Darwin",
    "AKDT": "America/Anchorage",
    "AKST": "America/Anchorage",
    "AST": "America/Halifax",
    "ADT": "America/Halifax",
    "BST": "Europe/London",
    "CDT": "America/Chicago",
    "CST": "America/Chicago",
    "CEST": "Europe/Paris",
    "CET": "Europe/Paris",
    "EDT": "America/New_York",
    "EST": "America/New_York",
    "GMT": "Etc/GMT",
    "HST": "Pacific/Honolulu",
    "IST": "Europe/Dublin",
    "MDT": "America/Denver",
    "MST": "America/Denver",
    "PDT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "UTC": "UTC",
}

TIMEZONE_ALIAS_FALLBACKS = {
    "ACDT": timezone(timedelta(hours=10, minutes=30)),
    "ACST": timezone(timedelta(hours=9, minutes=30)),
    "AKDT": timezone(timedelta(hours=-8)),
    "AKST": timezone(timedelta(hours=-9)),
    "AST": timezone(timedelta(hours=-4)),
    "ADT": timezone(timedelta(hours=-3)),
    "BST": timezone(timedelta(hours=1)),
    "CDT": timezone(timedelta(hours=-5)),
    "CST": timezone(timedelta(hours=-6)),
    "CEST": timezone(timedelta(hours=2)),
    "CET": timezone(timedelta(hours=1)),
    "EDT": timezone(timedelta(hours=-4)),
    "EST": timezone(timedelta(hours=-5)),
    "GMT": timezone.utc,
    "HST": timezone(timedelta(hours=-10)),
    "IST": timezone(timedelta(hours=1)),
    "MDT": timezone(timedelta(hours=-6)),
    "MST": timezone(timedelta(hours=-7)),
    "PDT": timezone(timedelta(hours=-7)),
    "PST": timezone(timedelta(hours=-8)),
    "UTC": timezone.utc,
}

# Track aliases that we have already warned about so we avoid spamming logs when
# the same timezone is requested repeatedly.
_MISSING_TIMEZONE_WARNINGS: set[str] = set()

WIKI_ICON_PATH = constants.MEDIA_PATH / "gw2wikiicons"
SELECT_CUSTOM_ID_PREFIX = "gw2tools:comp:signup"

ABSENT_OPTION_NAME = "Absent"
ABSENT_EMOJI = "\N{CROSS MARK}"

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


def _format_day_names(values: Sequence[int]) -> str:
    names: List[str] = []
    for day in values:
        if not isinstance(day, int) or not 0 <= day <= 6:
            continue
        name = calendar.day_name[day]
        if name not in names:
            names.append(name)
    return ", ".join(names)


def _format_schedule_text(schedule: CompSchedule) -> str:
    if schedule.post_days and schedule.post_time:
        day_names = _format_day_names(schedule.post_days)
        if day_names:
            return f"{day_names} at {schedule.post_time} {schedule.timezone}."
    return "Not scheduled."


def _format_class_summary(classes: Sequence[CompClassConfig], *, max_items: int = 10) -> str:
    if not classes:
        return "No classes configured."
    items: List[str] = []
    for entry in classes[:max_items]:
        if entry.required is None:
            items.append(f"• {entry.name}")
        else:
            items.append(f"• {entry.name} ({entry.required})")
    remaining = len(classes) - len(items)
    if remaining > 0:
        items.append(f"• +{remaining} more")
    return "\n".join(items)


def _resolve_timezone(value: str, *, strict: bool = True) -> tzinfo:
    cleaned = normalise_timezone(value)
    alias_key = cleaned.upper()
    if alias_key == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError:
        alias_target = TIMEZONE_ALIASES.get(alias_key)
        if alias_target and alias_target != cleaned:
            try:
                return ZoneInfo(alias_target)
            except ZoneInfoNotFoundError:
                if alias_key not in _MISSING_TIMEZONE_WARNINGS:
                    LOGGER.warning(
                        "Timezone alias '%s' resolved to unknown zone '%s'", alias_key, alias_target
                    )
                    _MISSING_TIMEZONE_WARNINGS.add(alias_key)
        fallback_tz = TIMEZONE_ALIAS_FALLBACKS.get(alias_key)
        if fallback_tz is not None:
            LOGGER.debug(
                "Using fixed-offset fallback for timezone alias '%s'", alias_key
            )
            return fallback_tz
        if strict:
            raise ValueError(f"Unknown timezone: {cleaned}") from None
        LOGGER.warning("Unknown timezone '%s', defaulting to UTC", cleaned)
        return timezone.utc


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    """Parse stored ISO timestamps, handling ``Z`` suffixes and naive values."""

    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    valid_names.add(ABSENT_OPTION_NAME)
    config.signups = {name: users for name, users in config.signups.items() if name in valid_names}
    for entry in config.classes:
        config.signups.setdefault(entry.name, [])
    config.signups.setdefault(ABSENT_OPTION_NAME, [])


OVERVIEW_TOKEN_RE = re.compile(r"(?<!<):([0-9A-Za-z][0-9A-Za-z _-]{0,30}):")


class CompSignupView(discord.ui.View):
    def __init__(
        self,
        cog: "CompCog",
        guild_id: int,
        *,
        schedule_id: Optional[str] = None,
        channel: Optional[discord.abc.GuildChannel] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.schedule_id = schedule_id
        self.channel = channel
        self.add_item(CompSignupSelect(self))


class CompSignupSelect(discord.ui.Select):
    def __init__(self, view: CompSignupView):
        self.comp_view = view
        _, _, comp_config = view.cog.resolve_comp_context(view.guild_id, schedule_id=view.schedule_id)
        if comp_config is None:
            comp_config = CompConfig()
        _sanitize_signups(comp_config)
        options: List[discord.SelectOption] = []
        guild = view.cog.bot.get_guild(view.guild_id)
        channel = view.channel
        if channel is None and guild and comp_config.channel_id:
            channel = guild.get_channel(comp_config.channel_id)
        for entry in comp_config.classes:
            description = "Sign up for this class"
            if entry.required is not None:
                current_signups = comp_config.signups.get(entry.name, [])
                remaining = max(entry.required - len(current_signups), 0)
                if remaining > 0:
                    description = f"{remaining} needed"
                else:
                    description = "Class is full"
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

        options.append(
            discord.SelectOption(
                label=ABSENT_OPTION_NAME,
                value=ABSENT_OPTION_NAME,
                description="Mark yourself as absent",
                emoji=ABSENT_EMOJI,
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
        config, schedule, comp_config = self.comp_view.cog.resolve_comp_context(
            self.comp_view.guild_id,
            schedule_id=self.comp_view.schedule_id,
        )
        if self.comp_view.schedule_id and schedule is None:
            await interaction.response.send_message(
                "This schedule is no longer available.", ephemeral=True
            )
            return
        if comp_config is None:
            await interaction.response.send_message(
                "The preset for this schedule could not be found.", ephemeral=True
            )
            return
        _sanitize_signups(comp_config)
        selection = self.values[0]
        if selection == "__none":
            await interaction.response.send_message("There are no classes to sign up for yet.", ephemeral=True)
            return

        if not comp_config.classes and selection != ABSENT_OPTION_NAME:
            await interaction.response.send_message("No classes are configured for signups yet.", ephemeral=True)
            return

        user_id = interaction.user.id
        current = None
        for name, users in comp_config.signups.items():
            if user_id in users:
                current = name
                break

        entry = discord.utils.get(comp_config.classes, name=selection)
        target_signups = comp_config.signups.setdefault(selection, [])

        guild = interaction.guild or self.comp_view.cog.bot.get_guild(self.comp_view.guild_id)
        channel = self.comp_view.channel
        if channel is None and interaction.channel and isinstance(
            interaction.channel, discord.abc.GuildChannel
        ):
            channel = interaction.channel
        if channel is None and guild and comp_config.channel_id:
            channel = guild.get_channel(comp_config.channel_id)
        if guild is None and channel is not None:
            guild = channel.guild

        def resolve_emoji_text() -> str:
            if entry and guild:
                emoji_obj = self.comp_view.cog._get_class_emoji(entry, guild=guild, channel=channel)
                if emoji_obj:
                    return f"{emoji_obj} "
            if selection == ABSENT_OPTION_NAME:
                return f"{ABSENT_EMOJI} "
            return ""

        if (
            selection != ABSENT_OPTION_NAME
            and entry
            and entry.required is not None
            and user_id not in target_signups
            and len(target_signups) >= entry.required
        ):
            emoji_text = resolve_emoji_text()
            await interaction.response.send_message(
                f"{emoji_text}**{selection}** is already full.", ephemeral=True
            )
            return

        removed = False
        if current == selection:
            comp_config.signups[selection].remove(user_id)
            removed = True
        else:
            if current:
                comp_config.signups[current].remove(user_id)
            if user_id not in target_signups:
                target_signups.append(user_id)

        if schedule is not None:
            schedule.signups = comp_config.signups
            self.comp_view.cog.bot.save_config(self.comp_view.guild_id, config)
            await self.comp_view.cog.refresh_signup_message(
                self.comp_view.guild_id, schedule_id=schedule.schedule_id
            )
        else:
            self.comp_view.cog.bot.save_config(self.comp_view.guild_id, config)
            await self.comp_view.cog.refresh_signup_message(self.comp_view.guild_id)

        if removed:
            await interaction.response.send_message(f"Removed you from **{selection}**.", ephemeral=True)
        else:
            emoji_text = resolve_emoji_text()
            message = f"Signed you up for {emoji_text}**{selection}**."
            await interaction.response.send_message(message, ephemeral=True)


class CompChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        view: "CompConfigView",
        default_channel: Optional[discord.abc.GuildChannel],
        *,
        row: Optional[int] = None,
    ):
        super().__init__(
            placeholder="Select the channel for scheduled compositions",
            channel_types=(discord.ChannelType.text, discord.ChannelType.news),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
            row=row,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        self.config_view.mark_modified()
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


class CompRoleSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        view: "CompConfigView",
        default_role: Optional[discord.Role],
        *,
        row: Optional[int] = None,
    ):
        super().__init__(
            placeholder="Select a role to ping with composition posts",
            min_values=0,
            max_values=1,
            default_values=[default_role] if default_role else None,
            row=row,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        self.config_view.mark_modified()
        comp_config = self.config_view.config.comp
        if self.values:
            role = self.values[0]
            comp_config.ping_role_id = role.id
            message = f"Composition posts will mention {role.mention}."
        else:
            comp_config.ping_role_id = None
            message = "Composition ping role cleared."
        self.config_view.persist()
        await self.config_view.refresh_summary(interaction)
        await interaction.response.send_message(message, ephemeral=True)


class ScheduleModal(discord.ui.Modal):
    def __init__(
        self, view: "CompConfigView", schedule: Optional[CompSchedule] = None
    ) -> None:
        title = "Create schedule" if schedule is None else "Edit schedule"
        super().__init__(title=title)
        self.config_view = view
        self.schedule = schedule

        default_name = schedule.name if schedule else ""
        default_preset = ""
        if schedule and schedule.preset_name:
            default_preset = schedule.preset_name
        elif view.selected_preset_name:
            default_preset = view.selected_preset_name
        elif view.presets:
            default_preset = view.presets[0].name

        default_day = _format_day_names(schedule.post_days) if schedule else ""
        default_time = schedule.post_time if schedule else ""
        default_tz = schedule.timezone if schedule else "UTC"

        self.name_input = discord.ui.TextInput(
            label="Schedule name",
            placeholder="Raid night",
            default=default_name,
            required=True,
            max_length=64,
        )
        self.preset_input = discord.ui.TextInput(
            label="Preset name",
            placeholder="Weekly Raid",
            default=default_preset,
            required=True,
            max_length=64,
        )
        self.day_input = discord.ui.TextInput(
            label="Day(s) of week",
            placeholder="Monday or Mon,Wed,Fri",
            default=default_day,
            required=False,
        )
        self.time_input = discord.ui.TextInput(
            label="Time (24h HH:MM)",
            placeholder="19:30",
            default=default_time or "",
            required=False,
        )
        self.tz_input = discord.ui.TextInput(
            label="Timezone (IANA name)",
            placeholder="UTC",
            default=default_tz or "UTC",
            required=True,
        )
        self.add_item(self.name_input)
        self.add_item(self.preset_input)
        self.add_item(self.day_input)
        self.add_item(self.time_input)
        self.add_item(self.tz_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        name_value = str(self.name_input.value).strip()
        preset_value = str(self.preset_input.value).strip()
        day_value = str(self.day_input.value).strip()
        time_value = str(self.time_input.value).strip()
        tz_value = normalise_timezone(self.tz_input.value)

        if not name_value:
            await interaction.response.send_message("Schedule name is required.", ephemeral=True)
            return

        presets = self.config_view.cog.bot.storage.get_comp_presets(self.config_view.guild.id)
        preset = None
        for item in presets:
            if item.name.casefold() == preset_value.casefold():
                preset = item
                break
        if not preset:
            await interaction.response.send_message(
                "Preset not found. Select or save a preset first.",
                ephemeral=True,
            )
            return

        for existing in self.config_view.config.comp_schedules:
            if existing.name.casefold() != name_value.casefold():
                continue
            if self.schedule and existing.schedule_id == self.schedule.schedule_id:
                continue
            await interaction.response.send_message(
                "A schedule with that name already exists.", ephemeral=True
            )
            return

        try:
            _resolve_timezone(tz_value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if not day_value and not time_value:
            parsed_days: List[int] = []
            parsed_time: Optional[time_cls] = None
        else:
            if not day_value or not time_value:
                await interaction.response.send_message(
                    "Please provide both a day of the week and time, or leave both blank to disable scheduling.",
                    ephemeral=True,
                )
                return

            parsed_days = []
            invalid_tokens: List[str] = []
            for token in day_value.split(","):
                cleaned = token.strip()
                if not cleaned:
                    continue
                day_index = _parse_day(cleaned)
                if day_index is None:
                    invalid_tokens.append(cleaned)
                    continue
                if day_index not in parsed_days:
                    parsed_days.append(day_index)

            if invalid_tokens:
                invalid_list = ", ".join(invalid_tokens)
                await interaction.response.send_message(
                    f"Unrecognised day(s) of the week: {invalid_list}. Try values like Monday, Tue, Friday, etc.",
                    ephemeral=True,
                )
                return

            if not parsed_days:
                await interaction.response.send_message(
                    "Please specify at least one valid day of the week.",
                    ephemeral=True,
                )
                return

            parsed_time = _parse_time(time_value)
            if not parsed_time:
                await interaction.response.send_message(
                    "Time must be provided in HH:MM 24-hour format.",
                    ephemeral=True,
                )
                return

        if self.schedule is None:
            schedule = CompSchedule(
                schedule_id=uuid.uuid4().hex,
                name=name_value,
                preset_name=preset.name,
                post_days=parsed_days,
                post_time=parsed_time.strftime("%H:%M") if parsed_time else None,
                timezone=tz_value,
            )
            self.config_view.config.comp_schedules.append(schedule)
            self.config_view.selected_schedule_id = schedule.schedule_id
        else:
            self.schedule.name = name_value
            self.schedule.preset_name = preset.name
            self.schedule.post_days = parsed_days
            self.schedule.post_time = parsed_time.strftime("%H:%M") if parsed_time else None
            self.schedule.timezone = tz_value

        self.config_view.persist()
        self.config_view.refresh_schedule_options()
        await self.config_view.refresh_summary(interaction)
        if self.config_view.selected_schedule_id:
            await self.config_view.cog.refresh_signup_message(
                self.config_view.guild.id,
                schedule_id=self.config_view.selected_schedule_id,
            )
        await interaction.response.send_message("Schedule saved.", ephemeral=True)


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
        self.config_view.mark_modified()
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
        self.config_view.mark_modified()
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
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(style=discord.ButtonStyle.success, label="Post now", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not view:
            return
        schedule = None
        if hasattr(view, "get_selected_schedule"):
            schedule = view.get_selected_schedule()  # type: ignore[call-arg]
        if schedule:
            _, _, comp_config = view.cog.resolve_comp_context(
                view.guild.id, schedule_id=schedule.schedule_id
            )
            if comp_config is None:
                await interaction.response.send_message(
                    "The preset for this schedule could not be found.", ephemeral=True
                )
                return
            if not comp_config.channel_id:
                await interaction.response.send_message(
                    "Set a channel before posting the composition.", ephemeral=True
                )
                return
            if not comp_config.classes:
                await interaction.response.send_message(
                    "Configure at least one class before posting.", ephemeral=True
                )
                return
            await view.cog.post_composition(
                view.guild.id,
                reset_signups=True,
                force_new_message=True,
                schedule_id=schedule.schedule_id,
            )
            await interaction.response.send_message(
                "Scheduled composition post updated.", ephemeral=True
            )
            return

        comp_config = view.config.comp
        if not comp_config.channel_id:
            await interaction.response.send_message(
                "Set a channel before posting the composition.", ephemeral=True
            )
            return
        if not comp_config.classes:
            await interaction.response.send_message(
                "Configure at least one class before posting.", ephemeral=True
            )
            return
        await view.cog.post_composition(
            view.guild.id, reset_signups=True, force_new_message=True
        )
        await interaction.response.send_message("Composition post updated.", ephemeral=True)


class CloseButton(discord.ui.Button["CompConfigView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Close", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if self.view and isinstance(self.view, CompConfigView):
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(content="Composition configuration closed.", view=None)
            self.view.stop()


class SavedCompSelect(discord.ui.Select):
    def __init__(self, view: "CompConfigView") -> None:
        options, enabled = view.build_preset_options()
        super().__init__(
            placeholder="Saved compositions",
            min_values=0,
            max_values=1,
            options=options,
        )
        self.config_view = view
        self.disabled = not enabled

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not self.config_view.presets:
            await interaction.response.defer()
            return
        if not self.values:
            self.config_view.selected_preset_name = None
            self.config_view.refresh_preset_options()
            await interaction.response.defer()
            return
        selection = self.values[0]
        if selection == "__none__":
            self.config_view.selected_preset_name = None
        else:
            self.config_view.selected_preset_name = selection
        self.config_view.refresh_preset_options()
        await interaction.response.defer()


class SavePresetModal(discord.ui.Modal):
    def __init__(self, view: "CompManageView") -> None:
        super().__init__(title="Save composition preset")
        self.config_view = view
        self.name_input = discord.ui.TextInput(
            label="Preset name",
            placeholder="e.g. Static Squad",
            max_length=80,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        raw_name = str(self.name_input.value).strip()
        if not raw_name:
            await interaction.response.send_message("Preset name cannot be empty.", ephemeral=True)
            return
        if self.config_view.find_preset(raw_name):
            await interaction.response.send_message(
                "A preset with that name already exists. Choose a different name or update the existing preset.",
                ephemeral=True,
            )
            return

        preset = CompPreset(
            name=raw_name,
            config=self.config_view.config.comp.copy(include_runtime_fields=False),
        )
        self.config_view.add_or_replace_preset(preset)
        self.config_view.selected_preset_name = preset.name
        self.config_view.config.comp_active_preset = preset.name
        self.config_view.persist_presets()
        self.config_view.persist()
        self.config_view.refresh_preset_options()
        await self.config_view.refresh_summary(interaction)
        await interaction.response.send_message(
            f"Saved preset **{preset.name}**.",
            ephemeral=True,
        )


class SavePresetButton(discord.ui.Button["CompManageView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Save as preset", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompManageView):
            return
        await interaction.response.send_modal(SavePresetModal(view))

class UpdatePresetButton(discord.ui.Button["CompManageView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Update preset", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompManageView):
            return
        preset = view.get_selected_preset()
        if not preset:
            await interaction.response.send_message("Select a preset to update first.", ephemeral=True)
            return
        updated = CompPreset(
            name=preset.name,
            config=view.config.comp.copy(include_runtime_fields=False),
        )
        view.add_or_replace_preset(updated)
        view.selected_preset_name = updated.name
        view.config.comp_active_preset = updated.name
        view.persist_presets()
        view.persist()
        view.refresh_preset_options()
        await view.refresh_summary(interaction)
        await interaction.response.send_message(
            f"Preset **{updated.name}** updated.",
            ephemeral=True,
        )


class LoadPresetButton(discord.ui.Button["CompManageView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Load preset", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompManageView):
            return
        preset = view.get_selected_preset()
        if not preset:
            await interaction.response.send_message("Select a preset to load first.", ephemeral=True)
            return
        view.config.comp = preset.config.copy(include_runtime_fields=False)
        view.config.comp_active_preset = preset.name
        view.selected_preset_name = preset.name
        view.persist()
        view.refresh_preset_options()
        await view.refresh_summary(interaction)
        await interaction.response.send_message(
            f"Loaded preset **{preset.name}**. Use 'Post now' to publish it immediately.",
            ephemeral=True,
        )

class DeletePresetButton(discord.ui.Button["CompManageView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Delete preset", style=discord.ButtonStyle.danger, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompManageView):
            return
        preset = view.get_selected_preset()
        if not preset:
            await interaction.response.send_message("Select a preset to delete first.", ephemeral=True)
            return
        blocking = [
            schedule.name
            for schedule in view.config.comp_schedules
            if schedule.preset_name
            and schedule.preset_name.casefold() == preset.name.casefold()
        ]
        if blocking:
            await interaction.response.send_message(
                "Preset is still used by schedules: " + ", ".join(blocking),
                ephemeral=True,
            )
            return
        removed_active = False
        if view.config.comp_active_preset and view.config.comp_active_preset.casefold() == preset.name.casefold():
            removed_active = True
        view.presets = [existing for existing in view.presets if existing.name.casefold() != preset.name.casefold()]
        view.selected_preset_name = None
        if removed_active:
            view.config.comp_active_preset = None
        view.persist_presets()
        view.persist()
        view.refresh_preset_options()
        await view.refresh_summary(interaction)
        await interaction.response.send_message(
            f"Preset **{preset.name}** deleted.",
            ephemeral=True,
        )


class SavedScheduleSelect(discord.ui.Select):
    def __init__(self, view: "CompConfigView", *, row: Optional[int] = None) -> None:
        options, enabled = view.build_schedule_options()
        super().__init__(
            placeholder="Saved schedules",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )
        self.config_view = view
        self.disabled = not enabled

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not self.config_view.config.comp_schedules:
            await interaction.response.defer()
            return
        selection = self.values[0]
        if selection == "__none__":
            self.config_view.selected_schedule_id = None
        else:
            self.config_view.selected_schedule_id = selection
        self.config_view.refresh_schedule_options()
        await self.config_view.refresh_summary(interaction)
        await interaction.response.defer()


class AddScheduleButton(discord.ui.Button["CompConfigView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Add schedule", style=discord.ButtonStyle.success, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompConfigView):
            return
        if not view.presets:
            await interaction.response.send_message(
                "Save a preset before creating a schedule.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ScheduleModal(view))


class EditScheduleButton(discord.ui.Button["CompConfigView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Edit schedule", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompConfigView):
            return
        schedule = view.get_selected_schedule()
        if not schedule:
            await interaction.response.send_message("Select a schedule to edit first.", ephemeral=True)
            return
        await interaction.response.send_modal(ScheduleModal(view, schedule))


class DeleteScheduleButton(discord.ui.Button["CompConfigView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="Delete schedule", style=discord.ButtonStyle.danger, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompConfigView):
            return
        schedule = view.get_selected_schedule()
        if not schedule:
            await interaction.response.send_message("Select a schedule to delete first.", ephemeral=True)
            return

        message_id = schedule.message_id
        schedule_id = schedule.schedule_id
        channel_id: Optional[int] = None
        _, _, comp_config = view.cog.resolve_comp_context(
            view.guild.id, schedule_id=schedule_id
        )
        if comp_config and comp_config.channel_id:
            channel_id = comp_config.channel_id
        view.config.comp_schedules = [
            existing for existing in view.config.comp_schedules if existing.schedule_id != schedule_id
        ]
        view.selected_schedule_id = None
        if view.config.comp_schedules:
            view.selected_schedule_id = view.config.comp_schedules[0].schedule_id
        view.persist()
        view.refresh_schedule_options()
        await view.refresh_summary(interaction)

        if message_id and channel_id:
            channel = view.guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await view.guild.fetch_channel(channel_id)
                except (discord.Forbidden, discord.HTTPException):
                    channel = None
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
                if message is not None:
                    try:
                        await message.edit(view=None)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        await interaction.response.send_message("Schedule deleted.", ephemeral=True)


class ScheduleListButton(discord.ui.Button["CompConfigView"]):
    def __init__(self, *, row: Optional[int] = None) -> None:
        super().__init__(label="List schedules", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        view = self.view
        if not isinstance(view, CompConfigView):
            return
        embed = view.cog.build_schedule_embed(
            view.guild, view.config, selected_schedule_id=view.selected_schedule_id
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CompManageView(discord.ui.View):
    def __init__(self, cog: "CompCog", guild: discord.Guild, config: GuildConfig):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config
        self.presets: List[CompPreset] = self.cog.bot.storage.get_comp_presets(guild.id)
        self.selected_preset_name: Optional[str] = config.comp_active_preset
        self.preset_select = SavedCompSelect(self)
        self.add_item(self.preset_select)
        self.add_item(SavePresetButton(row=1))
        self.add_item(UpdatePresetButton(row=1))
        self.add_item(LoadPresetButton(row=1))
        self.add_item(DeletePresetButton(row=1))
        comp_config = config.comp
        default_channel = guild.get_channel(comp_config.channel_id) if comp_config.channel_id else None
        self.add_item(CompChannelSelect(self, default_channel, row=2))
        default_role = guild.get_role(comp_config.ping_role_id) if comp_config.ping_role_id else None
        self.add_item(CompRoleSelect(self, default_role, row=3))
        self.add_item(
            discord.ui.Button(
                label="Edit overview",
                style=discord.ButtonStyle.primary,
                custom_id="comp_overview",
                row=4,
            )
        )
        self.children[-1].callback = self._overview_callback  # type: ignore[assignment]
        self.add_item(
            discord.ui.Button(
                label="Edit classes",
                style=discord.ButtonStyle.primary,
                custom_id="comp_classes",
                row=4,
            )
        )
        self.children[-1].callback = self._classes_callback  # type: ignore[assignment]
        self.add_item(PostNowButton(row=4))
        self.add_item(CloseButton(row=4))
        self.message: Optional[discord.InteractionMessage] = None

    def persist(self) -> None:
        self.cog.bot.save_config(self.guild.id, self.config)

    def persist_presets(self) -> None:
        self.cog.bot.storage.save_comp_presets(self.guild.id, self.presets)

    def build_preset_options(self) -> Tuple[List[discord.SelectOption], bool]:
        if not self.presets:
            placeholder = discord.SelectOption(
                label="No saved presets", value="__none__", description="Save a preset to enable this list."
            )
            return [placeholder], False
        self.presets.sort(key=lambda preset: preset.name.casefold())
        options = [
            discord.SelectOption(
                label=preset.name,
                value=preset.name,
                default=(preset.name == self.selected_preset_name),
            )
            for preset in self.presets
        ]
        return options, True

    def refresh_preset_options(self) -> None:
        options, enabled = self.build_preset_options()
        self.preset_select.options = options
        self.preset_select.disabled = not enabled

    def add_or_replace_preset(self, preset: CompPreset) -> None:
        replaced = False
        for index, existing in enumerate(self.presets):
            if existing.name.casefold() == preset.name.casefold():
                self.presets[index] = preset
                replaced = True
                break
        if not replaced:
            self.presets.append(preset)
        self.presets.sort(key=lambda item: item.name.casefold())

    def get_selected_preset(self) -> Optional[CompPreset]:
        if not self.selected_preset_name:
            return None
        name_lower = self.selected_preset_name.casefold()
        for preset in self.presets:
            if preset.name.casefold() == name_lower:
                return preset
        return None

    def find_preset(self, name: str) -> Optional[CompPreset]:
        name_lower = name.casefold()
        for preset in self.presets:
            if preset.name.casefold() == name_lower:
                return preset
        return None

    def mark_modified(self) -> None:
        if self.config.comp_active_preset or self.selected_preset_name:
            self.config.comp_active_preset = None
            self.selected_preset_name = None

    async def refresh_summary(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not self.message:
            if interaction.message:
                self.message = interaction.message  # type: ignore[assignment]
            else:
                try:
                    self.message = await interaction.original_response()
                except discord.HTTPException:
                    return
        embed = self.cog.build_summary_embed(
            self.guild,
            self.config,
            active_preset=self.config.comp_active_preset,
            selected_schedule_id=None,
            include_schedules=False,
        )
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            LOGGER.warning("Failed to update composition configuration summary for guild %s", self.guild.id)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _overview_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(OverviewModal(self))

    async def _classes_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ClassesModal(self))


class CompConfigView(discord.ui.View):
    def __init__(self, cog: "CompCog", guild: discord.Guild, config: GuildConfig):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config
        self.presets: List[CompPreset] = self.cog.bot.storage.get_comp_presets(guild.id)
        self.selected_preset_name: Optional[str] = config.comp_active_preset
        self.selected_schedule_id: Optional[str] = None
        if config.comp_schedules:
            self.selected_schedule_id = config.comp_schedules[0].schedule_id
        self.preset_select: Optional[SavedCompSelect] = None
        self.schedule_select = SavedScheduleSelect(self, row=0)
        self.add_item(self.schedule_select)
        self.add_item(AddScheduleButton(row=1))
        self.add_item(EditScheduleButton(row=1))
        self.add_item(DeleteScheduleButton(row=1))
        self.add_item(ScheduleListButton(row=1))
        self.add_item(CloseButton(row=2))
        self.message: Optional[discord.InteractionMessage] = None

    def persist(self) -> None:
        self.cog.bot.save_config(self.guild.id, self.config)

    def persist_presets(self) -> None:
        self.cog.bot.storage.save_comp_presets(self.guild.id, self.presets)

    def build_preset_options(self) -> Tuple[List[discord.SelectOption], bool]:
        if not self.presets:
            placeholder = discord.SelectOption(
                label="No saved presets", value="__none__", description="Save a preset to enable this list."
            )
            return [placeholder], False
        self.presets.sort(key=lambda preset: preset.name.casefold())
        options = [
            discord.SelectOption(
                label=preset.name,
                value=preset.name,
                default=(preset.name == self.selected_preset_name),
            )
            for preset in self.presets
        ]
        return options, True

    def refresh_preset_options(self) -> None:
        if self.preset_select is None:
            return
        options, enabled = self.build_preset_options()
        self.preset_select.options = options
        self.preset_select.disabled = not enabled

    def build_schedule_options(self) -> Tuple[List[discord.SelectOption], bool]:
        schedules = self.config.comp_schedules
        if not schedules:
            placeholder = discord.SelectOption(
                label="No schedules", value="__none__", description="Add a schedule to enable this list."
            )
            return [placeholder], False

        if (
            self.selected_schedule_id is None
            or not any(
                schedule.schedule_id == self.selected_schedule_id
                for schedule in schedules
            )
        ):
            self.selected_schedule_id = schedules[0].schedule_id

        sorted_schedules = sorted(schedules, key=lambda schedule: schedule.name.casefold())
        options: List[discord.SelectOption] = []
        for schedule in sorted_schedules:
            preset_label = schedule.preset_name or "Unlinked preset"
            schedule_text = _format_schedule_text(schedule)
            description = f"{preset_label} | {schedule_text}"
            if len(description) > 100:
                description = description[:97] + "..."
            options.append(
                discord.SelectOption(
                    label=schedule.name,
                    value=schedule.schedule_id,
                    description=description,
                    default=(schedule.schedule_id == self.selected_schedule_id),
                )
            )
        return options, True

    def refresh_schedule_options(self) -> None:
        options, enabled = self.build_schedule_options()
        self.schedule_select.options = options
        self.schedule_select.disabled = not enabled

    def add_or_replace_preset(self, preset: CompPreset) -> None:
        replaced = False
        for index, existing in enumerate(self.presets):
            if existing.name.casefold() == preset.name.casefold():
                self.presets[index] = preset
                replaced = True
                break
        if not replaced:
            self.presets.append(preset)
        self.presets.sort(key=lambda item: item.name.casefold())

    def get_selected_preset(self) -> Optional[CompPreset]:
        if not self.selected_preset_name:
            return None
        name_lower = self.selected_preset_name.casefold()
        for preset in self.presets:
            if preset.name.casefold() == name_lower:
                return preset
        return None

    def get_selected_schedule(self) -> Optional[CompSchedule]:
        if not self.selected_schedule_id:
            return None
        for schedule in self.config.comp_schedules:
            if schedule.schedule_id == self.selected_schedule_id:
                return schedule
        return None

    def find_preset(self, name: str) -> Optional[CompPreset]:
        name_lower = name.casefold()
        for preset in self.presets:
            if preset.name.casefold() == name_lower:
                return preset
        return None

    def mark_modified(self) -> None:
        if self.config.comp_active_preset or self.selected_preset_name:
            self.config.comp_active_preset = None
            self.selected_preset_name = None
            self.refresh_preset_options()

    async def refresh_summary(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not self.message:
            if interaction.message:
                self.message = interaction.message  # type: ignore[assignment]
            else:
                try:
                    self.message = await interaction.original_response()
                except discord.HTTPException:
                    return
        embed = self.cog.build_schedule_embed(
            self.guild,
            self.config,
            selected_schedule_id=self.selected_schedule_id,
        )
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            LOGGER.warning("Failed to update composition configuration summary for guild %s", self.guild.id)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _overview_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(OverviewModal(self))

    async def _classes_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ClassesModal(self))


class CompCog(commands.GroupCog, name="comp"):
    """Schedule and manage guild composition signups."""

    POST_CHECK_INTERVAL_MINUTES = 1
    schedule = app_commands.Group(
        name="schedule", description="Manage scheduled composition posts."
    )

    def __init__(self, bot: GW2ToolsBot) -> None:
        super().__init__()
        self.bot = bot
        self.poster_loop.start()
        self._view_init_task: asyncio.Task[None] | None = None
        env_home = os.getenv("GW2TOOLS_EMOJI_GUILD_ID")
        if not env_home:
            raise RuntimeError(
                "GW2TOOLS_EMOJI_GUILD_ID environment variable must be set to the guild containing class emojis."
            )
        try:
            self.emoji_home_guild_id: int = int(env_home)
        except ValueError as exc:
            raise RuntimeError(
                "GW2TOOLS_EMOJI_GUILD_ID must be an integer guild identifier."
            ) from exc

    def _find_schedule(
        self, config: GuildConfig, schedule_id: str
    ) -> Optional[CompSchedule]:
        for schedule in config.comp_schedules:
            if schedule.schedule_id == schedule_id:
                return schedule
        return None

    @staticmethod
    def _find_preset(
        presets: Sequence[CompPreset], preset_name: str
    ) -> Optional[CompPreset]:
        name_lower = preset_name.casefold()
        for preset in presets:
            if preset.name.casefold() == name_lower:
                return preset
        return None

    def _build_schedule_comp_config(
        self,
        config: GuildConfig,
        schedule: CompSchedule,
        *,
        presets: Optional[Sequence[CompPreset]] = None,
    ) -> Optional[CompConfig]:
        comp_config: CompConfig
        if schedule.preset_name:
            if presets is None:
                return None
            preset = self._find_preset(presets, schedule.preset_name)
            if preset is None:
                return None
            comp_config = preset.config.copy(include_runtime_fields=False)
        else:
            comp_config = config.comp.copy(include_runtime_fields=False)

        if config.comp.channel_id is not None:
            comp_config.channel_id = config.comp.channel_id
        if config.comp.ping_role_id is not None:
            comp_config.ping_role_id = config.comp.ping_role_id

        comp_config.post_days = list(schedule.post_days)
        comp_config.post_time = schedule.post_time
        comp_config.timezone = schedule.timezone or comp_config.timezone or "UTC"
        comp_config.signups = schedule.signups
        comp_config.message_id = schedule.message_id
        comp_config.last_post_at = schedule.last_post_at
        return comp_config

    def resolve_comp_context(
        self, guild_id: int, *, schedule_id: Optional[str] = None
    ) -> Tuple[GuildConfig, Optional[CompSchedule], Optional[CompConfig]]:
        config = self.bot.get_config(guild_id)
        if schedule_id:
            schedule = self._find_schedule(config, schedule_id)
            if schedule is None:
                return config, None, None
            presets = self.bot.storage.get_comp_presets(guild_id)
            comp_config = self._build_schedule_comp_config(
                config, schedule, presets=presets
            )
            return config, schedule, comp_config
        return config, None, config.comp

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

    @app_commands.command(name="manage", description="Manage composition presets and roster for this guild.")
    async def manage(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        view = CompManageView(self, interaction.guild, config)
        embed = self.build_summary_embed(
            interaction.guild,
            config,
            active_preset=config.comp_active_preset,
            selected_schedule_id=None,
            include_schedules=False,
        )
        await interaction.response.send_message(
            "Use the controls below to manage the roster and presets. "
            "To manage scheduled posts, run `/comp schedule manage`.",
            embed=embed,
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @schedule.command(name="manage", description="Manage scheduled composition posts for this guild.")
    async def schedule_manage(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        view = CompConfigView(self, interaction.guild, config)
        embed = self.build_schedule_embed(
            interaction.guild,
            config,
            selected_schedule_id=view.selected_schedule_id,
        )
        await interaction.response.send_message(
            "Use the controls below to manage scheduled composition posts. "
            "For roster and presets, run `/comp manage`.",
            embed=embed,
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @schedule.command(name="list", description="List scheduled composition posts for this guild.")
    async def schedule_list(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        schedules = config.comp_schedules
        embed = discord.Embed(
            title="Scheduled Composition Posts",
            color=BRAND_COLOUR,
        )
        if not schedules:
            embed.description = "No schedules configured yet."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        lines = []
        for schedule in sorted(schedules, key=lambda item: item.name.casefold()):
            preset_label = schedule.preset_name or "Unlinked preset"
            schedule_text = _format_schedule_text(schedule)
            lines.append(f"• **{schedule.name}** — {preset_label} — {schedule_text}")
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(minutes=POST_CHECK_INTERVAL_MINUTES)
    async def poster_loop(self) -> None:  # pragma: no cover - relies on Discord
        if not self.bot.guilds:
            return
        for guild in self.bot.guilds:
            try:
                await self._maybe_post_for_guild(guild)
            except Exception:  # pragma: no cover - defensive logging
                LOGGER.exception("Schedule loop failed for guild %s", guild.id)

    @poster_loop.before_loop
    async def before_poster_loop(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def _maybe_post_for_guild(self, guild: discord.Guild) -> None:
        config = self.bot.get_config(guild.id)
        if not config.comp_schedules:
            return
        presets = self.bot.storage.get_comp_presets(guild.id)
        for schedule in config.comp_schedules:
            await self._maybe_post_for_schedule(guild, config, schedule, presets)

    async def _maybe_post_for_schedule(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        schedule: CompSchedule,
        presets: Sequence[CompPreset],
    ) -> None:
        comp_config = self._build_schedule_comp_config(
            config, schedule, presets=presets
        )
        if comp_config is None:
            if schedule.preset_name:
                LOGGER.warning(
                    "Schedule '%s' in guild %s references missing preset '%s'",
                    schedule.name,
                    guild.id,
                    schedule.preset_name,
                )
            return
        if not comp_config.channel_id:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: no channel configured",
                schedule.name,
                guild.id,
            )
            return
        if not schedule.post_days:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: no post days configured",
                schedule.name,
                guild.id,
            )
            return
        if not schedule.post_time:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: no post time configured",
                schedule.name,
                guild.id,
            )
            return
        if not comp_config.classes:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: no classes configured",
                schedule.name,
                guild.id,
            )
            return

        try:
            tz = _resolve_timezone(comp_config.timezone or "UTC", strict=False)
        except ValueError:
            LOGGER.warning(
                "Invalid timezone '%s' for schedule '%s' in guild %s",
                comp_config.timezone,
                schedule.name,
                guild.id,
            )
            return

        now = datetime.now(tz)
        if now.weekday() not in schedule.post_days:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: %s not in %s",
                schedule.name,
                guild.id,
                now.weekday(),
                schedule.post_days,
            )
            return

        target_time = _parse_time(schedule.post_time or "")
        if not target_time:
            LOGGER.warning(
                "Schedule '%s' in guild %s skipped: invalid time '%s'",
                schedule.name,
                guild.id,
                schedule.post_time,
            )
            return

        target_dt = datetime.combine(now.date(), target_time, tz)
        if now < target_dt:
            LOGGER.debug(
                "Schedule '%s' in guild %s skipped: %s before target %s",
                schedule.name,
                guild.id,
                now.isoformat(),
                target_dt.isoformat(),
            )
            return

        last_post_at = schedule.last_post_at
        if last_post_at:
            last_post_dt = _parse_iso_datetime(last_post_at)
            if last_post_dt is not None:
                try:
                    last_local = last_post_dt.astimezone(tz)
                except ValueError:
                    last_local = last_post_dt.replace(tzinfo=timezone.utc).astimezone(tz)
                if last_local.date() == now.date() and last_local >= target_dt:
                    LOGGER.debug(
                        "Schedule '%s' in guild %s skipped: already posted at %s",
                        schedule.name,
                        guild.id,
                        last_local.isoformat(),
                    )
                    return

        LOGGER.info(
            "Posting scheduled composition '%s' for guild %s",
            schedule.name,
            guild.id,
        )
        await self.post_composition(
            guild.id,
            reset_signups=True,
            force_new_message=True,
            schedule_id=schedule.schedule_id,
        )

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
        emoji_home = self.bot.get_guild(self.emoji_home_guild_id)
        if emoji_home is None:
            LOGGER.warning(
                "Emoji home guild %s is not accessible; ensure the bot is a member.",
                self.emoji_home_guild_id,
            )
        available_by_name: Dict[str, discord.Emoji] = {}
        for emoji in self.bot.emojis:
            if not emoji.name:
                continue
            if emoji.guild_id == guild.id:
                available_by_name[emoji.name] = emoji
                continue
            if allow_external and emoji_home and emoji.guild_id == emoji_home.id:
                available_by_name.setdefault(emoji.name, emoji)
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
            LOGGER.debug(
                "No emoji found for class '%s' in guild %s", entry.name, guild.id
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
        schedule_id: Optional[str] = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        config = self.bot.get_config(guild_id)
        schedule: Optional[CompSchedule] = None
        if schedule_id:
            schedule = self._find_schedule(config, schedule_id)
            if schedule is None:
                return
            presets = self.bot.storage.get_comp_presets(guild_id)
            comp_config = self._build_schedule_comp_config(
                config, schedule, presets=presets
            )
            if comp_config is None:
                LOGGER.warning(
                    "Schedule '%s' in guild %s references missing preset '%s'",
                    schedule.name,
                    guild_id,
                    schedule.preset_name,
                )
                return
        else:
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

        message = None
        if comp_config.message_id:
            try:
                message = await channel.fetch_message(comp_config.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        send_new_message = force_new_message or message is None

        ping_role = None
        mention_text: Optional[str] = None
        allowed_mentions: Optional[discord.AllowedMentions] = None
        if comp_config.ping_role_id:
            ping_role = guild.get_role(comp_config.ping_role_id)
            if ping_role is None:
                try:
                    ping_role = await guild.fetch_role(comp_config.ping_role_id)
                except (discord.Forbidden, discord.HTTPException):
                    ping_role = None
            if ping_role is not None:
                mention_text = ping_role.mention
                allowed_mentions = discord.AllowedMentions(
                    roles=[ping_role],
                    users=False,
                    everyone=False,
                    replied_user=False,
                )

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

        if send_new_message:
            reset_signups = True

        _sanitize_signups(comp_config)

        if reset_signups:
            comp_config.signups = {entry.name: [] for entry in comp_config.classes}
            comp_config.signups[ABSENT_OPTION_NAME] = []

        emoji_updated = await self.ensure_class_emojis(
            guild, comp_config, channel=channel
        )
        if emoji_updated and schedule is None:
            self.bot.save_config(guild_id, config)

        embed = self._build_comp_embed(guild, comp_config, channel=channel)
        view = CompSignupView(
            self,
            guild_id,
            schedule_id=schedule.schedule_id if schedule else None,
            channel=channel,
        )

        if message is None:
            try:
                kwargs = {"embed": embed, "view": view}
                if mention_text:
                    kwargs["content"] = mention_text
                    if allowed_mentions is not None:
                        kwargs["allowed_mentions"] = allowed_mentions
                new_message = await channel.send(**kwargs)
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
        if schedule is not None:
            schedule.message_id = comp_config.message_id
            schedule.last_post_at = comp_config.last_post_at
            schedule.signups = comp_config.signups
        self.bot.save_config(guild_id, config)
        self.bot.add_view(view, message_id=comp_config.message_id)

    async def refresh_signup_message(
        self, guild_id: int, *, schedule_id: Optional[str] = None
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        config = self.bot.get_config(guild_id)
        schedule: Optional[CompSchedule] = None
        if schedule_id:
            schedule = self._find_schedule(config, schedule_id)
            if schedule is None:
                return
            presets = self.bot.storage.get_comp_presets(guild_id)
            comp_config = self._build_schedule_comp_config(
                config, schedule, presets=presets
            )
            if comp_config is None:
                return
        else:
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
            if schedule is not None:
                schedule.message_id = None
            else:
                comp_config.message_id = None
            self.bot.save_config(guild_id, config)
            return

        emoji_updated = await self.ensure_class_emojis(guild, comp_config, channel=channel)
        if emoji_updated and schedule is None:
            self.bot.save_config(guild_id, config)

        embed = self._build_comp_embed(guild, comp_config, channel=channel)
        view = CompSignupView(
            self,
            guild_id,
            schedule_id=schedule.schedule_id if schedule else None,
            channel=channel,
        )
        try:
            await message.edit(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Failed to refresh composition message in guild %s", guild_id)
            return
        if schedule is not None:
            schedule.signups = comp_config.signups
            self.bot.save_config(guild_id, config)
        self.bot.add_view(view, message_id=message.id)

    async def _register_persistent_view(self, guild_id: int) -> None:
        config = self.bot.get_config(guild_id)
        if config.comp_schedules:
            presets = self.bot.storage.get_comp_presets(guild_id)
            for schedule in config.comp_schedules:
                if not schedule.message_id:
                    continue
                comp_config = self._build_schedule_comp_config(
                    config, schedule, presets=presets
                )
                if comp_config is None:
                    continue
                _sanitize_signups(comp_config)
                guild = self.bot.get_guild(guild_id)
                channel = None
                if guild:
                    channel = (
                        guild.get_channel(comp_config.channel_id)
                        if comp_config.channel_id
                        else None
                    )
                    await self.ensure_class_emojis(guild, comp_config, channel=channel)
                view = CompSignupView(
                    self,
                    guild_id,
                    schedule_id=schedule.schedule_id,
                    channel=channel,
                )
                self.bot.add_view(view, message_id=comp_config.message_id)
            return

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

    def build_summary_embed(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        *,
        active_preset: Optional[str] = None,
        selected_schedule_id: Optional[str] = None,
        include_schedules: bool = True,
    ) -> discord.Embed:
        embed = discord.Embed(title="Guild Composition", color=BRAND_COLOUR)
        comp_config = config.comp
        if comp_config.channel_id:
            channel = guild.get_channel(comp_config.channel_id)
            if channel:
                channel_value = channel.mention
            else:
                channel_value = f"<#{comp_config.channel_id}>"
        else:
            channel = None
            channel_value = "Not set"
        if comp_config.ping_role_id:
            role = guild.get_role(comp_config.ping_role_id)
            if role:
                role_value = role.mention
            else:
                role_value = f"<@&{comp_config.ping_role_id}>"
        else:
            role_value = "Not set"
        embed.add_field(
            name="Posting",
            value=f"Channel: {channel_value}\nPing: {role_value}",
            inline=False,
        )

        if include_schedules:
            schedules = config.comp_schedules
            if schedules:
                lines: List[str] = []
                sorted_schedules = sorted(schedules, key=lambda item: item.name.casefold())
                max_lines = 10
                for schedule in sorted_schedules[:max_lines]:
                    prefix = "-> " if schedule.schedule_id == selected_schedule_id else "- "
                    preset_label = schedule.preset_name or "Unlinked preset"
                    schedule_text = _format_schedule_text(schedule)
                    lines.append(f"{prefix}{schedule.name} - {preset_label} - {schedule_text}")
                if len(sorted_schedules) > max_lines:
                    remaining = len(sorted_schedules) - max_lines
                    lines.append(f"...and {remaining} more")
                schedule_value = "\n".join(lines)
            else:
                schedule_value = "No schedules configured."
            embed.add_field(name="Schedules", value=schedule_value, inline=False)

        if active_preset:
            preset_text = f"Active: **{active_preset}**"
        else:
            preset_text = "Active: Not set"

        presets = self.bot.storage.get_comp_presets(guild.id)
        if presets:
            preset_names = [preset.name for preset in presets]
            preset_names.sort(key=str.casefold)
            if len(preset_names) > 10:
                preset_value = ", ".join(preset_names[:10]) + f", +{len(preset_names) - 10} more"
            else:
                preset_value = ", ".join(preset_names)
        else:
            preset_value = "No presets saved."
        embed.add_field(
            name="Presets",
            value=f"{preset_text}\nAvailable: {preset_value}",
            inline=False,
        )

        if comp_config.overview:
            overview_text = self._format_overview_text(
                comp_config.overview,
                comp_config,
                guild=guild,
                channel=channel,
            )
            if len(overview_text) > 500:
                overview_text = overview_text[:497] + "..."
            embed.add_field(name="Overview", value=overview_text, inline=False)
        else:
            embed.add_field(name="Overview", value="No overview set.", inline=False)

        embed.add_field(
            name="Classes",
            value=_format_class_summary(comp_config.classes),
            inline=False,
        )
        return embed

    def build_schedule_embed(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        *,
        selected_schedule_id: Optional[str] = None,
    ) -> discord.Embed:
        embed = discord.Embed(title="Scheduled Composition", color=BRAND_COLOUR)
        schedules = config.comp_schedules
        presets = self.bot.storage.get_comp_presets(guild.id)
        preset_lookup = {preset.name.casefold(): preset for preset in presets}

        if not schedules:
            embed.description = "No schedules configured."
            embed.set_footer(text="Use /comp manage to edit rosters and presets.")
            return embed

        selected = None
        if selected_schedule_id:
            for schedule in schedules:
                if schedule.schedule_id == selected_schedule_id:
                    selected = schedule
                    break
        if selected is None:
            selected = sorted(schedules, key=lambda item: item.name.casefold())[0]

        schedule_text = _format_schedule_text(selected)
        preset_label = selected.preset_name or "Unlinked preset"
        embed.add_field(
            name="Schedule",
            value=f"**{selected.name}**\n{schedule_text}",
            inline=False,
        )
        embed.add_field(name="Preset", value=preset_label, inline=False)

        preset = None
        if selected.preset_name:
            preset = preset_lookup.get(selected.preset_name.casefold())
        if preset is None:
            embed.add_field(name="Composition", value="Preset not found.", inline=False)
        else:
            embed.add_field(
                name="Composition",
                value=_format_class_summary(preset.config.classes),
                inline=False,
            )

            if preset.config.overview:
                overview = preset.config.overview.strip()
                if len(overview) > 300:
                    overview = overview[:297] + "..."
                embed.add_field(name="Overview", value=overview, inline=False)

        if len(schedules) > 1:
            other_lines = []
            for schedule in sorted(schedules, key=lambda item: item.name.casefold()):
                if schedule.schedule_id == selected.schedule_id:
                    continue
                other_text = _format_schedule_text(schedule)
                other_preset = schedule.preset_name or "Unlinked preset"
                other_lines.append(f"• {schedule.name} — {other_preset} — {other_text}")
            if other_lines:
                embed.add_field(name="Other Schedules", value="\n".join(other_lines), inline=False)
        embed.set_footer(text="Use /comp manage to edit rosters and presets.")
        return embed

    def _build_comp_embed(
        self,
        guild: discord.Guild,
        comp_config: CompConfig,
        *,
        channel: Optional[discord.abc.GuildChannel] = None,
    ) -> discord.Embed:
        _sanitize_signups(comp_config)
        if channel is None and comp_config.channel_id:
            resolved_channel = guild.get_channel(comp_config.channel_id)
            channel = resolved_channel
        embed = discord.Embed(title="Guild Composition Signup", color=BRAND_COLOUR)
        if comp_config.post_days and comp_config.post_time:
            day_names = _format_day_names(comp_config.post_days)
            if day_names:
                embed.description = (
                    f"Scheduled for **{day_names}** at **{comp_config.post_time}** {comp_config.timezone}.\n"
                    "Select your class using the dropdown below."
                )
            else:
                embed.description = "Select your class using the dropdown below."
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

        inline_count = 0
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
            inline_count += 1

        absent_signups = comp_config.signups.get(ABSENT_OPTION_NAME, [])
        absent_title = f"{ABSENT_EMOJI} {ABSENT_OPTION_NAME} ({len(absent_signups)})"
        embed.add_field(
            name=absent_title,
            value=_format_signups(guild, absent_signups),
            inline=True,
        )
        inline_count += 1

        if inline_count % 3 == 2:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.set_footer(text="Select the dropdown again to remove yourself from a class.")
        embed.timestamp = discord.utils.utcnow()
        return embed


async def setup(bot: GW2ToolsBot) -> None:  # pragma: no cover - discord.py lifecycle
    await bot.add_cog(CompCog(bot))
