import discord
import pytest

from axitools.api.discord_actions import ACTIONS, execute_action


class FakeHTTPResponse:
    status = 403
    reason = "Forbidden"


def make_forbidden() -> discord.Forbidden:
    return discord.Forbidden(FakeHTTPResponse(), "Cannot send messages to this user")


# ---------------------------------------------------------------------------
# Fakes (plain recording stand-ins for discord.py objects)
# ---------------------------------------------------------------------------

class FakeRole:
    def __init__(self, role_id: int, name: str = "Role", guild=None) -> None:
        self.id = role_id
        self.name = name
        self.guild = guild
        self.calls = []

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))

    async def delete(self, *, reason=None):
        self.calls.append(("delete", reason))


class FakeMember:
    def __init__(self, member_id: int, name: str = "member", guild=None) -> None:
        self.id = member_id
        self.name = name
        self.guild = guild
        self.calls = []
        self.dm_error = None  # set to an exception to make send() raise

    async def send(self, content):
        if self.dm_error is not None:
            raise self.dm_error
        self.calls.append(("send", content))
        return FakeMessage(556)

    async def add_roles(self, *roles, reason=None):
        self.calls.append(("add_roles", roles, reason))

    async def remove_roles(self, *roles, reason=None):
        self.calls.append(("remove_roles", roles, reason))

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))

    async def kick(self, *, reason=None):
        self.calls.append(("kick", reason))

    async def timeout(self, duration, *, reason=None):
        self.calls.append(("timeout", duration, reason))


class FakeMessage:
    def __init__(self, message_id: int = 555) -> None:
        self.id = message_id
        self.calls = []

    async def pin(self, *, reason=None):
        self.calls.append(("pin", reason))


class FakeChannel:
    def __init__(self, channel_id: int, name: str = "general", guild=None, type: str = "text") -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.type = type
        self.calls = []

    async def send(self, content):
        self.calls.append(("send", content))
        return FakeMessage(555)

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))

    async def delete(self, *, reason=None):
        self.calls.append(("delete", reason))


class FakeForumTag:
    def __init__(self, tag_id: int, name: str) -> None:
        self.id = tag_id
        self.name = name


class FakeForumChannel:
    def __init__(self, channel_id: int, name: str = "raid-forum", guild=None, tags=()) -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.type = "forum"
        self.available_tags = list(tags)


class FakeThread:
    def __init__(self, thread_id: int, name: str = "raid-plans", guild=None, parent=None) -> None:
        self.id = thread_id
        self.name = name
        self.guild = guild
        self.type = "public_thread"
        self.parent = parent
        self.calls = []

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))


class FakeGuild:
    def __init__(self, guild_id: int = 123, name: str = "Vigil Keep") -> None:
        self.id = guild_id
        self.name = name
        self.calls = []
        self._channels = {}
        self._roles = {}
        self._members = {}

    # -- registration helpers -------------------------------------------
    def add_channel(self, channel: FakeChannel) -> FakeChannel:
        if channel.guild is None:
            channel.guild = self
        self._channels[channel.id] = channel
        return channel

    def add_role(self, role: FakeRole) -> FakeRole:
        if role.guild is None:
            role.guild = self
        self._roles[role.id] = role
        return role

    def add_member(self, member: FakeMember) -> FakeMember:
        if member.guild is None:
            member.guild = self
        self._members[member.id] = member
        return member

    # -- discord.Guild surface ------------------------------------------
    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, member_id):
        return self._members.get(member_id)

    async def create_text_channel(self, name, *, category=None, topic=None, reason=None):
        self.calls.append(("create_text_channel", {
            "name": name, "category": category, "topic": topic, "reason": reason,
        }))
        return self.add_channel(FakeChannel(7001, name=name, guild=self))

    async def create_voice_channel(self, name, *, category=None, reason=None):
        self.calls.append(("create_voice_channel", {
            "name": name, "category": category, "reason": reason,
        }))
        return self.add_channel(FakeChannel(7002, name=name, guild=self, type="voice"))

    async def create_role(self, **kwargs):
        self.calls.append(("create_role", kwargs))
        return self.add_role(FakeRole(8001, name=kwargs["name"], guild=self))

    async def ban(self, member, *, reason=None, delete_message_seconds=0):
        self.calls.append(("ban", member, reason, delete_message_seconds))


@pytest.fixture
def guild():
    return FakeGuild()


