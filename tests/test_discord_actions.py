import pytest

from axitools.api.discord_actions import ACTIONS, execute_action


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
    "message_send": False,
    "message_pin": False,
    "thread_create": False,
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
            assert meta["type"] in ("string", "integer", "boolean")
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
    assert result == {"id": 555, "channel_id": 11}


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
    assert result == {"id": 7001, "name": "raids", "type": "text"}


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
    assert result == {"member_id": 30, "role_id": 20, "role_name": "Raider"}


@pytest.mark.asyncio
async def test_member_kick_passes_reason_to_audit_log(guild):
    member = guild.add_member(FakeMember(30, name="Logan"))
    result = await execute_action(
        None, guild, "member_kick", {"member_id": 30, "reason": "spam"}
    )
    assert member.calls == [("kick", "AxiVale: spam")]
    assert result == {"id": 30, "name": "Logan", "kicked": True}


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
    assert result == {"id": 555, "channel_id": 11}


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
