"""Localhost HTTP API for GW2 Officer (and other local clients).

Binds to 127.0.0.1 only. All requests require ``Authorization: Bearer <token>``.
The token comes from AXITOOLS_API_TOKEN, or is generated once and persisted
under the storage root as ``api_token`` (mode 0600).
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import hashlib
import json
import logging
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
from aiohttp import web

from ..storage import (
    BuildRecord,
    CompPreset,
    CompSchedule,
    RssFeedConfig,
    StreamSubscription,
    normalise_guild_id,
    utcnow,
)
from . import discord_actions

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8642
DEFAULT_PUBLIC_URL = "http://127.0.0.1:8642"

APP_KEY_PREFIX = "axt1."

API_ACTOR_ID = 0

# Serializes API-originated writes only (not bot-internal writes).
_write_lock = asyncio.Lock()


def resolve_api_token(root: Path) -> str:
    env = os.getenv("AXITOOLS_API_TOKEN")
    if env:
        return env.strip()
    token_path = root / "api_token"
    if token_path.exists():
        return token_path.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(32)
    root.mkdir(parents=True, exist_ok=True)
    # Deliberate: the token lives in the data root (unlike the DB key) so users
    # can find it easily to paste into GW2 Officer. It only grants localhost
    # API access, not at-rest decryption — override with AXITOOLS_API_TOKEN
    # to keep it out of data backups.
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def resolve_public_url() -> str:
    """Return the bot's public API base URL embedded into AxiVale keys."""
    env = os.getenv("AXITOOLS_PUBLIC_URL", "").strip()
    return env or DEFAULT_PUBLIC_URL


def generate_app_key(base_url: str | None = None) -> str:
    """Build a per-guild AxiVale key: ``axt1.<base64url(base_url)>.<secret>``."""
    if base_url is None:
        base_url = resolve_public_url()
    encoded_url = base64.urlsafe_b64encode(base_url.encode("utf-8")).rstrip(b"=").decode("ascii")
    secret = secrets.token_urlsafe(32)
    return f"{APP_KEY_PREFIX}{encoded_url}.{secret}"


def hash_app_key(key: str) -> str:
    """Return the sha256 hex digest of the full key string (what we persist)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    expected = f"Bearer {request.app['api_token']}"
    supplied = request.headers.get("Authorization", "")
    if secrets.compare_digest(supplied.encode(), expected.encode()):
        # Global token: full access.
        return await handler(request)

    bearer = supplied[len("Bearer "):] if supplied.startswith("Bearer ") else ""
    if not bearer.startswith(APP_KEY_PREFIX):
        return web.json_response({"error": "unauthorized"}, status=401)

    # Per-guild AxiVale key: look up by hash. Exact-match lookup of a sha256
    # digest is not secret-dependent timing, so no constant-time concern here.
    bot = request.app["bot"]
    token_hash = hash_app_key(bearer)
    guild_id = await asyncio.to_thread(bot.storage.get_app_key_guild, token_hash)
    if guild_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    request["scoped_guild_id"] = guild_id
    path_guild_id = request.match_info.get("guild_id")
    if path_guild_id is not None and int(path_guild_id) != guild_id:
        return web.json_response({"error": "key is scoped to another server"}, status=403)
    return await handler(request)


_SNOWFLAKE_KEYS = frozenset({
    "channel_id",
    "thread_id",
    "message_id",
    "created_by",
    "updated_by",
    "ping_role_id",
    "emoji_id",
    "build_channel_id",
    "moderator_role_ids",
    "update_notes_channel_id",
    "arcdps_channel_id",
    "audit_channel_id",
    "audit_channel_blacklist",
    "discord_channel_id",
    "role_id",
    "actor_id",
    "target_id",
    "member_id",
    "preferred_role_id",
})


def _with_string_ids(value, key=None, force=False):
    """Recursively stringify snowflake-keyed ints in a JSON-able payload.

    ``signups`` maps class names to lists of member ids, so everything under
    it is forced. Non-snowflake numbers (positions, counts, days) pass through.
    """
    if isinstance(value, dict):
        return {
            k: _with_string_ids(v, k, force or k == "signups")
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_with_string_ids(item, key, force) for item in value]
    if isinstance(value, int) and not isinstance(value, bool) and (force or key in _SNOWFLAKE_KEYS):
        return str(value)
    return value


def _build_to_json(record: BuildRecord) -> dict:
    return _with_string_ids(asdict(record))


def _sid(value) -> str | None:
    """Serialize a Discord snowflake id as a string (None passes through).

    Snowflakes are 64-bit ints that exceed JavaScript's Number.MAX_SAFE_INTEGER;
    JSON numbers silently lose precision in JS clients, so — like Discord's own
    REST API — every id crosses the API boundary as a string.
    """
    return None if value is None else str(value)


def _guild_ctx(request: web.Request):
    """Return (bot, guild_id) for guild-scoped handlers."""
    return request.app["bot"], int(request.match_info["guild_id"])


def _validate_build_fields(body: dict, required: bool = False) -> web.Response | None:
    """Validate build fields shared by create and update.

    When *required* is True, missing fields are also rejected.
    Returns a 400 Response on failure, or None on success.
    """
    _required_fields = ("name", "profession", "chat_code")
    if required:
        for field in _required_fields:
            if not body.get(field):
                return web.json_response({"error": f"missing field: {field}"}, status=400)
    # Reject empty-string or null values when the key is present in the body.
    for field in _required_fields:
        if field in body and not body[field]:
            return web.json_response({"error": f"invalid value for field: {field}"}, status=400)
    return None


async def _handle_guilds(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    scoped_guild_id = request.get("scoped_guild_id")
    guilds = bot.guilds
    if scoped_guild_id is not None:
        guilds = [g for g in guilds if g.id == scoped_guild_id]
    return web.json_response([{"id": _sid(g.id), "name": g.name} for g in guilds])


async def _parse_json_body(request: web.Request) -> dict | None:
    """Return parsed JSON body, or None if the body is missing / malformed."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


