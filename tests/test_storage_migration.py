import sqlite3

import pytest

from axitools.storage import migrate_data_dir, connect_encrypted, StorageManager

KEY = "00" * 32
PLAINTEXT_MAGIC = b"SQLite format 3\x00"

# Mirrors the real api_keys schema from ApiKeyStore._ensure_schema so the
# migrated database can be opened and read back through ApiKeyStore.
_API_KEYS_SCHEMA = """
CREATE TABLE api_keys (
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
"""


def _make_plaintext_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _make_plaintext_api_keys_db(path):
    """Create a plaintext api_keys.sqlite with one record, like a legacy deploy."""
    conn = sqlite3.connect(path)
    conn.executescript(_API_KEYS_SCHEMA)
    conn.execute(
        """
        INSERT INTO api_keys (
            guild_id, user_id, name, name_normalized, key, account_name,
            permissions, guild_ids, guild_labels, characters, created_at, updated_at
        ) VALUES (10, 20, 'Main', 'main', 'PLAINKEY-123', 'Acct.1',
                  '[]', '[]', '{}', '[]',
                  '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """
    )
    conn.commit()
    conn.close()


def test_migrates_plaintext_db_in_place(tmp_path):
    db = tmp_path / "api_keys.sqlite"
    _make_plaintext_db(db)
    assert db.open("rb").read(16) == PLAINTEXT_MAGIC  # sanity: starts plaintext

    migrate_data_dir(tmp_path, KEY)

    # File is now encrypted and a backup was kept.
    assert db.open("rb").read(16) != PLAINTEXT_MAGIC
    assert (tmp_path / "api_keys.sqlite.plaintext.bak").exists()

    # Data survived and is readable through an encrypted connection.
    conn = connect_encrypted(db, KEY)
    assert conn.execute("SELECT val FROM t").fetchone()[0] == "hello"
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "api_keys.sqlite"
    _make_plaintext_db(db)
    migrate_data_dir(tmp_path, KEY)
    first = db.read_bytes()

    migrate_data_dir(tmp_path, KEY)  # second run must be a no-op
    assert db.read_bytes() == first
    # No second backup of the backup.
    assert not (tmp_path / "api_keys.sqlite.plaintext.bak.plaintext.bak").exists()


def test_storage_manager_auto_migrates_on_init(tmp_path):
    """Constructing StorageManager over a legacy plaintext DB migrates it
    before any encrypted connection opens, and records read back intact.

    The autouse `_db_key_env` fixture sets AXITOOLS_DB_KEY to the same value as
    KEY, so resolve_db_key() inside StorageManager uses the migration key.
    """
    db = tmp_path / "api_keys.sqlite"
    _make_plaintext_api_keys_db(db)
    assert db.open("rb").read(16) == PLAINTEXT_MAGIC  # sanity: starts plaintext

    storage = StorageManager(tmp_path)

    # The pre-existing record is readable through the encrypted store...
    record = storage.api_key_store.find_api_key(10, 20, "Main")
    assert record is not None
    assert record.key == "PLAINKEY-123"
    assert record.account_name == "Acct.1"

    # ...the file on disk is now encrypted, and the plaintext backup was kept.
    assert db.open("rb").read(16) != PLAINTEXT_MAGIC
    assert (tmp_path / "api_keys.sqlite.plaintext.bak").exists()
