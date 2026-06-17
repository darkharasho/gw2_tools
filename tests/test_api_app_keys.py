import base64
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import (
    APP_KEY_PREFIX,
    DEFAULT_PUBLIC_URL,
    build_app,
    generate_app_key,
    hash_app_key,
)
from axitools.storage import StorageManager


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name


class FakeBot:
    """Minimal stand-in for AxiToolsBot: just .storage and .guilds."""

    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(123, "Vigil Keep"), FakeGuild(456, "Durmand Priory")]


@pytest.fixture(autouse=True)
def _global_token_disabled_by_default(monkeypatch):
    """Default posture: global token off unless a test opts in explicitly."""
    monkeypatch.delenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", raising=False)


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest_asyncio.fixture
async def global_token_client(aiohttp_client, bot, monkeypatch):
    """Client built with the global token opted in (legacy full-access path)."""
    monkeypatch.setenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", "1")
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest.fixture
def scoped_key(bot):
    """Generate an AxiVale key scoped to guild 123 and persist its hash."""
    key = generate_app_key()
    bot.storage.add_app_key(123, hash_app_key(key), created_by=42)
    return key


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Key generation format
# ---------------------------------------------------------------------------

def _decode_base64url(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def test_generate_app_key_format(monkeypatch):
    monkeypatch.setenv("AXITOOLS_PUBLIC_URL", "https://bot.example.com:8642")
    key = generate_app_key()
    assert key.startswith(APP_KEY_PREFIX)
    prefix, encoded_url, secret = key.split(".", 2)
    assert prefix == "axt1"
    assert _decode_base64url(encoded_url) == "https://bot.example.com:8642"
    # secrets.token_urlsafe(32) yields 43 url-safe characters.
    assert len(secret) == 43
    assert "." not in secret


def test_generate_app_key_default_url(monkeypatch):
    monkeypatch.delenv("AXITOOLS_PUBLIC_URL", raising=False)
    key = generate_app_key()
    _, encoded_url, _ = key.split(".", 2)
    assert _decode_base64url(encoded_url) == DEFAULT_PUBLIC_URL


def test_generated_keys_are_unique():
    assert generate_app_key() != generate_app_key()


# ---------------------------------------------------------------------------
# Storage roundtrip
# ---------------------------------------------------------------------------

def test_app_key_add_get_revoke_roundtrip(tmp_path):
    storage = StorageManager(tmp_path)
    storage.add_app_key(123, "hash-one", created_by=42)
    assert storage.get_app_key_guild("hash-one") == 123

    assert storage.revoke_app_key(123) == 1
    assert storage.get_app_key_guild("hash-one") is None
    assert storage.revoke_app_key(123) == 0


def test_multiple_keys_per_guild_coexist(tmp_path):
    storage = StorageManager(tmp_path)
    storage.add_app_key(123, "hash-old", created_by=42, label="laptop")
    storage.add_app_key(123, "hash-new", created_by=99, label="desktop")

    # Adding a second key leaves the first valid (no replace).
    assert storage.get_app_key_guild("hash-old") == 123
    assert storage.get_app_key_guild("hash-new") == 123
    assert {k.label for k in storage.list_app_keys(123)} == {"laptop", "desktop"}


def test_duplicate_label_is_suffixed(tmp_path):
    storage = StorageManager(tmp_path)
    a = storage.add_app_key(123, "h1", created_by=1, label="laptop")
    b = storage.add_app_key(123, "h2", created_by=1, label="laptop")
    assert a.label == "laptop"
    assert b.label == "laptop 2"


def test_revoke_by_label_targets_one_key(tmp_path):
    storage = StorageManager(tmp_path)
    storage.add_app_key(123, "h1", created_by=1, label="laptop")
    storage.add_app_key(123, "h2", created_by=1, label="desktop")

    assert storage.revoke_app_key(123, "laptop") == 1
    assert storage.get_app_key_guild("h1") is None
    assert storage.get_app_key_guild("h2") == 123  # untouched
    assert storage.revoke_app_key(123, "nope") == 0


def test_touch_app_key_records_last_used(tmp_path):
    storage = StorageManager(tmp_path)
    storage.add_app_key(123, "h1", created_by=1, label="laptop")
    assert storage.list_app_keys(123)[0].last_used_at is None
    storage.touch_app_key("h1")
    assert storage.list_app_keys(123)[0].last_used_at is not None


def test_migration_from_single_key_schema(tmp_path):
    # Recreate the legacy one-key-per-guild table, then reopen so _ensure_schema
    # runs the rebuild migration.
    storage = StorageManager(tmp_path)
    with storage.api_key_store._connect() as conn:
        conn.executescript(
            """
            DROP TABLE app_keys;
            CREATE TABLE app_keys (
                guild_id INTEGER PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO app_keys (guild_id, token_hash, created_by, created_at)
                VALUES (123, 'legacy-hash', 42, '2026-01-01T00:00:00.000000Z');
            """
        )

    migrated = StorageManager(tmp_path)
    assert migrated.get_app_key_guild("legacy-hash") == 123
    keys = migrated.list_app_keys(123)
    assert len(keys) == 1 and keys[0].label == "default"
    # The rebuilt table now accepts additional keys for the same guild.
    migrated.add_app_key(123, "second-hash", created_by=1, label="laptop")
    assert {k.label for k in migrated.list_app_keys(123)} == {"default", "laptop"}


# ---------------------------------------------------------------------------
# API auth scoping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scoped_key_reads_own_guild_builds(api_client, scoped_key):
    resp = await api_client.get("/guilds/123/builds", headers=_bearer(scoped_key))
    assert resp.status == 200
    assert await resp.json() == []


@pytest.mark.asyncio
async def test_scoped_key_rejected_for_other_guild(api_client, scoped_key):
    resp = await api_client.get("/guilds/456/builds", headers=_bearer(scoped_key))
    assert resp.status == 403
    assert await resp.json() == {"error": "key is scoped to another server"}


@pytest.mark.asyncio
async def test_scoped_key_guild_list_only_contains_own_guild(api_client, scoped_key):
    resp = await api_client.get("/guilds", headers=_bearer(scoped_key))
    assert resp.status == 200
    assert await resp.json() == [{"id": "123", "name": "Vigil Keep"}]


@pytest.mark.asyncio
async def test_unknown_app_key_is_unauthorized(api_client, scoped_key):
    bogus = generate_app_key()  # valid shape, never stored
    resp = await api_client.get("/guilds/123/builds", headers=_bearer(bogus))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_malformed_app_key_is_unauthorized(api_client):
    resp = await api_client.get("/guilds", headers=_bearer("axt1.not-a-real-key"))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_revoked_key_is_unauthorized(api_client, bot, scoped_key):
    bot.storage.revoke_app_key(123)
    resp = await api_client.get("/guilds/123/builds", headers=_bearer(scoped_key))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_global_token_rejected_by_default(api_client, scoped_key):
    # Flag off (default): the global token is just an unknown bearer -> 401,
    # while the per-guild axt1 key still authenticates and is scoped.
    resp = await api_client.get("/guilds/123/builds", headers=_bearer("test-token"))
    assert resp.status == 401

    scoped = await api_client.get("/guilds/123/builds", headers=_bearer(scoped_key))
    assert scoped.status == 200
    assert await scoped.json() == []
    other = await api_client.get("/guilds/456/builds", headers=_bearer(scoped_key))
    assert other.status == 403


@pytest.mark.asyncio
async def test_global_token_retains_full_access_when_enabled(
    global_token_client, scoped_key
):
    for path in ("/guilds/123/builds", "/guilds/456/builds"):
        resp = await global_token_client.get(path, headers=_bearer("test-token"))
        assert resp.status == 200
    resp = await global_token_client.get("/guilds", headers=_bearer("test-token"))
    assert await resp.json() == [
        {"id": "123", "name": "Vigil Keep"},
        {"id": "456", "name": "Durmand Priory"},
    ]
