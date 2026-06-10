import sqlite3

import pytest

from axitools.storage import migrate_data_dir, connect_encrypted

KEY = "00" * 32
PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def _make_plaintext_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
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
