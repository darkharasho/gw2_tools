"""Persistent storage utilities for the GW2 Tools bot."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ISOFORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> str:
    """Return the current UTC timestamp formatted for storage."""

    return datetime.utcnow().strftime(ISOFORMAT)


@dataclass
class CompClassConfig:
    """Configuration for an individual class within a scheduled composition."""

    name: str
    required: Optional[int] = None


@dataclass
class CompConfig:
    """Composition scheduling and signup configuration."""

    channel_id: Optional[int] = None
    post_day: Optional[int] = None
    post_time: Optional[str] = None
    timezone: str = "UTC"
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

        post_day = payload.get("post_day")
        if isinstance(post_day, str):
            try:
                post_day = int(post_day)
            except ValueError:
                post_day = None

        return cls(
            channel_id=payload.get("channel_id"),
            post_day=post_day,
            post_time=payload.get("post_time"),
            timezone=payload.get("timezone", "UTC"),
            classes=classes,
            signups=signups,
            message_id=payload.get("message_id"),
            last_post_at=payload.get("last_post_at"),
        )


@dataclass
class GuildConfig:
    """Server-specific configuration."""

    moderator_role_ids: List[int]
    build_channel_id: Optional[int] = None
    arcdps_channel_id: Optional[int] = None
    comp: CompConfig = field(default_factory=CompConfig)

    @classmethod
    def default(cls) -> "GuildConfig":
        return cls(moderator_role_ids=[], comp=CompConfig())


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
        comp_payload = payload.get("comp")
        if isinstance(comp_payload, dict):
            payload["comp"] = CompConfig.from_dict(comp_payload)
        else:
            payload["comp"] = CompConfig()
        return GuildConfig(**payload)

    def save_config(self, guild_id: int, config: GuildConfig) -> None:
        path = self._guild_path(guild_id) / "config.json"
        self._write_json(path, asdict(config))

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
