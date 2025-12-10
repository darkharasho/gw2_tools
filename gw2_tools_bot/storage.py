"""Persistent storage utilities for the GW2 Tools bot."""
from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ISOFORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

ZERO_WIDTH_CHARS = {
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
    "\u2060",  # word joiner
    "\ufeff",  # zero width no-break space / BOM
}
ZERO_WIDTH_TRANSLATION = {ord(char): None for char in ZERO_WIDTH_CHARS}


def normalise_timezone(value: str) -> str:
    """Return a cleaned timezone string suitable for IANA lookup."""

    if value is None:
        cleaned = ""
    else:
        cleaned = str(value)

    # Remove zero width characters that sneak through Discord inputs
    if ZERO_WIDTH_TRANSLATION:
        cleaned = cleaned.translate(ZERO_WIDTH_TRANSLATION)

    # Drop any remaining control / format characters that break ZoneInfo lookup
    cleaned = "".join(
        ch for ch in cleaned if unicodedata.category(ch) not in {"Cf", "Cc"}
    )

    # Replace non-breaking spaces with normal spaces so stripping works reliably
    cleaned = cleaned.replace("\u00a0", " ").replace("\u202f", " ")

    cleaned = cleaned.strip()
    if "  " in cleaned:
        cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "UTC"
    return cleaned


def utcnow() -> str:
    """Return the current UTC timestamp formatted for storage."""

    return datetime.utcnow().strftime(ISOFORMAT)


@dataclass
class CompClassConfig:
    """Configuration for an individual class within a scheduled composition."""

    name: str
    required: Optional[int] = None
    emoji_id: Optional[int] = None


@dataclass
class CompConfig:
    """Composition scheduling and signup configuration."""

    channel_id: Optional[int] = None
    ping_role_id: Optional[int] = None
    post_days: List[int] = field(default_factory=list)
    post_time: Optional[str] = None
    timezone: str = "UTC"
    overview: str = ""
    classes: List[CompClassConfig] = field(default_factory=list)
    signups: Dict[str, List[int]] = field(default_factory=dict)
    message_id: Optional[int] = None
    last_post_at: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CompConfig":
        classes_payload = payload.get("classes", []) or []
        classes: List[CompClassConfig] = []
        for item in classes_payload:
            if not isinstance(item, dict):
                continue
            try:
                classes.append(CompClassConfig(**item))
            except TypeError:
                continue

        signups_payload = payload.get("signups", {}) or {}
        signups: Dict[str, List[int]] = {}
        for class_name, values in signups_payload.items():
            if not isinstance(class_name, str) or not isinstance(values, list):
                continue
            valid: List[int] = []
            for raw in values:
                if isinstance(raw, int):
                    valid.append(raw)
                elif isinstance(raw, str):
                    try:
                        valid.append(int(raw))
                    except ValueError:
                        continue
            signups[class_name] = valid

        post_days_payload = payload.get("post_days")
        post_days: List[int] = []
        if isinstance(post_days_payload, list):
            for raw in post_days_payload:
                candidate: Optional[int] = None
                if isinstance(raw, int):
                    candidate = raw
                elif isinstance(raw, str):
                    try:
                        candidate = int(raw)
                    except ValueError:
                        candidate = None
                if candidate is None:
                    continue
                if 0 <= candidate <= 6 and candidate not in post_days:
                    post_days.append(candidate)

        post_day = payload.get("post_day")
        if post_days:
            post_day = None
        elif isinstance(post_day, str):
            try:
                post_day = int(post_day)
            except ValueError:
                post_day = None
        if isinstance(post_day, int) and 0 <= post_day <= 6:
            post_days.append(post_day)

        timezone_value = payload.get("timezone", "UTC")
        timezone_value = normalise_timezone(timezone_value)
        ping_role_raw = payload.get("ping_role_id")
        ping_role_id: Optional[int]
        if isinstance(ping_role_raw, int):
            ping_role_id = ping_role_raw
        elif isinstance(ping_role_raw, str):
            try:
                ping_role_id = int(ping_role_raw)
            except ValueError:
                ping_role_id = None
        else:
            ping_role_id = None
        return cls(
            channel_id=payload.get("channel_id"),
            ping_role_id=ping_role_id,
            post_days=post_days,
            post_time=payload.get("post_time"),
            timezone=timezone_value,
            overview=payload.get("overview", ""),
            classes=classes,
            signups=signups,
            message_id=payload.get("message_id"),
            last_post_at=payload.get("last_post_at"),
        )

    def copy(self, *, include_runtime_fields: bool = True) -> "CompConfig":
        """Return a duplicate of this configuration.

        When ``include_runtime_fields`` is ``False`` transient values such as
        signups, stored message identifiers, and timestamps are stripped. This
        is useful when persisting reusable presets.
        """

        payload = asdict(self)
        if not include_runtime_fields:
            payload["signups"] = {}
            payload["message_id"] = None
            payload["last_post_at"] = None
        return CompConfig.from_dict(payload)