async def _handle_builds_list(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    builds = await asyncio.to_thread(bot.storage.get_builds, gid)
    return web.json_response([_build_to_json(b) for b in builds])


async def _handle_builds_create(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    err = _validate_build_fields(body, required=True)
    if err is not None:
        return err
    now = utcnow()
    record = BuildRecord(
        build_id=secrets.token_hex(8),
        name=body["name"],
        profession=body["profession"],
        specialization=body.get("specialization"),
        url=body.get("url"),
        chat_code=body["chat_code"],
        description=body.get("description"),
        created_by=API_ACTOR_ID,
        created_at=now,
        updated_by=API_ACTOR_ID,
        updated_at=now,
    )

    async with _write_lock:
        await asyncio.to_thread(bot.storage.upsert_build, gid, record)

    return web.json_response(_build_to_json(record), status=201)


async def _handle_builds_update(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    build_id = request.match_info["build_id"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    err = _validate_build_fields(body, required=False)
    if err is not None:
        return err

    result: list[BuildRecord | None] = [None]

    def _find_mutate_save():
        existing = bot.storage.find_build(gid, build_id)
        if existing is None:
            return
        updated = BuildRecord(
            build_id=existing.build_id,
            name=body["name"] if "name" in body else existing.name,
            profession=body["profession"] if "profession" in body else existing.profession,
            specialization=body["specialization"] if "specialization" in body else existing.specialization,
            url=body["url"] if "url" in body else existing.url,
            chat_code=body["chat_code"] if "chat_code" in body else existing.chat_code,
            description=body["description"] if "description" in body else existing.description,
            created_by=existing.created_by,
            created_at=existing.created_at,
            updated_by=API_ACTOR_ID,
            updated_at=utcnow(),
            message_id=existing.message_id,
            channel_id=existing.channel_id,
            thread_id=existing.thread_id,
        )
        bot.storage.upsert_build(gid, updated)
        result[0] = updated

    async with _write_lock:
        await asyncio.to_thread(_find_mutate_save)

    if result[0] is None:
        return web.json_response({"error": "build not found"}, status=404)
    return web.json_response(_build_to_json(result[0]))


async def _handle_builds_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    build_id = request.match_info["build_id"]

    result: list[bool] = [False]

    def _find_delete():
        result[0] = bot.storage.delete_build(gid, build_id)

    async with _write_lock:
        await asyncio.to_thread(_find_delete)

    if not result[0]:
        return web.json_response({"error": "build not found"}, status=404)
    return web.Response(status=204)


async def _handle_comp_presets_list(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    presets = await asyncio.to_thread(bot.storage.get_comp_presets, gid)
    return web.json_response([_with_string_ids(p.to_dict()) for p in presets])


async def _handle_comp_presets_upsert(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    # Require a "config" dict in the body (even if empty) so bogus payloads are rejected.
    if "config" not in body or not isinstance(body.get("config"), dict):
        return web.json_response({"error": "missing or invalid field: config"}, status=400)
    # Path name wins over body name
    body = {**body, "name": name}
    try:
        preset = CompPreset.from_dict(body)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)

    result: list[CompPreset] = []

    def _upsert_preset():
        presets = bot.storage.get_comp_presets(gid)
        updated = [p for p in presets if p.name != name]
        updated.append(preset)
        bot.storage.save_comp_presets(gid, updated)
        result.append(preset)

    async with _write_lock:
        await asyncio.to_thread(_upsert_preset)

    return web.json_response(_with_string_ids(result[0].to_dict()))


async def _handle_comp_presets_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]

    result: list[bool] = [False]

    def _delete_preset():
        presets = bot.storage.get_comp_presets(gid)
        remaining = [p for p in presets if p.name != name]
        if len(remaining) < len(presets):
            bot.storage.save_comp_presets(gid, remaining)
            result[0] = True

    async with _write_lock:
        await asyncio.to_thread(_delete_preset)

    if not result[0]:
        return web.json_response({"error": "preset not found"}, status=404)
    return web.Response(status=204)


async def _handle_comp_schedules_list(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    return web.json_response([_with_string_ids(s.to_dict()) for s in config.comp_schedules])


async def _handle_comp_schedules_upsert(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    schedule_id = request.match_info["schedule_id"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    # Path schedule_id wins over body
    body = {**body, "schedule_id": schedule_id}
    try:
        schedule = CompSchedule.from_dict(body)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)

    result: list[CompSchedule] = []

    def _upsert_schedule():
        config = bot.storage.get_config(gid)
        updated = [s for s in config.comp_schedules if s.schedule_id != schedule_id]
        updated.append(schedule)
        config.comp_schedules = updated
        bot.storage.save_config(gid, config)
        result.append(schedule)

    async with _write_lock:
        await asyncio.to_thread(_upsert_schedule)

    return web.json_response(_with_string_ids(result[0].to_dict()))


async def _handle_comp_schedules_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    schedule_id = request.match_info["schedule_id"]
    removed: list[bool] = []

    def _delete_schedule():
        config = bot.storage.get_config(gid)
        before = len(config.comp_schedules)
        config.comp_schedules = [
            s for s in config.comp_schedules if s.schedule_id != schedule_id
        ]
        if len(config.comp_schedules) != before:
            bot.storage.save_config(gid, config)
            removed.append(True)

    async with _write_lock:
        await asyncio.to_thread(_delete_schedule)

    if not removed:
        return web.json_response({"error": "schedule not found"}, status=404)
    return web.Response(status=204)


_CONFIG_WHITELIST = (
    "moderator_role_ids",
    "build_channel_id",
    "comp_active_preset",
    "update_notes_channel_id",
    "arcdps_channel_id",
    "audit_channel_id",
    "audit_channel_blacklist",
)


def _comp_config_to_json(config) -> dict:
    comp = config.comp
    return {
        "channel_id": _sid(comp.channel_id),
        "ping_role_id": _sid(comp.ping_role_id),
        "post_days": list(comp.post_days),
        "post_time": comp.post_time,
        "timezone": comp.timezone,
        "overview": comp.overview,
        "active_preset": config.comp_active_preset,
    }


async def _handle_comp_config_get(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    return web.json_response(_comp_config_to_json(config))


async def _handle_comp_config_patch(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    channel_update = _UNSET = object()
    ping_update = _UNSET
    if "channel_id" in body:
        if body["channel_id"] is None:
            channel_update = None
        else:
            cid, cerr = _channel_in_guild(guild, body["channel_id"])
            if cerr is not None:
                return cerr
            channel_update = cid
    if "ping_role_id" in body:
        if body["ping_role_id"] is None:
            ping_update = None
        else:
            rid, rerr = _role_in_guild(guild, body["ping_role_id"])
            if rerr is not None:
                return rerr
            ping_update = rid

    post_days = body.get("post_days")
    if post_days is not None:
        if not isinstance(post_days, list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in post_days
        ):
            return web.json_response(
                {"error": "post_days must be a list of ints 0-6 (Monday=0)"}, status=400
            )

    post_time = body.get("post_time")
    if post_time is not None and post_time != "":
        import re as _re
        if not _re.fullmatch(r"[0-2]\d:[0-5]\d", str(post_time)):
            return web.json_response({"error": "post_time must be HH:MM"}, status=400)

    result: list = []

    def _merge_save():
        config = bot.storage.get_config(gid)
        if channel_update is not _UNSET:
            config.comp.channel_id = channel_update
        if ping_update is not _UNSET:
            config.comp.ping_role_id = ping_update
        if post_days is not None:
            config.comp.post_days = list(post_days)
        if post_time is not None:
            config.comp.post_time = post_time or None
        if "timezone" in body and body["timezone"]:
            config.comp.timezone = str(body["timezone"])
        if "overview" in body:
            config.comp.overview = str(body["overview"] or "")
        if "active_preset" in body:
            config.comp_active_preset = body["active_preset"] or None
        bot.storage.save_config(gid, config)
        result.append(config)

    async with _write_lock:
        await asyncio.to_thread(_merge_save)

    return web.json_response(_comp_config_to_json(result[0]))


async def _handle_config_get(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    return web.json_response(_config_to_json(config))


def _iso(value) -> str | None:
    """Render datetimes as ISO 8601; pass strings (and None) through."""
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _permissions_value(role) -> str:
    # Permissions are a 64-bit bitfield; Discord serializes them as a string.
    perms = getattr(role, "permissions", 0)
    return str(int(getattr(perms, "value", perms) or 0))


def _resolve_discord_guild(request: web.Request):
    """Return (guild, error_response) for /discord handlers."""
    bot, gid = _guild_ctx(request)
    guild = bot.get_guild(gid)
    if guild is None:
        return None, web.json_response({"error": "guild not found"}, status=404)
    return guild, None


def _channel_to_json(channel) -> dict:
    return {
        "id": _sid(channel.id),
        "name": channel.name,
        "type": str(channel.type),
        "category_id": _sid(getattr(channel, "category_id", None)),
        "topic": getattr(channel, "topic", None),
        "position": getattr(channel, "position", 0),
    }


async def _handle_discord_snapshot(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    categories = list(guild.categories)
    category_ids = {c.id for c in categories}
    body = {
        "guild": {
            "id": _sid(guild.id),
            "name": guild.name,
            "member_count": guild.member_count,
        },
        "categories": [
            {"id": _sid(c.id), "name": c.name, "position": getattr(c, "position", 0)}
            for c in categories
        ],
        "channels": [
            _channel_to_json(ch) for ch in guild.channels if ch.id not in category_ids
        ],
        "roles": [
            {
                "id": _sid(r.id),
                "name": r.name,
                "color": str(getattr(r, "color", "") or "#000000"),
                "position": getattr(r, "position", 0),
                "hoist": getattr(r, "hoist", False),
                "mentionable": getattr(r, "mentionable", False),
                "permissions": _permissions_value(r),
                "member_count": len(getattr(r, "members", ())),
            }
            for r in guild.roles
        ],
        "threads": [
            {
                "id": _sid(t.id),
                "name": t.name,
                "parent_id": _sid(getattr(t, "parent_id", None)),
                "archived": getattr(t, "archived", False),
            }
            for t in getattr(guild, "threads", ())
        ],
        "scheduled_events": [
            {
                "id": _sid(e.id),
                "name": e.name,
                "description": getattr(e, "description", None),
                "start_time": _iso(getattr(e, "start_time", None)),
                "end_time": _iso(getattr(e, "end_time", None)),
                "channel_id": _sid(getattr(e, "channel_id", None)),
                "location": getattr(e, "location", None),
            }
            for e in getattr(guild, "scheduled_events", ())
        ],
    }
    include = request.query.get("include", "")
    if "members" in include.split(","):
        members = list(guild.members)
        body["members_total"] = len(members)
        body["members"] = [
            {
                "id": _sid(m.id),
                "name": m.name,
                "display_name": getattr(m, "display_name", m.name),
                "role_ids": [_sid(r.id) for r in getattr(m, "roles", ())],
                "joined_at": _iso(getattr(m, "joined_at", None)),
            }
            for m in members[:1000]
        ]
    return web.json_response(body)


async def _handle_discord_messages(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    raw_channel_id = request.query.get("channel_id", "")
    if not raw_channel_id.isdigit():
        return web.json_response(
            {"error": "channel_id query parameter is required"}, status=400
        )
    raw_limit = request.query.get("limit", "25")
    if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 100:
        return web.json_response(
            {"error": "limit must be an integer between 1 and 100"}, status=400
        )
    try:
        channel = discord_actions.resolve_channel(guild, int(raw_channel_id))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    messages = [
        {
            "id": _sid(m.id),
            "author_id": _sid(m.author.id),
            "author_name": m.author.name,
            "content": m.content,
            "created_at": _iso(m.created_at),
            "pinned": getattr(m, "pinned", False),
        }
        async for m in channel.history(limit=int(raw_limit))
    ]
    return web.json_response(messages)


async def _handle_discord_actions_list(request: web.Request) -> web.Response:
    return web.json_response(discord_actions.registry_listing())


async def _handle_discord_actions_post(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    action = body.get("action")
    if action not in discord_actions.ACTIONS:
        return web.json_response(
            {
                "error": f"unknown action: {action}",
                "valid_actions": sorted(discord_actions.ACTIONS),
            },
            status=400,
        )
    params = body.get("params") or {}
    bot = request.app["bot"]
    try:
        result = await discord_actions.execute_action(bot, guild, action, params)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except discord.Forbidden as exc:
        return web.json_response(
            {"error": f"the bot lacks permission: {exc.text or action}"}, status=403
        )
    return web.json_response({"ok": True, "result": result})


# ---------------------------------------------------------------------------
# Shared validation helpers for the new domain routes
# ---------------------------------------------------------------------------

def _parse_snowflake(value) -> int | None:
    """Parse a snowflake id supplied as an int or a digit string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _channel_in_guild(guild, raw) -> tuple[int | None, web.Response | None]:
    """Validate that *raw* names a channel belonging to *guild*."""
    channel_id = _parse_snowflake(raw)
    if channel_id is None:
        return None, web.json_response(
            {"error": "channel_id must be a Discord channel id"}, status=400
        )
    if guild.get_channel(channel_id) is None:
        return None, web.json_response(
            {"error": f"channel {channel_id} does not belong to this server"}, status=400
        )
    return channel_id, None


def _role_in_guild(guild, raw) -> tuple[int | None, web.Response | None]:
    """Validate that *raw* names a role belonging to *guild*."""
    role_id = _parse_snowflake(raw)
    if role_id is None:
        return None, web.json_response(
            {"error": "role_id must be a Discord role id"}, status=400
        )
    if guild.get_role(role_id) is None:
        return None, web.json_response(
            {"error": f"role {role_id} does not belong to this server"}, status=400
        )
    return role_id, None


def _parse_limit(request: web.Request, *, default: int, maximum: int):
    raw = request.query.get("limit", "")
    if not raw:
        return default, None
    if not raw.isdigit() or not 1 <= int(raw) <= maximum:
        return None, web.json_response(
            {"error": f"limit must be an integer between 1 and {maximum}"}, status=400
        )
    return int(raw), None


# ---------------------------------------------------------------------------
# Audit (read-only)
# ---------------------------------------------------------------------------

async def _handle_audit_discord(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    limit, err = _parse_limit(request, default=50, maximum=200)
    if err is not None:
        return err
    event_type = request.query.get("event_type") or None
    actor = request.query.get("actor") or None
    target = request.query.get("target") or None

    def _query():
        store = bot.storage.get_audit_store(gid)
        return store.query_discord_events_filtered(
            event_type=event_type, actor=actor, target=target, limit=limit
        )

    rows = await asyncio.to_thread(_query)
    return web.json_response([
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "actor_id": _sid(row["actor_id"]),
            "actor_name": row["actor_name"],
            "target_id": _sid(row["target_id"]),
            "target_name": row["target_name"],
            "details": row["details"],
        }
        for row in rows
    ])


async def _handle_audit_gw2(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    limit, err = _parse_limit(request, default=50, maximum=200)
    if err is not None:
        return err
    event_type = request.query.get("event_type") or None
    user = request.query.get("user") or None
    raw_since = request.query.get("since_log_id", "")
    since_log_id = None
    if raw_since:
        if not raw_since.isdigit():
            return web.json_response(
                {"error": "since_log_id must be an integer"}, status=400
            )
        since_log_id = int(raw_since)

    def _query():
        store = bot.storage.get_audit_store(gid)
        return store.query_gw2_events_filtered(
            event_type=event_type, user=user, limit=limit, since_log_id=since_log_id
        )

    rows = await asyncio.to_thread(_query)
    return web.json_response([
        {
            "id": row["id"],
            "log_id": row["log_id"],
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "user": row["user"],
            "details": row["details"],
        }
        for row in rows
    ])


# ---------------------------------------------------------------------------
# RSS feeds
# ---------------------------------------------------------------------------

def _rss_feed_to_json(feed: RssFeedConfig) -> dict:
    return {
        "name": feed.name,
        "url": feed.url,
        "channel_id": _sid(feed.channel_id),
        "last_entry_published_at": feed.last_entry_published_at,
    }


async def _handle_rss_list(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    feeds = await asyncio.to_thread(bot.storage.get_rss_feeds, gid)
    return web.json_response([_rss_feed_to_json(feed) for feed in feeds])


async def _handle_rss_upsert(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        return web.json_response({"error": "missing field: url"}, status=400)
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return web.json_response({"error": "url must be an http(s) URL"}, status=400)

    channel_id, channel_err = _channel_in_guild(guild, body.get("channel_id"))
    if channel_err is not None:
        return channel_err

    result: list[RssFeedConfig] = []

    def _upsert():
        existing = bot.storage.find_rss_feed(gid, name)
        feed = RssFeedConfig(
            name=name,
            url=url,
            channel_id=channel_id,
            last_entry_id=existing.last_entry_id if existing else None,
            last_entry_published_at=existing.last_entry_published_at if existing else None,
        )
        bot.storage.upsert_rss_feed(gid, feed)
        result.append(feed)

    async with _write_lock:
        await asyncio.to_thread(_upsert)

    return web.json_response(_rss_feed_to_json(result[0]))


async def _handle_rss_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]

    result: list[bool] = [False]

    def _delete():
        result[0] = bot.storage.delete_rss_feed(gid, name)

    async with _write_lock:
        await asyncio.to_thread(_delete)

    if not result[0]:
        return web.json_response({"error": "feed not found"}, status=404)
    return web.Response(status=204)


# ---------------------------------------------------------------------------
# Stream subscriptions
# ---------------------------------------------------------------------------

def _stream_to_json(sub: StreamSubscription) -> dict:
    return {
        "name": sub.name,
        "platform": sub.platform,
        "channel_id": sub.channel_id,
        "channel_display_name": sub.channel_display_name,
        "discord_channel_id": _sid(sub.discord_channel_id),
        "ping_role_id": _sid(sub.ping_role_id),
        "is_live": sub.is_live,
        "last_live_at": sub.last_live_at,
    }


async def _handle_streams_list(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    subs = await asyncio.to_thread(bot.storage.get_stream_subscriptions, gid)
    return web.json_response([_stream_to_json(sub) for sub in subs])


async def _resolve_stream_channel(
    platform: str, channel_ref: str
) -> tuple[tuple[str, str, dict] | None, web.Response | None]:
    """Resolve ``channel_ref`` via the same helpers ``/stream add`` uses.

    Returns ((channel_id, display_name, prime_state), None) on success, or
    (None, error_response). ``prime_state`` carries initial live/vod state.
    """
    # Imported lazily: cogs.streaming imports ..bot which imports this module.
    from ..cogs import streaming

    if platform == "twitch":
        if not streaming.TWITCH_CLIENT_ID or not streaming.TWITCH_CLIENT_SECRET:
            return None, web.json_response(
                {"error": "Twitch credentials are not configured on this bot"},
                status=400,
            )
        tokens = streaming._TwitchTokenManager(
            streaming.TWITCH_CLIENT_ID, streaming.TWITCH_CLIENT_SECRET
        )
        async with aiohttp.ClientSession() as session:
            try:
                user = await streaming._fetch_twitch_user(
                    session, tokens, channel_ref.strip()
                )
                if not user:
                    return None, web.json_response(
                        {"error": f"could not find Twitch channel: {channel_ref}"},
                        status=400,
                    )
                login = user["login"]
                # Prime current live state so we don't immediately notify.
                stream = await streaming._fetch_twitch_stream(session, tokens, login)
            except aiohttp.ClientError as exc:
                return None, web.json_response(
                    {"error": f"Twitch lookup failed: {exc}"}, status=400
                )
        prime = {"is_live": stream is not None}
        if stream is not None:
            prime["last_live_at"] = utcnow()
        return (login, user["display_name"], prime), None

    # platform == "youtube"
    if not streaming.YOUTUBE_API_KEY:
        return None, web.json_response(
            {"error": "YouTube API key is not configured on this bot"}, status=400
        )
    async with aiohttp.ClientSession() as session:
        try:
            result = await streaming._resolve_youtube_channel(
                session, channel_ref.strip(), streaming.YOUTUBE_API_KEY
            )
            if not result:
                return None, web.json_response(
                    {"error": f"could not find YouTube channel: {channel_ref}"},
                    status=400,
                )
            channel_id, display_name = result
            # Prime the latest video id so old content is not announced.
            entries = await streaming._fetch_youtube_rss(session, channel_id)
        except aiohttp.ClientError as exc:
            return None, web.json_response(
                {"error": f"YouTube lookup failed: {exc}"}, status=400
            )
    prime = {"last_vod_id": entries[0].get("id") if entries else None}
    return (channel_id, display_name, prime), None


async def _handle_streams_upsert(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    platform = body.get("platform")
    if platform not in ("twitch", "youtube"):
        return web.json_response(
            {"error": "platform must be 'twitch' or 'youtube'"}, status=400
        )
    channel_ref = body.get("channel_ref")
    if not isinstance(channel_ref, str) or not channel_ref.strip():
        return web.json_response({"error": "missing field: channel_ref"}, status=400)

    raw_discord_channel = body.get("discord_channel_id")
    discord_channel_id = _parse_snowflake(raw_discord_channel)
    if discord_channel_id is None:
        return web.json_response(
            {"error": "discord_channel_id must be a Discord channel id"}, status=400
        )
    if guild.get_channel(discord_channel_id) is None:
        return web.json_response(
            {"error": f"channel {discord_channel_id} does not belong to this server"},
            status=400,
        )

    ping_role_id = None
    if body.get("ping_role_id") is not None:
        ping_role_id, role_err = _role_in_guild(guild, body.get("ping_role_id"))
        if role_err is not None:
            return role_err

    resolved, resolve_err = await _resolve_stream_channel(platform, channel_ref)
    if resolve_err is not None:
        return resolve_err
    channel_id, display_name, prime = resolved

    result: list[StreamSubscription] = []

    def _upsert():
        existing = bot.storage.find_stream_subscription(gid, name)
        if (
            existing is not None
            and existing.platform == platform
            and existing.channel_id == channel_id
        ):
            # Same stream: keep its live/vod tracking state.
            state = {
                "last_vod_id": existing.last_vod_id,
                "is_live": existing.is_live,
                "last_live_at": existing.last_live_at,
            }
        else:
            state = {
                "last_vod_id": prime.get("last_vod_id"),
                "is_live": prime.get("is_live", False),
                "last_live_at": prime.get("last_live_at"),
            }
        sub = StreamSubscription(
            name=name,
            platform=platform,
            channel_id=channel_id,
            channel_display_name=display_name,
            discord_channel_id=discord_channel_id,
            ping_role_id=ping_role_id,
            **state,
        )
        bot.storage.upsert_stream_subscription(gid, sub)
        result.append(sub)

    async with _write_lock:
        await asyncio.to_thread(_upsert)

    return web.json_response(_stream_to_json(result[0]))


async def _handle_streams_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    name = request.match_info["name"]

    result: list[bool] = [False]

    def _delete():
        result[0] = bot.storage.delete_stream_subscription(gid, name)

    async with _write_lock:
        await asyncio.to_thread(_delete)

    if not result[0]:
        return web.json_response({"error": "subscription not found"}, status=404)
    return web.Response(status=204)


# ---------------------------------------------------------------------------
# WvW alliance settings
# ---------------------------------------------------------------------------

_DAY_NAME_TO_INDEX = {name.lower(): index for index, name in enumerate(calendar.day_name)}


def _parse_day(value) -> int | None:
    """Accept a weekday index 0-6 (Monday=0) or a day name; None on failure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned.isdigit():
            candidate = int(cleaned)
            return candidate if 0 <= candidate <= 6 else None
        return _DAY_NAME_TO_INDEX.get(cleaned)
    return None


def _parse_hhmm(value) -> str | None:
    """Validate an HH:MM string the same way the alliance cog does."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return f"{hours:02d}:{minutes:02d}"


def _alliance_to_json(config) -> dict:
    return {
        "channel_id": _sid(config.alliance_channel_id),
        "guild_id": config.alliance_guild_id,
        "guild_name": config.alliance_guild_name,
        "server_id": config.alliance_server_id,
        "server_name": config.alliance_server_name,
        "prediction_day": config.alliance_prediction_day,
        "prediction_time": config.alliance_prediction_time,
        "current_day": config.alliance_current_day,
        "current_time": config.alliance_current_time,
        "relink_enabled": config.alliance_relink_enabled,
    }


async def _handle_alliance_get(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    return web.json_response(_alliance_to_json(config))


async def _handle_alliance_put(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    updates: dict = {}

    if "channel_id" in body:
        if body["channel_id"] is None:
            updates["alliance_channel_id"] = None
        else:
            channel_id, channel_err = _channel_in_guild(guild, body["channel_id"])
            if channel_err is not None:
                return channel_err
            updates["alliance_channel_id"] = channel_id

    if "guild_id" in body:
        raw = body["guild_id"]
        if raw is None:
            updates["alliance_guild_id"] = None
        else:
            cleaned = normalise_guild_id(raw) if isinstance(raw, str) else ""
            if not cleaned:
                return web.json_response(
                    {"error": "guild_id must be a Guild Wars 2 guild id"}, status=400
                )
            updates["alliance_guild_id"] = cleaned

    if "guild_name" in body:
        raw = body["guild_name"]
        if raw is not None and not isinstance(raw, str):
            return web.json_response({"error": "guild_name must be a string"}, status=400)
        updates["alliance_guild_name"] = raw.strip() if isinstance(raw, str) else None

    for field, attr in (
        ("prediction_day", "alliance_prediction_day"),
        ("current_day", "alliance_current_day"),
    ):
        if field in body:
            if body[field] is None:
                updates[attr] = None
                continue
            day = _parse_day(body[field])
            if day is None:
                return web.json_response(
                    {"error": f"{field} must be a weekday index 0-6 or a day name"},
                    status=400,
                )
            updates[attr] = day

    for field, attr in (
        ("prediction_time", "alliance_prediction_time"),
        ("current_time", "alliance_current_time"),
    ):
        if field in body:
            if body[field] is None:
                updates[attr] = None
                continue
            hhmm = _parse_hhmm(body[field])
            if hhmm is None:
                return web.json_response(
                    {"error": f"{field} must be a time in HH:MM format"}, status=400
                )
            updates[attr] = hhmm

    if "relink_enabled" in body:
        if not isinstance(body["relink_enabled"], bool):
            return web.json_response(
                {"error": "relink_enabled must be a boolean"}, status=400
            )
        updates["alliance_relink_enabled"] = body["relink_enabled"]

    result: list = []

    def _merge_save():
        config = bot.storage.get_config(gid)
        for attr, value in updates.items():
            setattr(config, attr, value)
        bot.storage.save_config(gid, config)
        result.append(config)

    async with _write_lock:
        await asyncio.to_thread(_merge_save)

    return web.json_response(_alliance_to_json(result[0]))


# ---------------------------------------------------------------------------
# Guild role mappings
# ---------------------------------------------------------------------------

def _guild_roles_to_json(config) -> dict:
    return {
        "mappings": [
            {"gw2_guild_id": gw2_guild_id, "role_id": _sid(role_id)}
            for gw2_guild_id, role_id in config.guild_role_ids.items()
        ],
        "allowlist": [_sid(role_id) for role_id in config.preferred_guild_role_allowlist],
    }


async def _handle_guild_roles_get(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    return web.json_response(_guild_roles_to_json(config))


async def _handle_guild_roles_put(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    gw2_guild_id = normalise_guild_id(request.match_info["gw2_guild_id"])
    if not gw2_guild_id:
        return web.json_response(
            {"error": "gw2_guild_id must be a Guild Wars 2 guild id"}, status=400
        )
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    role_id, role_err = _role_in_guild(guild, body.get("role_id"))
    if role_err is not None:
        return role_err

    def _merge_save():
        config = bot.storage.get_config(gid)
        config.guild_role_ids[gw2_guild_id] = role_id
        bot.storage.save_config(gid, config)

    async with _write_lock:
        await asyncio.to_thread(_merge_save)

    return web.json_response({"gw2_guild_id": gw2_guild_id, "role_id": _sid(role_id)})


async def _handle_guild_roles_delete(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    gw2_guild_id = normalise_guild_id(request.match_info["gw2_guild_id"])

    result: list[bool] = [False]

    def _delete():
        config = bot.storage.get_config(gid)
        if gw2_guild_id in config.guild_role_ids:
            del config.guild_role_ids[gw2_guild_id]
            bot.storage.save_config(gid, config)
            result[0] = True

    async with _write_lock:
        await asyncio.to_thread(_delete)

    if not result[0]:
        return web.json_response({"error": "mapping not found"}, status=404)
    return web.Response(status=204)


async def _handle_guild_roles_allowlist_put(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    raw_ids = body.get("role_ids")
    if not isinstance(raw_ids, list):
        return web.json_response({"error": "role_ids must be a list"}, status=400)
    role_ids: list[int] = []
    for raw in raw_ids:
        role_id, role_err = _role_in_guild(guild, raw)
        if role_err is not None:
            return role_err
        if role_id not in role_ids:
            role_ids.append(role_id)

    def _merge_save():
        config = bot.storage.get_config(gid)
        config.preferred_guild_role_allowlist = role_ids
        bot.storage.save_config(gid, config)

    async with _write_lock:
        await asyncio.to_thread(_merge_save)

    return web.json_response({"allowlist": [_sid(role_id) for role_id in role_ids]})


# ---------------------------------------------------------------------------
# Config PATCH
# ---------------------------------------------------------------------------

_PATCHABLE_CHANNEL_FIELDS = (
    "build_channel_id",
    "update_notes_channel_id",
    "arcdps_channel_id",
    "audit_channel_id",
)


def _config_to_json(config) -> dict:
    body = {key: getattr(config, key) for key in _CONFIG_WHITELIST}
    body["comp_schedule_count"] = len(config.comp_schedules)
    return _with_string_ids(body)


async def _handle_config_patch(request: web.Request) -> web.Response:
    guild, err = _resolve_discord_guild(request)
    if err is not None:
        return err
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    updates: dict = {}
    for field in _PATCHABLE_CHANNEL_FIELDS:
        if field not in body:
            continue
        if body[field] is None:
            updates[field] = None
            continue
        channel_id, channel_err = _channel_in_guild(guild, body[field])
        if channel_err is not None:
            return channel_err
        updates[field] = channel_id

    if "moderator_role_ids" in body:
        raw_ids = body["moderator_role_ids"]
        if not isinstance(raw_ids, list):
            return web.json_response(
                {"error": "moderator_role_ids must be a list"}, status=400
            )
        role_ids: list[int] = []
        for raw in raw_ids:
            role_id, role_err = _role_in_guild(guild, raw)
            if role_err is not None:
                return role_err
            if role_id not in role_ids:
                role_ids.append(role_id)
        updates["moderator_role_ids"] = role_ids

    result: list = []

    def _merge_save():
        config = bot.storage.get_config(gid)
        for attr, value in updates.items():
            setattr(config, attr, value)
        bot.storage.save_config(gid, config)
        result.append(config)

    async with _write_lock:
        await asyncio.to_thread(_merge_save)

    return web.json_response(_config_to_json(result[0]))


# ---------------------------------------------------------------------------
# Linked members (derived from the api_keys store; never exposes key material)
# ---------------------------------------------------------------------------

async def _handle_key_holders(request: web.Request) -> web.Response:
    """Existence-only key check: which of these GW2 account names have a key
    registered with the bot in ANY server. Booleans only — no key data, no
    cross-server registration details."""
    bot, gid = _guild_ctx(request)
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    names = body.get("account_names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return web.json_response({"error": "account_names must be a list of strings"}, status=400)
    if len(names) == 0 or len(names) > 500:
        return web.json_response({"error": "account_names must contain 1-500 names"}, status=400)

    def _query():
        holders = bot.storage.match_key_holders(names)
        linked_here = {
            user_id for _g, user_id, _r in bot.storage.query_api_keys(guild_id=gid)
        }
        return holders, len(linked_here)

    holders, linked_here = await asyncio.to_thread(_query)
    return web.json_response({
        "holders": holders,
        "matched": sum(1 for v in holders.values() if v),
        "linked_members_in_this_server": linked_here,
    })


async def _handle_members_linked(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    guild = bot.get_guild(gid)

    def _query():
        rows = bot.storage.query_api_keys(guild_id=gid)
        grouped: dict[int, list] = {}
        for _guild_id, user_id, record in rows:
            grouped.setdefault(user_id, []).append(record)
        preferred = {
            user_id: bot.storage.get_preferred_guild_role(gid, user_id)
            for user_id in grouped
        }
        return grouped, preferred

    grouped, preferred = await asyncio.to_thread(_query)

    members = []
    for user_id, records in grouped.items():
        member = guild.get_member(user_id) if guild is not None else None
        members.append({
            "member_id": _sid(user_id),
            "member_name": member.name if member is not None else None,
            "accounts": [
                {
                    "account_name": record.account_name,
                    "characters": list(record.characters),
                    "gw2_guild_ids": list(record.guild_ids),
                    "guild_labels": dict(record.guild_labels),
                }
                for record in records
            ],
            "preferred_role_id": _sid(preferred.get(user_id)),
        })
    return web.json_response(members)


def build_app(bot, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app["bot"] = bot
    app["api_token"] = token
    app.router.add_get("/guilds", _handle_guilds)
    app.router.add_get("/guilds/{guild_id:\\d+}/builds", _handle_builds_list)
    app.router.add_post("/guilds/{guild_id:\\d+}/builds", _handle_builds_create)
    app.router.add_put("/guilds/{guild_id:\\d+}/builds/{build_id}", _handle_builds_update)
    app.router.add_delete("/guilds/{guild_id:\\d+}/builds/{build_id}", _handle_builds_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/comp-config", _handle_comp_config_get)
    app.router.add_patch("/guilds/{guild_id:\\d+}/comp-config", _handle_comp_config_patch)
    app.router.add_get("/guilds/{guild_id:\\d+}/comp-presets", _handle_comp_presets_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/comp-presets/{name}", _handle_comp_presets_upsert)
    app.router.add_delete("/guilds/{guild_id:\\d+}/comp-presets/{name}", _handle_comp_presets_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/comp-schedules", _handle_comp_schedules_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/comp-schedules/{schedule_id}", _handle_comp_schedules_upsert)
    app.router.add_delete("/guilds/{guild_id:\\d+}/comp-schedules/{schedule_id}", _handle_comp_schedules_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/config", _handle_config_get)
    app.router.add_patch("/guilds/{guild_id:\\d+}/config", _handle_config_patch)
    app.router.add_get("/guilds/{guild_id:\\d+}/audit/discord", _handle_audit_discord)
    app.router.add_get("/guilds/{guild_id:\\d+}/audit/gw2", _handle_audit_gw2)
    app.router.add_get("/guilds/{guild_id:\\d+}/rss", _handle_rss_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/rss/{name}", _handle_rss_upsert)
    app.router.add_delete("/guilds/{guild_id:\\d+}/rss/{name}", _handle_rss_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/streams", _handle_streams_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/streams/{name}", _handle_streams_upsert)
    app.router.add_delete("/guilds/{guild_id:\\d+}/streams/{name}", _handle_streams_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/alliance", _handle_alliance_get)
    app.router.add_put("/guilds/{guild_id:\\d+}/alliance", _handle_alliance_put)
    app.router.add_get("/guilds/{guild_id:\\d+}/guild-roles", _handle_guild_roles_get)
    app.router.add_put(
        "/guilds/{guild_id:\\d+}/guild-roles/{gw2_guild_id}", _handle_guild_roles_put
    )
    app.router.add_delete(
        "/guilds/{guild_id:\\d+}/guild-roles/{gw2_guild_id}", _handle_guild_roles_delete
    )
    app.router.add_put(
        "/guilds/{guild_id:\\d+}/guild-roles-allowlist", _handle_guild_roles_allowlist_put
    )
    app.router.add_get("/guilds/{guild_id:\\d+}/members-linked", _handle_members_linked)
    app.router.add_post("/guilds/{guild_id:\\d+}/key-holders", _handle_key_holders)
    app.router.add_get("/guilds/{guild_id:\\d+}/discord", _handle_discord_snapshot)
    app.router.add_get("/guilds/{guild_id:\\d+}/discord/messages", _handle_discord_messages)
    app.router.add_get("/guilds/{guild_id:\\d+}/discord/actions", _handle_discord_actions_list)
    app.router.add_post("/guilds/{guild_id:\\d+}/discord/actions", _handle_discord_actions_post)
    return app


async def start_api(bot, *, host: str | None = None, port: int | None = None) -> web.AppRunner:
    """Start the API server inside the bot process. Returns the runner for cleanup.

    Binds to loopback by default. Set AXITOOLS_API_HOST (e.g. 0.0.0.0 or a LAN
    address) to serve other machines — auth still applies to every request.
    """
    if host is None:
        host = os.getenv("AXITOOLS_API_HOST", "127.0.0.1")
    if port is None:
        port = int(os.getenv("AXITOOLS_API_PORT", str(DEFAULT_PORT)))
    token = resolve_api_token(bot.storage.root)
    app = build_app(bot, token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    LOGGER.info("AxiTools API listening on http://%s:%s", host, port)
    return runner
