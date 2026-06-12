import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
import pytest_asyncio

from axitools.api.discord_actions import ACTIONS
from axitools.api.server import build_app, generate_app_key, hash_app_key
from axitools.storage import StorageManager

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Fakes (route-shape oriented; see test_discord_actions.py for executor fakes)
# ---------------------------------------------------------------------------

class FakeRole:
    def __init__(self, role_id, name, *, position=0, members=()):
        self.id = role_id
        self.name = name
        self.color = "#c8423a"
        self.position = position
        self.hoist = False
        self.mentionable = True
        self.permissions = SimpleNamespace(value=104320)
        self.members = list(members)


class FakeMember:
    def __init__(self, member_id, name, roles=()):
        self.id = member_id
        self.name = name
        self.display_name = name.title()
        self.roles = list(roles)
        self.joined_at = dt.datetime(2024, 1, 1, tzinfo=UTC)


class FakeMessage:
    def __init__(self, message_id, author, content, *, pinned=False):
        self.id = message_id
        self.author = author
        self.content = content
        self.created_at = dt.datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
        self.pinned = pinned


class FakeChannel:
    def __init__(self, channel_id, name, guild, *, type="text", category_id=None,
                 topic=None, position=0, messages=()):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.type = type
        self.category_id = category_id
        self.topic = topic
        self.position = position
        self.messages = list(messages)  # newest first
        self.sent = []
        self.send_error = None

    def history(self, *, limit):
        messages = self.messages[:limit]

        async def _gen():
            for message in messages:
                yield message

        return _gen()

    async def send(self, content):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(content)
        return FakeMessage(900, FakeMember(1, "axitools"), content)


class FakeGuild:
    def __init__(self, guild_id, name):
        self.id = guild_id
        self.name = name
        self.member_count = 0
        self.categories = []
        self.channels = []
        self.roles = []
        self.members = []
        self.threads = []
        self.scheduled_events = []

    def get_channel(self, channel_id):
        return next((c for c in self.channels if c.id == channel_id), None)


def _build_guild_123():
    guild = FakeGuild(123, "Vigil Keep")
    category = FakeChannel(10, "Operations", guild, type="category")
    author = FakeMember(30, "logan")
    general = FakeChannel(
        11, "general", guild, category_id=10, topic="Chatter", position=1,
        messages=[
            FakeMessage(101, author, "newest", pinned=True),
            FakeMessage(100, author, "older"),
        ],
    )
    guild.categories = [category]
    guild.channels = [category, general]
    role = FakeRole(20, "Raider", position=5, members=[author])
    guild.roles = [role]
    author.roles = [role]
    guild.members = [author, FakeMember(31, "rytlock")]
    guild.member_count = 2
    guild.threads = [SimpleNamespace(id=40, name="raid-plans", parent_id=11, archived=False)]
    guild.scheduled_events = [SimpleNamespace(
        id=50, name="Reset bash", description=None,
        start_time=dt.datetime(2026, 6, 12, 18, 0, tzinfo=UTC),
        end_time=None, channel_id=None, location="Eternal Battlegrounds",
    )]
    return guild


class FakeBot:
    """Minimal stand-in for AxiToolsBot: .storage, .guilds, .get_guild."""

    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [_build_guild_123(), FakeGuild(456, "Durmand Priory")]

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest.fixture
def scoped_key(bot):
    """Generate an AxiVale key scoped to guild 123 and persist its hash."""
    key = generate_app_key()
    bot.storage.set_app_key(123, hash_app_key(key), created_by=42)
    return key


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _auth():
    return _bearer("test-token")