# ---------------------------------------------------------------------------
# Registry contents
# ---------------------------------------------------------------------------

EXPECTED_ACTIONS = {
    "channel_create": False,
    "channel_update": False,
    "channel_delete": True,
    "role_create": False,
    "role_update": True,
    "role_delete": True,
    "role_assign": False,
    "role_unassign": False,
    "member_nick": False,
    "member_timeout": True,
    "member_kick": True,
    "member_ban": True,
    "member_dm": True,
    "members_dm": True,
    "message_send": False,
    "message_pin": False,
    "thread_create": False,
    "thread_update": False,
    "event_create": False,
}


def test_registry_names_and_destructive_flags():
    actual = {name: spec["destructive"] for name, spec in ACTIONS.items()}
    assert actual == EXPECTED_ACTIONS


def test_registry_params_are_json_able_specs():
    for name, spec in ACTIONS.items():
        assert callable(spec["executor"]), name
        for param_name, meta in spec["params"].items():
            assert set(meta) == {"type", "required", "description"}, (name, param_name)
            assert meta["type"] in ("string", "integer", "boolean", "array", "tag_array")
            assert isinstance(meta["required"], bool)


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_required_param_raises(guild):
    with pytest.raises(ValueError, match="missing required parameter.*channel_id"):
        await execute_action(None, guild, "channel_delete", {})


@pytest.mark.asyncio
async def test_wrong_type_param_raises(guild):
    with pytest.raises(ValueError, match="expected integer"):
        await execute_action(
            None, guild, "message_send", {"channel_id": "abc", "content": "hi"}
        )


@pytest.mark.asyncio
async def test_unknown_param_raises(guild):
    member = guild.add_member(FakeMember(30))
    with pytest.raises(ValueError, match="unknown parameter"):
        await execute_action(None, guild, "member_kick", {"member_id": 30, "bogus": 1})
    assert member.calls == []


@pytest.mark.asyncio
async def test_numeric_string_ids_are_coerced(guild):
    channel = guild.add_channel(FakeChannel(11))
    result = await execute_action(
        None, guild, "message_send", {"channel_id": "11", "content": "hello"}
    )
    assert channel.calls == [("send", "hello")]
    assert result == {"id": "555", "channel_id": "11"}


@pytest.mark.asyncio
async def test_full_precision_19_digit_string_id_coerces_exactly(guild):
    big = 1380283751703445199  # exceeds JS Number.MAX_SAFE_INTEGER
    channel = guild.add_channel(FakeChannel(big))
    result = await execute_action(
        None, guild, "message_send", {"channel_id": str(big), "content": "hi"}
    )
    assert channel.calls == [("send", "hi")]
    assert result["channel_id"] == "1380283751703445199"


@pytest.mark.asyncio
async def test_role_permissions_accepts_int_and_string(guild):
    await execute_action(None, guild, "role_create", {"name": "A", "permissions": 104320})
    await execute_action(None, guild, "role_create", {"name": "B", "permissions": "104320"})
    perms = [kwargs["permissions"] for name, kwargs in guild.calls if name == "create_role"]
    assert [p.value for p in perms] == [104320, 104320]


@pytest.mark.asyncio
async def test_invalid_color_raises(guild):
    with pytest.raises(ValueError, match="expected a hex string"):
        await execute_action(None, guild, "role_create", {"name": "Raider", "color": "red"})


# ---------------------------------------------------------------------------
# Guild scoping: foreign ids must never act
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_channel_id_errors(guild):
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(None, guild, "channel_delete", {"channel_id": 999})


@pytest.mark.asyncio
async def test_channel_from_other_guild_errors(guild):
    other = FakeGuild(456, "Durmand Priory")
    foreign = FakeChannel(12, guild=other)
    guild._channels[12] = foreign  # simulate a stale/poisoned cache entry
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(None, guild, "channel_delete", {"channel_id": 12})
    assert foreign.calls == []


@pytest.mark.asyncio
async def test_role_from_other_guild_errors(guild):
    other = FakeGuild(456)
    foreign = FakeRole(21, guild=other)
    guild._roles[21] = foreign
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(None, guild, "role_delete", {"role_id": 21})
    assert foreign.calls == []


@pytest.mark.asyncio
async def test_member_from_other_guild_errors(guild):
    other = FakeGuild(456)
    foreign = FakeMember(31, guild=other)
    guild._members[31] = foreign
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(None, guild, "member_kick", {"member_id": 31})
    assert foreign.calls == []


