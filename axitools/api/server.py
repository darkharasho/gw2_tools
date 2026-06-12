"""Localhost HTTP API for GW2 Officer (and other local clients).

Binds to 127.0.0.1 only. All requests require ``Authorization: Bearer <token>``.
The token comes from AXITOOLS_API_TOKEN, or is generated once and persisted
under the storage root as ``api_token`` (mode 0600).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from ..storage import BuildRecord, CompPreset, CompSchedule, GuildConfig, utcnow

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8642

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


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    expected = f"Bearer {request.app['api_token']}"
    supplied = request.headers.get("Authorization", "")
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
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
    return web.json_response([{"id": g.id, "name": g.name} for g in bot.guilds])


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
    return app


async def start_api(bot, *, host: str = "127.0.0.1", port: int | None = None) -> web.AppRunner:
    """Start the API server inside the bot process. Returns the runner for cleanup."""
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
