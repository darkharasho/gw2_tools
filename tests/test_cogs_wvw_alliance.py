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


@pytest.mark.asyncio
async def test_post_matchup_prediction_uses_sheet_world_when_available(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "Example Guild [EX]"
    config.alliance_server_id = 1001

    guild = MagicMock()
    guild.id = 123
    channel = MagicMock()
    channel.send = AsyncMock()

    teams = [MatchTeam(color="red", world_ids=[11002], victory_points=10)]

    cog._resolve_prediction_world_from_sheet = AsyncMock(return_value=11002)
    cog._refresh_guild_world = AsyncMock(return_value=2002)
    cog._fetch_matches = AsyncMock(return_value=[{"tier": 1}])
    cog._predict_tiers = MagicMock(return_value=[MagicMock(tier=1, teams=teams)])
    cog._extract_match_teams = MagicMock(return_value=teams)
    cog._resolve_team_alliances = AsyncMock(return_value=AllianceRoster(alliances=[], solo_guilds=[]))
    cog._build_embed = MagicMock(return_value=object())

    posted = await cog._post_matchup(guild=guild, channel=channel, config=config, prediction=True)

    assert posted is True
    assert config.alliance_server_id == 11002
    cog._resolve_prediction_world_from_sheet.assert_awaited_once_with(config)
    cog._refresh_guild_world.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_matchup_prediction_falls_back_to_api_world(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "Example Guild [EX]"
    config.alliance_server_id = 1001

    guild = MagicMock()
    guild.id = 123
    channel = MagicMock()
    channel.send = AsyncMock()

    teams = [MatchTeam(color="red", world_ids=[2002], victory_points=10)]

    cog._resolve_prediction_world_from_sheet = AsyncMock(return_value=None)
    cog._refresh_guild_world = AsyncMock(return_value=2002)
    cog._fetch_matches = AsyncMock(return_value=[{"tier": 1}])
    cog._predict_tiers = MagicMock(return_value=[MagicMock(tier=1, teams=teams)])
    cog._extract_match_teams = MagicMock(return_value=teams)
    cog._resolve_team_alliances = AsyncMock(return_value=AllianceRoster(alliances=[], solo_guilds=[]))
    cog._build_embed = MagicMock(return_value=object())

    posted = await cog._post_matchup(guild=guild, channel=channel, config=config, prediction=True)

    assert posted is True
    cog._resolve_prediction_world_from_sheet.assert_awaited_once_with(config)
    cog._refresh_guild_world.assert_awaited_once_with(config, force_refresh=True)


@pytest.mark.asyncio
async def test_guild_matcher_supports_bracket_prefix_format(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    assert cog._guild_matches_target("[EX] Example Guild", "[EX] Example Guild")
    assert cog._guild_matches_target("Example Guild [EX]", "[EX] Example Guild")


@pytest.mark.asyncio
async def test_guild_matcher_rejects_different_tags_even_if_name_overlaps(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    assert not cog._guild_matches_target("[DTF] - Defiance Of The Fearless", "[DEFI] Defîance")


@pytest.mark.asyncio
async def test_prediction_world_resolver_matches_alliance_name_rows(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "[DEFI] Defîance"

    async def fake_fetch(sheet_name: str):
        if sheet_name == "HoJ":
            return AllianceRoster(
                alliances=[("[DEFI] Defîance", ["[ABC] Some Guild"])],
                solo_guilds=[],
            )
        return AllianceRoster(alliances=[], solo_guilds=[])

    cog._fetch_alliances = AsyncMock(side_effect=fake_fetch)

    world_id = await cog._resolve_prediction_world_from_sheet(config)
    assert world_id == 11006
