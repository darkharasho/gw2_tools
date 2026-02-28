from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock

from gw2_tools_bot.storage import GuildConfig
from gw2_tools_bot.cogs.wvw_alliance import AllianceMatchupCog, AllianceRoster, MatchTeam

@pytest.fixture
def mock_bot_alliance():
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot

@pytest.mark.asyncio
async def test_wvw_alliance_init(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    assert cog is not None
    cog._poster_loop.cancel()


@pytest.mark.asyncio
async def test_fetch_guild_world_map_force_refresh_bypasses_cache(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    url = "https://example.com/wvw"
    cog._guild_world_cache[url] = {"abcdef": 1001}
    cog._guild_world_cache_at[url] = datetime.now(timezone.utc)
    cog._fetch_json = AsyncMock(return_value={"abcdef": 2002})

    result = await cog._fetch_guild_world_map(url, force_refresh=True)

    assert result == {"abcdef": 2002}
    cog._fetch_json.assert_awaited_once_with(url)


@pytest.mark.asyncio
async def test_post_matchup_refreshes_world_before_posting(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_id = "abcdef"
    config.alliance_server_id = 1001

    guild = MagicMock()
    guild.id = 123
    channel = MagicMock()
    channel.send = AsyncMock()

    teams = [MatchTeam(color="red", world_ids=[2002], victory_points=10)]

    cog._refresh_guild_world = AsyncMock(return_value=2002)
    cog._fetch_matches = AsyncMock(return_value=[])
    cog._fetch_match_for_world = AsyncMock(return_value={"tier": 1})
    cog._extract_match_teams = MagicMock(return_value=teams)
    cog._resolve_team_alliances = AsyncMock(return_value=AllianceRoster(alliances=[], solo_guilds=[]))
    cog._build_embed = MagicMock(return_value=object())

    posted = await cog._post_matchup(guild=guild, channel=channel, config=config, prediction=False)

    assert posted is True
    cog._refresh_guild_world.assert_awaited_once_with(config, force_refresh=True)
    cog._fetch_match_for_world.assert_awaited_once_with(2002)