@dataclass
class GuildConfig:
    """Server-specific configuration."""

    moderator_role_ids: List[int]
    guild_role_ids: Dict[str, int] = field(default_factory=dict)
    build_channel_id: Optional[int] = None
    arcdps_channel_id: Optional[int] = None
    update_notes_channel_id: Optional[int] = None
    comp: CompConfig = field(default_factory=CompConfig)
    comp_active_preset: Optional[str] = None

    @classmethod
    def default(cls) -> "GuildConfig":
        return cls(moderator_role_ids=[], guild_role_ids={}, comp=CompConfig())


@dataclass
class CompPreset:
    """Named reusable composition configuration."""

    name: str
    config: CompConfig

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CompPreset":
        raw_name = payload.get("name")
        if not isinstance(raw_name, str):
            raise ValueError("Preset name must be a string")
        name = raw_name.strip()
        if not name:
            raise ValueError("Preset name cannot be empty")
        config_payload = payload.get("config")
        if not isinstance(config_payload, dict):
            config_payload = {}
        config = CompConfig.from_dict(config_payload)
        return cls(name=name, config=config)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "config": asdict(self.config.copy(include_runtime_fields=False)),
        }


@dataclass
class RssFeedConfig:
    """Persisted configuration for an RSS or Atom feed subscription."""

    name: str
    url: str
    channel_id: int
    last_entry_id: Optional[str] = None
    last_entry_published_at: Optional[str] = None


@dataclass
class BuildRecord:
    """Persisted representation of a Guild Wars 2 build."""

    build_id: str
    name: str
    profession: str
    specialization: Optional[str]
    url: Optional[str]
    chat_code: str
    description: Optional[str]
    created_by: int
    created_at: str
    updated_by: int
    updated_at: str
    message_id: Optional[int] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None


@dataclass
class ArcDpsStatus:
    """Persisted information about the latest ArcDPS release."""

    last_checked_at: Optional[str] = None
    last_updated_at: Optional[str] = None


@dataclass
class UpdateNotesStatus:
    """Persisted information about the latest posted game update notes."""

    last_entry_id: Optional[str] = None
    last_entry_published_at: Optional[str] = None


@dataclass
class ApiKeyRecord:
    """Persisted Guild Wars 2 API key details for a member."""

    name: str
    key: str
    account_name: str = ""
    permissions: List[str] = field(default_factory=list)
    guild_ids: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ApiKeyRecord":
        if not isinstance(payload, dict):
            raise ValueError("API key payload must be a dictionary")

        name_raw = payload.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise ValueError("API key name must be provided")
        key_raw = payload.get("key")
        if not isinstance(key_raw, str) or not key_raw.strip():
            raise ValueError("API key value must be provided")

        permissions_payload = payload.get("permissions") or []
        permissions: List[str] = []
        if isinstance(permissions_payload, list):
            for value in permissions_payload:
                if isinstance(value, str):
                    cleaned = value.strip()
                    if cleaned:
                        permissions.append(cleaned)

        guilds_payload = payload.get("guild_ids") or []
        guild_ids: List[str] = []
        if isinstance(guilds_payload, list):
            for guild_id in guilds_payload:
                if isinstance(guild_id, str):
                    cleaned = guild_id.strip()
                    if cleaned:
                        guild_ids.append(cleaned)

        account_name_raw = payload.get("account_name")
        account_name = account_name_raw.strip() if isinstance(account_name_raw, str) else ""

        created_at = payload.get("created_at") or utcnow()
        updated_at = payload.get("updated_at") or created_at

        return cls(
            name=name_raw.strip(),
            key=key_raw.strip(),
            account_name=account_name,
            permissions=permissions,
            guild_ids=guild_ids,
            created_at=created_at,
            updated_at=updated_at,
        )


