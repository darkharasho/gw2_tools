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

import asyncio
import datetime as dt
import string

import aiohttp
import discord

# Maximum timeout Discord allows: 28 days.
_MAX_TIMEOUT_MINUTES = 28 * 24 * 60

_CHANNEL_TYPES = ("text", "voice", "category", "forum")

# DM content limit (kept under Discord's 2000-char cap to leave headroom).
_MAX_DM_CONTENT = 1900

# Maximum recipients for a bulk DM run.
_MAX_DM_RECIPIENTS = 250

# Discord's hard cap on custom-emoji image size.
_MAX_EMOJI_BYTES = 256 * 1024

# Politeness pacing between sequential DM sends. discord.py already handles
# hard rate limits; this just keeps the pattern non-spammy.
_DM_PACING_SECONDS = 0.6


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
    elif expected == "array":
        if isinstance(value, list):
            return [_coerce(action, name, "integer", item) for item in value]
    elif expected == "tag_array":
        # A list whose items are tag names (string) or tag ids (integer); resolved
        # against the forum's available tags later. bool is an int subclass — reject.
        if isinstance(value, list) and all(
            isinstance(item, str) or (isinstance(item, int) and not isinstance(item, bool))
            for item in value
        ):
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


def _validate_dm_content(action: str, content: str) -> str:
    if not content.strip():
        raise ValueError(f"{action} content must be a non-empty string")
    if len(content) > _MAX_DM_CONTENT:
        raise ValueError(
            f"{action} content must be at most {_MAX_DM_CONTENT} characters"
        )
    return content


async def _exec_member_dm(bot, guild, p: dict) -> dict:
    content = _validate_dm_content("member_dm", p["content"])
    member = await _resolve_member(guild, p["member_id"])
    try:
        await member.send(content)
    except discord.Forbidden:
        raise ValueError("member has DMs disabled or blocks the bot") from None
    return {"member_id": _sid(member.id), "sent": True}


async def _exec_members_dm(bot, guild, p: dict) -> dict:
    content = _validate_dm_content("members_dm", p["content"])
    member_ids = p["member_ids"]
    if not 1 <= len(member_ids) <= _MAX_DM_RECIPIENTS:
        raise ValueError(
            f"member_ids must contain between 1 and {_MAX_DM_RECIPIENTS} ids"
        )
    # Resolve every recipient up front so an unknown id fails the whole run
    # before any DM is sent.
    members = [await _resolve_member(guild, member_id) for member_id in member_ids]
    sent: list[str] = []
    failed: list[dict] = []
    for index, member in enumerate(members):
        if index:
            await asyncio.sleep(_DM_PACING_SECONDS)
        try:
            await member.send(content)
        except discord.Forbidden:
            failed.append({
                "member_id": _sid(member.id),
                "reason": "member has DMs disabled or blocks the bot",
            })
        except discord.HTTPException as exc:
            failed.append({"member_id": _sid(member.id), "reason": str(exc)})
        else:
            sent.append(_sid(member.id))
    return {"requested": len(member_ids), "sent": sent, "failed": failed}


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


def _resolve_forum_tags(parent, items: list):
    """Map tag names/ids to the forum's ForumTag objects, or raise with the list
    of valid tags. Tags are matched by id (int) or name (string, case-insensitive)."""
    available = list(getattr(parent, "available_tags", []) or [])
    by_id = {t.id: t for t in available}
    by_name = {t.name.lower(): t for t in available}
    resolved = []
    for item in items:
        tag = by_id.get(item) if isinstance(item, int) else by_name.get(str(item).lower())
        if tag is None:
            names = ", ".join(t.name for t in available) or "(this forum has no tags configured)"
            raise ValueError(f"forum tag {item!r} not found. Available tags: {names}")
        resolved.append(tag)
    return resolved


async def _exec_thread_update(bot, guild, p: dict) -> dict:
    thread = resolve_channel(guild, p["thread_id"])
    if "thread" not in str(getattr(thread, "type", "")):
        raise ValueError(f"channel {p['thread_id']} is not a thread / forum post")
    kwargs: dict = {}
    if "name" in p:
        kwargs["name"] = p["name"]
    if "pinned" in p:
        kwargs["pinned"] = p["pinned"]
    if "archived" in p:
        kwargs["archived"] = p["archived"]
    if "locked" in p:
        kwargs["locked"] = p["locked"]
    if "slowmode_seconds" in p:
        kwargs["slowmode_delay"] = p["slowmode_seconds"]
    if "applied_tags" in p:
        parent = getattr(thread, "parent", None)
        if parent is None or str(getattr(parent, "type", "")) != "forum":
            raise ValueError(
                "applied_tags can only be set on a forum post (a thread inside a forum channel)"
            )
        kwargs["applied_tags"] = _resolve_forum_tags(parent, p["applied_tags"])
    if not kwargs:
        raise ValueError("thread_update requires at least one field to change")
    await thread.edit(reason=audit_reason(None, "thread_update"), **kwargs)
    result = {"id": _sid(thread.id), "name": kwargs.get("name", thread.name)}
    for key in ("pinned", "archived", "locked"):
        if key in kwargs:
            result[key] = kwargs[key]
    if "applied_tags" in kwargs:
        result["applied_tags"] = [t.name for t in kwargs["applied_tags"]]
    return result


