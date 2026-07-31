# WvW Lockout Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable Discord reminder that warns a channel a set time before the next WvW team-assignment lockout, using the GW2 `/v2/wvw/timers/lockout` API.

**Architecture:** Extend the existing `AllianceMatchupCog` (`axitools/cogs/wvw_alliance.py`) rather than adding a new cog — reuse its aiohttp session and 5-minute `_poster_loop`. Config lives as new plain-JSON `GuildConfig` fields (no DB migration). The fire decision is a pure, unit-tested helper; Discord posting and the loop branch are thin wrappers around it. Config is editable via new `/alliance lockout ...` slash commands and the existing alliance web API.

**Tech Stack:** Python, discord.py (`app_commands`, `discord.ext.tasks`), aiohttp, aiohttp web (dashboard API), pytest + `unittest.mock`.

## Global Constraints

- Test runner: `pytest`, limit parallelism — `pytest -p no:cacheprovider` is fine; if using xdist keep workers ≤ 2 (machine memory limit).
- Lead time is stored in **minutes** (`wvw_lockout_lead_minutes`, default `1440` = 24h), clamped to a minimum of **5** minutes.
- Region values are exactly `"na"` or `"eu"` (lowercase), or `None` meaning auto-derive.
- World-ID → region: `world_id // 1000 == 11` → `"na"`, `== 12` → `"eu"`, else unresolvable. World names live in `axitools/constants.py` `WVW_SERVER_NAMES`.
- Dedupe key is the lockout timestamp string itself (`wvw_lockout_last_fired_for`); stamp it **only after a successful post**.
- New `GuildConfig` fields MUST get read-side normalization in `Storage.get_config`, or `GuildConfig(**payload)` raises on the unknown key.
- Endpoint: `GET https://api.guildwars2.com/v2/wvw/timers/lockout`, no auth, returns `{"na": "<iso>", "eu": "<iso>"}`.
- Follow existing patterns: `self.bot.get_config` / `self.bot.save_config`, `self.bot.ensure_authorised(interaction)`, `_parse_timestamp`, `_resolve_channel`, `BRAND_COLOUR`.

---

### Task 1: Config fields + storage normalization

**Files:**
- Modify: `axitools/storage.py` (`GuildConfig` dataclass ~line 301; `get_config` normalization block ~line 1962, just before `config = GuildConfig(**payload)` at line 1969)
- Test: `tests/test_config_status.py` (add cases; if a more specific storage test file is preferred, `tests/test_storage.py`)

**Interfaces:**
- Produces: new `GuildConfig` fields — `wvw_lockout_enabled: bool`, `wvw_lockout_channel_id: Optional[int]`, `wvw_lockout_lead_minutes: int`, `wvw_lockout_region: Optional[str]`, `wvw_lockout_last_fired_for: Optional[str]`. All later tasks read/write these.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_status.py`:

```python
def test_lockout_config_defaults(tmp_path):
    from axitools.storage import Storage
    storage = Storage(base_dir=tmp_path)
    config = storage.get_config("123")
    assert config.wvw_lockout_enabled is False
    assert config.wvw_lockout_channel_id is None
    assert config.wvw_lockout_lead_minutes == 1440
    assert config.wvw_lockout_region is None
    assert config.wvw_lockout_last_fired_for is None


def test_lockout_config_normalizes(tmp_path):
    from axitools.storage import Storage
    storage = Storage(base_dir=tmp_path)
    config = storage.get_config("123")
    config.wvw_lockout_enabled = True
    config.wvw_lockout_channel_id = 555
    config.wvw_lockout_lead_minutes = 2  # below the 5-minute minimum
    config.wvw_lockout_region = "NA"     # wrong case
    config.wvw_lockout_last_fired_for = "2025-03-04T07:59:00Z"
    storage.save_config("123", config)

    reloaded = storage.get_config("123")
    assert reloaded.wvw_lockout_enabled is True
    assert reloaded.wvw_lockout_channel_id == 555
    assert reloaded.wvw_lockout_lead_minutes == 5      # clamped up
    assert reloaded.wvw_lockout_region == "na"         # lowercased
    assert reloaded.wvw_lockout_last_fired_for == "2025-03-04T07:59:00Z"