class StorageManager:
    """Handle isolated storage per guild to respect data privacy."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _guild_path(self, guild_id: int) -> Path:
        guild_path = self.root / f"guild_{guild_id}"
        guild_path.mkdir(exist_ok=True)
        return guild_path

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, data: Any) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def get_config(self, guild_id: int) -> GuildConfig:
        path = self._guild_path(guild_id) / "config.json"
        payload = self._read_json(path, None)
        if not payload:
            return GuildConfig.default()
        payload = dict(payload)
        guild_role_ids = payload.get("guild_role_ids")
        if isinstance(guild_role_ids, dict):
            cleaned_roles: Dict[str, int] = {}
            for guild_key, role_id in guild_role_ids.items():
                if not isinstance(guild_key, str):
                    continue
                if isinstance(role_id, bool):
                    continue
                if isinstance(role_id, int):
                    cleaned_roles[guild_key] = role_id
                elif isinstance(role_id, str):
                    try:
                        cleaned_roles[guild_key] = int(role_id)
                    except ValueError:
                        continue
            payload["guild_role_ids"] = cleaned_roles
        else:
            payload["guild_role_ids"] = {}
        comp_payload = payload.get("comp")
        if isinstance(comp_payload, dict):
            payload["comp"] = CompConfig.from_dict(comp_payload)
        else:
            payload["comp"] = CompConfig()
        active_preset = payload.get("comp_active_preset")
        if isinstance(active_preset, str):
            payload["comp_active_preset"] = active_preset.strip() or None
        else:
            payload["comp_active_preset"] = None
        return GuildConfig(**payload)

    def save_config(self, guild_id: int, config: GuildConfig) -> None:
        if config.comp:
            config.comp.timezone = normalise_timezone(config.comp.timezone)
        if config.comp_active_preset:
            cleaned = str(config.comp_active_preset).strip()
            config.comp_active_preset = cleaned or None
        if config.guild_role_ids:
            cleaned_roles: Dict[str, int] = {}
            for guild_key, role_id in config.guild_role_ids.items():
                if not isinstance(guild_key, str):
                    continue
                guild_key = guild_key.strip()
                if not guild_key:
                    continue
                if isinstance(role_id, bool):
                    continue
                if isinstance(role_id, int):
                    cleaned_roles[guild_key] = role_id
                elif isinstance(role_id, str):
                    try:
                        cleaned_roles[guild_key] = int(role_id)
                    except ValueError:
                        continue
            config.guild_role_ids = cleaned_roles
        path = self._guild_path(guild_id) / "config.json"
        self._write_json(path, asdict(config))

    # ------------------------------------------------------------------
    # Composition presets
    # ------------------------------------------------------------------
    def get_comp_presets(self, guild_id: int) -> List[CompPreset]:
        path = self._guild_path(guild_id) / "comp_presets.json"
        payload = self._read_json(path, [])
        presets: List[CompPreset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                presets.append(CompPreset.from_dict(item))
            except ValueError:
                continue
        presets.sort(key=lambda preset: preset.name.casefold())
        return presets

    def save_comp_presets(self, guild_id: int, presets: List[CompPreset]) -> None:
        path = self._guild_path(guild_id) / "comp_presets.json"
        serialized = [preset.to_dict() for preset in presets]
        self._write_json(path, serialized)

    # ------------------------------------------------------------------
    # ArcDPS updates
    # ------------------------------------------------------------------
    def get_arcdps_status(self, guild_id: int) -> Optional[ArcDpsStatus]:
        path = self._guild_path(guild_id) / "arcdps.json"
        payload = self._read_json(path, None)
        if not payload:
            return None
        if "last_checked_at" not in payload and "last_updated_at" in payload:
            payload["last_checked_at"] = payload["last_updated_at"]
        return ArcDpsStatus(**payload)

    def save_arcdps_status(self, guild_id: int, status: ArcDpsStatus) -> None:
        path = self._guild_path(guild_id) / "arcdps.json"
        self._write_json(path, asdict(status))

    # ------------------------------------------------------------------
    # Game update notes
    # ------------------------------------------------------------------
    def get_update_notes_status(self, guild_id: int) -> Optional[UpdateNotesStatus]:
        path = self._guild_path(guild_id) / "update_notes.json"
        payload = self._read_json(path, None)
        if not payload:
            return None
        return UpdateNotesStatus(**payload)

    def save_update_notes_status(self, guild_id: int, status: UpdateNotesStatus) -> None:
        path = self._guild_path(guild_id) / "update_notes.json"
        self._write_json(path, asdict(status))

    # ------------------------------------------------------------------
    # API keys
    # ------------------------------------------------------------------
    def _api_keys_path(self, guild_id: int) -> Path:
        return self._guild_path(guild_id) / "api_keys.json"

    def get_user_api_keys(self, guild_id: int, user_id: int) -> List[ApiKeyRecord]:
        path = self._api_keys_path(guild_id)
        payload = self._read_json(path, {})
        user_payload = payload.get(str(user_id), []) if isinstance(payload, dict) else []
        records: List[ApiKeyRecord] = []
        if isinstance(user_payload, list):
            for item in user_payload:
                try:
                    records.append(ApiKeyRecord.from_dict(item))
                except ValueError:
                    continue
        return records

    def save_user_api_keys(
        self, guild_id: int, user_id: int, records: List[ApiKeyRecord]
    ) -> None:
        path = self._api_keys_path(guild_id)
        payload = self._read_json(path, {})
        if not isinstance(payload, dict):
            payload = {}
        payload[str(user_id)] = [asdict(record) for record in records]
        self._write_json(path, payload)

    def find_api_key(self, guild_id: int, user_id: int, name: str) -> Optional[ApiKeyRecord]:
        name_lower = name.lower()
        for record in self.get_user_api_keys(guild_id, user_id):
            if record.name.lower() == name_lower:
                return record
        return None

    def upsert_api_key(self, guild_id: int, user_id: int, record: ApiKeyRecord) -> None:
        keys = self.get_user_api_keys(guild_id, user_id)
        updated: List[ApiKeyRecord] = []
        replaced = False
        for existing in keys:
            if existing.name.lower() == record.name.lower():
                updated.append(record)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(record)
        self.save_user_api_keys(guild_id, user_id, updated)

    def delete_api_key(self, guild_id: int, user_id: int, name: str) -> bool:
        keys = self.get_user_api_keys(guild_id, user_id)
        remaining = [record for record in keys if record.name.lower() != name.lower()]
        if len(remaining) == len(keys):
            return False
        self.save_user_api_keys(guild_id, user_id, remaining)
        return True

    # ------------------------------------------------------------------
    # RSS feed subscriptions
    # ------------------------------------------------------------------
    def get_rss_feeds(self, guild_id: int) -> List[RssFeedConfig]:
        path = self._guild_path(guild_id) / "rss_feeds.json"
        payload = self._read_json(path, [])
        feeds: List[RssFeedConfig] = []
        for item in payload:
            try:
                feeds.append(RssFeedConfig(**item))
            except TypeError:
                continue
        return feeds

    def save_rss_feeds(self, guild_id: int, feeds: List[RssFeedConfig]) -> None:
        path = self._guild_path(guild_id) / "rss_feeds.json"
        self._write_json(path, [asdict(feed) for feed in feeds])

    def find_rss_feed(self, guild_id: int, name: str) -> Optional[RssFeedConfig]:
        name_lower = name.lower()
        for feed in self.get_rss_feeds(guild_id):
            if feed.name.lower() == name_lower:
                return feed
        return None

    def upsert_rss_feed(self, guild_id: int, feed: RssFeedConfig) -> None:
        feeds = self.get_rss_feeds(guild_id)
        updated: List[RssFeedConfig] = []
        replaced = False
        for existing in feeds:
            if existing.name.lower() == feed.name.lower():
                updated.append(feed)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(feed)
        self.save_rss_feeds(guild_id, updated)

    def delete_rss_feed(self, guild_id: int, name: str) -> bool:
        feeds = self.get_rss_feeds(guild_id)
        remaining = [feed for feed in feeds if feed.name.lower() != name.lower()]
        if len(remaining) == len(feeds):
            return False
        self.save_rss_feeds(guild_id, remaining)
        return True

    # ------------------------------------------------------------------
    # Builds
    # ------------------------------------------------------------------
    def get_builds(self, guild_id: int) -> List[BuildRecord]:
        path = self._guild_path(guild_id) / "builds.json"
        payload = self._read_json(path, [])
        return [BuildRecord(**item) for item in payload]

    def save_builds(self, guild_id: int, builds: List[BuildRecord]) -> None:
        path = self._guild_path(guild_id) / "builds.json"
        self._write_json(path, [asdict(build) for build in builds])

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def find_build(self, guild_id: int, build_id: str) -> Optional[BuildRecord]:
        for build in self.get_builds(guild_id):
            if build.build_id == build_id:
                return build
        return None

    def upsert_build(self, guild_id: int, record: BuildRecord) -> None:
        builds = self.get_builds(guild_id)
        updated: List[BuildRecord] = []
        replaced = False
        for build in builds:
            if build.build_id == record.build_id:
                updated.append(record)
                replaced = True
            else:
                updated.append(build)
        if not replaced:
            updated.append(record)
        self.save_builds(guild_id, updated)

    def delete_build(self, guild_id: int, build_id: str) -> bool:
        builds = self.get_builds(guild_id)
        remaining = [build for build in builds if build.build_id != build_id]
        if len(remaining) == len(builds):
            return False
        self.save_builds(guild_id, remaining)
        return True


DEFAULT_STORAGE_ROOT = Path("gw2_tools_bot") / "data"
