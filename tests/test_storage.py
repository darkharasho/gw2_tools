
import pytest
from axitools.storage import ApiKeyStore, ApiKeyRecord, GuildConfig

def test_api_key_store_init(api_key_store):
    assert api_key_store.path.exists()
    assert api_key_store.path.name == "api_keys.sqlite"


def test_api_key_crud(api_key_store):
    # Create
    record = ApiKeyRecord(
        name="test-key",
        key="ABC-123",
        account_name="Test.1234",
        permissions=["account", "characters"],
        guild_ids=["guild-1"],
        characters=["Char1"]
    )
    api_key_store.upsert_api_key(123, 456, record)

    # Read
    conn = api_key_store._connect()
    cursor = conn.execute("SELECT * FROM api_keys WHERE user_id = ?", (456,))
    row = cursor.fetchone()
    assert row is not None
    assert row["name"] == "test-key"
    conn.close()

    # Read All
    keys = api_key_store.all_api_keys()
    assert len(keys) == 1
    # keys is List[Tuple[guild_id, user_id, record]]
    assert keys[0][2].key == "ABC-123"

    # Delete
    api_key_store.delete_api_key(123, 456, "test-key")
    keys = api_key_store.all_api_keys()
    assert len(keys) == 0

def test_all_gw2_guild_ids(api_key_store):
    record1 = ApiKeyRecord(name="k1", key="k1", guild_ids=["aaaa-1111", "bbbb-2222"])
    record2 = ApiKeyRecord(name="k2", key="k2", guild_ids=["bbbb-2222", "cccc-3333"])
    
    api_key_store.upsert_api_key(1, 101, record1)
    api_key_store.upsert_api_key(2, 102, record2)
    
    guilds = api_key_store.all_gw2_guild_ids()
    assert set(guilds) == {"aaaa-1111", "bbbb-2222", "cccc-3333"}


def test_count_api_keys_returns_zero_for_empty_guild(api_key_store):
    assert api_key_store.count_api_keys(guild_id=999) == 0


def test_audit_gw2_api_key_storage_round_trip(tmp_path):
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    guild_id = 987654
    storage.save_audit_gw2_api_keys(
        guild_id,
        {
            " Main Key ": " KEY-ONE ",
            "ALT.KEY": "KEY-TWO",
            "": "ignored",
        },
    )

    keys = storage.get_audit_gw2_api_keys(guild_id)
    assert keys == {
        "main key": "KEY-ONE",
        "alt.key": "KEY-TWO",
    }


def test_stream_subscriptions_round_trip(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 111

    sub = StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=999,
        ping_role_id=None,
        last_vod_id=None,
        last_live_at=None,
        is_live=False,
    )
    storage.save_stream_subscriptions(guild_id, [sub])
    loaded = storage.get_stream_subscriptions(guild_id)

    assert len(loaded) == 1
    assert loaded[0].name == "arenanet"
    assert loaded[0].platform == "twitch"
    assert loaded[0].channel_display_name == "ArenaNet"
    assert loaded[0].is_live is False


