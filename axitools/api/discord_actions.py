"""Registry of Discord management actions exposed via the localhost API.

Each entry in :data:`ACTIONS` maps an action name to a spec:

- ``destructive``: whether the action removes or punishes something.
- ``params``: name -> {type, required, description} (JSON-able, served to clients).
- ``executor``: ``async (bot, guild, params) -> dict`` returning a small
  JSON-able description of the affected entity.

All resource ids (channel/role/member/message) are resolved strictly within
the target guild; ids belonging to another guild fail with a
"not found in this server" ValueError before any Discord call is made.
"""

from __future__ import annotations

import datetime as dt
import string

import discord

# Maximum timeout Discord allows: 28 days.
_MAX_TIMEOUT_MINUTES = 28 * 24 * 60

_CHANNEL_TYPES = ("text", "voice", "category", "forum")


def audit_reason(reason: str | None, action: str) -> str:
    """Audit-log attribution for every API-originated Discord change."""
    return f"AxiVale: {reason or action}"


def _param(type_: str, required: bool = False, description: str = "") -> dict:
    return {"type": type_, "required": required, "description": description}


def _sid(value) -> str | None:
    """Serialize a snowflake id as a string for JSON results (None passes through).

    Snowflakes overflow JavaScript's Number.MAX_SAFE_INTEGER, so ids cross the
    API boundary as strings — same as Discord's own REST API.
    """
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------

def _coerce(action: str, name: str, expected: str, value):
    if expected == "string":
        if isinstance(value, str):
            return value
    elif expected == "integer":
        # bool is an int subclass; reject it explicitly.
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return int(value)
    elif expected == "boolean":
        if isinstance(value, bool):
            return value
    raise ValueError(f"invalid value for {action} parameter '{name}': expected {expected}")


def validate_params(action: str, spec_params: dict, params: dict) -> dict:
    """Check required/typed params; return the coerced dict or raise ValueError."""
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    unknown = set(params) - set(spec_params)
    if unknown:
        raise ValueError(
            f"unknown parameter(s) for {action}: {', '.join(sorted(unknown))}"
        )
    cleaned: dict = {}
    for name, meta in spec_params.items():
        value = params.get(name)
        if value is None:
            if meta["required"]:
                raise ValueError(f"missing required parameter for {action}: {name}")
            continue
        cleaned[name] = _coerce(action, name, meta["type"], value)
    return cleaned


# ---------------------------------------------------------------------------
# Guild-scoped resource resolution
# ---------------------------------------------------------------------------

def resolve_channel(guild, channel_id: int):
    """Return the channel (or thread) iff it belongs to *guild*."""
    getter = getattr(guild, "get_channel_or_thread", None) or guild.get_channel
    channel = getter(channel_id)
    if channel is None or getattr(channel, "guild", guild).id != guild.id:
        raise ValueError(f"channel {channel_id} not found in this server")
    return channel


def _resolve_category(guild, category_id: int):
    category = resolve_channel(guild, category_id)
    if str(getattr(category, "type", "")) != "category":
        raise ValueError(f"channel {category_id} is not a category in this server")
    return category


def _resolve_role(guild, role_id: int):
    role = guild.get_role(role_id)
    if role is None or getattr(role, "guild", guild).id != guild.id:
        raise ValueError(f"role {role_id} not found in this server")
    return role


async def _resolve_member(guild, member_id: int):
    member = guild.get_member(member_id)
    if member is None:
        fetch = getattr(guild, "fetch_member", None)
        if fetch is not None:
            try:
                member = await fetch(member_id)
            except discord.HTTPException:
                member = None
    if member is None or getattr(member, "guild", guild).id != guild.id:
        raise ValueError(f"member {member_id} not found in this server")
    return member


async def _resolve_message(channel, message_id: int):
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        raise ValueError(f"message {message_id} not found in this server") from None


def _parse_color(value: str) -> discord.Colour:
    raw = value.lstrip("#")
    if len(raw) != 6 or any(c not in string.hexdigits for c in raw):
        raise ValueError(f"invalid color {value!r}: expected a hex string like '#c8423a'")
    return discord.Colour(int(raw, 16))