# -- messages & reactions ----------------------------------------------------

async def _exec_message_unpin(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    await message.unpin(reason=audit_reason(None, "message_unpin"))
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "pinned": False}


async def _exec_message_delete(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    await message.delete()
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "deleted": True}


async def _exec_message_edit(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    try:
        await message.edit(content=p["content"])
    except discord.Forbidden:
        raise ValueError("can only edit the bot's own messages") from None
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "edited": True}


def _reaction_emoji(guild, emoji: str):
    """A reaction emoji string: unicode, '<:name:id>'/'name:id', or a bare custom
    emoji id (resolved to the guild's emoji)."""
    s = str(emoji).strip()
    if s.isdigit():
        return _resolve_emoji(guild, int(s))
    return s


async def _exec_reaction_add(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    await message.add_reaction(_reaction_emoji(guild, p["emoji"]))
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "emoji": p["emoji"]}


async def _exec_reaction_remove(bot, guild, p: dict) -> dict:
    channel = resolve_channel(guild, p["channel_id"])
    message = await _resolve_message(channel, p["message_id"])
    await message.remove_reaction(_reaction_emoji(guild, p["emoji"]), guild.me)
    return {"id": _sid(message.id), "channel_id": _sid(channel.id), "emoji": p["emoji"]}


# -- forum tag management ----------------------------------------------------

def _resolve_forum(guild, channel_id: int):
    channel = resolve_channel(guild, channel_id)
    if str(getattr(channel, "type", "")) != "forum":
        raise ValueError(f"channel {channel_id} is not a forum channel")
    return channel


def _resolve_forum_tag(forum, tag):
    available = list(getattr(forum, "available_tags", []) or [])
    s = str(tag)
    by_name = {t.name.lower(): t for t in available}
    if s.lower() in by_name:
        return by_name[s.lower()]
    if s.isdigit():
        for t in available:
            if t.id == int(s):
                return t
    names = ", ".join(t.name for t in available) or "(this forum has no tags configured)"
    raise ValueError(f"forum tag {tag!r} not found. Available tags: {names}")


def _forum_tag_emoji(guild, emoji: str):
    s = str(emoji).strip()
    return _resolve_emoji(guild, int(s)) if s.isdigit() else s


async def _exec_forum_tag_create(bot, guild, p: dict) -> dict:
    forum = _resolve_forum(guild, p["channel_id"])
    kwargs: dict = {"name": p["name"], "reason": audit_reason(None, "forum_tag_create")}
    if "emoji" in p:
        kwargs["emoji"] = _forum_tag_emoji(guild, p["emoji"])
    if "moderated" in p:
        kwargs["moderated"] = p["moderated"]
    tag = await forum.create_tag(**kwargs)
    return {"id": _sid(tag.id), "name": tag.name, "channel_id": _sid(forum.id)}


async def _exec_forum_tag_edit(bot, guild, p: dict) -> dict:
    forum = _resolve_forum(guild, p["channel_id"])
    tag = _resolve_forum_tag(forum, p["tag"])
    kwargs: dict = {}
    if "name" in p:
        kwargs["name"] = p["name"]
    if "emoji" in p:
        kwargs["emoji"] = _forum_tag_emoji(guild, p["emoji"])
    if "moderated" in p:
        kwargs["moderated"] = p["moderated"]
    if not kwargs:
        raise ValueError("forum_tag_edit requires at least one field to change")
    await tag.edit(**kwargs)
    return {"id": _sid(tag.id), "name": kwargs.get("name", tag.name), "channel_id": _sid(forum.id)}


async def _exec_forum_tag_delete(bot, guild, p: dict) -> dict:
    forum = _resolve_forum(guild, p["channel_id"])
    tag = _resolve_forum_tag(forum, p["tag"])
    await tag.delete()
    return {"id": _sid(tag.id), "name": tag.name, "deleted": True}


# -- guild emoji management --------------------------------------------------

def _resolve_emoji(guild, emoji_id: int):
    emoji = discord.utils.get(guild.emojis, id=emoji_id)
    if emoji is None:
        raise ValueError(f"emoji {emoji_id} not found in this server")
    return emoji


async def _fetch_image_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise ValueError(f"could not fetch image_url (HTTP {resp.status})")
            return await resp.read()


