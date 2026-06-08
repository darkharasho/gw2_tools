# Alliance Relink Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poll the GW2 alliance spreadsheet and post a full roster announcement to the alliance channel whenever the configured guild's WvW server assignment changes.

**Architecture:** Add two fields to `GuildConfig`, then wire up three new methods and a `relink` subgroup of commands inside the existing `AllianceMatchupCog`. Detection runs inside the existing `_poster_loop`; no new background task needed. All sheet scanning reuses the existing `_resolve_prediction_world_from_sheet` helper.

**Tech Stack:** Python 3.10+, discord.py app_commands, aiohttp (existing session), pytest + pytest-asyncio

---

### Task 1: Add `alliance_relink_enabled` and `alliance_relink_last_server` to `GuildConfig`

**Files:**
- Modify: `axitools/storage.py:207-243`
- Test: `tests/test_storage.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
from axitools.storage import GuildConfig

def test_guild_config_relink_defaults():
    config = GuildConfig.default()
    assert config.alliance_relink_enabled is False
    assert config.alliance_relink_last_server is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_storage.py::test_guild_config_relink_defaults -v
```

Expected: `AttributeError: 'GuildConfig' object has no attribute 'alliance_relink_enabled'`

- [ ] **Step 3: Add the two fields to `GuildConfig`**

In `axitools/storage.py`, insert after line 230 (`alliance_current_day: Optional[int] = None`):

```python
    alliance_relink_enabled: bool = False
    alliance_relink_last_server: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_storage.py::test_guild_config_relink_defaults -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/storage.py tests/test_storage.py
git commit -m "feat: add alliance_relink_enabled and alliance_relink_last_server to GuildConfig"
```

---

### Task 2: Add `_find_relink_server_tab` helper to `AllianceMatchupCog`

This method reuses `_resolve_prediction_world_from_sheet` (which already scans all tabs) and maps the returned world ID back to its sheet tab name. The tab name is what we store in `alliance_relink_last_server` for comparison on each poll cycle.

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (add method after `_resolve_prediction_world_from_sheet` ~line 678)
- Test: `tests/test_cogs_wvw_alliance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cogs_wvw_alliance.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_find_relink_server_tab_returns_tab_name tests/test_cogs_wvw_alliance.py::test_find_relink_server_tab_returns_none_when_not_found -v
```

Expected: `AttributeError: '_find_relink_server_tab'`

- [ ] **Step 3: Add `_find_relink_server_tab` to `AllianceMatchupCog`**

Insert after the `_resolve_prediction_world_from_sheet` method (~line 678 in `axitools/cogs/wvw_alliance.py`):

```python
    async def _find_relink_server_tab(self, config: GuildConfig) -> Optional[str]:
        world_id = await self._resolve_prediction_world_from_sheet(config)
        if world_id is None:
            return None
        return WVW_ALLIANCE_SHEET_TABS.get(world_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_find_relink_server_tab_returns_tab_name tests/test_cogs_wvw_alliance.py::test_find_relink_server_tab_returns_none_when_not_found -v
```

Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat: add _find_relink_server_tab helper to AllianceMatchupCog"
```

---

### Task 3: Add `_build_relink_embed` and `_check_relink` methods

`_build_relink_embed` constructs the announcement embed. `_check_relink` contains the detection logic: compare the current tab against the stored one, post if changed, always update the stored value.

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py`
- Test: `tests/test_cogs_wvw_alliance.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cogs_wvw_alliance.py
def test_build_relink_embed_contains_server_and_roster(mock_bot_alliance):
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_build_relink_embed_contains_server_and_roster tests/test_cogs_wvw_alliance.py::test_check_relink_posts_when_server_changes tests/test_cogs_wvw_alliance.py::test_check_relink_no_post_when_server_unchanged tests/test_cogs_wvw_alliance.py::test_check_relink_no_post_on_first_detection -v
```

Expected: `AttributeError` for both missing methods

- [ ] **Step 3: Add `_build_relink_embed` to `AllianceMatchupCog`**

Insert after `_resolve_sheet_url` (~line 737 in `axitools/cogs/wvw_alliance.py`):