def _parse_timestamp(name: str, value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid {name}: expected an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

async def _exec_channel_create(bot, guild, p: dict) -> dict:
    ctype = p.get("type", "text")
    if ctype not in _CHANNEL_TYPES:
        raise ValueError(
            f"invalid channel type {ctype!r}: expected one of {', '.join(_CHANNEL_TYPES)}"
        )
    reason = audit_reason(None, "channel_create")
    category = _resolve_category(guild, p["category_id"]) if "category_id" in p else None
    if ctype == "category":
        channel = await guild.create_category(p["name"], reason=reason)
    elif ctype == "voice":
        channel = await guild.create_voice_channel(p["name"], category=category, reason=reason)
    elif ctype == "forum":
        channel = await guild.create_forum(
            p["name"], category=category, topic=p.get("topic"), reason=reason
        )
    else:
        channel = await guild.create_text_channel(
            p["name"], category=category, topic=p.get("topic"), reason=reason
        )
    return {"id": _sid(channel.id), "name": channel.name, "type": ctype}


async def _exec_channel_update(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    kwargs: dict = {}
    if "name" in p:
        kwargs["name"] = p["name"]
    if "topic" in p:
        kwargs["topic"] = p["topic"]
    if "category_id" in p:
        kwargs["category"] = _resolve_category(guild, p["category_id"])
    if "slowmode_seconds" in p:
        kwargs["slowmode_delay"] = p["slowmode_seconds"]
    if "nsfw" in p:
        kwargs["nsfw"] = p["nsfw"]
    if not kwargs:
        raise ValueError("channel_update requires at least one field to change")
    await channel.edit(reason=audit_reason(None, "channel_update"), **kwargs)
    return {"id": _sid(channel.id), "name": kwargs.get("name", channel.name)}


async def _exec_channel_delete(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    await channel.delete(reason=audit_reason(p.get("reason"), "channel_delete"))
    return {"id": _sid(channel.id), "name": channel.name, "deleted": True}


async def _exec_role_create(bot, guild, p: dict) -> dict:
    kwargs: dict = {"name": p["name"], "reason": audit_reason(None, "role_create")}
    if "color" in p:
        kwargs["colour"] = _parse_color(p["color"])
    if "hoist" in p:
        kwargs["hoist"] = p["hoist"]
    if "mentionable" in p:
        kwargs["mentionable"] = p["mentionable"]
    if "permissions" in p:
        kwargs["permissions"] = discord.Permissions(p["permissions"])
    role = await guild.create_role(**kwargs)
    return {"id": _sid(role.id), "name": role.name}


async def _exec_role_update(bot, guild, p: dict) -> dict:
    role = _resolve_role(guild, p["role_id"])
    kwargs: dict = {}
    if "name" in p:
        kwargs["name"] = p["name"]
    if "color" in p:
        kwargs["colour"] = _parse_color(p["color"])
    if "hoist" in p:
        kwargs["hoist"] = p["hoist"]
    if "mentionable" in p:
        kwargs["mentionable"] = p["mentionable"]
    if "permissions" in p:
        kwargs["permissions"] = discord.Permissions(p["permissions"])
    if not kwargs:
        raise ValueError("role_update requires at least one field to change")
    await role.edit(reason=audit_reason(None, "role_update"), **kwargs)
    return {"id": _sid(role.id), "name": kwargs.get("name", role.name)}


async def _exec_role_delete(bot, guild, p: dict) -> dict:
    role = _resolve_role(guild, p["role_id"])
    await role.delete(reason=audit_reason(p.get("reason"), "role_delete"))
    return {"id": _sid(role.id), "name": role.name, "deleted": True}


async def _exec_role_assign(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    role = _resolve_role(guild, p["role_id"])
    await member.add_roles(role, reason=audit_reason(None, "role_assign"))
    return {"member_id": _sid(member.id), "role_id": _sid(role.id), "role_name": role.name}


async def _exec_role_unassign(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    role = _resolve_role(guild, p["role_id"])
    await member.remove_roles(role, reason=audit_reason(None, "role_unassign"))
    return {"member_id": _sid(member.id), "role_id": _sid(role.id), "role_name": role.name}


async def _exec_member_nick(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    nick = p.get("nick") or None  # null / empty / missing all clear the nick
    await member.edit(nick=nick, reason=audit_reason(None, "member_nick"))
    return {"id": _sid(member.id), "nick": nick}


async def _exec_member_timeout(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    minutes = p["minutes"]
    if not 1 <= minutes <= _MAX_TIMEOUT_MINUTES:
        raise ValueError(
            f"minutes must be between 1 and {_MAX_TIMEOUT_MINUTES} (28 days)"
        )
    await member.timeout(
        dt.timedelta(minutes=minutes),
        reason=audit_reason(p.get("reason"), "member_timeout"),
    )
    return {"id": _sid(member.id), "name": member.name, "timeout_minutes": minutes}


async def _exec_member_kick(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    await member.kick(reason=audit_reason(p.get("reason"), "member_kick"))
    return {"id": _sid(member.id), "name": member.name, "kicked": True}


async def _exec_member_ban(bot, guild, p: dict) -> dict:
    member = await _resolve_member(guild, p["member_id"])
    days = p.get("delete_message_days", 0)
    if not 0 <= days <= 7:
        raise ValueError("delete_message_days must be between 0 and 7")
    await guild.ban(
        member,
        reason=audit_reason(p.get("reason"), "member_ban"),
        delete_message_seconds=days * 86400,
    )
    return {"id": _sid(member.id), "name": member.name, "banned": True}


async def _exec_message_send(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await channel.send(p["content"])
    return {"id": _sid(message.id), "channel_id": _sid(channel.id)}


async def _exec_message_pin(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    await message.pin(reason=audit_reason(None, "message_pin"))
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "pinned": True}


async def _exec_thread_create(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    kwargs: dict = {"name": p["name"], "reason": audit_reason(None, "thread_create")}
    if "message_id" in p:
        kwargs["message"] = await _resolve_message(channel, p["message_id"])
    else:
        kwargs["type"] = discord.ChannelType.public_thread
    thread = await channel.create_thread(**kwargs)
    return {"id": _sid(thread.id), "name": thread.name, "parent_id": _sid(channel.id)}


async def _exec_event_create(bot, guild, p: dict) -> dict:
    start = _parse_timestamp("start_time", p["start_time"])
    end = _parse_timestamp("end_time", p["end_time"]) if "end_time" in p else None
    kwargs: dict = {
        "name": p["name"],
        "start_time": start,
        "privacy_level": discord.PrivacyLevel.guild_only,
        "reason": audit_reason(None, "event_create"),
    }
    if "description" in p:
        kwargs["description"] = p["description"]
    if "channel_id" in p:
        channel = resolve_channel(guild, p["channel_id"])
        if str(getattr(channel, "type", "")) not in ("voice", "stage_voice"):
            raise ValueError(
                f"channel {p['channel_id']} is not a voice or stage channel"
            )
        kwargs["channel"] = channel
    elif "location" in p:
        if end is None:
            raise ValueError("end_time is required for external (location) events")
        kwargs["entity_type"] = discord.EntityType.external
        kwargs["location"] = p["location"]
    else:
        raise ValueError("event_create requires either channel_id or location")
    if end is not None:
        kwargs["end_time"] = end
    event = await guild.create_scheduled_event(**kwargs)
    return {"id": _sid(event.id), "name": event.name}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ACTIONS: dict[str, dict] = {
    "channel_create": {
        "destructive": False,
        "params": {
            "name": _param("string", True, "Channel name"),
            "type": _param("string", False, "One of: text, voice, category, forum (default text)"),
            "category_id": _param("integer", False, "Parent category id"),
            "topic": _param("string", False, "Channel topic (text/forum only)"),
        },
        "executor": _exec_channel_create,
    },
    "channel_update": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel to update"),
            "name": _param("string", False, "New name"),
            "topic": _param("string", False, "New topic"),
            "category_id": _param("integer", False, "New parent category id"),
            "slowmode_seconds": _param("integer", False, "Slowmode delay in seconds"),
            "nsfw": _param("boolean", False, "Mark channel NSFW"),
        },
        "executor": _exec_channel_update,
    },
    "channel_delete": {
        "destructive": True,
        "params": {
            "channel_id": _param("integer", True, "Channel to delete"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_channel_delete,
    },
    "role_create": {
        "destructive": False,
        "params": {
            "name": _param("string", True, "Role name"),
            "color": _param("string", False, "Hex color string like '#c8423a'"),
            "hoist": _param("boolean", False, "Show role members separately"),
            "mentionable": _param("boolean", False, "Allow anyone to mention the role"),
            "permissions": _param("integer", False, "Permissions bitfield (integer or numeric string)"),
        },
        "executor": _exec_role_create,
    },
    "role_update": {
        "destructive": True,
        "params": {
            "role_id": _param("integer", True, "Role to update"),
            "name": _param("string", False, "New name"),
            "color": _param("string", False, "Hex color string like '#c8423a'"),
            "hoist": _param("boolean", False, "Show role members separately"),
            "mentionable": _param("boolean", False, "Allow anyone to mention the role"),
            "permissions": _param("integer", False, "Permissions bitfield (integer or numeric string)"),
        },
        "executor": _exec_role_update,
    },
    "role_delete": {
        "destructive": True,
        "params": {
            "role_id": _param("integer", True, "Role to delete"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_role_delete,
    },
    "role_assign": {
        "destructive": False,
        "params": {
            "member_id": _param("integer", True, "Member receiving the role"),
            "role_id": _param("integer", True, "Role to assign"),
        },
        "executor": _exec_role_assign,
    },
    "role_unassign": {
        "destructive": False,
        "params": {
            "member_id": _param("integer", True, "Member losing the role"),
            "role_id": _param("integer", True, "Role to remove"),
        },
        "executor": _exec_role_unassign,
    },
    "member_nick": {
        "destructive": False,
        "params": {
            "member_id": _param("integer", True, "Member to rename"),
            "nick": _param("string", False, "New nickname (null/empty clears it)"),
        },
        "executor": _exec_member_nick,
    },
    "member_timeout": {
        "destructive": True,
        "params": {
            "member_id": _param("integer", True, "Member to time out"),
            "minutes": _param("integer", True, "Timeout duration in minutes"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_member_timeout,
    },
    "member_kick": {
        "destructive": True,
        "params": {
            "member_id": _param("integer", True, "Member to kick"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_member_kick,
    },
    "member_ban": {
        "destructive": True,
        "params": {
            "member_id": _param("integer", True, "Member to ban"),
            "reason": _param("string", False, "Audit log reason"),
            "delete_message_days": _param("integer", False, "Delete recent messages (0-7 days)"),
        },
        "executor": _exec_member_ban,
    },
    "message_send": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel to send to"),
            "content": _param("string", True, "Message content"),
        },
        "executor": _exec_message_send,
    },
    "message_pin": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to pin"),
        },
        "executor": _exec_message_pin,
    },
    "thread_create": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Parent channel"),
            "name": _param("string", True, "Thread name"),
            "message_id": _param("integer", False, "Start the thread from this message"),
        },
        "executor": _exec_thread_create,
    },
    "event_create": {
        "destructive": False,
        "params": {
            "name": _param("string", True, "Event name"),
            "start_time": _param("string", True, "ISO 8601 start time"),
            "end_time": _param("string", False, "ISO 8601 end time (required for external events)"),
            "description": _param("string", False, "Event description"),
            "channel_id": _param("integer", False, "Voice/stage channel to host in"),
            "location": _param("string", False, "External location (required if no channel_id)"),
        },
        "executor": _exec_event_create,
    },
}


def registry_listing() -> list[dict]:
    """JSON-able view of the registry for client introspection."""
    return [
        {"action": name, "destructive": spec["destructive"], "params": spec["params"]}
        for name, spec in ACTIONS.items()
    ]


async def execute_action(bot, guild, action: str, params: dict) -> dict:
    """Validate *params* against the spec for *action* and run its executor."""
    spec = ACTIONS[action]
    cleaned = validate_params(action, spec["params"], params)
    return await spec["executor"](bot, guild, cleaned)
