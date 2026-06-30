from axitools.api.server import _discord_event_to_dict


def test_discord_event_to_dict_structured():
    row = {
        "id": 5,
        "created_at": "2026-06-30T07:38:00Z",
        "event_type": "channel_create",
        "actor_id": 42,
        "actor_name": "<@42> (rooster)",
        "target_id": None,
        "target_name": None,
        "details": "",
        "channel_id": "1449262177046495356",
        "channel_name": "raid-signups",
        "actor_is_bot": 1,
        "target_type": "channel",
    }
    out = _discord_event_to_dict(row)
    assert out["channel_id"] == "1449262177046495356"
    assert out["channel_name"] == "raid-signups"
    assert out["actor_is_bot"] is True
    assert out["target_type"] == "channel"
    # back-compat fields preserved
    assert out["event_type"] == "channel_create"
    assert out["actor_id"] == "42"


def test_discord_event_to_dict_old_row_nulls():
    row = {
        "id": 6,
        "created_at": "2026-06-30T07:38:00Z",
        "event_type": "member_leave",
        "actor_id": None,
        "actor_name": None,
        "target_id": 7,
        "target_name": "<@7> (khava)",
        "details": "Details: Member left the server.",
        "channel_id": None,
        "channel_name": None,
        "actor_is_bot": None,
        "target_type": None,
    }
    out = _discord_event_to_dict(row)
    assert out["channel_id"] is None
    assert out["channel_name"] is None
    assert out["actor_is_bot"] is None
    assert out["target_type"] is None
