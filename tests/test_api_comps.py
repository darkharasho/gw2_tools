import pytest
import pytest_asyncio

from axitools.api.server import build_app
from axitools.storage import CompConfig, CompPreset

from tests.test_api_server import FakeBot, _auth

GID = 123


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_comp_presets_crud(api_client, bot):
    resp = await api_client.get(f"/guilds/{GID}/comp-presets", headers=_auth())
    assert await resp.json() == []

    # create/replace by name — build payload in the exact to_dict() shape:
    template = CompPreset(name="Tuesday WvW Raid", config=CompConfig()).to_dict()
    resp = await api_client.put(
        f"/guilds/{GID}/comp-presets/Tuesday WvW Raid",
        json=template,
        headers=_auth(),
    )
    assert resp.status == 200
    names = [p.name for p in bot.storage.get_comp_presets(GID)]
    assert names == ["Tuesday WvW Raid"]

    resp = await api_client.get(f"/guilds/{GID}/comp-presets", headers=_auth())
    assert [p["name"] for p in await resp.json()] == ["Tuesday WvW Raid"]

    resp = await api_client.delete(
        f"/guilds/{GID}/comp-presets/Tuesday WvW Raid", headers=_auth()
    )
    assert resp.status == 204
    assert bot.storage.get_comp_presets(GID) == []


@pytest.mark.asyncio
async def test_comp_preset_delete_missing_404(api_client):
    resp = await api_client.delete(f"/guilds/{GID}/comp-presets/nope", headers=_auth())
    assert resp.status == 404


@pytest.mark.asyncio
async def test_comp_schedules_crud(api_client, bot):
    resp = await api_client.get(f"/guilds/{GID}/comp-schedules", headers=_auth())
    assert resp.status == 200
    assert await resp.json() == []

    # create/replace by schedule_id; CompSchedule.from_dict requires "name" as a string
    payload = {
        "name": "Tuesday Raid",
        "preset_name": None,
        "post_days": [1],
        "post_time": "19:00",
        "timezone": "UTC",
    }
    resp = await api_client.put(
        f"/guilds/{GID}/comp-schedules/sched-1", json=payload, headers=_auth()
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["schedule_id"] == "sched-1"
    assert [s.schedule_id for s in bot.storage.get_config(GID).comp_schedules] == ["sched-1"]

    # replace updates in place (no duplicates)
    resp = await api_client.put(
        f"/guilds/{GID}/comp-schedules/sched-1",
        json=dict(payload, name="Renamed"),
        headers=_auth(),
    )
    assert resp.status == 200
    schedules = bot.storage.get_config(GID).comp_schedules
    assert len(schedules) == 1 and schedules[0].name == "Renamed"


@pytest.mark.asyncio
async def test_config_excludes_secrets(api_client, bot):
    config = bot.storage.get_config(GID)
    config.audit_gw2_admin_api_key = "SECRET-KEY"
    bot.storage.save_config(GID, config)

    resp = await api_client.get(f"/guilds/{GID}/config", headers=_auth())
    body = await resp.json()
    assert "SECRET-KEY" not in str(body)
    assert "moderator_role_ids" in body


@pytest.mark.asyncio
async def test_invalid_preset_payload_400(api_client):
    # {"bogus": True} lacks a "config" dict → rejected by the pre-validation guard → 400
    resp = await api_client.put(
        f"/guilds/{GID}/comp-presets/X", json={"bogus": True}, headers=_auth()
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_invalid_schedule_payload_400(api_client):
    # non-string name → CompSchedule.from_dict raises → 400
    resp = await api_client.put(
        f"/guilds/{GID}/comp-schedules/sched-x", json={"name": 42}, headers=_auth()
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_comp_schedule_delete(api_client, bot):
    await api_client.put(
        f"/guilds/{GID}/comp-schedules/s1",
        json={"name": "Tuesday raid", "post_days": [2], "post_time": "18:00"},
        headers=_auth(),
    )
    resp = await api_client.delete(f"/guilds/{GID}/comp-schedules/s1", headers=_auth())
    assert resp.status == 204
    listing = await (await api_client.get(f"/guilds/{GID}/comp-schedules", headers=_auth())).json()
    assert listing == []
    resp = await api_client.delete(f"/guilds/{GID}/comp-schedules/s1", headers=_auth())
    assert resp.status == 404