# ---------------------------------------------------------------------------
# GET /guilds/{id}/discord (snapshot)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_shape(api_client):
    resp = await api_client.get("/guilds/123/discord", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["guild"] == {"id": 123, "name": "Vigil Keep", "member_count": 2}
    assert body["categories"] == [{"id": 10, "name": "Operations", "position": 0}]
    # Categories are excluded from the flat channel list.
    assert body["channels"] == [{
        "id": 11, "name": "general", "type": "text",
        "category_id": 10, "topic": "Chatter", "position": 1,
    }]
    assert body["roles"] == [{
        "id": 20, "name": "Raider", "color": "#c8423a", "position": 5,
        "hoist": False, "mentionable": True, "permissions": 104320,
        "member_count": 1,
    }]
    assert body["threads"] == [
        {"id": 40, "name": "raid-plans", "parent_id": 11, "archived": False}
    ]
    assert body["scheduled_events"] == [{
        "id": 50, "name": "Reset bash", "description": None,
        "start_time": "2026-06-12T18:00:00+00:00", "end_time": None,
        "channel_id": None, "location": "Eternal Battlegrounds",
    }]
    assert "members" not in body


@pytest.mark.asyncio
async def test_snapshot_include_members(api_client):
    resp = await api_client.get("/guilds/123/discord?include=members", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["members_total"] == 2
    assert body["members"][0] == {
        "id": 30, "name": "logan", "display_name": "Logan",
        "role_ids": [20], "joined_at": "2024-01-01T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_snapshot_caps_members_at_1000(api_client, bot):
    guild = bot.get_guild(123)
    guild.members = [FakeMember(1000 + i, f"m{i}") for i in range(1005)]
    resp = await api_client.get("/guilds/123/discord?include=members", headers=_auth())
    body = await resp.json()
    assert body["members_total"] == 1005
    assert len(body["members"]) == 1000


@pytest.mark.asyncio
async def test_snapshot_unknown_guild_404(api_client, bot):
    bot.guilds = []
    resp = await api_client.get("/guilds/123/discord", headers=_auth())
    assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /guilds/{id}/discord/messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_shape_newest_first(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11", headers=_auth()
    )
    assert resp.status == 200
    body = await resp.json()
    assert [m["id"] for m in body] == [101, 100]
    assert body[0] == {
        "id": 101, "author_id": 30, "author_name": "logan",
        "content": "newest", "created_at": "2026-06-11T12:00:00+00:00",
        "pinned": True,
    }


@pytest.mark.asyncio
async def test_messages_limit_param(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&limit=1", headers=_auth()
    )
    body = await resp.json()
    assert [m["id"] for m in body] == [101]


@pytest.mark.asyncio
async def test_messages_requires_channel_id(api_client):
    resp = await api_client.get("/guilds/123/discord/messages", headers=_auth())
    assert resp.status == 400


@pytest.mark.asyncio
async def test_messages_rejects_bad_limit(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&limit=500", headers=_auth()
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_messages_channel_not_in_guild_404(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=999", headers=_auth()
    )
    assert resp.status == 404
    assert "not found in this server" in (await resp.json())["error"]


# ---------------------------------------------------------------------------
# GET /guilds/{id}/discord/actions (introspection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_actions_listing(api_client):
    resp = await api_client.get("/guilds/123/discord/actions", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    by_name = {entry["action"]: entry for entry in body}
    assert set(by_name) == set(ACTIONS)
    assert by_name["member_ban"]["destructive"] is True
    assert by_name["message_send"]["destructive"] is False
    assert by_name["message_send"]["params"]["content"]["required"] is True


# ---------------------------------------------------------------------------
# POST /guilds/{id}/discord/actions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_action_happy_path(api_client, bot):
    resp = await api_client.post(
        "/guilds/123/discord/actions",
        json={"action": "message_send", "params": {"channel_id": 11, "content": "o7"}},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body == {"ok": True, "result": {"id": 900, "channel_id": 11}}
    assert bot.get_guild(123).get_channel(11).sent == ["o7"]


@pytest.mark.asyncio
async def test_post_unknown_action_400_lists_valid_names(api_client):
    resp = await api_client.post(
        "/guilds/123/discord/actions",
        json={"action": "channel_nuke", "params": {}},
        headers=_auth(),
    )
    assert resp.status == 400
    body = await resp.json()
    assert "unknown action" in body["error"]
    assert body["valid_actions"] == sorted(ACTIONS)


@pytest.mark.asyncio
async def test_post_validation_error_400(api_client):
    resp = await api_client.post(
        "/guilds/123/discord/actions",
        json={"action": "message_send", "params": {"channel_id": 11}},
        headers=_auth(),
    )
    assert resp.status == 400
    assert "content" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_post_forbidden_maps_to_403(api_client, bot):
    channel = bot.get_guild(123).get_channel(11)
    channel.send_error = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )
    resp = await api_client.post(
        "/guilds/123/discord/actions",
        json={"action": "message_send", "params": {"channel_id": 11, "content": "o7"}},
        headers=_auth(),
    )
    assert resp.status == 403
    assert "the bot lacks permission" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_post_cross_guild_resource_400_no_action(api_client, bot):
    # Channel 11 lives in guild 123; acting on it via guild 456 must fail.
    other = FakeGuild(456, "Durmand Priory")
    other.channels = []
    resp = await api_client.post(
        "/guilds/456/discord/actions",
        json={"action": "message_send", "params": {"channel_id": 11, "content": "o7"}},
        headers=_auth(),
    )
    assert resp.status == 400
    assert "not found in this server" in (await resp.json())["error"]
    assert bot.get_guild(123).get_channel(11).sent == []


# ---------------------------------------------------------------------------
# Auth scoping holds for the new routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scoped_key_can_use_discord_routes(api_client, scoped_key):
    for path in (
        "/guilds/123/discord",
        "/guilds/123/discord/messages?channel_id=11",
        "/guilds/123/discord/actions",
    ):
        resp = await api_client.get(path, headers=_bearer(scoped_key))
        assert resp.status == 200, path
    resp = await api_client.post(
        "/guilds/123/discord/actions",
        json={"action": "message_send", "params": {"channel_id": 11, "content": "hi"}},
        headers=_bearer(scoped_key),
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_scoped_key_rejected_for_other_guild_discord_routes(api_client, scoped_key):
    for method, path in (
        ("get", "/guilds/456/discord"),
        ("get", "/guilds/456/discord/messages?channel_id=11"),
        ("get", "/guilds/456/discord/actions"),
        ("post", "/guilds/456/discord/actions"),
    ):
        kwargs = {"headers": _bearer(scoped_key)}
        if method == "post":
            kwargs["json"] = {"action": "message_send", "params": {}}
        resp = await getattr(api_client, method)(path, **kwargs)
        assert resp.status == 403, path
        assert await resp.json() == {"error": "key is scoped to another server"}
