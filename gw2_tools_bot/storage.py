"""Persistent storage utilities for the GW2 Tools bot."""
from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


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


_GUILD_ID_ALLOWED = re.compile(r"[a-f0-9-]+")


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


def normalise_guild_id(guild_id: str) -> str:
    """Return a canonical Guild Wars 2 guild ID for matching and persistence."""

    cleaned = guild_id.strip().lower()
    # Strip any invisible or non-hex characters to avoid mismatches from pasted values
    cleaned = "".join(_GUILD_ID_ALLOWED.findall(cleaned))
    return cleaned


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
    alliance_channel_id: Optional[int] = None
    alliance_guild_id: Optional[str] = None
    alliance_guild_name: Optional[str] = None
    alliance_server_id: Optional[int] = None
    alliance_server_name: Optional[str] = None
    alliance_last_prediction_at: Optional[str] = None
    alliance_last_actual_at: Optional[str] = None
    alliance_prediction_time: Optional[str] = None
    alliance_current_time: Optional[str] = None
    alliance_prediction_day: Optional[int] = None
    alliance_current_day: Optional[int] = None
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
    guild_labels: Dict[str, str] = field(default_factory=dict)
    characters: List[str] = field(default_factory=list)
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
                    cleaned = normalise_guild_id(guild_id)
                    if cleaned:
                        guild_ids.append(cleaned)

        labels_payload = payload.get("guild_labels") or {}
        guild_labels: Dict[str, str] = {}
        if isinstance(labels_payload, dict):
            for guild_id, label in labels_payload.items():
                if not isinstance(guild_id, str) or not isinstance(label, str):
                    continue
                gid_clean = normalise_guild_id(guild_id)
                label_clean = label.strip()
                if gid_clean and label_clean:
                    guild_labels[gid_clean] = label_clean

        characters_payload = payload.get("characters") or []
        characters: List[str] = []
        if isinstance(characters_payload, list):
            for character in characters_payload:
                if isinstance(character, str):
                    cleaned = character.strip()
                    if cleaned:
                        characters.append(cleaned)

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
            guild_labels=guild_labels,
            characters=characters,
            created_at=created_at,
            updated_at=updated_at,
        )


