"""Alliance guild WvW matchup reporting."""
from __future__ import annotations

import calendar
import csv
import io
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import GW2ToolsBot
from ..branding import BRAND_COLOUR
from ..constants import WVW_ALLIANCE_SHEET_TABS, WVW_SERVER_NAMES
from ..storage import GuildConfig, normalise_guild_id, utcnow

LOGGER = logging.getLogger(__name__)

GW2_GUILD_SEARCH_URL = "https://api.guildwars2.com/v2/guild/search"
GW2_GUILD_INFO_URL = "https://api.guildwars2.com/v2/guild/{guild_id}"
GW2_GUILD_WVW_URLS = (
    "https://api.guildwars2.com/v2/wvw/guilds/na",
    "https://api.guildwars2.com/v2/wvw/guilds/eu",
)
GW2_MATCHES_URL = "https://api.guildwars2.com/v2/wvw/matches"
SHEET_ID = "1Txjpcet-9FDVek6uJ0N3OciwgbpE0cfWozUK7ATfWx4"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
WVW_MATCHES = ["1-1", "1-2", "1-3", "1-4"]

PST = ZoneInfo("America/Los_Angeles")
PREDICTION_TIME = time(9, 0)
RESET_TIME = time(19, 30)
CHECK_INTERVAL_MINUTES = 15
DEFAULT_POST_DAY = 4
TIME_OPTION_MINUTES = (0, 15, 30, 45)

COLOR_EMOJI = {
    "green": "🟢",
    "blue": "🔵",
    "red": "🔴",
}


@dataclass(frozen=True)
class MatchTeam:
    color: str
    world_ids: Sequence[int]
    victory_points: int


@dataclass(frozen=True)
class TierPrediction:
    tier: int
    teams: Sequence[MatchTeam]


@dataclass(frozen=True)
class AllianceRoster:
    alliances: List[tuple[str, List[str]]]
    solo_guilds: List[str]