```python
    def _build_relink_embed(self, *, server_name: str, roster: AllianceRoster, world_id: int) -> discord.Embed:
        embed = discord.Embed(
            description="# 🔗 New Server Link Announced",
            color=BRAND_COLOUR,
        )
        embed.add_field(name="Server", value=server_name, inline=False)
        roster_text = self._trim_field_value(self._format_alliance_list(roster))
        embed.add_field(name="Roster", value=roster_text or "No roster data.", inline=False)
        sheet_url = self._resolve_sheet_url([world_id])
        if sheet_url:
            embed.set_footer(text=sheet_url)
        return embed
```

- [ ] **Step 4: Add `_check_relink` to `AllianceMatchupCog`**

Insert after `_build_relink_embed`:

```python
    async def _check_relink(
        self, guild: discord.Guild, channel: discord.TextChannel, config: GuildConfig
    ) -> None:
        tab = await self._find_relink_server_tab(config)
        if tab is None:
            LOGGER.warning("Relink check: guild not found in any sheet tab for Discord guild %s", guild.id)
            return
        if config.alliance_relink_last_server == tab:
            return
        if config.alliance_relink_last_server is not None:
            world_id = next(
                (wid for wid, name in WVW_ALLIANCE_SHEET_TABS.items() if name == tab), None
            )
            if world_id is not None:
                roster = await self._fetch_alliances(tab)
                server_name = WVW_SERVER_NAMES.get(world_id, tab)
                embed = self._build_relink_embed(server_name=server_name, roster=roster, world_id=world_id)
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    LOGGER.warning(
                        "Failed to post relink announcement for Discord guild %s", guild.id, exc_info=True
                    )
                    return
        config.alliance_relink_last_server = tab
        self.bot.save_config(guild.id, config)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_build_relink_embed_contains_server_and_roster tests/test_cogs_wvw_alliance.py::test_check_relink_posts_when_server_changes tests/test_cogs_wvw_alliance.py::test_check_relink_no_post_when_server_unchanged tests/test_cogs_wvw_alliance.py::test_check_relink_no_post_on_first_detection -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat: add _build_relink_embed and _check_relink to AllianceMatchupCog"
```

---

### Task 4: Wire `_check_relink` into `_poster_loop`

The existing loop already iterates guilds, resolves the channel, and checks config. We add the relink call at the end of each iteration when the flag is set.

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py:980-1005`

The `_poster_loop` body currently ends at (~line 1005):

```python
            if now.weekday() == current_day and now_time >= current_time:
                if not self._already_posted(config.alliance_last_actual_at, now):
                    LOGGER.info("Posting alliance current matchup for guild %s", guild.id)
                    await self._post_matchup(guild=guild, channel=channel, config=config, prediction=False)
```

- [ ] **Step 1: Add the relink call after existing matchup checks**

Replace the end of the `_poster_loop` body so it reads:

```python
            if now.weekday() == prediction_day:
                if now_time >= prediction_time:
                    if not self._already_posted(config.alliance_last_prediction_at, now):
                        LOGGER.info("Posting alliance prediction matchup for guild %s", guild.id)
                        await self._post_matchup(guild=guild, channel=channel, config=config, prediction=True)
            if now.weekday() == current_day and now_time >= current_time:
                if not self._already_posted(config.alliance_last_actual_at, now):
                    LOGGER.info("Posting alliance current matchup for guild %s", guild.id)
                    await self._post_matchup(guild=guild, channel=channel, config=config, prediction=False)
            if config.alliance_relink_enabled:
                await self._check_relink(guild, channel, config)
