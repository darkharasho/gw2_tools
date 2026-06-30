from types import SimpleNamespace

from axitools.cogs.audit import _derive_target_type, build_discord_event_fields


def test_derive_target_type():
    assert _derive_target_type("channel_create") == "channel"
    assert _derive_target_type("role_update") == "role"
    assert _derive_target_type("message_delete") == "message"
    assert _derive_target_type("emoji_update") == "emoji"
    assert _derive_target_type("guild_update") == "guild"
    assert _derive_target_type("member_leave") == "user"
    assert _derive_target_type("something_else") is None


def test_build_fields_for_channel_event():
    actor = SimpleNamespace(id=42, name="rooster", bot=True)
    channel = SimpleNamespace(id=1449262177046495356, name="raid-signups")
    fields = build_discord_event_fields(event_type="channel_create", actor=actor, channel=channel)
    assert fields == {
        "actor_is_bot": True,
        "target_type": "channel",
        "channel_id": "1449262177046495356",
        "channel_name": "raid-signups",
    }


def test_build_fields_member_event_no_channel_no_actor():
    fields = build_discord_event_fields(event_type="member_leave", actor=None, channel=None)
    assert fields == {
        "actor_is_bot": None,
        "target_type": "user",
        "channel_id": None,
        "channel_name": None,
    }