def test_lockout_config_rejects_bad_region(tmp_path):
    from axitools.storage import Storage
    storage = Storage(base_dir=tmp_path)
    config = storage.get_config("123")
    config.wvw_lockout_region = "asia"
    storage.save_config("123", config)
    assert storage.get_config("123").wvw_lockout_region is None
```

> Note: check how `Storage` is constructed in the existing tests in this file and match it (the ctor arg may be `base_dir=` or positional). Adjust the three `Storage(...)` calls to match the established pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_status.py -k lockout -v`
Expected: FAIL — `AttributeError`/`TypeError` on unknown `wvw_lockout_*` field.

- [ ] **Step 3: Add the dataclass fields**

In `axitools/storage.py`, after `alliance_relink_last_server: Optional[str] = None` (line 302):

```python
    wvw_lockout_enabled: bool = False
    wvw_lockout_channel_id: Optional[int] = None
    wvw_lockout_lead_minutes: int = 1440
    wvw_lockout_region: Optional[str] = None
    wvw_lockout_last_fired_for: Optional[str] = None
```

- [ ] **Step 4: Add normalization in `get_config`**

In `axitools/storage.py`, immediately before `config = GuildConfig(**payload)` (line 1969):

```python
        lockout_enabled = payload.get("wvw_lockout_enabled")
        payload["wvw_lockout_enabled"] = (
            bool(lockout_enabled) if isinstance(lockout_enabled, bool) else False
        )
        lockout_channel_id = payload.get("wvw_lockout_channel_id")
        if isinstance(lockout_channel_id, int):
            payload["wvw_lockout_channel_id"] = lockout_channel_id
        elif isinstance(lockout_channel_id, str):
            try:
                payload["wvw_lockout_channel_id"] = int(lockout_channel_id)
            except ValueError:
                payload["wvw_lockout_channel_id"] = None
        else:
            payload["wvw_lockout_channel_id"] = None
        lockout_lead = payload.get("wvw_lockout_lead_minutes")
        if isinstance(lockout_lead, bool):
            lockout_lead = None
        elif isinstance(lockout_lead, str):
            try:
                lockout_lead = int(lockout_lead)
            except ValueError:
                lockout_lead = None
        if isinstance(lockout_lead, int):
            payload["wvw_lockout_lead_minutes"] = max(5, lockout_lead)
        else:
            payload["wvw_lockout_lead_minutes"] = 1440
        lockout_region = payload.get("wvw_lockout_region")
        if isinstance(lockout_region, str) and lockout_region.strip().lower() in ("na", "eu"):
            payload["wvw_lockout_region"] = lockout_region.strip().lower()
        else:
            payload["wvw_lockout_region"] = None
        lockout_last = payload.get("wvw_lockout_last_fired_for")
        if isinstance(lockout_last, str) and lockout_last.strip():
            payload["wvw_lockout_last_fired_for"] = lockout_last.strip()
        else:
            payload["wvw_lockout_last_fired_for"] = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_config_status.py -k lockout -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full config test module to check for regressions**

Run: `pytest tests/test_config_status.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add axitools/storage.py tests/test_config_status.py
git commit -m "feat(wvw): add lockout reminder config fields + normalization"
```

---

### Task 2: Lockout fetch + parse helper

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (add constant near the other `GW2_*` constants ~line 34; add `_fetch_lockout` method near `_fetch_matches` ~line 464)
- Test: `tests/test_cogs_wvw_alliance.py`

**Interfaces:**
- Consumes: existing `self._fetch_json(url)` (raises `ValueError` on failure).
- Produces: `GW2_WVW_LOCKOUT_URL: str`; `async def _fetch_lockout(self) -> Optional[Dict[str, str]]` returning `{"na": "<iso>", "eu": "<iso>"}` with only the keys present/valid, or `None` if nothing valid.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_wvw_alliance.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_wvw_alliance.py -k fetch_lockout -v`
Expected: FAIL — `AttributeError: _fetch_lockout`.

- [ ] **Step 3: Add the constant**

In `axitools/cogs/wvw_alliance.py`, after `GW2_MATCHES_URL = "https://api.guildwars2.com/v2/wvw/matches"` (line 34):

```python
GW2_WVW_LOCKOUT_URL = "https://api.guildwars2.com/v2/wvw/timers/lockout"
```