class AllianceScheduleView(discord.ui.View):
    def __init__(self, cog: "AllianceMatchupCog", guild: discord.Guild, config: GuildConfig) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config
        self.active_target = "prediction"
        self.target_select = _AllianceTargetSelect(self, row=0)
        self.day_select = _AllianceDaySelect(self, row=1)
        self.hour_select = _AllianceHourSelect(self, row=2)
        self.minute_select = _AllianceMinuteSelect(self, row=3)
        self.close_button = _AllianceCloseButton()
        self.add_item(self.target_select)
        self.add_item(self.day_select)
        self.add_item(self.hour_select)
        self.add_item(self.minute_select)
        self.add_item(self.close_button)
        self.sync_selects()

    def persist(self) -> None:
        self.cog.bot.save_config(self.guild.id, self.config)

    def _current_time(self) -> time:
        fallback = PREDICTION_TIME if self.active_target == "prediction" else RESET_TIME
        return self.cog._resolve_post_time(
            getattr(self.config, f"alliance_{self.active_target}_time"),
            fallback,
        )

    def sync_selects(self) -> None:
        current_day = self.cog._resolve_post_day(
            getattr(self.config, f"alliance_{self.active_target}_day"),
            DEFAULT_POST_DAY,
        )
        current_time = self._current_time()
        self.target_select.sync(current=self.active_target)
        self.day_select.sync(current_day=current_day)
        self.hour_select.sync(current_hour=current_time.hour)
        self.minute_select.sync(current_minute=current_time.minute)

    def build_message(self) -> str:
        prediction_day = self.cog._resolve_post_day(self.config.alliance_prediction_day, DEFAULT_POST_DAY)
        current_day = self.cog._resolve_post_day(self.config.alliance_current_day, DEFAULT_POST_DAY)
        prediction_time = self.cog._resolve_post_time(self.config.alliance_prediction_time, PREDICTION_TIME)
        current_time = self.cog._resolve_post_time(self.config.alliance_current_time, RESET_TIME)
        return (
            "Use the dropdowns below to configure when alliance matchup posts are sent.\n"
            f"**Prediction:** {self.cog._format_day(prediction_day)} at "
            f"**{self.cog._format_time(prediction_time)}** PST\n\n"
            f"**Current:** {self.cog._format_day(current_day)} at "
            f"**{self.cog._format_time(current_time)}** PST"
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class _AllianceTargetSelect(discord.ui.Select):
    def __init__(self, view: AllianceScheduleView, *, row: int) -> None:
        self.schedule_view = view
        options = [
            discord.SelectOption(label="Prediction schedule", value="prediction"),
            discord.SelectOption(label="Current schedule", value="current"),
        ]
        super().__init__(
            placeholder="Select schedule",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else "prediction"
        if selected not in {"prediction", "current"}:
            selected = "prediction"
        self.schedule_view.active_target = selected
        self.schedule_view.sync_selects()
        await interaction.response.edit_message(content=self.schedule_view.build_message(), view=self.schedule_view)

    def sync(self, *, current: str) -> None:
        for option in self.options:
            option.default = option.value == current
        self.placeholder = "Prediction schedule" if current == "prediction" else "Current schedule"


class _AllianceDaySelect(discord.ui.Select):
    def __init__(self, view: AllianceScheduleView, *, row: int) -> None:
        self.schedule_view = view
        options = [discord.SelectOption(label=calendar.day_name[index], value=str(index)) for index in range(7)]
        super().__init__(
            placeholder="Select day",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )

    def sync(self, *, current_day: int) -> None:
        self.placeholder = calendar.day_name[current_day]
        for option in self.options:
            option.default = option.value == str(current_day)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            day_value = int(self.values[0])
        except (TypeError, ValueError, IndexError):
            day_value = DEFAULT_POST_DAY
        setattr(self.schedule_view.config, f"alliance_{self.schedule_view.active_target}_day", day_value)
        self.schedule_view.persist()
        self.schedule_view.sync_selects()
        await interaction.response.edit_message(content=self.schedule_view.build_message(), view=self.schedule_view)


class _AllianceHourSelect(discord.ui.Select):
    def __init__(self, view: AllianceScheduleView, *, row: int) -> None:
        self.schedule_view = view
        options = [discord.SelectOption(label=f"{hour:02d}", value=str(hour)) for hour in range(24)]
        super().__init__(
            placeholder="Select hour",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )

    def sync(self, *, current_hour: int) -> None:
        self.placeholder = f"{current_hour:02d}"
        for option in self.options:
            option.default = option.value == str(current_hour)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            hour_value = int(self.values[0])
        except (TypeError, ValueError, IndexError):
            hour_value = 0
        base_time = self.schedule_view._current_time()
        new_time = time(hour_value, base_time.minute)
        setattr(
            self.schedule_view.config,
            f"alliance_{self.schedule_view.active_target}_time",
            self.schedule_view.cog._format_time(new_time),
        )
        self.schedule_view.persist()
        self.schedule_view.sync_selects()
        await interaction.response.edit_message(content=self.schedule_view.build_message(), view=self.schedule_view)


class _AllianceMinuteSelect(discord.ui.Select):
    def __init__(self, view: AllianceScheduleView, *, row: int) -> None:
        self.schedule_view = view
        options = [
            discord.SelectOption(label=f"{minute:02d}", value=str(minute))
            for minute in TIME_OPTION_MINUTES
        ]
        super().__init__(
            placeholder="Select minute",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )

    def sync(self, *, current_minute: int) -> None:
        self.placeholder = f"{current_minute:02d}"
        for option in self.options:
            option.default = option.value == str(current_minute)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            minute_value = int(self.values[0])
        except (TypeError, ValueError, IndexError):
            minute_value = 0
        base_time = self.schedule_view._current_time()
        new_time = time(base_time.hour, minute_value)
        setattr(
            self.schedule_view.config,
            f"alliance_{self.schedule_view.active_target}_time",
            self.schedule_view.cog._format_time(new_time),
        )
        self.schedule_view.persist()
        self.schedule_view.sync_selects()
        await interaction.response.edit_message(content=self.schedule_view.build_message(), view=self.schedule_view)


class _AllianceCloseButton(discord.ui.Button[AllianceScheduleView]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Close", row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Alliance matchup schedule configuration closed.", view=None)
        self.view.stop()


class AllianceMatchupCog(commands.GroupCog, name="alliance"):
    """Configure and post WvW matchup summaries for alliance guilds."""

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._guild_world_cache: Optional[Dict[str, int]] = None
        self._guild_world_cache_at: Optional[datetime] = None
        self._sheet_cache: Dict[str, AllianceRoster] = {}
        self._poster_loop.start()

    async def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poster_loop.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://gw2mists.com/",
                }
            )
        return self._session

    async def _fetch_json(self, url: str, *, params: Optional[Dict[str, str]] = None) -> object:
        session = await self._get_session()
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise ValueError(f"Request failed: {exc}") from exc

    async def _fetch_text(self, url: str, *, params: Optional[Dict[str, str]] = None) -> str:
        session = await self._get_session()
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientError as exc:
            raise ValueError(f"Request failed: {exc}") from exc

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _parse_hhmm(self, value: Optional[str]) -> Optional[time]:
        if not value:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parts = cleaned.split(":")
        if len(parts) != 2:
            return None
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
        except ValueError:
            return None
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return None
        return time(hours, minutes)

    def _resolve_post_time(self, value: Optional[str], fallback: time) -> time:
        parsed = self._parse_hhmm(value)
        return parsed if parsed else fallback

    def _format_time(self, value: time) -> str:
        return value.strftime("%H:%M")

    def _resolve_post_day(self, value: Optional[int], fallback: int) -> int:
        if isinstance(value, int) and 0 <= value <= 6:
            return value
        return fallback

    def _format_day(self, value: int) -> str:
        return calendar.day_name[value] if 0 <= value <= 6 else "Unknown"

    async def _lookup_guild(self, name: str) -> Optional[tuple[str, str]]:
        try:
            results = await self._fetch_json(GW2_GUILD_SEARCH_URL, params={"name": name})
        except ValueError:
            return None
        if not isinstance(results, list) or not results:
            return None
        candidate_ids = [gid for gid in results if isinstance(gid, str)]
        if not candidate_ids:
            return None

        normalized_name = name.strip().casefold()
        best_match: Optional[tuple[str, str]] = None
        for guild_id in candidate_ids:
            try:
                details = await self._fetch_json(GW2_GUILD_INFO_URL.format(guild_id=guild_id))
            except ValueError:
                continue
            if not isinstance(details, dict):
                continue
            guild_name = details.get("name")
            tag = details.get("tag")
            if not isinstance(guild_name, str):
                continue
            label = f"{guild_name} [{tag}]" if isinstance(tag, str) and tag else guild_name
            if guild_name.casefold() == normalized_name:
                return guild_id, label
            if best_match is None:
                best_match = (guild_id, label)
        return best_match

    async def _fetch_guild_world_map(self) -> Dict[str, int]:
        now = datetime.now(timezone.utc)
        if self._guild_world_cache and self._guild_world_cache_at:
            if (now - self._guild_world_cache_at).total_seconds() < 3600:
                return self._guild_world_cache
        mapped: Dict[str, int] = {}
        for url in GW2_GUILD_WVW_URLS:
            payload = await self._fetch_json(url)
            if not isinstance(payload, dict):
                raise ValueError("Unexpected response from GW2 guild WvW endpoint")
            for guild_id, world_id in payload.items():
                if not isinstance(guild_id, str):
                    continue
                normalized = normalise_guild_id(guild_id)
                if not normalized:
                    continue
                try:
                    mapped[normalized] = int(world_id)
                except (TypeError, ValueError):
                    continue
        self._guild_world_cache = mapped
        self._guild_world_cache_at = now
        return mapped

    async def _resolve_guild_world(self, guild_id: str) -> Optional[int]:
        normalized = normalise_guild_id(guild_id)
        if not normalized:
            return None
        try:
            mapped = await self._fetch_guild_world_map()
        except ValueError:
            return None
        return mapped.get(normalized)

    def _resolve_tier(self, match: dict) -> int:
        tier = match.get("tier")
        if isinstance(tier, int):
            return tier
        match_id = match.get("id")
        if isinstance(match_id, str) and "-" in match_id:
            try:
                return int(match_id.split("-", maxsplit=1)[1])
            except ValueError:
                return 0
        return 0

    async def _fetch_matches(self) -> List[dict]:
        payload = await self._fetch_json(GW2_MATCHES_URL, params={"ids": ",".join(WVW_MATCHES)})
        if not isinstance(payload, list):
            raise ValueError("Unexpected response from GW2 matches endpoint")
        matches: List[dict] = []
        for match in payload:
            if not isinstance(match, dict):
                continue
            match["tier"] = self._resolve_tier(match)
            matches.append(match)
        return matches

    async def _fetch_match_for_world(self, world_id: int) -> Optional[dict]:
        payload = await self._fetch_json(GW2_MATCHES_URL, params={"world": str(world_id)})
        if not isinstance(payload, dict):
            raise ValueError("Unexpected response from GW2 matches endpoint")
        payload["tier"] = self._resolve_tier(payload)
        return payload

    def _extract_match_teams(self, match: dict) -> List[MatchTeam]:
        data = match.get("data") if isinstance(match.get("data"), dict) else {}
        worlds = match.get("all_worlds") if isinstance(match.get("all_worlds"), dict) else {}
        victory_points = match.get("victory_points") if isinstance(match.get("victory_points"), dict) else {}
        if not worlds:
            worlds = data.get("all_worlds") if isinstance(data.get("all_worlds"), dict) else {}
        if not victory_points:
            victory_points = data.get("victory_points") if isinstance(data.get("victory_points"), dict) else {}

        teams: List[MatchTeam] = []
        for color in ("green", "blue", "red"):
            world_ids = worlds.get(color)
            if not isinstance(world_ids, list):
                continue
            vp = victory_points.get(color)
            if not isinstance(vp, int):
                vp = 0
            teams.append(MatchTeam(color=color, world_ids=world_ids, victory_points=vp))
        return teams

    def _predict_tiers(self, matches: List[dict]) -> List[TierPrediction]:
        tiers: Dict[int, List[MatchTeam]] = {}
        max_tier = max((match.get("tier", 0) for match in matches if isinstance(match.get("tier"), int)), default=0)

        for match in matches:
            tier = match.get("tier")
            if not isinstance(tier, int):
                continue
            teams = self._extract_match_teams(match)
            if len(teams) != 3:
                continue
            ranked = sorted(teams, key=lambda team: team.victory_points, reverse=True)
            winner, middle, loser = ranked

            winner_tier = tier if tier == 1 else tier - 1
            winner_color = "green" if tier == 1 else "red"
            tiers.setdefault(winner_tier, []).append(
                MatchTeam(color=winner_color, world_ids=winner.world_ids, victory_points=winner.victory_points)
            )

            tiers.setdefault(tier, []).append(
                MatchTeam(color="blue", world_ids=middle.world_ids, victory_points=middle.victory_points)
            )

            loser_tier = tier if tier == max_tier else tier + 1
            loser_color = "red" if tier == max_tier else "green"
            tiers.setdefault(loser_tier, []).append(
                MatchTeam(color=loser_color, world_ids=loser.world_ids, victory_points=loser.victory_points)
            )

        predictions: List[TierPrediction] = []
        color_order = {"green": 0, "blue": 1, "red": 2}
        for tier, team_list in tiers.items():
            ordered = sorted(team_list, key=lambda team: color_order.get(team.color, 99))
            if ordered:
                predictions.append(TierPrediction(tier=tier, teams=ordered))
        return sorted(predictions, key=lambda item: item.tier)

    async def _fetch_alliances(self, sheet_name: str) -> AllianceRoster:
        if sheet_name in self._sheet_cache:
            return self._sheet_cache[sheet_name]
        try:
            text = await self._fetch_text(SHEET_URL, params={"tqx": "out:csv", "sheet": sheet_name})
        except ValueError:
            roster = AllianceRoster(alliances=[], solo_guilds=[])
            self._sheet_cache[sheet_name] = roster
            return roster

        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        alliances: List[tuple[str, List[str]]] = []
        solo_guilds: List[str] = []
        in_solo = False

        def _normalized_text(value: str) -> str:
            normalized = unicodedata.normalize("NFKD", value)
            filtered = "".join(char for char in normalized if char.isalpha() or char.isspace())
            return " ".join(filtered.split()).casefold()

        def _is_solo_header(value: str) -> bool:
            return "solo" in _normalized_text(value)

        for row in rows[1:]:
            first = row[0].strip() if len(row) > 0 and row[0] else ""
            second = row[1].strip() if len(row) > 1 and row[1] else ""
            if not first and not second:
                continue
            if (first and _is_solo_header(first)) or (second and _is_solo_header(second)):
                in_solo = True
                continue
            if in_solo:
                if first:
                    solo_guilds.extend([line.strip() for line in first.splitlines() if line.strip()])
                if second:
                    solo_guilds.extend([line.strip() for line in second.splitlines() if line.strip()])
                continue
            if not first:
                continue
            guilds: List[str] = []
            if second:
                for line in second.splitlines():
                    cleaned = line.strip()
                    if cleaned:
                        guilds.append(cleaned)
            alliances.append((first, guilds))

        roster = AllianceRoster(alliances=alliances, solo_guilds=solo_guilds)
        self._sheet_cache[sheet_name] = roster
        return roster

    async def _resolve_team_alliances(self, world_ids: Sequence[int]) -> AllianceRoster:
        alliance_map: Dict[str, List[str]] = {}
        solo_seen: set[str] = set()
        solo_guilds: List[str] = []
        for world_id in world_ids:
            sheet_name = WVW_ALLIANCE_SHEET_TABS.get(world_id)
            if not sheet_name:
                continue
            roster = await self._fetch_alliances(sheet_name)
            for name, guilds in roster.alliances:
                existing = alliance_map.setdefault(name, [])
                for guild in guilds:
                    if guild not in existing:
                        existing.append(guild)
            for guild in roster.solo_guilds:
                if guild not in solo_seen:
                    solo_seen.add(guild)
                    solo_guilds.append(guild)
        return AllianceRoster(alliances=list(alliance_map.items()), solo_guilds=solo_guilds)

    def _format_worlds(self, world_ids: Sequence[int]) -> str:
        names: List[str] = []
        for world_id in world_ids:
            name = WVW_SERVER_NAMES.get(world_id)
            if name:
                names.append(name)
        return ", ".join(names) if names else "Unknown world"

    def _format_alliance_list(self, roster: AllianceRoster) -> str:
        if not roster.alliances and not roster.solo_guilds:
            return "No roster data found."
        alliances_lines: List[str] = []
        for name, guilds in roster.alliances:
            alliances_lines.append(f"**{name}**")
            for guild in guilds:
                alliances_lines.append(f"• {guild}")
            alliances_lines.append("")
        solo_lines: List[str] = []
        if roster.solo_guilds:
            if alliances_lines and alliances_lines[-1] != "":
                solo_lines.append("")
            solo_lines.append("**Solo Guilds**")
            for guild in roster.solo_guilds:
                solo_lines.append(f"• {guild}")
        lines = alliances_lines + solo_lines
        while lines and lines[-1] == "":
            lines.pop()
        combined = "\n".join(lines)
        if len(combined) <= 1000:
            return combined
        trimmed: List[str] = []
        max_length = 980

        def _lines_length(items: List[str]) -> int:
            if not items:
                return 0
            return sum(len(item) for item in items) + (len(items) - 1)

        def _trim_lines(items: List[str], limit: int) -> List[str]:
            kept: List[str] = []
            total = 0
            for line in items:
                next_total = total + len(line) + (1 if kept else 0)
                if next_total > limit:
                    break
                kept.append(line)
                total = next_total
            return kept

        if solo_lines:
            solo_length = _lines_length(solo_lines)
            if solo_length > max_length:
                trimmed = _trim_lines(solo_lines, max_length)
                return "\n".join(trimmed) + "\n…"
            remaining = max_length - solo_length
            trimmed = _trim_lines(alliances_lines, remaining)
            if not trimmed and solo_lines and solo_lines[0] == "":
                solo_lines = solo_lines[1:]
            return "\n".join(trimmed + solo_lines) + "\n…"

        trimmed = _trim_lines(lines, max_length)
        return "\n".join(trimmed) + "\n…"

    def _build_embed(
        self,
        *,
        title: str,
        config: GuildConfig,
        tier: int,
        teams: Sequence[MatchTeam],
        home_world_id: int,
        alliances: Dict[str, AllianceRoster],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            color=BRAND_COLOUR,
        )
        embed.add_field(
            name="Alliance guild",
            value=config.alliance_guild_name or "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Home world",
            value=WVW_SERVER_NAMES.get(home_world_id, str(home_world_id)),
            inline=True,
        )
        home_team = next((team for team in teams if home_world_id in team.world_ids), None)
        if home_team:
            color_label = home_team.color.capitalize()
            emoji = COLOR_EMOJI.get(home_team.color, "")
            embed.add_field(name="Your team color", value=f"{emoji} {color_label}".strip(), inline=True)
        embed.add_field(name="Tier", value=f"Tier {tier}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        for team in teams:
            world_label = self._format_worlds(team.world_ids)
            is_home = home_world_id in team.world_ids
            color_name = team.color.capitalize()
            if is_home:
                continue
            name = f"{COLOR_EMOJI.get(team.color, '')} {color_name} — {world_label}"
            alliance_list = alliances.get(world_label, AllianceRoster(alliances=[], solo_guilds=[]))
            value = self._format_alliance_list(alliance_list)
            embed.add_field(name=name, value=value, inline=True)

        return embed

    async def _resolve_channel(self, guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel is not None:
            return None
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    async def _refresh_guild_world(self, config: GuildConfig) -> Optional[int]:
        if not config.alliance_guild_id:
            return None
        world_id = await self._resolve_guild_world(config.alliance_guild_id)
        if world_id:
            config.alliance_server_id = world_id
            config.alliance_server_name = WVW_SERVER_NAMES.get(world_id, str(world_id))
        return world_id

    async def _post_matchup(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        config: GuildConfig,
        prediction: bool,
    ) -> bool:
        world_id = config.alliance_server_id
        if not world_id:
            world_id = await self._refresh_guild_world(config)
        if not world_id:
            LOGGER.warning("No WvW world configured for guild %s", guild.id)
            return False

        try:
            matches = await self._fetch_matches()
        except ValueError:
            LOGGER.warning("Failed to fetch WvW matches", exc_info=True)
            return False

        if prediction:
            tiers = self._predict_tiers(matches)
            tier_match: Optional[TierPrediction] = None
            for entry in tiers:
                if any(world_id in team.world_ids for team in entry.teams):
                    tier_match = entry
                    break
            if not tier_match:
                LOGGER.warning("No predicted matchup found for world %s", world_id)
                return False
            teams = tier_match.teams
            tier = tier_match.tier
            title = "Predictive WvW Matchup"
        else:
            try:
                match = await self._fetch_match_for_world(world_id)
            except ValueError:
                LOGGER.warning("Failed to fetch WvW match for world %s", world_id, exc_info=True)
                return False
            if not match:
                LOGGER.warning("No matchup found for world %s", world_id)
                return False
            teams = self._extract_match_teams(match)
            tier = match.get("tier", 0)
            title = "Current WvW Matchup"

        alliances: Dict[str, AllianceRoster] = {}
        for team in teams:
            world_label = self._format_worlds(team.world_ids)
            alliances[world_label] = await self._resolve_team_alliances(team.world_ids)

        embed = self._build_embed(
            title=title,
            config=config,
            tier=tier,
            teams=teams,
            home_world_id=world_id,
            alliances=alliances,
        )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            LOGGER.warning("Failed to send WvW matchup post in guild %s", guild.id, exc_info=True)
            return False

        now_iso = utcnow()
        if prediction:
            config.alliance_last_prediction_at = now_iso
        else:
            config.alliance_last_actual_at = now_iso
        self.bot.save_config(guild.id, config)
        return True

    def _already_posted(self, timestamp: Optional[str], now: datetime) -> bool:
        last_post = self._parse_timestamp(timestamp)
        if not last_post:
            return False
        return last_post.astimezone(PST).date() == now.date()

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poster_loop(self) -> None:  # pragma: no cover - requires Discord
        if not self.bot.guilds:
            return
        now = datetime.now(PST)
        for guild in self.bot.guilds:
            config = self.bot.get_config(guild.id)
            if not config.alliance_channel_id or not config.alliance_guild_id:
                continue
            channel = await self._resolve_channel(guild, config.alliance_channel_id)
            if not channel:
                continue
            prediction_time = self._resolve_post_time(config.alliance_prediction_time, PREDICTION_TIME)
            current_time = self._resolve_post_time(config.alliance_current_time, RESET_TIME)
            prediction_day = self._resolve_post_day(config.alliance_prediction_day, DEFAULT_POST_DAY)
            current_day = self._resolve_post_day(config.alliance_current_day, DEFAULT_POST_DAY)
            if now.weekday() == prediction_day:
                if prediction_day == current_day:
                    if now.time() >= prediction_time and now.time() < current_time:
                        if not self._already_posted(config.alliance_last_prediction_at, now):
                            await self._post_matchup(guild=guild, channel=channel, config=config, prediction=True)
                else:
                    if now.time() >= prediction_time:
                        if not self._already_posted(config.alliance_last_prediction_at, now):
                            await self._post_matchup(guild=guild, channel=channel, config=config, prediction=True)
            if now.weekday() == current_day and now.time() >= current_time:
                if not self._already_posted(config.alliance_last_actual_at, now):
                    await self._post_matchup(guild=guild, channel=channel, config=config, prediction=False)

    @_poster_loop.before_loop
    async def _before_loop(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    @app_commands.command(name="setguild", description="Set the alliance guild to track for WvW matchups.")
    async def set_guild(self, interaction: discord.Interaction, guild_name: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        cleaned_name = guild_name.strip()
        if not cleaned_name:
            await interaction.response.send_message("Please provide a guild name.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        lookup = await self._lookup_guild(cleaned_name)
        if not lookup:
            await interaction.followup.send("No guild found with that name.", ephemeral=True)
            return
        guild_id, guild_label = lookup
        config = self.bot.get_config(interaction.guild.id)
        config.alliance_guild_id = guild_id
        config.alliance_guild_name = guild_label
        world_id = await self._refresh_guild_world(config)
        self.bot.save_config(interaction.guild.id, config)
        if world_id:
            server_name = WVW_SERVER_NAMES.get(world_id, str(world_id))
            await interaction.followup.send(
                f"Alliance guild set to **{guild_label}**. Current WvW world: **{server_name}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Alliance guild set to **{guild_label}**. WvW world not found yet.",
                ephemeral=True,
            )

    @app_commands.command(name="setchannel", description="Set the channel for WvW matchup posts.")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        config.alliance_channel_id = channel.id
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message(
            f"Alliance matchup posts will be sent to {channel.mention}.", ephemeral=True
        )

    @app_commands.command(
        name="settime",
        description="Configure when the predictive and current matchup posts are sent.",
    )
    async def set_times(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        view = AllianceScheduleView(self, interaction.guild, config)
        await interaction.response.send_message(
            view.build_message(),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show alliance matchup configuration.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        config = self.bot.get_config(interaction.guild.id)
        guild_label = config.alliance_guild_name or "Not set"
        channel_obj = (
            interaction.guild.get_channel(config.alliance_channel_id)
            if config.alliance_channel_id
            else None
        )
        channel_label = channel_obj.mention if channel_obj else "Not set"
        world_label = config.alliance_server_name or "Unknown"
        prediction_time = self._format_time(
            self._resolve_post_time(config.alliance_prediction_time, PREDICTION_TIME)
        )
        current_time = self._format_time(self._resolve_post_time(config.alliance_current_time, RESET_TIME))
        prediction_day = self._format_day(
            self._resolve_post_day(config.alliance_prediction_day, DEFAULT_POST_DAY)
        )
        current_day = self._format_day(
            self._resolve_post_day(config.alliance_current_day, DEFAULT_POST_DAY)
        )
        embed = discord.Embed(title="Alliance matchup settings", color=BRAND_COLOUR)
        embed.add_field(name="Guild", value=guild_label, inline=False)
        embed.add_field(name="Channel", value=channel_label, inline=False)
        embed.add_field(name="WvW World", value=world_label, inline=False)
        embed.add_field(
            name="Post Times (PST)",
            value=(
                f"Prediction: **{prediction_day}** at **{prediction_time}**\n"
                f"Current: **{current_day}** at **{current_time}**"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="postnow",
        description="Post the WvW matchup summary immediately (admin only).",
    )
    async def post_now(self, interaction: discord.Interaction, prediction: bool = False) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        if not config.alliance_channel_id:
            await interaction.response.send_message(
                "Alliance matchup channel is not configured yet.", ephemeral=True
            )
            return
        channel = await self._resolve_channel(interaction.guild, config.alliance_channel_id)
        if not channel:
            await interaction.response.send_message(
                "Unable to resolve the configured matchup channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        mode = "prediction" if prediction else "results"
        posted = await self._post_matchup(
            guild=interaction.guild, channel=channel, config=config, prediction=prediction
        )
        if posted:
            await interaction.followup.send(f"Posted matchup {mode} to {channel.mention}.", ephemeral=True)
        else:
            await interaction.followup.send(
                "Unable to post the matchup right now. Check bot permissions and logs for details.",
                ephemeral=True,
            )


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(AllianceMatchupCog(bot))
