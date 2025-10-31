"""Persistent storage utilities for the GW2 Tools bot."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ISOFORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> str:
    """Return the current UTC timestamp formatted for storage."""

    return datetime.utcnow().strftime(ISOFORMAT)


@dataclass
class GuildConfig:
    """Server-specific configuration."""

    moderator_role_ids: List[int]
    build_channel_id: Optional[int] = None
    arcdps_channel_id: Optional[int] = None

    @classmethod
    def default(cls) -> "GuildConfig":
        return cls(moderator_role_ids=[])


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
