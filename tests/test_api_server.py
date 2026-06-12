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
    assert await resp.json() == [{"id": 123, "name": "Vigil Keep"}]


def test_resolve_api_token_generates_and_persists(tmp_path):
    first = resolve_api_token(tmp_path)
    second = resolve_api_token(tmp_path)
    assert first == second
    assert len(first) == 64  # token_hex(32)
    assert (tmp_path / "api_token").exists()