@pytest.mark.asyncio
async def test_unknown_member_id_errors(guild):
    role = guild.add_role(FakeRole(20))
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(None, guild, "role_assign", {"member_id": 999, "role_id": 20})
    assert role.calls == []


# ---------------------------------------------------------------------------
# Representative executors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_create_text(guild):
    result = await execute_action(
        None, guild, "channel_create", {"name": "raids", "topic": "Raid signups"}
    )
    assert guild.calls == [("create_text_channel", {
        "name": "raids",
        "category": None,
        "topic": "Raid signups",
        "reason": "AxiVale: channel_create",
    })]
    assert result == {"id": "7001", "name": "raids", "type": "text"}


@pytest.mark.asyncio
async def test_channel_create_rejects_bad_type(guild):
    with pytest.raises(ValueError, match="invalid channel type"):
        await execute_action(None, guild, "channel_create", {"name": "x", "type": "dm"})


@pytest.mark.asyncio
async def test_channel_create_with_category(guild):
    category = guild.add_channel(FakeChannel(10, name="Ops", type="category"))
    await execute_action(
        None, guild, "channel_create", {"name": "raids", "category_id": 10}
    )
    assert guild.calls[0][1]["category"] is category


@pytest.mark.asyncio
async def test_role_assign_calls_add_roles(guild):
    member = guild.add_member(FakeMember(30, name="Logan"))
    role = guild.add_role(FakeRole(20, name="Raider"))
    result = await execute_action(
        None, guild, "role_assign", {"member_id": 30, "role_id": 20}
    )
    assert member.calls == [("add_roles", (role,), "AxiVale: role_assign")]
    assert result == {"member_id": "30", "role_id": "20", "role_name": "Raider"}


@pytest.mark.asyncio
async def test_member_kick_passes_reason_to_audit_log(guild):
    member = guild.add_member(FakeMember(30, name="Logan"))
    result = await execute_action(
        None, guild, "member_kick", {"member_id": 30, "reason": "spam"}
    )
    assert member.calls == [("kick", "AxiVale: spam")]
    assert result == {"id": "30", "name": "Logan", "kicked": True}


@pytest.mark.asyncio
async def test_member_ban_converts_days_to_seconds(guild):
    member = guild.add_member(FakeMember(30, name="Logan"))
    await execute_action(
        None, guild, "member_ban", {"member_id": 30, "delete_message_days": 2}
    )
    assert guild.calls == [("ban", member, "AxiVale: member_ban", 2 * 86400)]


@pytest.mark.asyncio
async def test_member_ban_rejects_out_of_range_days(guild):
    member = guild.add_member(FakeMember(30))
    with pytest.raises(ValueError, match="between 0 and 7"):
        await execute_action(
            None, guild, "member_ban", {"member_id": 30, "delete_message_days": 9}
        )
    assert guild.calls == []
    assert member.calls == []


@pytest.mark.asyncio
async def test_message_send(guild):
    channel = guild.add_channel(FakeChannel(11))
    result = await execute_action(
        None, guild, "message_send", {"channel_id": 11, "content": "o7"}
    )
    assert channel.calls == [("send", "o7")]
    assert result == {"id": "555", "channel_id": "11"}


# ---------------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------------

@pytest.fixture
def sleeps(monkeypatch):
    """Replace pacing sleeps with a recorder so tests don't wait."""
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(
        "axitools.api.discord_actions.asyncio.sleep", fake_sleep
    )
    return recorded


def test_member_dm_registry_spec():
    spec = ACTIONS["member_dm"]
    assert spec["destructive"] is True
    assert spec["params"]["member_id"] == {
        "type": "integer", "required": True,
        "description": spec["params"]["member_id"]["description"],
    }
    assert spec["params"]["content"]["type"] == "string"
    assert spec["params"]["content"]["required"] is True


def test_members_dm_registry_spec():
    spec = ACTIONS["members_dm"]
    assert spec["destructive"] is True
    assert spec["params"]["member_ids"]["type"] == "array"
    assert spec["params"]["member_ids"]["required"] is True
    assert spec["params"]["content"]["type"] == "string"
    assert spec["params"]["content"]["required"] is True