class ApiKeyStore:
    """SQLite-backed persistence for API keys with query-friendly indexes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "api_keys.sqlite"
        self._ensure_schema()
        self._migrate_json_stores()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    name_normalized TEXT NOT NULL,
                    key TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    guild_ids TEXT NOT NULL,
                    guild_labels TEXT NOT NULL DEFAULT '{}',
                    characters TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(guild_id, user_id, name_normalized)
                );
                CREATE TABLE IF NOT EXISTS api_key_guilds (
                    api_key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    guild_id TEXT NOT NULL,
                    PRIMARY KEY(api_key_id, guild_id)
                );
                CREATE TABLE IF NOT EXISTS guild_details (
                    guild_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tag TEXT,
                    label TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_api_keys_guild_user ON api_keys(guild_id, user_id);
                CREATE INDEX IF NOT EXISTS idx_api_keys_guild ON api_keys(guild_id);
                CREATE INDEX IF NOT EXISTS idx_api_key_guilds_lookup ON api_key_guilds(guild_id, api_key_id);
                CREATE INDEX IF NOT EXISTS idx_api_key_guilds_api ON api_key_guilds(api_key_id);
                """
            )

            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(api_keys)").fetchall()
            }
            if "characters" not in columns:
                connection.execute(
                    "ALTER TABLE api_keys ADD COLUMN characters TEXT NOT NULL DEFAULT '[]'"
                )
            if "guild_labels" not in columns:
                connection.execute(
                    "ALTER TABLE api_keys ADD COLUMN guild_labels TEXT NOT NULL DEFAULT '{}'"
                )

    def _migrate_json_stores(self) -> None:
        """Import legacy JSON API key stores into SQLite once per guild."""

        for guild_dir in self.root.glob("guild_*"):
            if not guild_dir.is_dir():
                continue
            try:
                guild_id = int(str(guild_dir.name).split("guild_", 1)[1])
            except (IndexError, ValueError):
                continue

            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT COUNT(1) FROM api_keys WHERE guild_id = ?", (guild_id,)
                ).fetchone()[0]
            if existing:
                continue

            path = guild_dir / "api_keys.json"
            if not path.exists():
                continue

            try:
                payload = self._read_json(path, {})
            except Exception:
                logger.exception("Failed to import legacy API keys for guild %s", guild_id)
                continue

            if not isinstance(payload, dict):
                continue

            for user_id_raw, records_payload in payload.items():
                try:
                    user_id = int(user_id_raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(records_payload, list):
                    continue
                for item in records_payload:
                    try:
                        record = ApiKeyRecord.from_dict(item)
                    except ValueError:
                        continue
                    self.upsert_api_key(guild_id, user_id, record)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _normalise_permissions(permissions: Iterable[str]) -> List[str]:
        seen: set[str] = set()
        cleaned: List[str] = []
        for permission in permissions or []:
            if not isinstance(permission, str):
                continue
            permission_clean = permission.strip()
            if not permission_clean:
                continue
            key = permission_clean.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(permission_clean)
        return cleaned

    @staticmethod
    def _normalise_guild_ids(guild_ids: Iterable[str]) -> List[str]:
        seen: set[str] = set()
        cleaned: List[str] = []
        for guild_id in guild_ids or []:
            if not isinstance(guild_id, str):
                continue
            normalised = normalise_guild_id(guild_id)
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            cleaned.append(normalised)
        return cleaned

    @staticmethod
    def _normalise_characters(characters: Iterable[str]) -> List[str]:
        seen: set[str] = set()
        cleaned: List[str] = []
        for character in characters or []:
            if not isinstance(character, str):
                continue
            name_clean = character.strip()
            if not name_clean:
                continue
            key = name_clean.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(name_clean)
        return cleaned

    def upsert_guild_details(
        self, details: Mapping[str, Tuple[str, Optional[str]]]
    ) -> None:
        """Store or refresh Guild Wars 2 guild metadata."""

        rows: List[Tuple[str, str, Optional[str], str, str]] = []
        for guild_id, (name, tag) in details.items():
            normalized_id = normalise_guild_id(guild_id)
            name_clean = name.strip()
            tag_clean = tag.strip() if isinstance(tag, str) else None
            if not normalized_id or not name_clean:
                continue
            label = f"{name_clean} [{tag_clean}]" if tag_clean else name_clean
            rows.append((normalized_id, name_clean, tag_clean, label, utcnow()))

        if not rows:
            return

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO guild_details (guild_id, name, tag, label, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    name=excluded.name,
                    tag=excluded.tag,
                    label=excluded.label,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_guild_labels(self, guild_ids: Iterable[str]) -> Dict[str, str]:
        """Return cached guild labels for the provided guild IDs."""

        normalized = [gid for gid in (normalise_guild_id(gid) for gid in guild_ids) if gid]
        if not normalized:
            return {}

        placeholders = ",".join("?" for _ in normalized)
        query = f"SELECT guild_id, label FROM guild_details WHERE guild_id IN ({placeholders})"

        with self._connect() as connection:
            rows = connection.execute(query, normalized).fetchall()

        return {row["guild_id"]: row["label"] for row in rows}

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ApiKeyRecord:
        permissions = json.loads(row["permissions"]) if row["permissions"] else []
        guild_ids = json.loads(row["guild_ids"]) if row["guild_ids"] else []
        guild_labels = (
            json.loads(row["guild_labels"])
            if "guild_labels" in row.keys() and row["guild_labels"]
            else {}
        )
        characters = (
            json.loads(row["characters"])
            if "characters" in row.keys() and row["characters"]
            else []
        )
        return ApiKeyRecord(
            name=row["name"],
            key=row["key"],
            account_name=row["account_name"],
            permissions=permissions,
            guild_ids=guild_ids,
            guild_labels=guild_labels,
            characters=characters,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _persist_guild_links(
        self, connection: sqlite3.Connection, api_key_id: int, guild_ids: Sequence[str]
    ) -> None:
        connection.executemany(
            "INSERT OR IGNORE INTO api_key_guilds (api_key_id, guild_id) VALUES (?, ?)",
            [(api_key_id, guild_id) for guild_id in guild_ids],
        )

    def _fetch_records(self, query: str, params: Sequence[Any]) -> List[ApiKeyRecord]:
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_user_api_keys(self, guild_id: int, user_id: int) -> List[ApiKeyRecord]:
        return self._fetch_records(
            "SELECT * FROM api_keys WHERE guild_id = ? AND user_id = ? ORDER BY name_normalized",
            (guild_id, user_id),
        )

    def save_user_api_keys(
        self, guild_id: int, user_id: int, records: List[ApiKeyRecord]
    ) -> None:
        normalised_records = [
            ApiKeyRecord(
                name=record.name.strip(),
                key=record.key.strip(),
                account_name=record.account_name.strip(),
                permissions=self._normalise_permissions(record.permissions),
                guild_ids=self._normalise_guild_ids(record.guild_ids),
                guild_labels={
                    gid: label.strip()
                    for gid, label in (record.guild_labels or {}).items()
                    if normalise_guild_id(gid) and isinstance(label, str) and label.strip()
                },
                characters=self._normalise_characters(record.characters),
                created_at=record.created_at,
                updated_at=utcnow(),
            )
            for record in records
            if record.name.strip() and record.key.strip()
        ]

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM api_keys WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
            )
            for record in normalised_records:
                cursor = connection.execute(
                    """
                    INSERT INTO api_keys (
                        guild_id, user_id, name, name_normalized, key, account_name,
                        permissions, guild_ids, guild_labels, characters, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        record.name,
                        record.name.lower(),
                        record.key,
                        record.account_name,
                        json.dumps(self._normalise_permissions(record.permissions)),
                        json.dumps(self._normalise_guild_ids(record.guild_ids)),
                        json.dumps(record.guild_labels),
                        json.dumps(self._normalise_characters(record.characters)),
                        record.created_at,
                        record.updated_at,
                    ),
                )
                api_key_id = cursor.lastrowid
                self._persist_guild_links(
                    connection, api_key_id, self._normalise_guild_ids(record.guild_ids)
                )

    def find_api_key(self, guild_id: int, user_id: int, name: str) -> Optional[ApiKeyRecord]:
        results = self._fetch_records(
            """
            SELECT * FROM api_keys
            WHERE guild_id = ? AND user_id = ? AND name_normalized = ?
            LIMIT 1
            """,
            (guild_id, user_id, name.lower()),
        )
        return results[0] if results else None

    def upsert_api_key(self, guild_id: int, user_id: int, record: ApiKeyRecord) -> None:
        permissions = self._normalise_permissions(record.permissions)
        guild_ids = self._normalise_guild_ids(record.guild_ids)
        name_normalized = record.name.strip().lower()
        account_name = record.account_name.strip()
        key_value = record.key.strip()
        guild_labels = {
            gid: label.strip()
            for gid, label in (record.guild_labels or {}).items()
            if normalise_guild_id(gid) and isinstance(label, str) and label.strip()
        }
        characters = self._normalise_characters(record.characters)
        if not name_normalized or not key_value:
            return

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, created_at FROM api_keys
                WHERE guild_id = ? AND user_id = ? AND name_normalized = ?
                LIMIT 1
                """,
                (guild_id, user_id, name_normalized),
            ).fetchone()

            if existing:
                created_at = existing["created_at"] or record.created_at
                connection.execute(
                    """
                    UPDATE api_keys
                    SET name = ?, key = ?, account_name = ?, permissions = ?, guild_ids = ?,
                        guild_labels = ?, characters = ?, updated_at = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (
                        record.name.strip(),
                        key_value,
                        account_name,
                        json.dumps(permissions),
                        json.dumps(guild_ids),
                        json.dumps(guild_labels),
                        json.dumps(characters),
                        utcnow(),
                        created_at,
                        existing["id"],
                    ),
                )
                connection.execute(
                    "DELETE FROM api_key_guilds WHERE api_key_id = ?", (existing["id"],)
                )
                self._persist_guild_links(connection, existing["id"], guild_ids)
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO api_keys (
                        guild_id, user_id, name, name_normalized, key, account_name,
                        permissions, guild_ids, guild_labels, characters, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        record.name.strip(),
                        name_normalized,
                        key_value,
                        account_name,
                        json.dumps(permissions),
                        json.dumps(guild_ids),
                        json.dumps(guild_labels),
                        json.dumps(characters),
                        record.created_at,
                        utcnow(),
                    ),
                )
                api_key_id = cursor.lastrowid
                self._persist_guild_links(connection, api_key_id, guild_ids)

    def delete_api_key(self, guild_id: int, user_id: int, name: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM api_keys WHERE guild_id = ? AND user_id = ? AND name_normalized = ?",
                (guild_id, user_id, name.lower()),
            )
            return cursor.rowcount > 0

    def query_api_keys(
        self,
        *,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        gw2_guild_id: Optional[str] = None,
    ) -> List[Tuple[int, int, ApiKeyRecord]]:
        """Return API keys matching the provided filters for reporting."""

        if guild_id is None:
            raise ValueError("A Discord guild_id is required to query API keys safely")

        clauses: List[str] = ["ak.guild_id = ?"]
        params: List[Any] = [guild_id]
        joins: List[str] = []

        if user_id is not None:
            clauses.append("ak.user_id = ?")
            params.append(user_id)
        if gw2_guild_id:
            joins.append("INNER JOIN api_key_guilds g ON g.api_key_id = ak.id")
            clauses.append("g.guild_id = ?")
            params.append(normalise_guild_id(gw2_guild_id))

        where_clause = f"WHERE {' AND '.join(clauses)}"
        join_clause = " ".join(joins)
        query = (
            "SELECT ak.* FROM api_keys ak "
            f"{join_clause} "
            f"{where_clause} "
            "ORDER BY ak.guild_id, ak.user_id, ak.name_normalized"
        )

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            (row["guild_id"], row["user_id"], self._row_to_record(row))
            for row in rows
        ]

    def all_api_keys(self) -> List[Tuple[int, int, ApiKeyRecord]]:
        """Return every stored API key across all guilds."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM api_keys ORDER BY guild_id, user_id, name_normalized"
            ).fetchall()

        return [
            (row["guild_id"], row["user_id"], self._row_to_record(row))
            for row in rows
        ]

    def all_gw2_guild_ids(self) -> List[str]:
        """Return all distinct Guild Wars 2 guild IDs referenced by stored keys."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT guild_id FROM api_key_guilds ORDER BY guild_id"
            ).fetchall()

        return [row["guild_id"] for row in rows if row["guild_id"]]

    def clear_guild_details(self) -> None:
        """Remove all cached guild details."""

        with self._connect() as connection:
            connection.execute("DELETE FROM guild_details")


class StorageManager:
    """Handle isolated storage per guild to respect data privacy."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.api_key_store = ApiKeyStore(self.root)

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
                key_clean = normalise_guild_id(guild_key)
                if not key_clean:
                    continue
                if isinstance(role_id, int):
                    cleaned_roles[key_clean] = role_id
                elif isinstance(role_id, str):
                    try:
                        cleaned_roles[key_clean] = int(role_id)
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
        alliance_channel_id = payload.get("alliance_channel_id")
        if isinstance(alliance_channel_id, int):
            payload["alliance_channel_id"] = alliance_channel_id
        elif isinstance(alliance_channel_id, str):
            try:
                payload["alliance_channel_id"] = int(alliance_channel_id)
            except ValueError:
                payload["alliance_channel_id"] = None
        else:
            payload["alliance_channel_id"] = None
        alliance_server_id = payload.get("alliance_server_id")
        if isinstance(alliance_server_id, int):
            payload["alliance_server_id"] = alliance_server_id
        elif isinstance(alliance_server_id, str):
            try:
                payload["alliance_server_id"] = int(alliance_server_id)
            except ValueError:
                payload["alliance_server_id"] = None
        else:
            payload["alliance_server_id"] = None
        alliance_guild_id = payload.get("alliance_guild_id")
        if isinstance(alliance_guild_id, str):
            cleaned = normalise_guild_id(alliance_guild_id)
            payload["alliance_guild_id"] = cleaned or None
        else:
            payload["alliance_guild_id"] = None
        alliance_guild_name = payload.get("alliance_guild_name")
        if isinstance(alliance_guild_name, str):
            payload["alliance_guild_name"] = alliance_guild_name.strip() or None
        else:
            payload["alliance_guild_name"] = None
        alliance_server_name = payload.get("alliance_server_name")
        if isinstance(alliance_server_name, str):
            payload["alliance_server_name"] = alliance_server_name.strip() or None
        else:
            payload["alliance_server_name"] = None
        alliance_prediction_time = payload.get("alliance_prediction_time")
        if isinstance(alliance_prediction_time, str):
            payload["alliance_prediction_time"] = alliance_prediction_time.strip() or None
        else:
            payload["alliance_prediction_time"] = None
        alliance_current_time = payload.get("alliance_current_time")
        if isinstance(alliance_current_time, str):
            payload["alliance_current_time"] = alliance_current_time.strip() or None
        else:
            payload["alliance_current_time"] = None
        alliance_prediction_day = payload.get("alliance_prediction_day")
        if isinstance(alliance_prediction_day, int) and 0 <= alliance_prediction_day <= 6:
            payload["alliance_prediction_day"] = alliance_prediction_day
        elif isinstance(alliance_prediction_day, str):
            try:
                day_value = int(alliance_prediction_day)
            except ValueError:
                payload["alliance_prediction_day"] = None
            else:
                payload["alliance_prediction_day"] = day_value if 0 <= day_value <= 6 else None
        else:
            payload["alliance_prediction_day"] = None
        alliance_current_day = payload.get("alliance_current_day")
        if isinstance(alliance_current_day, int) and 0 <= alliance_current_day <= 6:
            payload["alliance_current_day"] = alliance_current_day
        elif isinstance(alliance_current_day, str):
            try:
                day_value = int(alliance_current_day)
            except ValueError:
                payload["alliance_current_day"] = None
            else:
                payload["alliance_current_day"] = day_value if 0 <= day_value <= 6 else None
        else:
            payload["alliance_current_day"] = None
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
                guild_key = normalise_guild_id(guild_key)
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
        if config.alliance_guild_id:
            cleaned_guild_id = normalise_guild_id(config.alliance_guild_id)
            config.alliance_guild_id = cleaned_guild_id or None
        if config.alliance_guild_name:
            cleaned_name = str(config.alliance_guild_name).strip()
            config.alliance_guild_name = cleaned_name or None
        if config.alliance_server_name:
            cleaned_server = str(config.alliance_server_name).strip()
            config.alliance_server_name = cleaned_server or None
        if config.alliance_prediction_time:
            cleaned_time = str(config.alliance_prediction_time).strip()
            config.alliance_prediction_time = cleaned_time or None
        if config.alliance_current_time:
            cleaned_time = str(config.alliance_current_time).strip()
            config.alliance_current_time = cleaned_time or None
        if config.alliance_prediction_day is not None:
            if isinstance(config.alliance_prediction_day, int) and 0 <= config.alliance_prediction_day <= 6:
                config.alliance_prediction_day = int(config.alliance_prediction_day)
            else:
                config.alliance_prediction_day = None
        if config.alliance_current_day is not None:
            if isinstance(config.alliance_current_day, int) and 0 <= config.alliance_current_day <= 6:
                config.alliance_current_day = int(config.alliance_current_day)
            else:
                config.alliance_current_day = None
        if config.alliance_channel_id is not None:
            try:
                config.alliance_channel_id = int(config.alliance_channel_id)
            except (TypeError, ValueError):
                config.alliance_channel_id = None
        if config.alliance_server_id is not None:
            try:
                config.alliance_server_id = int(config.alliance_server_id)
            except (TypeError, ValueError):
                config.alliance_server_id = None
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
    def get_user_api_keys(self, guild_id: int, user_id: int) -> List[ApiKeyRecord]:
        return self.api_key_store.get_user_api_keys(guild_id, user_id)

    def save_user_api_keys(
        self, guild_id: int, user_id: int, records: List[ApiKeyRecord]
    ) -> None:
        self.api_key_store.save_user_api_keys(guild_id, user_id, records)

    def find_api_key(self, guild_id: int, user_id: int, name: str) -> Optional[ApiKeyRecord]:
        return self.api_key_store.find_api_key(guild_id, user_id, name)

    def upsert_api_key(self, guild_id: int, user_id: int, record: ApiKeyRecord) -> None:
        self.api_key_store.upsert_api_key(guild_id, user_id, record)

    def delete_api_key(self, guild_id: int, user_id: int, name: str) -> bool:
        return self.api_key_store.delete_api_key(guild_id, user_id, name)

    def query_api_keys(
        self,
        *,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        gw2_guild_id: Optional[str] = None,
    ) -> List[Tuple[int, int, ApiKeyRecord]]:
        return self.api_key_store.query_api_keys(
            guild_id=guild_id, user_id=user_id, gw2_guild_id=gw2_guild_id
        )

    def upsert_guild_details(self, details: Mapping[str, Tuple[str, Optional[str]]]) -> None:
        self.api_key_store.upsert_guild_details(details)

    def get_guild_labels(self, guild_ids: Iterable[str]) -> Dict[str, str]:
        return self.api_key_store.get_guild_labels(guild_ids)

    def all_api_keys(self) -> List[Tuple[int, int, ApiKeyRecord]]:
        return self.api_key_store.all_api_keys()

    def all_gw2_guild_ids(self) -> List[str]:
        return self.api_key_store.all_gw2_guild_ids()

    def clear_guild_details(self) -> None:
        self.api_key_store.clear_guild_details()

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