- [ ] **Step 4: Add the `_fetch_lockout` method**

In `axitools/cogs/wvw_alliance.py`, near `_fetch_matches` (~line 464):

```python
    async def _fetch_lockout(self) -> Optional[Dict[str, str]]:
        try:
            payload = await self._fetch_json(GW2_WVW_LOCKOUT_URL)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        result: Dict[str, str] = {}
        for region in ("na", "eu"):
            value = payload.get(region)
            if isinstance(value, str) and value.strip():
                result[region] = value.strip()
        return result or None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_cogs_wvw_alliance.py -k fetch_lockout -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat(wvw): fetch + parse WvW lockout timer endpoint"
```

---

### Task 3: Region derivation + fire-decision helpers

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (add two methods near `_already_posted` ~line 1064)
- Test: `tests/test_cogs_wvw_alliance.py`

**Interfaces:**
- Consumes: `self._parse_timestamp` (existing), `GuildConfig` lockout fields (Task 1), `_fetch_lockout` output shape (Task 2).
- Produces:
  - `@staticmethod _derive_lockout_region(world_id: Optional[int]) -> Optional[str]`
  - `_lockout_fire_target(self, config: GuildConfig, lockout: Dict[str, str], now: datetime, home_world_id: Optional[int]) -> Optional[Tuple[str, str]]` — returns `(region, target_iso)` when a post is due now, else `None`. Task 4's loop consumes this tuple.
