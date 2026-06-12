"""Localhost HTTP API for GW2 Officer (and other local clients).

Binds to 127.0.0.1 only. All requests require ``Authorization: Bearer <token>``.
The token comes from AXITOOLS_API_TOKEN, or is generated once and persisted
under the storage root as ``api_token`` (mode 0600).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
from dataclasses import asdict
from pathlib import Path

import discord
from aiohttp import web

from ..storage import BuildRecord, CompPreset, CompSchedule, utcnow
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


def _build_to_json(record: BuildRecord) -> dict:
    return asdict(record)


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
    return web.json_response([{"id": g.id, "name": g.name} for g in guilds])


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
    return web.json_response([p.to_dict() for p in presets])


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

    return web.json_response(result[0].to_dict())


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
    return web.json_response([s.to_dict() for s in config.comp_schedules])


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

    return web.json_response(result[0].to_dict())


_CONFIG_WHITELIST = ("moderator_role_ids", "build_channel_id", "comp_active_preset")


async def _handle_config_get(request: web.Request) -> web.Response:
    bot, gid = _guild_ctx(request)
    config = await asyncio.to_thread(bot.storage.get_config, gid)
    body = {key: getattr(config, key) for key in _CONFIG_WHITELIST}
    body["comp_schedule_count"] = len(config.comp_schedules)
    return web.json_response(body)


def _iso(value) -> str | None:
    """Render datetimes as ISO 8601; pass strings (and None) through."""
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _permissions_value(role) -> int:
    perms = getattr(role, "permissions", 0)
    return int(getattr(perms, "value", perms) or 0)


def _resolve_discord_guild(request: web.Request):
    """Return (guild, error_response) for /discord handlers."""
    bot, gid = _guild_ctx(request)
    guild = bot.get_guild(gid)
    if guild is None:
        return None, web.json_response({"error": "guild not found"}, status=404)
    return guild, None


def _channel_to_json(channel) -> dict:
    return {
        "id": channel.id,
        "name": channel.name,
        "type": str(channel.type),
        "category_id": getattr(channel, "category_id", None),
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
            "id": guild.id,
            "name": guild.name,
            "member_count": guild.member_count,
        },
        "categories": [
            {"id": c.id, "name": c.name, "position": getattr(c, "position", 0)}
            for c in categories
        ],
        "channels": [
            _channel_to_json(ch) for ch in guild.channels if ch.id not in category_ids
        ],
        "roles": [
            {
                "id": r.id,
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
                "id": t.id,
                "name": t.name,
                "parent_id": getattr(t, "parent_id", None),
                "archived": getattr(t, "archived", False),
            }
            for t in getattr(guild, "threads", ())
        ],
        "scheduled_events": [
            {
                "id": e.id,
                "name": e.name,
                "description": getattr(e, "description", None),
                "start_time": _iso(getattr(e, "start_time", None)),
                "end_time": _iso(getattr(e, "end_time", None)),
                "channel_id": getattr(e, "channel_id", None),
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
                "id": m.id,
                "name": m.name,
                "display_name": getattr(m, "display_name", m.name),
                "role_ids": [r.id for r in getattr(m, "roles", ())],
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
            "id": m.id,
            "author_id": m.author.id,
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


def build_app(bot, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app["bot"] = bot
    app["api_token"] = token
    app.router.add_get("/guilds", _handle_guilds)
    app.router.add_get("/guilds/{guild_id:\\d+}/builds", _handle_builds_list)
    app.router.add_post("/guilds/{guild_id:\\d+}/builds", _handle_builds_create)
    app.router.add_put("/guilds/{guild_id:\\d+}/builds/{build_id}", _handle_builds_update)
    app.router.add_delete("/guilds/{guild_id:\\d+}/builds/{build_id}", _handle_builds_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/comp-presets", _handle_comp_presets_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/comp-presets/{name}", _handle_comp_presets_upsert)
    app.router.add_delete("/guilds/{guild_id:\\d+}/comp-presets/{name}", _handle_comp_presets_delete)
    app.router.add_get("/guilds/{guild_id:\\d+}/comp-schedules", _handle_comp_schedules_list)
    app.router.add_put("/guilds/{guild_id:\\d+}/comp-schedules/{schedule_id}", _handle_comp_schedules_upsert)
    app.router.add_get("/guilds/{guild_id:\\d+}/config", _handle_config_get)
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
