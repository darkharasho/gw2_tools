"""API tests for the audit / rss / streams / alliance / guild-roles /
config-PATCH / members-linked domains."""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import build_app, generate_app_key, hash_app_key
from axitools.cogs import streaming
from axitools.storage import ApiKeyRecord, StorageManager

GID = 123
OTHER_GID = 456


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


class FakeChannel:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name


class FakeMember:
    def __init__(self, member_id, name):
        self.id = member_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id, name, *, channels=(), roles=(), members=()):
        self.id = guild_id
        self.name = name
        self.channels = list(channels)
        self.roles = list(roles)
        self.members = list(members)

    def get_channel(self, channel_id):
        return next((c for c in self.channels if c.id == channel_id), None)

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_member(self, member_id):
        return next((m for m in self.members if m.id == member_id), None)


def _build_guild_123():
    return FakeGuild(
        GID,
        "Vigil Keep",
        channels=[FakeChannel(11, "general"), FakeChannel(12, "announcements")],
        roles=[FakeRole(20, "Raider"), FakeRole(21, "Officer")],
        members=[FakeMember(30, "logan")],
    )


class FakeBot:
    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [_build_guild_123(), FakeGuild(OTHER_GID, "Durmand Priory")]

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)


@pytest.fixture(autouse=True)
def _allow_global_token(monkeypatch):
    """The _auth() helper uses the global token, which is opt-in."""
    monkeypatch.setenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", "1")


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot, _allow_global_token):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest.fixture
def scoped_key(bot):
    """An AxiVale key scoped to guild 123."""
    key = generate_app_key()
    bot.storage.add_app_key(GID, hash_app_key(key), created_by=42)
    return key


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _auth():
    return _bearer("test-token")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _seed_discord_audit(bot):
    store = bot.storage.get_audit_store(GID)
    store.add_discord_event(
        created_at="2026-06-10T10:00:00+00:00", event_type="member_join",
        actor_id=None, actor_name=None, target_id=30, target_name="logan",
        details="joined",
    )
    store.add_discord_event(
        created_at="2026-06-11T10:00:00+00:00", event_type="message_delete",
        actor_id=31, actor_name="rytlock", target_id=30, target_name="logan",
        details="deleted a message",
    )
    store.add_discord_event(
        created_at="2026-06-12T10:00:00+00:00", event_type="message_delete",
        actor_id=32, actor_name="eir", target_id=None, target_name=None,
        details=None,
    )


def _seed_gw2_audit(bot):
    store = bot.storage.get_audit_store(GID)
    store.add_gw2_event(
        created_at="2026-06-10T10:00:00+00:00", event_type="joined",
        user="Logan.1234", details="joined the guild", log_id=100,
    )
    store.add_gw2_event(
        created_at="2026-06-11T10:00:00+00:00", event_type="kick",
        user="Rytlock.5678", details="kicked someone", log_id=101,
    )
    store.add_gw2_event(
        created_at="2026-06-12T10:00:00+00:00", event_type="joined",
        user="Eir.9999", details=None, log_id=102,
    )


