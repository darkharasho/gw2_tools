from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock

from axitools.storage import GuildConfig
from axitools.cogs.wvw_alliance import AllianceMatchupCog, AllianceRoster, MatchTeam

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
async def test_alliance_command_surface(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    qualified_names = {
        cmd.qualified_name for cmd in cog.walk_app_commands()
    }

    expected = {
        "alliance setup guild",
        "alliance setup channel",
        "alliance setup time",
        "alliance post",
        "alliance status",
        "alliance refresh",
        "alliance relink enable",
        "alliance relink disable",
        "alliance lockout enable",
        "alliance lockout disable",
        "alliance lockout channel",
        "alliance lockout lead",
        "alliance lockout region",
    }
    assert expected <= qualified_names

    old = {
        "alliance setguild",
        "alliance setchannel",
        "alliance settime",
        "alliance postnow",
    }
    assert not (old & qualified_names)


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
async def test_post_matchup_skips_when_home_world_absent_from_match(mock_bot_alliance):
    # Regression: around WvW reset the fetched match's teams can transiently
    # disagree with the resolved home world. The current-matchup path must not
    # post a "homeless" embed (3 opponents, no home team) and must not stamp the
    # last-posted timestamp, so the 5-minute loop retries once the API settles.
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_id = "abcdef"
    config.alliance_server_id = 1001

    guild = MagicMock()
    guild.id = 123
    channel = MagicMock()
    channel.send = AsyncMock()

    # Home world resolves to 2002, but none of the three fetched teams contain it.
    teams = [
        MatchTeam(color="red", world_ids=[3003], victory_points=10),
        MatchTeam(color="blue", world_ids=[4004], victory_points=8),
        MatchTeam(color="green", world_ids=[5005], victory_points=6),
    ]

    cog._refresh_guild_world = AsyncMock(return_value=2002)
    cog._fetch_matches = AsyncMock(return_value=[])
    cog._fetch_match_for_world = AsyncMock(return_value={"tier": 1})
    cog._extract_match_teams = MagicMock(return_value=teams)
    cog._resolve_team_alliances = AsyncMock(return_value=AllianceRoster(alliances=[], solo_guilds=[]))
    cog._build_embed = MagicMock(return_value=object())

    posted = await cog._post_matchup(guild=guild, channel=channel, config=config, prediction=False)

    assert posted is False
    channel.send.assert_not_awaited()
    assert config.alliance_last_actual_at is None


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


@pytest.mark.asyncio
async def test_format_alliance_list_trims_in_display_order(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    roster = AllianceRoster(
        alliances=[
            (
                "[DFVG] Dire Fang Vanguard",
                [
                    "[DWLC] - Devonas Legendary Wvw Champions",
                    "[FANG] - Onlyfangs",
                    "[KFT] - Killz For Thrillz",
                    "[LLVG] - Leather And Lace Vanguard (NA)",
                ],
            ),
            (
                "[OCD] Organized Chaos By Design",
                [
                    "[AIR] - The Order Of Storms",
                    "[CA] - Central Anime",
                    "[CH] - Contemporary Heroes",
                    "[PAIN] - Brethren",
                    "[solo] - Together Yet usually",
                    "[STAR] - Glory to the Stars",
                    "[WAR] - The Sons Of Tzu (NA)",
                ],
            ),
            (
                "[OINK] Respectfully",
                [
                    "[BS] - Washed Up (Innactive)",
                    "[DIS] - Dissentient (NA)",
                    "[HEX] Sisterhood Of Thugs",
                    "[PIG] - Pretty Insensitive Girl (NA)",
                ],
            ),
            (
                "[PALS] Quick Build Siege",
                [
                    "[Grim] - The Charr Empire (NA)",
                    "[SoX] - Standard of Heroes (OCX)",
                ],
            ),
            (
                "[SA] Seriously Annoying",
                [
                    "[BADA] - Bad Aces",
                    "[BANE] - Nightmares Bane (NA)",
                    "[PACK] - Wölves Of Wär",
                ],
            ),
            ("[ZERO] Roll Tha Dice", ["[DPS] - Dodged"]),
        ],
        solo_guilds=[
            "[Bana] - Bellicose Banana Club (SEA)",
            "[Crew] - The Tides Of Tyria",
            "[DFR] - Devonas Final Rest",
            "[LH] - Long Horizon (NA)",
            "[Mist] - Phantoms Of The",
            "[MoB] - Invictus Nox",
            "[TEAM] - Operation Get Em",
            "[UA] - The Underachíevers (NA)",
        ],
    )

    value = cog._format_alliance_list(roster)

    assert value.endswith("…")
    assert "[PACK] - Wölves Of Wär" in value


@pytest.mark.asyncio
async def test_find_relink_server_tab_returns_tab_name(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "My Guild [MG]"

    cog._resolve_prediction_world_from_sheet = AsyncMock(return_value=11006)

    result = await cog._find_relink_server_tab(config)

    assert result == "HoJ"


@pytest.mark.asyncio
async def test_find_relink_server_tab_returns_none_when_not_found(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "Unknown Guild"

    cog._resolve_prediction_world_from_sheet = AsyncMock(return_value=None)

    result = await cog._find_relink_server_tab(config)

    assert result is None


@pytest.mark.asyncio
async def test_find_relink_server_tab_returns_none_for_unknown_world_id(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    cog._resolve_prediction_world_from_sheet = AsyncMock(return_value=99999)  # not in WVW_ALLIANCE_SHEET_TABS

    result = await cog._find_relink_server_tab(config)

    assert result is None


@pytest.mark.asyncio
async def test_build_relink_embed_contains_server_and_roster(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    roster = AllianceRoster(
        alliances=[("Renegades", ["Guild A", "Guild B"])],
        solo_guilds=["Solo Guild"],
    )

    embed = cog._build_relink_embed(server_name="Hall of Judgement", roster=roster, world_id=11006)

    assert "New Server Link Announced" in (embed.description or "")
    field_names = [f.name for f in embed.fields]
    assert "Server" in field_names
    assert "Roster" in field_names
    server_field = next(f for f in embed.fields if f.name == "Server")
    assert "Hall of Judgement" in server_field.value


@pytest.mark.asyncio
async def test_check_relink_posts_when_server_changes(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "My Guild [MG]"
    config.alliance_relink_enabled = True
    config.alliance_relink_last_server = "RR"  # was Rall's Rest

    guild = MagicMock()
    guild.id = 42
    channel = MagicMock()
    channel.send = AsyncMock()

    roster = AllianceRoster(alliances=[("Alliance A", ["Guild X"])], solo_guilds=[])
    cog._find_relink_server_tab = AsyncMock(return_value="HoJ")
    cog._fetch_alliances = AsyncMock(return_value=roster)
    cog._build_relink_embed = MagicMock(return_value=MagicMock())
    mock_bot_alliance.save_config = MagicMock()

    await cog._check_relink(guild, channel, config)

    channel.send.assert_awaited_once()
    assert config.alliance_relink_last_server == "HoJ"
    mock_bot_alliance.save_config.assert_called_once_with(42, config)


@pytest.mark.asyncio
async def test_check_relink_no_post_when_server_unchanged(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_relink_last_server = "HoJ"

    guild = MagicMock()
    guild.id = 42
    channel = MagicMock()
    channel.send = AsyncMock()

    cog._find_relink_server_tab = AsyncMock(return_value="HoJ")
    mock_bot_alliance.save_config = MagicMock()

    await cog._check_relink(guild, channel, config)

    channel.send.assert_not_awaited()
    mock_bot_alliance.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_check_relink_no_post_on_first_detection(mock_bot_alliance):
    """last_server=None means this is the priming run; no post, just store."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_relink_last_server = None

    guild = MagicMock()
    guild.id = 42
    channel = MagicMock()
    channel.send = AsyncMock()

    cog._find_relink_server_tab = AsyncMock(return_value="HoJ")
    mock_bot_alliance.save_config = MagicMock()

    await cog._check_relink(guild, channel, config)

    channel.send.assert_not_awaited()
    assert config.alliance_relink_last_server == "HoJ"
    mock_bot_alliance.save_config.assert_called_once_with(42, config)


@pytest.mark.asyncio
async def test_check_relink_skips_when_world_id_not_in_tab_map(mock_bot_alliance):
    """If the tab name has no matching world_id, log and return without updating state."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_relink_last_server = "RR"  # previous server

    guild = MagicMock()
    guild.id = 42
    channel = MagicMock()
    channel.send = AsyncMock()

    # Return a tab name that has no entry in WVW_ALLIANCE_SHEET_TABS
    cog._find_relink_server_tab = AsyncMock(return_value="UNKNOWN_TAB")
    mock_bot_alliance.save_config = MagicMock()

    await cog._check_relink(guild, channel, config)

    channel.send.assert_not_awaited()
    assert config.alliance_relink_last_server == "RR"  # state not updated
    mock_bot_alliance.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_relink_enable_requires_guild_and_channel(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    # alliance_guild_id and alliance_channel_id both None
    mock_bot_alliance.ensure_authorised = AsyncMock(return_value=True)
    mock_bot_alliance.get_config = MagicMock(return_value=config)

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.send_message = AsyncMock()

    await cog.relink_enable.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    assert "setup guild" in (args[0] if args else kwargs.get("content", ""))


@pytest.mark.asyncio
async def test_relink_enable_requires_guild_name(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.alliance_channel_id = 999
    # alliance_guild_name is None

    mock_bot_alliance.ensure_authorised = AsyncMock(return_value=True)
    mock_bot_alliance.get_config = MagicMock(return_value=config)

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.send_message = AsyncMock()

    await cog.relink_enable.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_relink_enable_primes_last_server(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.alliance_channel_id = 999
    config.alliance_guild_name = "My Guild [MG]"

    mock_bot_alliance.ensure_authorised = AsyncMock(return_value=True)
    mock_bot_alliance.get_config = MagicMock(return_value=config)
    mock_bot_alliance.save_config = MagicMock()

    cog._find_relink_server_tab = AsyncMock(return_value="HoJ")

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await cog.relink_enable.callback(cog, interaction)

    assert config.alliance_relink_enabled is True
    assert config.alliance_relink_last_server == "HoJ"
    mock_bot_alliance.save_config.assert_called_once_with(1, config)


@pytest.mark.asyncio
async def test_relink_disable_clears_flag(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_relink_enabled = True
    config.alliance_relink_last_server = "HoJ"

    mock_bot_alliance.ensure_authorised = AsyncMock(return_value=True)
    mock_bot_alliance.get_config = MagicMock(return_value=config)
    mock_bot_alliance.save_config = MagicMock()

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.send_message = AsyncMock()

    await cog.relink_disable.callback(cog, interaction)

    assert config.alliance_relink_enabled is False
    assert config.alliance_relink_last_server == "HoJ"  # preserved
    mock_bot_alliance.save_config.assert_called_once_with(1, config)


@pytest.mark.asyncio
async def test_status_shows_relink_state(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()

    config = GuildConfig.default()
    config.alliance_guild_name = "My Guild [MG]"
    config.alliance_channel_id = 555
    config.alliance_relink_enabled = True
    config.alliance_relink_last_server = "HoJ"

    mock_bot_alliance.get_config = MagicMock(return_value=config)
    mock_bot_alliance.ensure_authorised = AsyncMock(return_value=True)

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.guild.get_channel = MagicMock(return_value=None)
    interaction.response.send_message = AsyncMock()

    await cog.status.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    field_names = [f.name for f in embed.fields]
    assert "Relink Announcements" in field_names
    relink_field = next(f for f in embed.fields if f.name == "Relink Announcements")
    assert "Enabled" in relink_field.value
    assert "HoJ" in relink_field.value


@pytest.mark.asyncio
async def test_fetch_lockout_parses_payload(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    cog._fetch_json = AsyncMock(
        return_value={"na": "2025-03-04T07:59:00Z", "eu": "2025-03-04T07:59:00Z"}
    )
    result = await cog._fetch_lockout()
    assert result == {"na": "2025-03-04T07:59:00Z", "eu": "2025-03-04T07:59:00Z"}


@pytest.mark.asyncio
async def test_fetch_lockout_drops_invalid_and_returns_none(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    cog._fetch_json = AsyncMock(return_value={"na": "", "eu": None})
    assert await cog._fetch_lockout() is None


@pytest.mark.asyncio
async def test_fetch_lockout_handles_fetch_error(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    cog._fetch_json = AsyncMock(side_effect=ValueError("boom"))
    assert await cog._fetch_lockout() is None


@pytest.mark.asyncio
async def test_poster_loop_survives_lockout_fetch_error(mock_bot_alliance):
    """An unexpected error fetching lockout must not propagate: discord.py stops a
    tasks.loop permanently on an unhandled exception, which would silently disable
    every alliance/reset post until the process restarts."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    guild = MagicMock()
    guild.id = 1
    mock_bot_alliance.guilds = [guild]
    mock_bot_alliance.get_config = MagicMock(
        return_value=GuildConfig(moderator_role_ids=[], alliance_channel_id=None, alliance_guild_id=None)
    )
    cog._fetch_lockout = AsyncMock(side_effect=RuntimeError("boom"))

    await cog._poster_loop.coro(cog)


@pytest.mark.asyncio
async def test_poster_loop_survives_per_guild_error(mock_bot_alliance):
    """One broken guild must not stop the remaining guilds from being processed."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    bad, good = MagicMock(), MagicMock()
    bad.id, good.id = 1, 2
    mock_bot_alliance.guilds = [bad, good]
    seen = []

    def _get_config(guild_id):
        seen.append(guild_id)
        if guild_id == 1:
            raise RuntimeError("boom")
        return GuildConfig(moderator_role_ids=[], alliance_channel_id=None, alliance_guild_id=None)

    mock_bot_alliance.get_config = MagicMock(side_effect=_get_config)
    cog._fetch_lockout = AsyncMock(return_value=None)

    await cog._poster_loop.coro(cog)

    assert seen == [1, 2]


class _FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_fetch_json_retries_after_rate_limit(monkeypatch, mock_bot_alliance):
    """A 429 must back off and retry, not blow up the caller (and the poster loop)."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    session = _FakeSession(
        [_FakeResponse(429, None), _FakeResponse(200, {"na": "2026-08-15T01:59:00Z"})]
    )
    cog._get_session = AsyncMock(return_value=session)
    slept = []

    async def _fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr("axitools.cogs.wvw_alliance.asyncio.sleep", _fake_sleep)

    result = await cog._fetch_json("https://example.invalid/lockout")

    assert result == {"na": "2026-08-15T01:59:00Z"}
    assert slept == [2]
    assert session.calls == 2


@pytest.mark.asyncio
async def test_fetch_lockout_survives_rate_limit(monkeypatch, mock_bot_alliance):
    """The lockout fetch is the first thing the poster loop does; a 429 there must
    not escape and kill the loop for every guild."""
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    session = _FakeSession([_FakeResponse(429, None) for _ in range(3)])
    cog._get_session = AsyncMock(return_value=session)

    async def _fake_sleep(delay):
        return None

    monkeypatch.setattr("axitools.cogs.wvw_alliance.asyncio.sleep", _fake_sleep)

    assert await cog._fetch_lockout() is None


def _lockout_config(**overrides):
    base = dict(
        moderator_role_ids=[],
        wvw_lockout_enabled=True,
        wvw_lockout_channel_id=555,
        wvw_lockout_lead_minutes=60,
        wvw_lockout_region=None,
        wvw_lockout_last_fired_for=None,
    )
    base.update(overrides)
    return GuildConfig(**base)


LOCKOUT = {"na": "2025-03-04T08:00:00Z", "eu": "2025-03-04T09:00:00Z"}


def _at(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_derive_lockout_region(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    assert cog._derive_lockout_region(11005) == "na"
    assert cog._derive_lockout_region(12003) == "eu"
    assert cog._derive_lockout_region(9999) is None
    assert cog._derive_lockout_region(None) is None


@pytest.mark.asyncio
async def test_lockout_fires_at_lead_boundary(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    config = _lockout_config()  # region auto, home world drives it
    # Before window: 06:59Z, lead 60m, target 08:00Z -> no fire
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T06:59:00Z"), 11005) is None
    # Exactly at target - lead (07:00Z) -> fire NA
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T07:00:00Z"), 11005) == ("na", "2025-03-04T08:00:00Z")
    # Inside window (07:30Z) -> fire NA
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T07:30:00Z"), 11005) == ("na", "2025-03-04T08:00:00Z")
    # After target (08:01Z) -> no fire
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T08:01:00Z"), 11005) is None


@pytest.mark.asyncio
async def test_lockout_region_override_wins(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    config = _lockout_config(wvw_lockout_region="eu")  # explicit EU despite NA home world
    # EU target 09:00Z, lead 60m -> fires at 08:00Z; NA home world ignored
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T08:00:00Z"), 11005) == ("eu", "2025-03-04T09:00:00Z")


@pytest.mark.asyncio
async def test_lockout_dedupe(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    config = _lockout_config(wvw_lockout_last_fired_for="2025-03-04T08:00:00Z")
    # Same target already fired -> no fire
    assert cog._lockout_fire_target(config, LOCKOUT, _at("2025-03-04T07:30:00Z"), 11005) is None
    # A new lockout timestamp fires again
    new_lockout = {"na": "2025-03-11T08:00:00Z", "eu": "2025-03-11T09:00:00Z"}
    assert cog._lockout_fire_target(config, new_lockout, _at("2025-03-11T07:30:00Z"), 11005) == ("na", "2025-03-11T08:00:00Z")


@pytest.mark.asyncio
async def test_lockout_skips(mock_bot_alliance):
    cog = AllianceMatchupCog(mock_bot_alliance)
    cog._poster_loop.cancel()
    now = _at("2025-03-04T07:30:00Z")
    # disabled
    assert cog._lockout_fire_target(_lockout_config(wvw_lockout_enabled=False), LOCKOUT, now, 11005) is None
    # no channel
    assert cog._lockout_fire_target(_lockout_config(wvw_lockout_channel_id=None), LOCKOUT, now, 11005) is None
    # region unresolvable (auto + no derivable home world)
    assert cog._lockout_fire_target(_lockout_config(), LOCKOUT, now, None) is None