```

- [ ] **Step 2: Run the full alliance test suite**

```bash
pytest tests/test_cogs_wvw_alliance.py -v
```

Expected: all existing + new tests PASS

- [ ] **Step 3: Commit**

```bash
git add axitools/cogs/wvw_alliance.py
git commit -m "feat: call _check_relink in _poster_loop when relink is enabled"
```

---

### Task 5: Add `/alliance relink enable` and `/alliance relink disable` commands

Add a `relink_group` class-level `app_commands.Group` to `AllianceMatchupCog`. The `enable` command primes `alliance_relink_last_server` so the first poll doesn't false-trigger.

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (add group + two commands before `setup`)
- Test: `tests/test_cogs_wvw_alliance.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cogs_wvw_alliance.py
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

    await cog.relink_enable(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    assert "setguild" in (args[0] if args else kwargs.get("content", ""))


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

    await cog.relink_enable(interaction)

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

    await cog.relink_disable(interaction)

    assert config.alliance_relink_enabled is False
    assert config.alliance_relink_last_server == "HoJ"  # preserved
    mock_bot_alliance.save_config.assert_called_once_with(1, config)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_relink_enable_requires_guild_and_channel tests/test_cogs_wvw_alliance.py::test_relink_enable_primes_last_server tests/test_cogs_wvw_alliance.py::test_relink_disable_clears_flag -v
```

Expected: `AttributeError: 'AllianceMatchupCog' object has no attribute 'relink_enable'`

- [ ] **Step 3: Add the `relink_group` and commands to `AllianceMatchupCog`**

In `axitools/cogs/wvw_alliance.py`, add the group as a class attribute immediately after the class declaration line (`class AllianceMatchupCog(commands.GroupCog, name="alliance"):`):

```python
    relink_group = app_commands.Group(name="relink", description="Configure server link announcements.")
```

Then add the two command methods inside the class, before the `setup` function at the bottom:

```python
    @relink_group.command(name="enable", description="Enable server link announcements when the guild's server changes.")
    async def relink_enable(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        if not config.alliance_guild_id or not config.alliance_channel_id:
            await interaction.response.send_message(
                "Set the alliance guild (`/alliance setguild`) and channel (`/alliance setchannel`) first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        tab = await self._find_relink_server_tab(config)
        config.alliance_relink_enabled = True
        config.alliance_relink_last_server = tab
        self.bot.save_config(interaction.guild.id, config)
        if tab:
            world_id = next(
                (wid for wid, name in WVW_ALLIANCE_SHEET_TABS.items() if name == tab), None
            )
            server_name = WVW_SERVER_NAMES.get(world_id, tab) if world_id else tab
            await interaction.followup.send(
                f"Relink announcements enabled. Current server: **{server_name}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Relink announcements enabled. Guild not currently found in sheet — will announce when found.",
                ephemeral=True,
            )

    @relink_group.command(name="disable", description="Disable server link announcements.")
    async def relink_disable(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        config.alliance_relink_enabled = False
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message("Relink announcements disabled.", ephemeral=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_relink_enable_requires_guild_and_channel tests/test_cogs_wvw_alliance.py::test_relink_enable_primes_last_server tests/test_cogs_wvw_alliance.py::test_relink_disable_clears_flag -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat: add /alliance relink enable and disable commands"
```

---

### Task 6: Update `/alliance status` to show relink state

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py:1071-1107`
- Test: `tests/test_cogs_wvw_alliance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cogs_wvw_alliance.py
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

    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.guild.get_channel = MagicMock(return_value=None)
    interaction.response.send_message = AsyncMock()

    await cog.status(interaction)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    field_names = [f.name for f in embed.fields]
    assert "Relink Announcements" in field_names
    relink_field = next(f for f in embed.fields if f.name == "Relink Announcements")
    assert "Enabled" in relink_field.value
    assert "HoJ" in relink_field.value
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_status_shows_relink_state -v
```

Expected: `AssertionError` — "Relink Announcements" not in field names

- [ ] **Step 3: Add relink field to the `status` command embed**

In `axitools/cogs/wvw_alliance.py`, find the `status` method. After the existing `embed.add_field(name="Post Times (PST)", ...)` call, add:

```python
        relink_status = "Enabled" if config.alliance_relink_enabled else "Disabled"
        relink_server = config.alliance_relink_last_server or "Not yet detected"
        embed.add_field(
            name="Relink Announcements",
            value=f"{relink_status} — last server: **{relink_server}**",
            inline=False,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_cogs_wvw_alliance.py::test_status_shows_relink_state -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_cogs_wvw_alliance.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat: show relink state in /alliance status"
```