def test_stream_subscriptions_upsert(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 222

    sub = StreamSubscription(
        name="mychannel",
        platform="youtube",
        channel_id="UCxxxxxxx",
        channel_display_name="My Channel",
        discord_channel_id=888,
    )
    storage.upsert_stream_subscription(guild_id, sub)
    storage.upsert_stream_subscription(guild_id, StreamSubscription(
        name="mychannel",
        platform="youtube",
        channel_id="UCxxxxxxx",
        channel_display_name="My Channel Updated",
        discord_channel_id=777,
    ))
    loaded = storage.get_stream_subscriptions(guild_id)
    assert len(loaded) == 1
    assert loaded[0].discord_channel_id == 777
    assert loaded[0].channel_display_name == "My Channel Updated"


def test_stream_subscriptions_delete(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 333

    sub = StreamSubscription(
        name="todelete",
        platform="twitch",
        channel_id="todelete",
        channel_display_name="To Delete",
        discord_channel_id=555,
    )
    storage.upsert_stream_subscription(guild_id, sub)
    deleted = storage.delete_stream_subscription(guild_id, "todelete")
    assert deleted is True
    assert storage.get_stream_subscriptions(guild_id) == []

    not_deleted = storage.delete_stream_subscription(guild_id, "todelete")
    assert not_deleted is False


def test_stream_subscriptions_empty(tmp_path):
    from axitools.storage import StorageManager
    storage = StorageManager(tmp_path)
    assert storage.get_stream_subscriptions(99999) == []


def test_stream_subscriptions_find_hit(tmp_path):
    from axitools.storage import StorageManager, StreamSubscription
    storage = StorageManager(tmp_path)
    guild_id = 444

    sub = StreamSubscription(
        name="arenanet",
        platform="twitch",
        channel_id="arenanet",
        channel_display_name="ArenaNet",
        discord_channel_id=999,
    )
    storage.upsert_stream_subscription(guild_id, sub)

    found = storage.find_stream_subscription(guild_id, "arenanet")
    assert found is not None
    assert found.name == "arenanet"
    assert found.channel_display_name == "ArenaNet"


def test_stream_subscriptions_find_miss(tmp_path):
    from axitools.storage import StorageManager
    storage = StorageManager(tmp_path)
    guild_id = 555

    result = storage.find_stream_subscription(guild_id, "doesnotexist")
    assert result is None


def test_guild_config_relink_defaults():
    config = GuildConfig.default()
    assert config.alliance_relink_enabled is False
    assert config.alliance_relink_last_server is None


def test_audit_channel_blacklist_round_trip(tmp_path):
    from axitools.storage import StorageManager, GuildConfig

    storage = StorageManager(tmp_path)
    guild_id = 424242
    config = GuildConfig.default()
    # mix of int, str-int, duplicate, and invalid entries
    config.audit_channel_blacklist = [111, "222", 111, "bad", 333]
    storage.save_config(guild_id, config)

    loaded = storage.get_config(guild_id)
    assert loaded.audit_channel_blacklist == [111, 222, 333]


def test_audit_channel_blacklist_defaults_empty(tmp_path):
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    loaded = storage.get_config(999001)
    assert loaded.audit_channel_blacklist == []


def test_discord_audit_structured_fields_round_trip(tmp_path):
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    store = storage.get_audit_store(123)
    store.add_discord_event(
        created_at="2026-06-30T07:38:00Z",
        event_type="channel_create",
        actor_id=42,
        actor_name="<@42> (rooster)",
        target_id=None,
        target_name=None,
        details="",
        channel_id="1449262177046495356",
        channel_name="raid-signups",
        actor_is_bot=True,
        target_type="channel",
    )
    rows = store.query_discord_events_filtered(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["channel_id"] == "1449262177046495356"
    assert row["channel_name"] == "raid-signups"
    assert row["actor_is_bot"] == 1
    assert row["target_type"] == "channel"


def test_discord_audit_structured_fields_default_null(tmp_path):
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    store = storage.get_audit_store(124)
    store.add_discord_event(
        created_at="2026-06-30T07:38:00Z",
        event_type="member_leave",
        actor_id=None,
        actor_name=None,
        target_id=7,
        target_name="<@7> (khava)",
        details="Details: Member left the server.",
    )
    row = store.query_discord_events_filtered(limit=10)[0]
    assert row["channel_id"] is None
    assert row["channel_name"] is None
    assert row["actor_is_bot"] is None
    assert row["target_type"] is None


# ---------------------------------------------------------------------------
# Test 1 — ALTER TABLE migration path
#
# Strategy: use a plain sqlite3 connection with row_factory=sqlite3.Row to
# create the old 10-column schema (missing the 4 new columns), then call
# AuditStore._migrate_discord_columns() directly.  This avoids SQLCipher
# entirely while still exercising the exact ALTER TABLE branch.
# ---------------------------------------------------------------------------

def test_migrate_discord_columns_adds_missing_columns():
    """_migrate_discord_columns adds the 4 new columns to a pre-migration DB."""
    import sqlite3 as _sqlite3
    from axitools.storage import AuditStore

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row

    # Create the old schema — 10 columns, none of the 4 new ones.
    conn.execute(
        """
        CREATE TABLE discord_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            actor_name_normalized TEXT,
            target_id INTEGER,
            target_name TEXT,
            target_name_normalized TEXT,
            details TEXT
        )
        """
    )
    conn.commit()

    # Verify the columns are absent before migration.
    cols_before = {row["name"] for row in conn.execute("PRAGMA table_info(discord_audit_events)")}
    for missing in ("channel_id", "channel_name", "actor_is_bot", "target_type"):
        assert missing not in cols_before, f"column {missing!r} should not exist before migration"

    # Run the migration.
    AuditStore._migrate_discord_columns(conn)

    # All four columns must now exist.
    cols_after = {row["name"] for row in conn.execute("PRAGMA table_info(discord_audit_events)")}
    for expected in ("channel_id", "channel_name", "actor_is_bot", "target_type"):
        assert expected in cols_after, f"column {expected!r} missing after migration"

    conn.close()


def test_migrate_discord_columns_idempotent():
    """Calling _migrate_discord_columns twice raises no error (idempotent)."""
    import sqlite3 as _sqlite3
    from axitools.storage import AuditStore

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row

    conn.execute(
        """
        CREATE TABLE discord_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            actor_name_normalized TEXT,
            target_id INTEGER,
            target_name TEXT,
            target_name_normalized TEXT,
            details TEXT
        )
        """
    )
    conn.commit()

    AuditStore._migrate_discord_columns(conn)
    # Second call must not raise.
    AuditStore._migrate_discord_columns(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(discord_audit_events)")}
    for expected in ("channel_id", "channel_name", "actor_is_bot", "target_type"):
        assert expected in cols

    conn.close()


def test_migrate_discord_columns_old_rows_read_null(tmp_path):
    """Rows inserted before migration read back NULL for the 4 new columns."""
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    store = storage.get_audit_store(200)

    # Reach into the encrypted DB and rebuild the table with only the old
    # 10-column schema, then re-run migration so the store is in a consistent
    # state.  We use store._connect() — the same handle the store uses — so
    # the SQLCipher key is already set up by the StorageManager fixture.
    with store._connect() as conn:
        conn.executescript(
            """
            DROP TABLE discord_audit_events;
            CREATE TABLE discord_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id INTEGER,
                actor_name TEXT,
                actor_name_normalized TEXT,
                target_id INTEGER,
                target_name TEXT,
                target_name_normalized TEXT,
                details TEXT
            );
            """
        )
        # Insert a row using only the old columns.
        conn.execute(
            """
            INSERT INTO discord_audit_events
                (created_at, event_type, actor_id, actor_name, actor_name_normalized,
                 target_id, target_name, target_name_normalized, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2025-01-01T00:00:00Z",
                "member_join",
                11,
                "OldUser",
                "olduser",
                None,
                None,
                None,
                "pre-migration row",
            ),
        )

    # Now run _ensure_schema again — this is the migration entry point.
    store._ensure_schema()

    # The four new columns must now exist.
    with store._connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(discord_audit_events)")}
    for expected in ("channel_id", "channel_name", "actor_is_bot", "target_type"):
        assert expected in cols, f"column {expected!r} missing after _ensure_schema migration"

    # The old row must read back NULL for every new column.
    rows = store.query_discord_events_filtered(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["channel_id"] is None
    assert row["channel_name"] is None
    assert row["actor_is_bot"] is None
    assert row["target_type"] is None


# ---------------------------------------------------------------------------
# Test 2 — actor_is_bot=False stored as 0 and round-trips correctly
# ---------------------------------------------------------------------------

def test_actor_is_bot_false_stored_and_read_back(tmp_path):
    """add_discord_event(actor_is_bot=False) stores 0 and reads back 0."""
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    store = storage.get_audit_store(300)
    store.add_discord_event(
        created_at="2026-06-30T08:00:00Z",
        event_type="message_delete",
        actor_id=99,
        actor_name="HumanUser",
        target_id=None,
        target_name=None,
        details="",
        actor_is_bot=False,
    )
    rows = store.query_discord_events_filtered(limit=10)
    assert len(rows) == 1
    assert rows[0]["actor_is_bot"] == 0, "actor_is_bot=False must be stored as integer 0, not NULL"