@pytest.mark.asyncio
async def test_member_dm_sends_and_reports(guild):
    member = guild.add_member(FakeMember(30, name="Logan"))
    result = await execute_action(
        None, guild, "member_dm", {"member_id": 30, "content": "o7"}
    )
    assert member.calls == [("send", "o7")]
    assert result == {"member_id": "30", "sent": True}


@pytest.mark.asyncio
async def test_member_dm_forbidden_becomes_value_error(guild):
    member = guild.add_member(FakeMember(30))
    member.dm_error = make_forbidden()
    with pytest.raises(ValueError, match="member has DMs disabled or blocks the bot"):
        await execute_action(
            None, guild, "member_dm", {"member_id": 30, "content": "o7"}
        )


@pytest.mark.asyncio
async def test_member_dm_unknown_member_errors(guild):
    with pytest.raises(ValueError, match="not found in this server"):
        await execute_action(
            None, guild, "member_dm", {"member_id": 999, "content": "o7"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   ", "x" * 1901])
async def test_member_dm_rejects_bad_content(guild, content):
    member = guild.add_member(FakeMember(30))
    with pytest.raises(ValueError, match="content must be"):
        await execute_action(
            None, guild, "member_dm", {"member_id": 30, "content": content}
        )
    assert member.calls == []


@pytest.mark.asyncio
async def test_members_dm_happy_path_paces_between_sends(guild, sleeps):
    alpha = guild.add_member(FakeMember(30, name="Logan"))
    bravo = guild.add_member(FakeMember(31, name="Rytlock"))
    charlie = guild.add_member(FakeMember(32, name="Caithe"))
    result = await execute_action(
        None, guild, "members_dm", {"member_ids": [30, 31, 32], "content": "reset at 8"}
    )
    assert alpha.calls == [("send", "reset at 8")]
    assert bravo.calls == [("send", "reset at 8")]
    assert charlie.calls == [("send", "reset at 8")]
    assert sleeps == [0.6, 0.6]  # between sends, not before the first
    assert result == {"requested": 3, "sent": ["30", "31", "32"], "failed": []}


@pytest.mark.asyncio
async def test_members_dm_accepts_string_ids(guild, sleeps):
    member = guild.add_member(FakeMember(1380283751703445199))
    result = await execute_action(
        None, guild, "members_dm",
        {"member_ids": ["1380283751703445199"], "content": "hi"},
    )
    assert member.calls == [("send", "hi")]
    assert result["sent"] == ["1380283751703445199"]


@pytest.mark.asyncio
async def test_members_dm_rejects_empty_list(guild, sleeps):
    with pytest.raises(ValueError, match="between 1 and 250"):
        await execute_action(
            None, guild, "members_dm", {"member_ids": [], "content": "hi"}
        )


@pytest.mark.asyncio
async def test_members_dm_rejects_oversized_list(guild, sleeps):
    with pytest.raises(ValueError, match="between 1 and 250"):
        await execute_action(
            None, guild, "members_dm",
            {"member_ids": list(range(1, 252)), "content": "hi"},
        )


@pytest.mark.asyncio
async def test_members_dm_rejects_non_list(guild, sleeps):
    with pytest.raises(ValueError, match="expected array"):
        await execute_action(
            None, guild, "members_dm", {"member_ids": 30, "content": "hi"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   ", "x" * 1901])
async def test_members_dm_rejects_bad_content(guild, sleeps, content):
    member = guild.add_member(FakeMember(30))
    with pytest.raises(ValueError, match="content must be"):
        await execute_action(
            None, guild, "members_dm", {"member_ids": [30], "content": content}
        )
    assert member.calls == []


@pytest.mark.asyncio
async def test_members_dm_unknown_id_fails_fast_with_nothing_sent(guild, sleeps):
    known = guild.add_member(FakeMember(30))
    with pytest.raises(ValueError, match="member 999 not found in this server"):
        await execute_action(
            None, guild, "members_dm", {"member_ids": [30, 999], "content": "hi"}
        )
    assert known.calls == []
    assert sleeps == []


@pytest.mark.asyncio
async def test_members_dm_continues_past_forbidden_member(guild, sleeps):
    alpha = guild.add_member(FakeMember(30))
    bravo = guild.add_member(FakeMember(31))
    charlie = guild.add_member(FakeMember(32))
    bravo.dm_error = make_forbidden()
    result = await execute_action(
        None, guild, "members_dm", {"member_ids": [30, 31, 32], "content": "hi"}
    )
    assert alpha.calls == [("send", "hi")]
    assert bravo.calls == []
    assert charlie.calls == [("send", "hi")]
    assert result["requested"] == 3
    assert result["sent"] == ["30", "32"]
    assert result["failed"] == [
        {"member_id": "31", "reason": "member has DMs disabled or blocks the bot"}
    ]


@pytest.mark.asyncio
async def test_members_dm_collects_http_errors(guild, sleeps):
    alpha = guild.add_member(FakeMember(30))
    bravo = guild.add_member(FakeMember(31))
    bravo.dm_error = discord.HTTPException(FakeHTTPResponse(), "boom")
    result = await execute_action(
        None, guild, "members_dm", {"member_ids": [30, 31], "content": "hi"}
    )
    assert result["sent"] == ["30"]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["member_id"] == "31"
    assert "boom" in result["failed"][0]["reason"]


@pytest.mark.asyncio
async def test_event_create_external_requires_end_time(guild):
    with pytest.raises(ValueError, match="end_time is required"):
        await execute_action(None, guild, "event_create", {
            "name": "Reset bash",
            "start_time": "2026-06-12T18:00:00Z",
            "location": "Eternal Battlegrounds",
        })


@pytest.mark.asyncio
async def test_event_create_requires_channel_or_location(guild):
    with pytest.raises(ValueError, match="channel_id or location"):
        await execute_action(None, guild, "event_create", {
            "name": "Reset bash",
            "start_time": "2026-06-12T18:00:00Z",
        })


# ---------------------------------------------------------------------------
# thread_update (forum post pin + tags)
# ---------------------------------------------------------------------------

def _forum_with_thread(guild):
    forum = guild.add_channel(
        FakeForumChannel(900, guild=guild, tags=[FakeForumTag(1, "Active"), FakeForumTag(2, "Archived")])
    )
    thread = guild.add_channel(FakeThread(901, guild=guild, parent=forum))
    return forum, thread


@pytest.mark.asyncio
async def test_thread_update_pins_a_forum_post(guild):
    _forum, thread = _forum_with_thread(guild)
    result = await execute_action(None, guild, "thread_update", {"thread_id": 901, "pinned": True})
    assert thread.calls[-1][0] == "edit"
    assert thread.calls[-1][1]["pinned"] is True
    assert result["pinned"] is True


@pytest.mark.asyncio
async def test_thread_update_applies_tags_by_name(guild):
    _forum, thread = _forum_with_thread(guild)
    result = await execute_action(None, guild, "thread_update", {"thread_id": 901, "applied_tags": ["Active"]})
    assert [t.name for t in thread.calls[-1][1]["applied_tags"]] == ["Active"]
    assert result["applied_tags"] == ["Active"]


@pytest.mark.asyncio
async def test_thread_update_applies_tags_by_id(guild):
    _forum, thread = _forum_with_thread(guild)
    await execute_action(None, guild, "thread_update", {"thread_id": 901, "applied_tags": [2]})
    assert [t.name for t in thread.calls[-1][1]["applied_tags"]] == ["Archived"]


@pytest.mark.asyncio
async def test_thread_update_unknown_tag_lists_available(guild):
    _forum_with_thread(guild)
    with pytest.raises(ValueError, match="not found. Available tags: Active, Archived"):
        await execute_action(None, guild, "thread_update", {"thread_id": 901, "applied_tags": ["Nope"]})


@pytest.mark.asyncio
async def test_thread_update_tags_require_a_forum_parent(guild):
    text = guild.add_channel(FakeChannel(950, type="text"))
    guild.add_channel(FakeThread(951, guild=guild, parent=text))
    with pytest.raises(ValueError, match="forum post"):
        await execute_action(None, guild, "thread_update", {"thread_id": 951, "applied_tags": ["Active"]})


@pytest.mark.asyncio
async def test_thread_update_rejects_non_thread(guild):
    guild.add_channel(FakeChannel(960, type="text"))
    with pytest.raises(ValueError, match="is not a thread"):
        await execute_action(None, guild, "thread_update", {"thread_id": 960, "pinned": True})


@pytest.mark.asyncio
async def test_thread_update_requires_a_field(guild):
    _forum_with_thread(guild)
    with pytest.raises(ValueError, match="at least one field"):
        await execute_action(None, guild, "thread_update", {"thread_id": 901})