async def _exec_emoji_create(bot, guild, p: dict) -> dict:
    data = await _fetch_image_bytes(p["image_url"])
    if len(data) > _MAX_EMOJI_BYTES:
        raise ValueError(f"emoji image must be at most {_MAX_EMOJI_BYTES // 1024} KB")
    emoji = await guild.create_custom_emoji(
        name=p["name"], image=data, reason=audit_reason(None, "emoji_create")
    )
    return {"id": _sid(emoji.id), "name": emoji.name}


async def _exec_emoji_edit(bot, guild, p: dict) -> dict:
    emoji = _resolve_emoji(guild, p["emoji_id"])
    await emoji.edit(name=p["name"], reason=audit_reason(None, "emoji_edit"))
    return {"id": _sid(emoji.id), "name": p["name"]}


async def _exec_emoji_delete(bot, guild, p: dict) -> dict:
    emoji = _resolve_emoji(guild, p["emoji_id"])
    await emoji.delete(reason=audit_reason(p.get("reason"), "emoji_delete"))
    return {"id": _sid(emoji.id), "name": emoji.name, "deleted": True}


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
    "member_dm": {
        "destructive": True,
        "params": {
            "member_id": _param("integer", True, "Member to direct-message"),
            "content": _param("string", True, "Message content (max 1900 characters)"),
        },
        "executor": _exec_member_dm,
    },
    "members_dm": {
        "destructive": True,
        "params": {
            "member_ids": _param("array", True, "Members to direct-message (1-250 ids)"),
            "content": _param("string", True, "Message content (max 1900 characters)"),
        },
        "executor": _exec_members_dm,
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
    "thread_update": {
        "destructive": False,
        "params": {
            "thread_id": _param("integer", True, "Thread or forum post to update"),
            "name": _param("string", False, "New thread/post title"),
            "pinned": _param("boolean", False, "Pin (true) or unpin (false) — pins a forum post to the top of its forum"),
            "applied_tags": _param("tag_array", False, "Forum tag names or ids to set on a forum post, e.g. [\"Active\"] (replaces the post's current tags)"),
            "archived": _param("boolean", False, "Archive (true) or reopen (false) the thread"),
            "locked": _param("boolean", False, "Lock (true) or unlock (false) the thread"),
            "slowmode_seconds": _param("integer", False, "Slowmode delay in seconds"),
        },
        "executor": _exec_thread_update,
    },
    "message_unpin": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to unpin"),
        },
        "executor": _exec_message_unpin,
    },
    "message_delete": {
        "destructive": True,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to delete"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_message_delete,
    },
    "message_edit": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to edit (must be the bot's own message)"),
            "content": _param("string", True, "New message content"),
        },
        "executor": _exec_message_edit,
    },
    "reaction_add": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to react to"),
            "emoji": _param("string", True, "Unicode emoji, '<:name:id>', or a custom emoji id"),
        },
        "executor": _exec_reaction_add,
    },
    "reaction_remove": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Channel containing the message"),
            "message_id": _param("integer", True, "Message to remove the bot's reaction from"),
            "emoji": _param("string", True, "Unicode emoji, '<:name:id>', or a custom emoji id"),
        },
        "executor": _exec_reaction_remove,
    },
    "forum_tag_create": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Forum channel to add the tag to"),
            "name": _param("string", True, "Tag name"),
            "emoji": _param("string", False, "Unicode emoji or custom emoji id for the tag"),
            "moderated": _param("boolean", False, "Only moderators can apply this tag"),
        },
        "executor": _exec_forum_tag_create,
    },
    "forum_tag_edit": {
        "destructive": False,
        "params": {
            "channel_id": _param("integer", True, "Forum channel containing the tag"),
            "tag": _param("string", True, "Existing tag name or id to edit"),
            "name": _param("string", False, "New tag name"),
            "emoji": _param("string", False, "Unicode emoji or custom emoji id"),
            "moderated": _param("boolean", False, "Only moderators can apply this tag"),
        },
        "executor": _exec_forum_tag_edit,
    },
    "forum_tag_delete": {
        "destructive": True,
        "params": {
            "channel_id": _param("integer", True, "Forum channel containing the tag"),
            "tag": _param("string", True, "Tag name or id to delete"),
        },
        "executor": _exec_forum_tag_delete,
    },
    "emoji_create": {
        "destructive": False,
        "params": {
            "name": _param("string", True, "Emoji name (letters, numbers, underscores)"),
            "image_url": _param("string", True, "URL of the image (png/gif, <= 256 KB)"),
        },
        "executor": _exec_emoji_create,
    },
    "emoji_edit": {
        "destructive": False,
        "params": {
            "emoji_id": _param("integer", True, "Custom emoji id to rename"),
            "name": _param("string", True, "New emoji name"),
        },
        "executor": _exec_emoji_edit,
    },
    "emoji_delete": {
        "destructive": True,
        "params": {
            "emoji_id": _param("integer", True, "Custom emoji id to delete"),
            "reason": _param("string", False, "Audit log reason"),
        },
        "executor": _exec_emoji_delete,
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