@pytest.mark.asyncio
async def test_audit_discord_shape_newest_first(api_client, bot):
    _seed_discord_audit(bot)
    resp = await api_client.get(f"/guilds/{GID}/audit/discord", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert len(body) == 3
    assert body[0] == {
        "id": 3,
        "created_at": "2026-06-12T10:00:00+00:00",
        "event_type": "message_delete",
        "actor_id": "32",
        "actor_name": "eir",
        "target_id": None,
        "target_name": None,
        "details": None,
        "channel_id": None,
        "channel_name": None,
        "actor_is_bot": None,
        "target_type": None,
    }
    # Snowflake ids serialize as strings.
    assert body[1]["actor_id"] == "31" and body[1]["target_id"] == "30"


@pytest.mark.asyncio
async def test_audit_discord_filters(api_client, bot):
    _seed_discord_audit(bot)
    # event_type exact
    resp = await api_client.get(
        f"/guilds/{GID}/audit/discord?event_type=member_join", headers=_auth()
    )
    body = await resp.json()
    assert [e["event_type"] for e in body] == ["member_join"]
    # actor by exact id
    resp = await api_client.get(
        f"/guilds/{GID}/audit/discord?actor=31", headers=_auth()
    )
    body = await resp.json()
    assert len(body) == 1 and body[0]["actor_name"] == "rytlock"
    # target by name substring (case-insensitive)
    resp = await api_client.get(
        f"/guilds/{GID}/audit/discord?target=LOG", headers=_auth()
    )
    body = await resp.json()
    assert len(body) == 2
    # limit
    resp = await api_client.get(
        f"/guilds/{GID}/audit/discord?limit=1", headers=_auth()
    )
    body = await resp.json()
    assert len(body) == 1


@pytest.mark.asyncio
async def test_audit_discord_invalid_limit_400(api_client):
    for raw in ("0", "201", "abc"):
        resp = await api_client.get(
            f"/guilds/{GID}/audit/discord?limit={raw}", headers=_auth()
        )
        assert resp.status == 400, raw


@pytest.mark.asyncio
async def test_audit_gw2_shape_and_filters(api_client, bot):
    _seed_gw2_audit(bot)
    resp = await api_client.get(f"/guilds/{GID}/audit/gw2", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body[0] == {
        "id": 3,
        "log_id": 102,
        "created_at": "2026-06-12T10:00:00+00:00",
        "event_type": "joined",
        "user": "Eir.9999",
        "details": None,
    }
    # event_type
    resp = await api_client.get(
        f"/guilds/{GID}/audit/gw2?event_type=kick", headers=_auth()
    )
    body = await resp.json()
    assert [e["user"] for e in body] == ["Rytlock.5678"]
    # user substring
    resp = await api_client.get(f"/guilds/{GID}/audit/gw2?user=logan", headers=_auth())
    body = await resp.json()
    assert [e["user"] for e in body] == ["Logan.1234"]
    # since_log_id is exclusive
    resp = await api_client.get(
        f"/guilds/{GID}/audit/gw2?since_log_id=100", headers=_auth()
    )
    body = await resp.json()
    assert sorted(e["log_id"] for e in body) == [101, 102]


@pytest.mark.asyncio
async def test_audit_gw2_invalid_since_log_id_400(api_client):
    resp = await api_client.get(
        f"/guilds/{GID}/audit/gw2?since_log_id=abc", headers=_auth()
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rss_crud(api_client, bot):
    resp = await api_client.get(f"/guilds/{GID}/rss", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == []

    resp = await api_client.put(
        f"/guilds/{GID}/rss/gw2-news",
        json={"url": "https://example.com/feed.xml", "channel_id": "11"},
        headers=_auth(),
    )
    assert resp.status == 200
    assert await resp.json() == {
        "name": "gw2-news",
        "url": "https://example.com/feed.xml",
        "channel_id": "11",
        "last_entry_published_at": None,
    }
    feeds = bot.storage.get_rss_feeds(GID)
    assert len(feeds) == 1 and feeds[0].channel_id == 11

    resp = await api_client.get(f"/guilds/{GID}/rss", headers=_auth())
    assert [f["name"] for f in await resp.json()] == ["gw2-news"]

    resp = await api_client.delete(f"/guilds/{GID}/rss/gw2-news", headers=_auth())
    assert resp.status == 204
    assert bot.storage.get_rss_feeds(GID) == []

    resp = await api_client.delete(f"/guilds/{GID}/rss/gw2-news", headers=_auth())
    assert resp.status == 404


@pytest.mark.asyncio
async def test_rss_upsert_preserves_poll_state(api_client, bot):
    from axitools.storage import RssFeedConfig

    bot.storage.upsert_rss_feed(GID, RssFeedConfig(
        name="gw2-news", url="https://old.example.com/feed", channel_id=11,
        last_entry_id="e-9", last_entry_published_at="2026-06-01T00:00:00+00:00",
    ))
    resp = await api_client.put(
        f"/guilds/{GID}/rss/gw2-news",
        json={"url": "https://example.com/feed.xml", "channel_id": 12},
        headers=_auth(),
    )
    assert resp.status == 200
    feed = bot.storage.find_rss_feed(GID, "gw2-news")
    assert feed.url == "https://example.com/feed.xml"
    assert feed.channel_id == 12
    assert feed.last_entry_id == "e-9"
    assert feed.last_entry_published_at == "2026-06-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_rss_validation_400(api_client):
    # non-http(s) url
    resp = await api_client.put(
        f"/guilds/{GID}/rss/x",
        json={"url": "ftp://example.com/feed", "channel_id": 11},
        headers=_auth(),
    )
    assert resp.status == 400
    # missing url
    resp = await api_client.put(
        f"/guilds/{GID}/rss/x", json={"channel_id": 11}, headers=_auth()
    )
    assert resp.status == 400
    # channel not in this guild
    resp = await api_client.put(
        f"/guilds/{GID}/rss/x",
        json={"url": "https://example.com/feed", "channel_id": 999},
        headers=_auth(),
    )
    assert resp.status == 400
    # bogus channel id
    resp = await api_client.put(
        f"/guilds/{GID}/rss/x",
        json={"url": "https://example.com/feed", "channel_id": "nope"},
        headers=_auth(),
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

@pytest.fixture
def twitch_env(monkeypatch):
    monkeypatch.setattr(streaming, "TWITCH_CLIENT_ID", "cid")
    monkeypatch.setattr(streaming, "TWITCH_CLIENT_SECRET", "csecret")

    async def fake_user(session, tokens, login):
        if login != "arenanet":
            return None
        return {"login": "arenanet", "display_name": "ArenaNet"}

    async def fake_stream(session, tokens, login):
        return None  # not currently live

    monkeypatch.setattr(streaming, "_fetch_twitch_user", fake_user)
    monkeypatch.setattr(streaming, "_fetch_twitch_stream", fake_stream)


@pytest.fixture
def youtube_env(monkeypatch):
    monkeypatch.setattr(streaming, "YOUTUBE_API_KEY", "ytkey")

    async def fake_resolve(session, ref, api_key):
        if ref != "@teapot":
            return None
        return "UCteapot123", "The Teapot"

    async def fake_rss(session, channel_id):
        return [{"id": "yt:video:abc123"}]

    monkeypatch.setattr(streaming, "_resolve_youtube_channel", fake_resolve)
    monkeypatch.setattr(streaming, "_fetch_youtube_rss", fake_rss)


@pytest.mark.asyncio
async def test_streams_twitch_crud(api_client, bot, twitch_env):
    resp = await api_client.get(f"/guilds/{GID}/streams", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == []

    resp = await api_client.put(
        f"/guilds/{GID}/streams/anet",
        json={
            "platform": "twitch",
            "channel_ref": "arenanet",
            "discord_channel_id": "11",
            "ping_role_id": "20",
        },
        headers=_auth(),
    )
    assert resp.status == 200
    assert await resp.json() == {
        "name": "anet",
        "platform": "twitch",
        "channel_id": "arenanet",
        "channel_display_name": "ArenaNet",
        "discord_channel_id": "11",
        "ping_role_id": "20",
        "is_live": False,
        "last_live_at": None,
    }
    subs = bot.storage.get_stream_subscriptions(GID)
    assert len(subs) == 1 and subs[0].discord_channel_id == 11

    resp = await api_client.get(f"/guilds/{GID}/streams", headers=_auth())
    assert [s["name"] for s in await resp.json()] == ["anet"]

    resp = await api_client.delete(f"/guilds/{GID}/streams/anet", headers=_auth())
    assert resp.status == 204
    resp = await api_client.delete(f"/guilds/{GID}/streams/anet", headers=_auth())
    assert resp.status == 404


@pytest.mark.asyncio
async def test_streams_youtube_upsert_primes_vod(api_client, bot, youtube_env):
    resp = await api_client.put(
        f"/guilds/{GID}/streams/teapot",
        json={
            "platform": "youtube",
            "channel_ref": "@teapot",
            "discord_channel_id": 11,
        },
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["channel_id"] == "UCteapot123"
    assert body["ping_role_id"] is None
    sub = bot.storage.find_stream_subscription(GID, "teapot")
    # Latest video id primed so old content is not announced.
    assert sub.last_vod_id == "yt:video:abc123"


@pytest.mark.asyncio
async def test_streams_upsert_same_channel_keeps_live_state(api_client, bot, twitch_env):
    from axitools.storage import StreamSubscription

    bot.storage.upsert_stream_subscription(GID, StreamSubscription(
        name="anet", platform="twitch", channel_id="arenanet",
        channel_display_name="ArenaNet", discord_channel_id=11,
        is_live=True, last_live_at="2026-06-11T00:00:00+00:00",
    ))
    resp = await api_client.put(
        f"/guilds/{GID}/streams/anet",
        json={
            "platform": "twitch",
            "channel_ref": "arenanet",
            "discord_channel_id": 12,
        },
        headers=_auth(),
    )
    assert resp.status == 200
    sub = bot.storage.find_stream_subscription(GID, "anet")
    assert sub.discord_channel_id == 12
    assert sub.is_live is True
    assert sub.last_live_at == "2026-06-11T00:00:00+00:00"


@pytest.mark.asyncio
async def test_streams_missing_credentials_400(api_client, monkeypatch):
    monkeypatch.setattr(streaming, "TWITCH_CLIENT_ID", "")
    monkeypatch.setattr(streaming, "TWITCH_CLIENT_SECRET", "")
    monkeypatch.setattr(streaming, "YOUTUBE_API_KEY", "")

    resp = await api_client.put(
        f"/guilds/{GID}/streams/anet",
        json={"platform": "twitch", "channel_ref": "arenanet", "discord_channel_id": 11},
        headers=_auth(),
    )
    assert resp.status == 400
    assert "Twitch credentials" in (await resp.json())["error"]

    resp = await api_client.put(
        f"/guilds/{GID}/streams/teapot",
        json={"platform": "youtube", "channel_ref": "@teapot", "discord_channel_id": 11},
        headers=_auth(),
    )
    assert resp.status == 400
    assert "YouTube API key" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_streams_validation_400(api_client, twitch_env):
    # unknown platform
    resp = await api_client.put(
        f"/guilds/{GID}/streams/x",
        json={"platform": "vimeo", "channel_ref": "a", "discord_channel_id": 11},
        headers=_auth(),
    )
    assert resp.status == 400
    # discord channel not in guild
    resp = await api_client.put(
        f"/guilds/{GID}/streams/x",
        json={"platform": "twitch", "channel_ref": "arenanet", "discord_channel_id": 999},
        headers=_auth(),
    )
    assert resp.status == 400
    # ping role not in guild
    resp = await api_client.put(
        f"/guilds/{GID}/streams/x",
        json={
            "platform": "twitch", "channel_ref": "arenanet",
            "discord_channel_id": 11, "ping_role_id": 999,
        },
        headers=_auth(),
    )
    assert resp.status == 400
    # unresolvable channel
    resp = await api_client.put(
        f"/guilds/{GID}/streams/x",
        json={"platform": "twitch", "channel_ref": "nobody", "discord_channel_id": 11},
        headers=_auth(),
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Alliance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alliance_get_defaults(api_client):
    resp = await api_client.get(f"/guilds/{GID}/alliance", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == {
        "channel_id": None,
        "guild_id": None,
        "guild_name": None,
        "server_id": None,
        "server_name": None,
        "prediction_day": None,
        "prediction_time": None,
        "current_day": None,
        "current_time": None,
        "relink_enabled": False,
        "lockout_enabled": False,
        "lockout_channel_id": None,
        "lockout_lead_minutes": 1440,
        "lockout_region": None,
    }


@pytest.mark.asyncio
async def test_alliance_put_merges_subset(api_client, bot):
    resp = await api_client.put(
        f"/guilds/{GID}/alliance",
        json={
            "channel_id": "11",
            "guild_id": "ABCDEF12-3456-7890-ABCD-EF1234567890",
            "guild_name": "The Vigil",
            "prediction_day": "Friday",
            "prediction_time": "19:30",
            "current_day": 5,
            "current_time": "9:5",
            "relink_enabled": True,
        },
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["channel_id"] == "11"
    assert body["guild_id"] == "abcdef12-3456-7890-abcd-ef1234567890"
    assert body["guild_name"] == "The Vigil"
    assert body["prediction_day"] == 4  # Friday
    assert body["prediction_time"] == "19:30"
    assert body["current_day"] == 5
    assert body["current_time"] == "09:05"
    assert body["relink_enabled"] is True

    config = bot.storage.get_config(GID)
    assert config.alliance_channel_id == 11
    assert config.alliance_prediction_day == 4

    # A later subset update leaves other fields intact, and null clears.
    resp = await api_client.put(
        f"/guilds/{GID}/alliance",
        json={"channel_id": None},
        headers=_auth(),
    )
    body = await resp.json()
    assert body["channel_id"] is None
    assert body["prediction_time"] == "19:30"


@pytest.mark.asyncio
async def test_alliance_put_validation_400(api_client):
    cases = [
        {"channel_id": 999},               # not in guild
        {"prediction_day": "Funday"},      # bad day name
        {"prediction_day": 7},             # out of range
        {"prediction_time": "25:00"},      # bad hour
        {"current_time": "19h30"},         # bad format
        {"relink_enabled": "yes"},         # not a bool
        {"guild_id": "!!!"},               # no hex chars at all
    ]
    for body in cases:
        resp = await api_client.put(
            f"/guilds/{GID}/alliance", json=body, headers=_auth()
        )
        assert resp.status == 400, body


@pytest.mark.asyncio
async def test_alliance_put_sets_lockout_fields(api_client, bot):
    resp = await api_client.put(
        f"/guilds/{GID}/alliance",
        json={
            "lockout_enabled": True,
            "lockout_lead_minutes": 720,
            "lockout_region": "eu",
        },
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["lockout_enabled"] is True
    assert body["lockout_lead_minutes"] == 720
    assert body["lockout_region"] == "eu"

    config = bot.storage.get_config(GID)
    assert config.wvw_lockout_enabled is True
    assert config.wvw_lockout_lead_minutes == 720
    assert config.wvw_lockout_region == "eu"


@pytest.mark.asyncio
async def test_alliance_put_rejects_bad_lockout_fields(api_client):
    cases = [
        {"lockout_region": "asia"},        # not na/eu/null
        {"lockout_lead_minutes": "soon"},  # not an int
        {"lockout_enabled": "yes"},        # not a bool
    ]
    for body in cases:
        resp = await api_client.put(
            f"/guilds/{GID}/alliance", json=body, headers=_auth()
        )
        assert resp.status == 400, body


# ---------------------------------------------------------------------------
# Guild roles
# ---------------------------------------------------------------------------

GW2_GUILD = "abcdef12-3456-7890-abcd-ef1234567890"


@pytest.mark.asyncio
async def test_guild_roles_crud(api_client, bot):
    resp = await api_client.get(f"/guilds/{GID}/guild-roles", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == {"mappings": [], "allowlist": []}

    resp = await api_client.put(
        f"/guilds/{GID}/guild-roles/{GW2_GUILD.upper()}",
        json={"role_id": "20"},
        headers=_auth(),
    )
    assert resp.status == 200
    assert await resp.json() == {"gw2_guild_id": GW2_GUILD, "role_id": "20"}
    assert bot.storage.get_config(GID).guild_role_ids == {GW2_GUILD: 20}

    resp = await api_client.get(f"/guilds/{GID}/guild-roles", headers=_auth())
    body = await resp.json()
    assert body["mappings"] == [{"gw2_guild_id": GW2_GUILD, "role_id": "20"}]

    resp = await api_client.delete(
        f"/guilds/{GID}/guild-roles/{GW2_GUILD}", headers=_auth()
    )
    assert resp.status == 204
    assert bot.storage.get_config(GID).guild_role_ids == {}

    resp = await api_client.delete(
        f"/guilds/{GID}/guild-roles/{GW2_GUILD}", headers=_auth()
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_guild_roles_put_unknown_role_400(api_client):
    resp = await api_client.put(
        f"/guilds/{GID}/guild-roles/{GW2_GUILD}",
        json={"role_id": 999},
        headers=_auth(),
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_guild_roles_allowlist_replace(api_client, bot):
    config = bot.storage.get_config(GID)
    config.preferred_guild_role_allowlist = [21]
    bot.storage.save_config(GID, config)

    resp = await api_client.put(
        f"/guilds/{GID}/guild-roles-allowlist",
        json={"role_ids": ["20"]},
        headers=_auth(),
    )
    assert resp.status == 200
    assert await resp.json() == {"allowlist": ["20"]}
    assert bot.storage.get_config(GID).preferred_guild_role_allowlist == [20]


@pytest.mark.asyncio
async def test_guild_roles_allowlist_validation_400(api_client):
    resp = await api_client.put(
        f"/guilds/{GID}/guild-roles-allowlist",
        json={"role_ids": "20"},
        headers=_auth(),
    )
    assert resp.status == 400
    resp = await api_client.put(
        f"/guilds/{GID}/guild-roles-allowlist",
        json={"role_ids": [999]},
        headers=_auth(),
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Config GET + PATCH
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_get_includes_new_fields(api_client):
    resp = await api_client.get(f"/guilds/{GID}/config", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    for field in (
        "moderator_role_ids", "build_channel_id", "comp_active_preset",
        "update_notes_channel_id", "arcdps_channel_id", "audit_channel_id",
        "audit_channel_blacklist",
    ):
        assert field in body, field


@pytest.mark.asyncio
async def test_config_patch_merges_and_serializes(api_client, bot):
    resp = await api_client.patch(
        f"/guilds/{GID}/config",
        json={
            "build_channel_id": "11",
            "audit_channel_id": 12,
            "moderator_role_ids": ["20", "21"],
        },
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["build_channel_id"] == "11"
    assert body["audit_channel_id"] == "12"
    assert body["moderator_role_ids"] == ["20", "21"]
    config = bot.storage.get_config(GID)
    assert config.build_channel_id == 11
    assert config.audit_channel_id == 12
    assert config.moderator_role_ids == [20, 21]

    # null clears a channel; untouched fields persist.
    resp = await api_client.patch(
        f"/guilds/{GID}/config",
        json={"audit_channel_id": None},
        headers=_auth(),
    )
    body = await resp.json()
    assert body["audit_channel_id"] is None
    assert body["build_channel_id"] == "11"


@pytest.mark.asyncio
async def test_config_patch_validation_400(api_client):
    # channel not in guild
    resp = await api_client.patch(
        f"/guilds/{GID}/config", json={"build_channel_id": 999}, headers=_auth()
    )
    assert resp.status == 400
    # roles must belong to the guild
    resp = await api_client.patch(
        f"/guilds/{GID}/config", json={"moderator_role_ids": [999]}, headers=_auth()
    )
    assert resp.status == 400
    # moderator_role_ids must be a list
    resp = await api_client.patch(
        f"/guilds/{GID}/config", json={"moderator_role_ids": 20}, headers=_auth()
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Members-linked
# ---------------------------------------------------------------------------

def _seed_api_keys(bot):
    bot.storage.upsert_api_key(GID, 30, ApiKeyRecord(
        name="main", key="SECRET-API-KEY-VALUE",
        account_name="Logan.1234",
        characters=["Logan Thackeray"],
        guild_ids=[GW2_GUILD],
        guild_labels={GW2_GUILD: "The Vigil [VGL]"},
    ))
    bot.storage.upsert_api_key(GID, 30, ApiKeyRecord(
        name="alt", key="SECOND-SECRET-KEY",
        account_name="LoganAlt.4321",
    ))
    # A member who has since left the Discord guild.
    bot.storage.upsert_api_key(GID, 99, ApiKeyRecord(
        name="ghost", key="GHOST-SECRET-KEY",
        account_name="Ghost.0001",
    ))
    bot.storage.set_preferred_guild_role(GID, 30, 20)


def _collect_keys(value, found):
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _collect_keys(v, found)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, found)


@pytest.mark.asyncio
async def test_members_linked_shape(api_client, bot):
    _seed_api_keys(bot)
    resp = await api_client.get(f"/guilds/{GID}/members-linked", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    by_id = {m["member_id"]: m for m in body}
    assert set(by_id) == {"30", "99"}

    logan = by_id["30"]
    assert logan["member_name"] == "logan"
    assert logan["preferred_role_id"] == "20"
    accounts = {a["account_name"]: a for a in logan["accounts"]}
    assert set(accounts) == {"Logan.1234", "LoganAlt.4321"}
    assert accounts["Logan.1234"]["characters"] == ["Logan Thackeray"]
    assert accounts["Logan.1234"]["gw2_guild_ids"] == [GW2_GUILD]
    assert accounts["Logan.1234"]["guild_labels"] == {GW2_GUILD: "The Vigil [VGL]"}

    ghost = by_id["99"]
    assert ghost["member_name"] is None
    assert ghost["preferred_role_id"] is None


@pytest.mark.asyncio
async def test_members_linked_never_exposes_key_material(api_client, bot):
    _seed_api_keys(bot)
    resp = await api_client.get(f"/guilds/{GID}/members-linked", headers=_auth())
    body = await resp.json()
    rendered = json.dumps(body)
    assert "SECRET-API-KEY-VALUE" not in rendered
    assert "SECOND-SECRET-KEY" not in rendered
    assert "GHOST-SECRET-KEY" not in rendered
    found_keys: set = set()
    _collect_keys(body, found_keys)
    assert "key" not in found_keys


@pytest.mark.asyncio
async def test_members_linked_empty(api_client):
    resp = await api_client.get(f"/guilds/{GID}/members-linked", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == []


# ---------------------------------------------------------------------------
# Auth + cross-guild scoping on representative new routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_routes_require_auth(api_client):
    for path in (
        f"/guilds/{GID}/audit/discord",
        f"/guilds/{GID}/rss",
        f"/guilds/{GID}/streams",
        f"/guilds/{GID}/alliance",
        f"/guilds/{GID}/guild-roles",
        f"/guilds/{GID}/members-linked",
    ):
        resp = await api_client.get(path)
        assert resp.status == 401, path


@pytest.mark.asyncio
async def test_scoped_key_allows_own_guild(api_client, scoped_key, bot):
    _seed_discord_audit(bot)
    resp = await api_client.get(
        f"/guilds/{GID}/audit/discord", headers=_bearer(scoped_key)
    )
    assert resp.status == 200
    resp = await api_client.get(f"/guilds/{GID}/rss", headers=_bearer(scoped_key))
    assert resp.status == 200
    resp = await api_client.get(
        f"/guilds/{GID}/members-linked", headers=_bearer(scoped_key)
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_scoped_key_rejected_for_other_guild(api_client, scoped_key):
    get_paths = (
        f"/guilds/{OTHER_GID}/audit/discord",
        f"/guilds/{OTHER_GID}/audit/gw2",
        f"/guilds/{OTHER_GID}/rss",
        f"/guilds/{OTHER_GID}/streams",
        f"/guilds/{OTHER_GID}/alliance",
        f"/guilds/{OTHER_GID}/guild-roles",
        f"/guilds/{OTHER_GID}/members-linked",
    )
    for path in get_paths:
        resp = await api_client.get(path, headers=_bearer(scoped_key))
        assert resp.status == 403, path
        assert await resp.json() == {"error": "key is scoped to another server"}

    resp = await api_client.put(
        f"/guilds/{OTHER_GID}/rss/x",
        json={"url": "https://example.com/feed", "channel_id": 11},
        headers=_bearer(scoped_key),
    )
    assert resp.status == 403
    resp = await api_client.patch(
        f"/guilds/{OTHER_GID}/config",
        json={"build_channel_id": None},
        headers=_bearer(scoped_key),
    )
    assert resp.status == 403


# ---------------------------------------------------------------------------
# Key holders (existence-only, cross-server)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_key_holders_flags_known_accounts_case_insensitively(api_client, bot):
    _seed_api_keys(bot)
    # A key registered in ANOTHER Discord server still counts as "has a key".
    bot.storage.upsert_api_key(OTHER_GID, 77, ApiKeyRecord(
        name="elsewhere", key="ELSEWHERE-SECRET",
        account_name="Riversong.2837",
    ))
    resp = await api_client.post(
        f"/guilds/{GID}/key-holders",
        json={"account_names": ["logan.1234", "Riversong.2837", "Stranger.9999"]},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["holders"] == {
        "logan.1234": True,
        "Riversong.2837": True,
        "Stranger.9999": False,
    }
    assert body["matched"] == 2
    # Only GID registrations count for the local linked-member tally (users 30 + 99).
    assert body["linked_members_in_this_server"] == 2
    assert "ELSEWHERE-SECRET" not in json.dumps(body)


@pytest.mark.asyncio
async def test_key_holders_validates_input(api_client):
    for bad in [{}, {"account_names": "Logan.1234"}, {"account_names": []},
                {"account_names": ["x"] * 501}]:
        resp = await api_client.post(
            f"/guilds/{GID}/key-holders", json=bad, headers=_auth()
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_key_holders_respects_key_scoping(api_client, bot):
    scoped_key = generate_app_key("http://127.0.0.1:8642")
    bot.storage.add_app_key(OTHER_GID, hash_app_key(scoped_key), 1)
    resp = await api_client.post(
        f"/guilds/{GID}/key-holders",
        json={"account_names": ["Logan.1234"]},
        headers=_bearer(scoped_key),
    )
    assert resp.status == 403


# ---------------------------------------------------------------------------
# Comp posting config (config.comp.*)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_comp_config_get_default_shape(api_client):
    body = await (await api_client.get(f"/guilds/{GID}/comp-config", headers=_auth())).json()
    assert set(body) >= {
        "channel_id", "ping_role_id", "post_days", "post_time", "timezone", "active_preset"
    }


@pytest.mark.asyncio
async def test_comp_config_patch_merges(api_client):
    resp = await api_client.patch(
        f"/guilds/{GID}/comp-config",
        json={"channel_id": "11", "ping_role_id": "20", "post_days": [1, 3],
              "post_time": "18:00", "timezone": "UTC", "active_preset": "tuesday-wvw"},
        headers=_auth(),
    )
    assert resp.status == 200
    out = await resp.json()
    assert out["channel_id"] == "11"
    assert out["ping_role_id"] == "20"
    assert out["post_days"] == [1, 3]
    assert out["post_time"] == "18:00"
    assert out["active_preset"] == "tuesday-wvw"
    # null clears
    cleared = await (await api_client.patch(
        f"/guilds/{GID}/comp-config", json={"channel_id": None}, headers=_auth()
    )).json()
    assert cleared["channel_id"] is None
    assert cleared["ping_role_id"] == "20"  # untouched


@pytest.mark.asyncio
async def test_comp_config_validates(api_client):
    assert (await api_client.patch(f"/guilds/{GID}/comp-config", json={"post_time": "9pm"}, headers=_auth())).status == 400
    assert (await api_client.patch(f"/guilds/{GID}/comp-config", json={"post_days": [9]}, headers=_auth())).status == 400
    assert (await api_client.patch(f"/guilds/{GID}/comp-config", json={"channel_id": "999999"}, headers=_auth())).status == 400
