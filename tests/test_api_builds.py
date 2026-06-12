from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import build_app
from axitools.storage import StorageManager

from tests.test_api_server import FakeBot, _auth  # reuse helpers

GID = 123


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_builds_crud(api_client, bot):
    # empty list
    resp = await api_client.get(f"/guilds/{GID}/builds", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == []

    # create
    payload = {
        "name": "Quickness Firebrand",
        "profession": "Guardian",
        "specialization": "Firebrand",
        "chat_code": "[&DQEAAA==]",
        "url": "https://gw2skills.net/x",
        "description": "Stab + quickness support",
    }
    resp = await api_client.post(f"/guilds/{GID}/builds", json=payload, headers=_auth())
    assert resp.status == 201
    created = await resp.json()
    assert created["name"] == "Quickness Firebrand"
    build_id = created["build_id"]
    assert build_id

    # list shows it (and storage agrees)
    resp = await api_client.get(f"/guilds/{GID}/builds", headers=_auth())
    assert [b["build_id"] for b in await resp.json()] == [build_id]
    assert bot.storage.get_builds(GID)[0].name == "Quickness Firebrand"

    # update
    resp = await api_client.put(
        f"/guilds/{GID}/builds/{build_id}",
        json={"description": "Updated"},
        headers=_auth(),
    )
    assert resp.status == 200
    assert (await resp.json())["description"] == "Updated"
    assert bot.storage.get_builds(GID)[0].description == "Updated"

    # delete
    resp = await api_client.delete(f"/guilds/{GID}/builds/{build_id}", headers=_auth())
    assert resp.status == 204
    assert bot.storage.get_builds(GID) == []


@pytest.mark.asyncio
async def test_build_not_found(api_client):
    resp = await api_client.put(
        f"/guilds/{GID}/builds/missing", json={"name": "x"}, headers=_auth()
    )
    assert resp.status == 404
    resp = await api_client.delete(f"/guilds/{GID}/builds/missing", headers=_auth())
    assert resp.status == 404


@pytest.mark.asyncio
async def test_build_create_requires_fields(api_client):
    valid = {"name": "X", "profession": "Guardian", "chat_code": "[&x]"}
    for field in ("name", "profession", "chat_code"):
        payload = {k: v for k, v in valid.items() if k != field}
        resp = await api_client.post(f"/guilds/{GID}/builds", json=payload, headers=_auth())
        assert resp.status == 400
        assert (await resp.json())["error"] == f"missing field: {field}"
        # empty string is also rejected
        payload = dict(valid, **{field: ""})
        resp = await api_client.post(f"/guilds/{GID}/builds", json=payload, headers=_auth())
        assert resp.status == 400


@pytest.mark.asyncio
async def test_malformed_json_returns_400(api_client):
    resp = await api_client.post(
        f"/guilds/{GID}/builds", data="not json",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status == 400

    resp = await api_client.post(
        f"/guilds/{GID}/builds", data="[1,2,3]",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status == 400

    resp = await api_client.put(
        f"/guilds/{GID}/builds/whatever", data="42",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status == 400
