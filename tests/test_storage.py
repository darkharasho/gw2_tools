
import pytest
from axitools.storage import ApiKeyStore, ApiKeyRecord

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
