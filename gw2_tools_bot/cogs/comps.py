"""Guild composition scheduling and signup management."""
from __future__ import annotations

import calendar
import io
import logging
import re
from datetime import datetime, time as time_cls, timezone
from typing import Dict, List, Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import constants
from ..bot import GW2ToolsBot
from ..storage import CompClassConfig, CompConfig, GuildConfig

LOGGER = logging.getLogger(__name__)

ICON_SHEET_FILENAME = "comp_icons.png"
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


def _build_icon_sheet(classes: Sequence[CompClassConfig]) -> Optional[discord.File]:
    if not classes:
        return None

    icons: List[Image.Image] = []
    for entry in classes:
        icon_path = _icon_path_for_class(entry.name)
        if not icon_path:
            continue
        try:
            image = Image.open(icon_path).convert("RGBA")
        except (FileNotFoundError, OSError):
            continue
        image.thumbnail((64, 64))
        icons.append(image)

    if not icons:
        return None

    columns = min(4, max(1, len(icons)))
    rows = (len(icons) + columns - 1) // columns
    width = columns * 70
    height = rows * 70
    sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    for index, icon in enumerate(icons):
        row = index // columns
        col = index % columns
        x = col * 70 + (70 - icon.width) // 2
        y = row * 70 + (70 - icon.height) // 2
        sheet.alpha_composite(icon, (x, y))

    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=ICON_SHEET_FILENAME)


def _format_signups(guild: discord.Guild, signups: Sequence[int]) -> str:
    if not signups:
        return "No signups yet."

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


def _build_summary_embed(guild: discord.Guild, config: CompConfig) -> discord.Embed:
    embed = discord.Embed(title="Guild Composition Settings", color=discord.Color.blurple())
    schedule_text = "Posting schedule not configured."
    if config.post_day is not None and config.post_time:
        schedule_text = f"Scheduled for **{_get_day_name(config.post_day)}** at **{config.post_time}** {config.timezone}."
    if config.channel_id:
        channel = guild.get_channel(config.channel_id)
        if channel:
            channel_value = channel.mention
        else:
            channel_value = f"<#{config.channel_id}>"
    else:
        channel_value = "Not set"
    embed.add_field(name="Post Channel", value=channel_value, inline=False)
    embed.add_field(name="Schedule", value=schedule_text, inline=False)

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


class CompSignupView(discord.ui.View):
    def __init__(self, cog: "CompCog", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.add_item(CompSignupSelect(self))


class CompSignupSelect(discord.ui.Select):
    def __init__(self, view: CompSignupView):
        self.comp_view = view
        config = view.cog.bot.get_config(view.guild_id).comp
        _sanitize_signups(config)
        options: List[discord.SelectOption] = []
        for entry in config.classes:
            description = "Sign up for this class"
            if entry.required is not None:
                description = f"{entry.required} needed"
            options.append(
                discord.SelectOption(
                    label=entry.name,
                    value=entry.name,
                    description=description,
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
        await view.cog.post_composition(view.guild.id, reset_signups=False)
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
        embed = _build_summary_embed(self.guild, self.config.comp)
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            LOGGER.warning("Failed to update composition configuration summary for guild %s", self.guild.id)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _schedule_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ScheduleModal(self))

    async def _classes_callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        await interaction.response.send_modal(ClassesModal(self))


class CompCog(commands.GroupCog, name="comp"):
    """Schedule and manage guild composition signups."""

    POST_CHECK_INTERVAL_MINUTES = 1

    def __init__(self, bot: GW2ToolsBot) -> None:
        super().__init__()
        self.bot = bot
        self.poster_loop.start()

    async def cog_load(self) -> None:
        await super().cog_load()
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._register_persistent_view(guild.id)

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self.poster_loop.cancel()

    @app_commands.command(name="manage", description="Configure the scheduled composition post for this guild.")
    async def manage(self, interaction: discord.Interaction) -> None:  # pragma: no cover - requires Discord
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        view = CompConfigView(self, interaction.guild, config)
        embed = _build_summary_embed(interaction.guild, config.comp)
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

    async def post_composition(self, guild_id: int, *, reset_signups: bool) -> None:
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

        embed, file = self._build_comp_embed(guild, comp_config)
        view = CompSignupView(self, guild_id)

        message = None
        if comp_config.message_id:
            try:
                message = await channel.fetch_message(comp_config.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            try:
                if file:
                    new_message = await channel.send(embed=embed, view=view, files=[file])
                else:
                    new_message = await channel.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to send composition message in guild %s", guild_id)
                return
            comp_config.message_id = new_message.id
        else:
            try:
                edit_kwargs: Dict[str, object] = {"embed": embed, "view": view}
                if file:
                    edit_kwargs["attachments"] = [file]
                else:
                    edit_kwargs["attachments"] = []
                await message.edit(**edit_kwargs)
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

        embed, file = self._build_comp_embed(guild, comp_config)
        view = CompSignupView(self, guild_id)
        kwargs: Dict[str, object] = {"embed": embed, "view": view}
        if file:
            kwargs["attachments"] = [file]
        else:
            kwargs["attachments"] = []
        try:
            await message.edit(**kwargs)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Failed to refresh composition message in guild %s", guild_id)
            return
        self.bot.add_view(view, message_id=message.id)

    async def _register_persistent_view(self, guild_id: int) -> None:
        config = self.bot.get_config(guild_id)
        comp_config = config.comp
        if comp_config.message_id:
            _sanitize_signups(comp_config)
            view = CompSignupView(self, guild_id)
            self.bot.add_view(view, message_id=comp_config.message_id)

    def _build_comp_embed(self, guild: discord.Guild, comp_config: CompConfig) -> tuple[discord.Embed, Optional[discord.File]]:
        embed = discord.Embed(title="Guild Composition Signup", color=discord.Color.dark_teal())
        if comp_config.post_day is not None and comp_config.post_time:
            embed.description = (
                f"Scheduled for **{_get_day_name(comp_config.post_day)}** at **{comp_config.post_time}** {comp_config.timezone}.\n"
                "Select your class using the dropdown below."
            )
        else:
            embed.description = "Select your class using the dropdown below."

        for entry in comp_config.classes:
            signups = comp_config.signups.get(entry.name, [])
            requirement = "Optional"
            if entry.required is not None:
                requirement = f"Needed: {entry.required}"
            value = f"{requirement}\nTotal: {len(signups)}\n{_format_signups(guild, signups)}"
            embed.add_field(name=entry.name, value=value, inline=True)

        embed.set_footer(text="Select the dropdown again to remove yourself from a class.")
        embed.timestamp = discord.utils.utcnow()

        file = _build_icon_sheet(comp_config.classes)
        if file:
            embed.set_image(url=f"attachment://{ICON_SHEET_FILENAME}")
        return embed, file


async def setup(bot: GW2ToolsBot) -> None:  # pragma: no cover - discord.py lifecycle
    await bot.add_cog(CompCog(bot))
