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
    def __init__(self, member_id, name, roles=(), bot=False):
        self.id = member_id
        self.name = name
        self.display_name = name.title()
        self.roles = list(roles)
        self.bot = bot
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

    def history(self, *, limit, before=None, after=None):
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


class FakeForumChannel:
    """A forum channel holds posts (threads), not messages — so, like discord.py's
    ForumChannel, it deliberately has no history()."""

    def __init__(self, channel_id, name, guild):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.type = "forum"
        self.available_tags = [SimpleNamespace(id=70, name="Active")]


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
        self.emojis = []

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
    forum = FakeForumChannel(12, "raid-forum", guild)
    guild.categories = [category]
    guild.channels = [category, general, forum]
    role = FakeRole(20, "Raider", position=5, members=[author])
    guild.roles = [role]
    author.roles = [role]
    guild.members = [author, FakeMember(31, "rytlock")]
    guild.member_count = 2
    guild.emojis = [SimpleNamespace(id=80, name="luminary", animated=False)]
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
    """Generate an AxiVale key scoped to guild 123 and persist its hash."""
    key = generate_app_key()
    bot.storage.add_app_key(123, hash_app_key(key), created_by=42)
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
    # Snowflake ids (and the 64-bit permissions bitfield) serialize as strings.
    assert body["guild"] == {"id": "123", "name": "Vigil Keep", "member_count": 2}
    assert body["categories"] == [{"id": "10", "name": "Operations", "position": 0}]
    # Categories are excluded from the flat channel list; other channel kinds
    # (e.g. forums) are included.
    assert body["channels"] == [
        {
            "id": "11", "name": "general", "type": "text",
            "category_id": "10", "topic": "Chatter", "position": 1,
        },
        {
            "id": "12", "name": "raid-forum", "type": "forum",
            "category_id": None, "topic": None, "position": 0,
            "available_tags": [{"id": "70", "name": "Active"}],
        },
    ]
    assert body["emojis"] == [{"id": "80", "name": "luminary", "animated": False}]
    assert body["roles"] == [{
        "id": "20", "name": "Raider", "color": "#c8423a", "position": 5,
        "hoist": False, "mentionable": True, "permissions": "104320",
        "member_count": 1,
    }]
    assert body["threads"] == [
        {"id": "40", "name": "raid-plans", "parent_id": "11", "archived": False}
    ]
    assert body["scheduled_events"] == [{
        "id": "50", "name": "Reset bash", "description": None,
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
        "id": "30", "name": "logan", "display_name": "Logan", "bot": False,
        "role_ids": ["20"], "joined_at": "2024-01-01T00:00:00+00:00",
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
async def test_19_digit_snowflake_round_trips_as_exact_string(aiohttp_client, tmp_path):
    # 1380283751703445199 rounds to ...200 as a JS number; the API must emit
    # the exact digits as a string through /guilds and the snapshot.
    big = 1380283751703445199
    bot = FakeBot(tmp_path)
    bot.guilds = [FakeGuild(big, "Big Keep")]
    client = await aiohttp_client(build_app(bot, token="test-token"))

    resp = await client.get("/guilds", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == [{"id": "1380283751703445199", "name": "Big Keep"}]

    resp = await client.get(f"/guilds/{big}/discord", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["guild"]["id"] == "1380283751703445199"


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
    assert [m["id"] for m in body] == ["101", "100"]
    assert body[0] == {
        "id": "101", "author_id": "30", "author_name": "logan",
        "content": "newest", "created_at": "2026-06-11T12:00:00+00:00",
        "pinned": True,
    }


@pytest.mark.asyncio
async def test_messages_limit_param(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&limit=1", headers=_auth()
    )
    body = await resp.json()
    assert [m["id"] for m in body] == ["101"]


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


@pytest.mark.asyncio
async def test_messages_forum_channel_400_not_500(api_client):
    # A forum channel has no history() — must be a clear 400, not an AttributeError 500.
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=12", headers=_auth()
    )
    assert resp.status == 400
    body = await resp.json()
    assert "has no messages" in body["error"]
    assert "thread_id" in body["error"]  # forum hint to read a post instead


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
    assert body == {"ok": True, "result": {"id": "900", "channel_id": "11"}}
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


# ---------------------------------------------------------------------------
# parse_history_window (pure) — the digits/ISO/garbage logic
# ---------------------------------------------------------------------------

from multidict import MultiDict

from axitools.api.server import parse_history_window


def _win(**params):
    """parse_history_window takes a mapping like request.query."""
    return parse_history_window(MultiDict(params))


def test_window_defaults_to_no_bounds():
    win, err = _win()
    assert err is None
    assert win == {"before": None, "after": None}


def test_window_before_after_as_snowflake_ids():
    win, err = _win(before="101", after="100")
    assert err is None
    assert isinstance(win["before"], discord.Object)
    assert win["before"].id == 101
    assert isinstance(win["after"], discord.Object)
    assert win["after"].id == 100


def test_window_before_as_iso_date_is_utc_aware():
    win, err = _win(before="2026-06-10")
    assert err is None
    value = win["before"]
    assert isinstance(value, dt.datetime)
    assert value == dt.datetime(2026, 6, 10, tzinfo=UTC)


def test_window_after_as_iso_datetime_with_offset():
    win, err = _win(after="2026-06-10T08:30:00+00:00")
    assert err is None
    assert win["after"] == dt.datetime(2026, 6, 10, 8, 30, tzinfo=UTC)


def test_window_garbage_before_is_error():
    win, err = _win(before="not-a-date")
    assert win is None
    assert "before" in err


def test_window_garbage_after_is_error():
    win, err = _win(after="2026-13-99")
    assert win is None
    assert "after" in err


# ---------------------------------------------------------------------------
# GET /guilds/{id}/discord/messages — thread + paging integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_reads_a_thread_by_thread_id(api_client, bot):
    guild = bot.get_guild(123)
    author = FakeMember(30, "logan")
    thread = FakeChannel(
        77, "raid-plans", guild, type="public_thread",
        messages=[FakeMessage(201, author, "thread msg")],
    )
    guild.channels.append(thread)
    resp = await api_client.get(
        "/guilds/123/discord/messages?thread_id=77", headers=_auth()
    )
    assert resp.status == 200
    body = await resp.json()
    assert [m["id"] for m in body] == ["201"]


@pytest.mark.asyncio
async def test_messages_requires_channel_or_thread(api_client):
    resp = await api_client.get("/guilds/123/discord/messages", headers=_auth())
    assert resp.status == 400
    assert "channel_id or thread_id" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_messages_bad_before_is_400_before_reading(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&before=garbage", headers=_auth()
    )
    assert resp.status == 400
    assert "before" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_messages_accepts_before_after_passthrough(api_client):
    # The fake channel ignores before/after; this asserts valid params don't 400
    # and the response shape is unchanged. (Live before/after filtering is
    # verified against a real Discord connection — see plan note.)
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&before=101&after=2026-06-01",
        headers=_auth(),
    )
    assert resp.status == 200
    assert [m["id"] for m in await resp.json()] == ["101", "100"]


@pytest.mark.asyncio
async def test_messages_unknown_thread_404(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?thread_id=99999", headers=_auth()
    )
    assert resp.status == 404
    assert "not found in this server" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_messages_rejects_both_channel_id_and_thread_id(api_client):
    resp = await api_client.get(
        "/guilds/123/discord/messages?channel_id=11&thread_id=77", headers=_auth()
    )
    assert resp.status == 400
    assert (await resp.json())["error"] == (
        "provide exactly one of channel_id or thread_id, not both"
    )
