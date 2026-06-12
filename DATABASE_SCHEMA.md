# Database schema

The bot stores persistent data in SQLite at `axitools/data/api_keys.sqlite`. The tables below outline the current schema.

Audit logging data is stored per Discord guild in `axitools/data/guild_<guild_id>/audit.sqlite`.

## Encryption at rest

All persistent SQLite databases are encrypted at rest with SQLCipher. The key is
resolved (in order) from the `AXITOOLS_DB_KEY` env var, the `AXITOOLS_DB_KEY_FILE`
env var, or an auto-generated key file at `${XDG_CONFIG_HOME:-~/.config}/axitools/db_key`
(kept separate from the data directory). **Back up this key** — if it is lost, the
encrypted data is unrecoverable. On startup, any pre-existing plaintext database is
re-encrypted in place, leaving a `<name>.plaintext.bak` backup you should delete once
you have confirmed reads work.

This protects against a leaked database file (backup, stray copy, accidental commit,
stolen disk). It does **not** protect against an attacker with host access, nor does it
prevent the operator from decrypting; see `docs/superpowers/specs/2026-06-09-encrypt-data-at-rest-design.md`.

## Tables

### `api_keys`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Unique row identifier. |
| `guild_id` | INTEGER NOT NULL | Discord guild ID the key belongs to. |
| `user_id` | INTEGER NOT NULL | Discord user ID the key belongs to. |
| `name` | TEXT NOT NULL | User-defined key label. |
| `name_normalized` | TEXT NOT NULL | Lowercased key label used for uniqueness. |
| `key` | TEXT NOT NULL | Stored Guild Wars 2 API key. |
| `account_name` | TEXT NOT NULL | Guild Wars 2 account name linked to the key. |
| `permissions` | TEXT NOT NULL | JSON-encoded list of granted API permissions. |
| `guild_ids` | TEXT NOT NULL | JSON-encoded list of Guild Wars 2 guild IDs tied to the account. |
| `guild_labels` | TEXT NOT NULL | JSON object mapping guild IDs to cached display labels. |
| `characters` | TEXT NOT NULL | JSON-encoded list of character names from the key. |
| `created_at` | TEXT NOT NULL | ISO 8601 timestamp of record creation. |
| `updated_at` | TEXT NOT NULL | ISO 8601 timestamp of last update. |

### `api_key_guilds`
| Column | Type | Notes |
| --- | --- | --- |
| `api_key_id` | INTEGER NOT NULL | Foreign key to `api_keys.id` (cascade delete). |
| `guild_id` | TEXT NOT NULL | Guild Wars 2 guild ID associated with the API key. |

### `guild_details`
| Column | Type | Notes |
| --- | --- | --- |
| `guild_id` | TEXT PRIMARY KEY | Guild Wars 2 guild ID. |
| `name` | TEXT NOT NULL | Full guild name cached from the GW2 API. |
| `tag` | TEXT | Optional guild tag from the GW2 API. |
| `label` | TEXT NOT NULL | Display label combining the guild name and tag (when available). |
| `updated_at` | TEXT NOT NULL | ISO 8601 timestamp of the most recent cache refresh. |

### `app_keys`
| Column | Type | Notes |
| --- | --- | --- |
| `guild_id` | INTEGER PRIMARY KEY | Discord guild ID the AxiVale key is scoped to (one key per guild). |
| `token_hash` | TEXT NOT NULL UNIQUE | SHA-256 hex digest of the full `axt1.` key string; the key itself is never stored. |
| `created_by` | INTEGER NOT NULL | Discord user ID that generated the key. |
| `created_at` | TEXT NOT NULL | ISO 8601 timestamp when the key was generated. |

## Audit tables

### `discord_audit_events`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Unique row identifier. |
| `created_at` | TEXT NOT NULL | ISO 8601 timestamp for the audit entry. |
| `event_type` | TEXT NOT NULL | Discord audit event identifier. |
| `actor_id` | INTEGER | Discord user ID responsible for the event, when available. |
| `actor_name` | TEXT | Display label for the actor, when available. |
| `target_id` | INTEGER | Discord user ID targeted by the event, when available. |
| `target_name` | TEXT | Display label for the target, when available. |
| `details` | TEXT | Summary of the audit event. |

### `gw2_audit_events`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Unique row identifier. |
| `log_id` | INTEGER | Guild Wars 2 log entry ID. |
| `created_at` | TEXT NOT NULL | Timestamp from the GW2 API log entry. |
| `event_type` | TEXT NOT NULL | Guild log entry type. |
| `user` | TEXT | Guild Wars 2 account name on the entry. |
| `details` | TEXT | JSON payload from the GW2 API log entry. |

### `gw2_sync_state`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Singleton row (always `1`). |
| `last_log_id` | INTEGER | Most recent GW2 log ID fetched. |
| `last_checked_at` | TEXT | ISO 8601 timestamp of the last GW2 log sync. |
