from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import build_app, resolve_api_token
from axitools.storage import StorageManager


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name


class FakeBot:
    """Minimal stand-in for AxiToolsBot: just .storage and .guilds."""

    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(123, "Vigil Keep")]


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


def _auth():
    return {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_rejects_missing_token(api_client):
    resp = await api_client.get("/guilds")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_rejects_wrong_token(api_client):
    resp = await api_client.get("/guilds", headers={"Authorization": "Bearer nope"})
    assert resp.status == 401


@pytest.mark.asyncio
async def test_lists_guilds(api_client):
    resp = await api_client.get("/guilds", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == [{"id": "123", "name": "Vigil Keep"}]


def test_resolve_api_token_generates_and_persists(tmp_path):
    first = resolve_api_token(tmp_path)
    second = resolve_api_token(tmp_path)
    assert first == second
    assert len(first) == 64  # token_hex(32)
    assert (tmp_path / "api_token").exists()
    assert (tmp_path / "api_token").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_start_api_lifecycle(tmp_path):
    import socket
    import aiohttp
    from axitools.api.server import start_api, resolve_api_token

    # Bind to port 0 to let the OS pick a free ephemeral port.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]

    bot = FakeBot(tmp_path)
    runner = await start_api(bot, port=free_port)
    try:
        token = resolve_api_token(tmp_path)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{free_port}/guilds",
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                assert resp.status == 200
    finally:
        await runner.cleanup()
