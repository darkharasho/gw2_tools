
import pytest
from gw2_tools_bot.storage import ApiKeyStore, ApiKeyRecord

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


def test_audit_gw2_api_key_storage_round_trip(tmp_path):
    from gw2_tools_bot.storage import StorageManager

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
