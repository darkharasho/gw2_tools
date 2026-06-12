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

from ..storage import BuildRecord, utcnow

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8642

API_ACTOR_ID = 0


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
    bot = request.app["bot"]
    gid = int(request.match_info["guild_id"])
    builds = await asyncio.to_thread(bot.storage.get_builds, gid)
    return web.json_response([_build_to_json(b) for b in builds])


async def _handle_builds_create(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = int(request.match_info["guild_id"])
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    for field in ("name", "profession", "chat_code"):
        if not body.get(field):
            return web.json_response({"error": f"missing field: {field}"}, status=400)
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
    await asyncio.to_thread(bot.storage.upsert_build, gid, record)
    return web.json_response(_build_to_json(record), status=201)


async def _handle_builds_update(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = int(request.match_info["guild_id"])
    build_id = request.match_info["build_id"]
    body = await _parse_json_body(request)
    if body is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    existing = await asyncio.to_thread(bot.storage.find_build, gid, build_id)
    if existing is None:
        return web.json_response({"error": "build not found"}, status=404)
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
    await asyncio.to_thread(bot.storage.upsert_build, gid, updated)
    return web.json_response(_build_to_json(updated))


async def _handle_builds_delete(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = int(request.match_info["guild_id"])
    build_id = request.match_info["build_id"]
    deleted = await asyncio.to_thread(bot.storage.delete_build, gid, build_id)
    if not deleted:
        return web.json_response({"error": "build not found"}, status=404)
    return web.Response(status=204)


def build_app(bot, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app["bot"] = bot
    app["api_token"] = token
    app.router.add_get("/guilds", _handle_guilds)
    app.router.add_get("/guilds/{guild_id}/builds", _handle_builds_list)
    app.router.add_post("/guilds/{guild_id}/builds", _handle_builds_create)
    app.router.add_put("/guilds/{guild_id}/builds/{build_id}", _handle_builds_update)
    app.router.add_delete("/guilds/{guild_id}/builds/{build_id}", _handle_builds_delete)
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
