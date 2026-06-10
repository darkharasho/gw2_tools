import sqlite3

import pytest

from axitools.storage import StorageManager, ApiKeyRecord

PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def test_database_is_not_plaintext_on_disk(tmp_path):
    storage = StorageManager(tmp_path)
    record = ApiKeyRecord(name="main", key="SECRET-GW2-KEY", account_name="Foo.1234")
    storage.api_key_store.upsert_api_key(1, 2, record)

    db_path = tmp_path / "api_keys.sqlite"
    with db_path.open("rb") as fh:
        header = fh.read(16)
    assert header != PLAINTEXT_MAGIC  # encrypted, not a plain SQLite file

    # A plain sqlite3 driver cannot read an encrypted file.
    with pytest.raises(sqlite3.DatabaseError):
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT name FROM api_keys").fetchall()


def test_round_trip_through_encrypted_store(tmp_path):
    storage = StorageManager(tmp_path)
    record = ApiKeyRecord(name="main", key="SECRET-GW2-KEY", account_name="Foo.1234")
    storage.api_key_store.upsert_api_key(1, 2, record)

    fetched = storage.api_key_store.find_api_key(1, 2, "main")
    assert fetched is not None
    assert fetched.key == "SECRET-GW2-KEY"
    assert fetched.account_name == "Foo.1234"