- Note: `Tuple` must be imported — add it to the `from typing import ...` line (line 12) if absent.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_wvw_alliance.py`:

```python
def _lockout_config(**overrides):
    base = dict(
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_wvw_alliance.py -k lockout -v`
Expected: FAIL — `AttributeError` on `_derive_lockout_region` / `_lockout_fire_target`.

- [ ] **Step 3: Ensure `Tuple` is imported**

In `axitools/cogs/wvw_alliance.py` line 12, if `Tuple` is not already in the typing import, change:

```python
from typing import Dict, List, Optional, Sequence
```
to:
```python
from typing import Dict, List, Optional, Sequence, Tuple
```

- [ ] **Step 4: Add the helpers**

In `axitools/cogs/wvw_alliance.py`, after `_already_posted` (~line 1068):

```python
    @staticmethod
    def _derive_lockout_region(world_id: Optional[int]) -> Optional[str]:
        if not isinstance(world_id, int):
            return None
        bucket = world_id // 1000
        if bucket == 11:
            return "na"
        if bucket == 12:
            return "eu"
        return None

    def _lockout_fire_target(
        self,
        config: GuildConfig,
        lockout: Dict[str, str],
        now: datetime,
        home_world_id: Optional[int],
    ) -> Optional[Tuple[str, str]]:
        if not config.wvw_lockout_enabled or not config.wvw_lockout_channel_id:
            return None
        region = config.wvw_lockout_region or self._derive_lockout_region(home_world_id)
        if region not in ("na", "eu"):
            return None
        target_iso = lockout.get(region)
        if not target_iso:
            return None
        target = self._parse_timestamp(target_iso)
        if not target:
            return None
        if config.wvw_lockout_last_fired_for == target_iso:
            return None
        lead = timedelta(minutes=config.wvw_lockout_lead_minutes)
        now_utc = now.astimezone(timezone.utc)
        if target - lead <= now_utc < target:
            return region, target_iso
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_cogs_wvw_alliance.py -k lockout -v`
Expected: PASS (all lockout decision tests + Task 2 fetch tests).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat(wvw): lockout region derivation + fire-decision helper"
```

---

### Task 4: Discord wiring — post, loop branch, commands, status

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (add `lockout_group` on the cog class ~line 284; add `_post_lockout` near `_post_matchup`; add a branch in `_poster_loop` ~line 1096; add lockout commands near the `relink` commands ~line 1305; extend the `status` command embed ~line 1163)
- Test: `tests/test_cogs_wvw_alliance.py` (command surface)

**Interfaces:**
- Consumes: `_lockout_fire_target` (Task 3), `_fetch_lockout` (Task 2), `_resolve_channel`, `self.bot.get_config/save_config`, `self.bot.ensure_authorised`, `BRAND_COLOUR`, `config.alliance_server_id` (cached home world).
- Produces: slash commands `alliance lockout enable|disable|channel|lead|region`; `_post_lockout(...) -> bool`.

- [ ] **Step 1: Write the failing test (command surface)**

Extend the `expected` set in `test_alliance_command_surface` in `tests/test_cogs_wvw_alliance.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_wvw_alliance.py -k command_surface -v`
Expected: FAIL — assertion `expected <= qualified_names` false (lockout commands missing).

- [ ] **Step 3: Declare the command group**

In `axitools/cogs/wvw_alliance.py`, alongside the existing group declarations (after line 284):

```python
    lockout_group = app_commands.Group(name="lockout", description="Configure WvW team-lockout reminders.")
```

- [ ] **Step 4: Add `_post_lockout`**

In `axitools/cogs/wvw_alliance.py`, near `_post_matchup`:

```python
    async def _post_lockout(
        self,
        *,
        channel: discord.TextChannel,
        region: str,
        target_iso: str,
    ) -> bool:  # pragma: no cover - requires Discord
        target = self._parse_timestamp(target_iso)
        if not target:
            return False
        unix = int(target.timestamp())
        embed = discord.Embed(
            title="⚔️ WvW Team Lockout Incoming",
            description=(
                f"WvW team assignments lock **<t:{unix}:R>** (<t:{unix}:F>).\n"
                "Roster and server transfer changes are locked at that time."
            ),
            colour=BRAND_COLOUR,
        )
        embed.add_field(name="Region", value=region.upper(), inline=True)
        await channel.send(embed=embed)
        return True
```

- [ ] **Step 5: Add the loop branch**

In `_poster_loop`, fetch the lockout once before the guild loop. After `now_time = now.time()...` (line 1075) add:

```python
        lockout = await self._fetch_lockout()
```

Then inside the `for guild in self.bot.guilds:` loop, after the relink block (line 1097), add:

```python
            if lockout:
                fire = self._lockout_fire_target(config, lockout, now, config.alliance_server_id)
                if fire:
                    region, target_iso = fire
                    lockout_channel = await self._resolve_channel(guild, config.wvw_lockout_channel_id)
                    if lockout_channel:
                        LOGGER.info("Posting WvW lockout reminder for guild %s", guild.id)
                        posted = await self._post_lockout(
                            channel=lockout_channel, region=region, target_iso=target_iso
                        )
                        if posted:
                            config.wvw_lockout_last_fired_for = target_iso
                            self.bot.save_config(guild.id, config)
```

> Note: the current loop `continue`s early when `alliance_channel_id`/`alliance_guild_id` are unset (line 1078). The lockout reminder must work even when the alliance matchup poster is not configured. Restructure so the lockout branch is reached regardless: move the lockout handling to run before that `continue`, OR change the guard so it only skips the matchup-specific blocks. Simplest: replace the early `continue` with wrapping the matchup/relink blocks in `if config.alliance_channel_id and config.alliance_guild_id:` and place the lockout branch outside that `if`. Keep `channel`/`prediction_time`/etc. resolution inside the matchup `if` so they are not computed when alliance is unconfigured.

- [ ] **Step 6: Add the lockout commands**

In `axitools/cogs/wvw_alliance.py`, after `relink_disable` (~line 1305):

```python
    @lockout_group.command(name="channel", description="Set the channel for WvW lockout reminders.")
    async def lockout_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        config.wvw_lockout_channel_id = channel.id
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message(
            f"WvW lockout reminders will be sent to {channel.mention}.", ephemeral=True
        )

    @lockout_group.command(name="enable", description="Enable WvW lockout reminders.")
    async def lockout_enable(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        if not config.wvw_lockout_channel_id:
            await interaction.response.send_message(
                "Set the lockout channel first with `/alliance lockout channel`.", ephemeral=True
            )
            return
        config.wvw_lockout_enabled = True
        self.bot.save_config(interaction.guild.id, config)
        hours = config.wvw_lockout_lead_minutes / 60
        await interaction.response.send_message(
            f"WvW lockout reminders enabled ({hours:g}h before lockout).", ephemeral=True
        )

    @lockout_group.command(name="disable", description="Disable WvW lockout reminders.")
    async def lockout_disable(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        config.wvw_lockout_enabled = False
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message("WvW lockout reminders disabled.", ephemeral=True)

    @lockout_group.command(name="lead", description="Set how many hours before the lockout to remind.")
    async def lockout_lead(self, interaction: discord.Interaction, hours: float) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        if hours <= 0:
            await interaction.response.send_message("Hours must be greater than 0.", ephemeral=True)
            return
        config = self.bot.get_config(interaction.guild.id)
        config.wvw_lockout_lead_minutes = max(5, round(hours * 60))
        self.bot.save_config(interaction.guild.id, config)
        applied = config.wvw_lockout_lead_minutes / 60
        await interaction.response.send_message(
            f"WvW lockout reminders will fire {applied:g}h before lockout.", ephemeral=True
        )

    @lockout_group.command(name="region", description="Set the WvW region for lockout reminders.")
    @app_commands.choices(
        region=[
            app_commands.Choice(name="Auto (from home world)", value="auto"),
            app_commands.Choice(name="North America", value="na"),
            app_commands.Choice(name="Europe", value="eu"),
        ]
    )
    async def lockout_region(
        self, interaction: discord.Interaction, region: app_commands.Choice[str]
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        assert interaction.guild is not None
        config = self.bot.get_config(interaction.guild.id)
        config.wvw_lockout_region = None if region.value == "auto" else region.value
        self.bot.save_config(interaction.guild.id, config)
        label = "auto (from home world)" if region.value == "auto" else region.value.upper()
        await interaction.response.send_message(
            f"WvW lockout region set to **{label}**.", ephemeral=True
        )
```

- [ ] **Step 7: Extend the `status` embed**

In the `status` command (~line 1163), after the existing alliance fields are added to the status embed/message, add a lockout summary. Locate where fields are appended to the status embed and add:

```python
        lockout_state = "on" if config.wvw_lockout_enabled else "off"
        lockout_channel = (
            interaction.guild.get_channel(config.wvw_lockout_channel_id)
            if config.wvw_lockout_channel_id
            else None
        )
        lockout_channel_label = lockout_channel.mention if lockout_channel else "Not set"
        lockout_region_label = (config.wvw_lockout_region or "auto").upper()
        lockout_lead_label = f"{config.wvw_lockout_lead_minutes / 60:g}h"
        embed.add_field(
            name="Lockout reminder",
            value=(
                f"State: **{lockout_state}**\n"
                f"Channel: {lockout_channel_label}\n"
                f"Region: {lockout_region_label}\n"
                f"Lead: {lockout_lead_label}"
            ),
            inline=False,
        )
```

> Note: read the `status` body first (lines 1163–~1230) to confirm the embed variable name (`embed`) and that it uses `embed.add_field`. If `status` builds a plain text message instead of an embed, append an equivalent text block in the same style rather than calling `add_field`.

- [ ] **Step 8: Run the command-surface test**

Run: `pytest tests/test_cogs_wvw_alliance.py -k command_surface -v`
Expected: PASS.

- [ ] **Step 9: Run the full cog test module**

Run: `pytest tests/test_cogs_wvw_alliance.py -v`
Expected: PASS (no regressions in existing matchup/relink tests — verify the `_poster_loop` restructure in Step 5 did not break existing behavior).

- [ ] **Step 10: Commit**

```bash
git add axitools/cogs/wvw_alliance.py tests/test_cogs_wvw_alliance.py
git commit -m "feat(wvw): lockout reminder commands, poster-loop branch, status"
```

---

### Task 5: Web API fields

**Files:**
- Modify: `axitools/api/server.py` (`_alliance_to_json` ~line 1227; `_handle_alliance_put` ~line 1248)
- Test: `tests/` — mirror an existing alliance API test (search for `alliance` in `tests/test_api_*.py`); if none targets alliance, add to the closest API test module.

**Interfaces:**
- Consumes: `GuildConfig` lockout fields (Task 1), existing `_sid`, `_channel_in_guild`, `_merge_save`, `_write_lock` helpers.
- Produces: the alliance GET/PUT JSON now includes `lockout_enabled`, `lockout_channel_id`, `lockout_lead_minutes`, `lockout_region`.

- [ ] **Step 1: Write the failing test**

First locate the existing alliance API test pattern:

Run: `grep -rn "alliance" tests/ | grep -i "put\|get\|_handle\|json" | head`

Add a test mirroring that pattern (adjust fixture/client setup to match the existing API tests in the repo):

```python
@pytest.mark.asyncio
async def test_alliance_put_sets_lockout_fields(alliance_api_client):  # match existing fixture name
    client, bot, gid = alliance_api_client
    resp = await client.put(
        f"/guilds/{gid}/alliance",
        json={
            "lockout_enabled": True,
            "lockout_lead_minutes": 720,
            "lockout_region": "eu",
        },
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["lockout_enabled"] is True
    assert data["lockout_lead_minutes"] == 720
    assert data["lockout_region"] == "eu"

    config = bot.storage.get_config(gid)
    assert config.wvw_lockout_enabled is True
    assert config.wvw_lockout_lead_minutes == 720
    assert config.wvw_lockout_region == "eu"


@pytest.mark.asyncio
async def test_alliance_put_rejects_bad_lockout_region(alliance_api_client):
    client, bot, gid = alliance_api_client
    resp = await client.put(f"/guilds/{gid}/alliance", json={"lockout_region": "asia"})
    assert resp.status == 400
```

> Note: if the repo has no alliance-API fixture, model the test on the nearest existing `tests/test_api_*.py` (how it builds the aiohttp test client, seeds a bot/storage, and resolves a guild). Reuse that harness exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ -k lockout -v` (the new API tests)
Expected: FAIL — response lacks `lockout_*` keys / does not reject bad region.

- [ ] **Step 3: Extend `_alliance_to_json`**

In `axitools/api/server.py`, add to the returned dict in `_alliance_to_json` (after `"relink_enabled"`):

```python
        "lockout_enabled": config.wvw_lockout_enabled,
        "lockout_channel_id": _sid(config.wvw_lockout_channel_id),
        "lockout_lead_minutes": config.wvw_lockout_lead_minutes,
        "lockout_region": config.wvw_lockout_region,
```

- [ ] **Step 4: Extend `_handle_alliance_put`**

In `axitools/api/server.py`, after the `relink_enabled` block (line 1322) and before `result: list = []` (line 1324):

```python
    if "lockout_enabled" in body:
        if not isinstance(body["lockout_enabled"], bool):
            return web.json_response(
                {"error": "lockout_enabled must be a boolean"}, status=400
            )
        updates["wvw_lockout_enabled"] = body["lockout_enabled"]

    if "lockout_channel_id" in body:
        if body["lockout_channel_id"] is None:
            updates["wvw_lockout_channel_id"] = None
        else:
            channel_id, channel_err = _channel_in_guild(guild, body["lockout_channel_id"])
            if channel_err is not None:
                return channel_err
            updates["wvw_lockout_channel_id"] = channel_id

    if "lockout_lead_minutes" in body:
        raw = body["lockout_lead_minutes"]
        if isinstance(raw, bool) or not isinstance(raw, int):
            return web.json_response(
                {"error": "lockout_lead_minutes must be an integer"}, status=400
            )
        updates["wvw_lockout_lead_minutes"] = max(5, raw)

    if "lockout_region" in body:
        raw = body["lockout_region"]
        if raw is None:
            updates["wvw_lockout_region"] = None
        elif isinstance(raw, str) and raw.strip().lower() in ("na", "eu"):
            updates["wvw_lockout_region"] = raw.strip().lower()
        else:
            return web.json_response(
                {"error": "lockout_region must be 'na', 'eu', or null"}, status=400
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/ -k lockout -v`
Expected: PASS.

- [ ] **Step 6: Run the API test module for regressions**

Run: `pytest tests/ -k "alliance or api" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add axitools/api/server.py tests/
git commit -m "feat(wvw): expose lockout reminder fields in alliance web API"
```

---

### Final verification

- [ ] **Run the full suite**

Run: `pytest -q` (respect the ≤2-worker memory limit if using xdist)
Expected: all pass.

- [ ] **Manual smoke (optional, requires a running bot):** enable via `/alliance lockout channel #x`, `/alliance lockout enable`, set a large lead so it fires immediately (e.g. lead just under the time-to-next-lockout), confirm one embed posts and does not repeat on the next tick.
