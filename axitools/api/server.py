"""Localhost HTTP API for GW2 Officer (and other local clients).

Binds to 127.0.0.1 only. All requests require ``Authorization: Bearer <token>``.
The token comes from AXITOOLS_API_TOKEN, or is generated once and persisted
under the storage root as ``api_token`` (mode 0600).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from aiohttp import web

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8642


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


async def _handle_guilds(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    return web.json_response([{"id": g.id, "name": g.name} for g in bot.guilds])


def build_app(bot, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app["bot"] = bot
    app["api_token"] = token
    app.router.add_get("/guilds", _handle_guilds)
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
