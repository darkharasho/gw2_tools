# Audit Channel Blacklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let server admins exclude specific channels from audit logging so events tied to those channels are never posted to the audit feed.

**Architecture:** Add a per-guild `audit_channel_blacklist: List[int]` to `GuildConfig` (persisted in `config.json`). In `AuditCog`, a `_is_channel_blacklisted` helper guards every channel-tied listener and early-returns before logging. A new `/audit blacklist` subgroup (add/remove/list) manages the list, and the audit status embed surfaces it.

**Tech Stack:** Python, discord.py (`app_commands`), dataclasses, pytest.

---

## File Structure

- `axitools/storage.py` — add the dataclass field + load/save normalization. (modify)
- `axitools/cogs/audit.py` — add subgroup, commands, autocomplete, listener guards, status field. (modify)
- `tests/test_storage.py` — round-trip test for the new field. (modify)
- `tests/test_audit_blacklist.py` — helper, listener-suppression, and command tests. (create)
- `tests/test_config_status.py` — status-embed test for the blacklist field. (modify)

Notes for the implementer:
- `GuildConfig` is built via `GuildConfig(**payload)` in `StorageManager.get_config` (storage.py:1542), so every payload key must be a real field. Always set `payload["audit_channel_blacklist"]` during load.
- `save_config` serializes with `asdict(config)` (storage.py:1705).
- Existing `test_audit_cog_status_*` tests use a `MagicMock` config and assert `all(f.state == "ok"/"missing")`. The status code MUST guard the blacklist field with `isinstance(blacklist, list)` so a MagicMock attribute (truthy, non-list) is skipped and those tests keep passing.

---

## Task 1: Add `audit_channel_blacklist` to GuildConfig + persistence

**Files:**
- Modify: `axitools/storage.py` (dataclass ~line 217; load normalization ~line 1452; save normalization ~line 1693)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
def test_audit_channel_blacklist_round_trip(tmp_path):
    from axitools.storage import StorageManager, GuildConfig

    storage = StorageManager(tmp_path)
    guild_id = 424242
    config = GuildConfig.default()
    # mix of int, str-int, duplicate, and invalid entries
    config.audit_channel_blacklist = [111, "222", 111, "bad", 333]
    storage.save_config(guild_id, config)

    loaded = storage.get_config(guild_id)
    assert loaded.audit_channel_blacklist == [111, 222, 333]


def test_audit_channel_blacklist_defaults_empty(tmp_path):
    from axitools.storage import StorageManager

    storage = StorageManager(tmp_path)
    loaded = storage.get_config(999001)
    assert loaded.audit_channel_blacklist == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py::test_audit_channel_blacklist_round_trip -v`
Expected: FAIL — `GuildConfig` has no attribute `audit_channel_blacklist` / assertion error.

- [ ] **Step 3: Add the dataclass field**

In `axitools/storage.py`, in `class GuildConfig` after line 219 (`audit_gw2_guild_id: Optional[str] = None`):

```python
    audit_channel_blacklist: List[int] = field(default_factory=list)
```

- [ ] **Step 4: Normalize on load**

In `StorageManager.get_config`, immediately after the `audit_gw2_guild_id` block (after storage.py:1464, the `payload["audit_gw2_guild_id"] = None` line), add:

```python
        raw_blacklist = payload.get("audit_channel_blacklist")
        cleaned_blacklist: List[int] = []
        if isinstance(raw_blacklist, list):
            seen_blacklist: set[int] = set()
            for entry in raw_blacklist:
                if isinstance(entry, bool):
                    continue
                if isinstance(entry, int):
                    value = entry
                elif isinstance(entry, str):
                    try:
                        value = int(entry)
                    except ValueError:
                        continue
                else:
                    continue
                if value in seen_blacklist:
                    continue
                cleaned_blacklist.append(value)
                seen_blacklist.add(value)
        payload["audit_channel_blacklist"] = cleaned_blacklist
```

- [ ] **Step 5: Normalize on save**

In `StorageManager.save_config`, after the `audit_gw2_guild_id` block (after storage.py:1693), add:

```python
        if isinstance(config.audit_channel_blacklist, list):
            cleaned_blacklist: List[int] = []
            seen_blacklist: set[int] = set()
            for entry in config.audit_channel_blacklist:
                if isinstance(entry, bool):
                    continue
                if isinstance(entry, int):
                    value = entry
                elif isinstance(entry, str):
                    try:
                        value = int(entry)
                    except ValueError:
                        continue
                else:
                    continue
                if value in seen_blacklist:
                    continue
                cleaned_blacklist.append(value)
                seen_blacklist.add(value)
            config.audit_channel_blacklist = cleaned_blacklist
        else:
            config.audit_channel_blacklist = []
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -k audit_channel_blacklist -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add axitools/storage.py tests/test_storage.py
git commit -m "feat: add audit_channel_blacklist to GuildConfig with persistence"
```

---

## Task 2: Suppress channel-tied audit events for blacklisted channels

**Files:**
- Modify: `axitools/cogs/audit.py` (add helper; guard listeners at lines ~605, ~636, ~715, ~860, ~874, ~890)
- Test: `tests/test_audit_blacklist.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_blacklist.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from axitools.cogs.audit import AuditCog
from axitools.storage import GuildConfig


def _make_cog(blacklist):
    config = GuildConfig.default()
    config.audit_channel_blacklist = list(blacklist)
    cog = AuditCog.__new__(AuditCog)
    bot = MagicMock()
    bot.get_config.return_value = config
    cog.bot = bot
    return cog


def test_is_channel_blacklisted_true_and_false():
    cog = _make_cog([123])
    guild = MagicMock()
    guild.id = 1
    assert cog._is_channel_blacklisted(guild, 123) is True
    assert cog._is_channel_blacklisted(guild, 999) is False
    assert cog._is_channel_blacklisted(guild, None) is False


def test_message_delete_suppressed_when_blacklisted():
    cog = _make_cog([123])
    cog._log_discord_event = AsyncMock()
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.channel.id = 123
    asyncio.run(cog.on_message_delete(message))
    cog._log_discord_event.assert_not_called()


def test_voice_state_suppressed_when_before_channel_blacklisted():
    cog = _make_cog([55])
    cog._log_discord_event = AsyncMock()
    member = MagicMock()
    member.guild = MagicMock()
    member.guild.id = 1
    before = MagicMock()
    before.channel.id = 55
    after = MagicMock()
    after.channel.id = 77
    asyncio.run(cog.on_voice_state_update(member, before, after))
    cog._log_discord_event.assert_not_called()


def test_channel_delete_suppressed_when_blacklisted():
    cog = _make_cog([321])
    cog._log_discord_event = AsyncMock()
    cog._find_audit_entry_user = AsyncMock(return_value=None)
    channel = MagicMock()
    channel.guild = MagicMock()
    channel.guild.id = 1
    channel.id = 321
    asyncio.run(cog.on_guild_channel_delete(channel))
    cog._log_discord_event.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_audit_blacklist.py -v`
Expected: FAIL — `AuditCog` has no attribute `_is_channel_blacklisted`; listeners still call `_log_discord_event`.

- [ ] **Step 3: Add the helper**

In `axitools/cogs/audit.py`, add this method to `AuditCog` (place it just before `_audit_channel_id` at line 1039):

```python
    def _is_channel_blacklisted(
        self, guild: discord.Guild, channel_id: Optional[int]
    ) -> bool:
        if channel_id is None:
            return False
        config = self.bot.get_config(guild.id)
        return channel_id in config.audit_channel_blacklist
```

- [ ] **Step 4: Guard `on_message_delete`**

In `on_message_delete` (audit.py:605), after the existing guild check:

```python
        if message.guild is None:
            return
```

add:

```python
        if self._is_channel_blacklisted(message.guild, message.channel.id):
            return
```

- [ ] **Step 5: Guard `on_message_edit`**

In `on_message_edit` (audit.py:636), after:

```python
        if after.guild is None:
            return
```

add:

```python
        if self._is_channel_blacklisted(after.guild, after.channel.id):
            return
```

- [ ] **Step 6: Guard `on_voice_state_update`**

In `on_voice_state_update` (audit.py:715), after:

```python
        if member.guild is None:
            return
```

add:

```python
        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None
        if self._is_channel_blacklisted(
            member.guild, before_id
        ) or self._is_channel_blacklisted(member.guild, after_id):
            return
```

- [ ] **Step 7: Guard `on_guild_channel_create`, `on_guild_channel_delete`, `on_guild_channel_update`**

In `on_guild_channel_create` (audit.py:860), as the first line of the body:

```python
        if self._is_channel_blacklisted(channel.guild, channel.id):
            return
```

In `on_guild_channel_delete` (audit.py:874), as the first line of the body:

```python
        if self._is_channel_blacklisted(channel.guild, channel.id):
            return
```

In `on_guild_channel_update` (audit.py:890), as the first line of the body (before building `details`):

```python
        if self._is_channel_blacklisted(after.guild, after.id):
            return
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_audit_blacklist.py -v`
Expected: PASS (all four tests).

- [ ] **Step 9: Commit**

```bash
git add axitools/cogs/audit.py tests/test_audit_blacklist.py
git commit -m "feat: suppress audit events for blacklisted channels"
```

---

## Task 3: Add `/audit blacklist` add/remove/list commands

**Files:**
- Modify: `axitools/cogs/audit.py` (subgroup declaration ~line 112; new commands after the `gw2_guild` command at line 348)
- Test: `tests/test_audit_blacklist.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_blacklist.py`:

```python
def _make_command_cog(blacklist):
    config = GuildConfig.default()
    config.audit_channel_blacklist = list(blacklist)
    cog = AuditCog.__new__(AuditCog)
    bot = MagicMock()
    bot.get_config.return_value = config
    bot.save_config = MagicMock()
    bot.ensure_authorised = AsyncMock(return_value=True)
    cog.bot = bot
    return cog, config


def _make_interaction():
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.send_message = AsyncMock()
    return interaction


def test_blacklist_add_is_idempotent():
    cog, config = _make_command_cog([])
    interaction = _make_interaction()
    channel = MagicMock()
    channel.id = 700
    channel.mention = "<#700>"

    asyncio.run(cog.audit_blacklist_add_command.callback(cog, interaction, channel))
    asyncio.run(cog.audit_blacklist_add_command.callback(cog, interaction, channel))

    assert config.audit_channel_blacklist == [700]
    assert cog.bot.save_config.called


def test_blacklist_remove_by_channel():
    cog, config = _make_command_cog([700, 800])
    interaction = _make_interaction()
    channel = MagicMock()
    channel.id = 700

    asyncio.run(
        cog.audit_blacklist_remove_command.callback(cog, interaction, channel, None)
    )

    assert config.audit_channel_blacklist == [800]


def test_blacklist_remove_by_raw_id():
    cog, config = _make_command_cog([700, 800])
    interaction = _make_interaction()

    asyncio.run(
        cog.audit_blacklist_remove_command.callback(cog, interaction, None, "800")
    )

    assert config.audit_channel_blacklist == [700]


def test_blacklist_list_runs():
    cog, config = _make_command_cog([700])
    interaction = _make_interaction()
    asyncio.run(cog.audit_blacklist_list_command.callback(cog, interaction))
    interaction.response.send_message.assert_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_audit_blacklist.py -k blacklist_add or blacklist_remove or blacklist_list -v`
Expected: FAIL — command attributes do not exist.

- [ ] **Step 3: Declare the subgroup**

In `axitools/cogs/audit.py`, after the `audit_gw2_key` group declaration (line 112), add:

```python
    audit_blacklist = app_commands.Group(
        name="blacklist",
        description="Exclude channels from audit logging.",
        parent=audit,
    )
```

- [ ] **Step 4: Implement the commands**

In `axitools/cogs/audit.py`, after `audit_gw2_guild_command` (ends at line 348), add:

```python
    @audit_blacklist.command(
        name="add",
        description="Exclude a channel from audit logging.",
    )
    @app_commands.describe(channel="Channel to exclude from audit logging.")
    async def audit_blacklist_add_command(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        config = self.bot.get_config(interaction.guild.id)
        if channel.id in config.audit_channel_blacklist:
            await interaction.response.send_message(
                f"<#{channel.id}> is already excluded from audit logging.",
                ephemeral=True,
            )
            return
        config.audit_channel_blacklist.append(channel.id)
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message(
            f"<#{channel.id}> will no longer be audit logged.",
            ephemeral=True,
        )

    @audit_blacklist.command(
        name="remove",
        description="Resume audit logging for a previously excluded channel.",
    )
    @app_commands.describe(
        channel="Channel to resume audit logging for.",
        channel_id="Raw channel ID (use when the channel no longer exists).",
    )
    async def audit_blacklist_remove_command(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.abc.GuildChannel] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        target_id: Optional[int] = None
        if channel is not None:
            target_id = channel.id
        elif channel_id:
            try:
                target_id = int(channel_id.strip())
            except ValueError:
                target_id = None
        if target_id is None:
            await interaction.response.send_message(
                "Provide a channel or a numeric channel ID to remove.",
                ephemeral=True,
            )
            return

        config = self.bot.get_config(interaction.guild.id)
        if target_id not in config.audit_channel_blacklist:
            await interaction.response.send_message(
                f"<#{target_id}> is not in the audit blacklist.",
                ephemeral=True,
            )
            return
        config.audit_channel_blacklist = [
            cid for cid in config.audit_channel_blacklist if cid != target_id
        ]
        self.bot.save_config(interaction.guild.id, config)
        await interaction.response.send_message(
            f"<#{target_id}> will be audit logged again.",
            ephemeral=True,
        )

    @audit_blacklist_remove_command.autocomplete("channel_id")
    async def audit_blacklist_channel_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        config = self.bot.get_config(interaction.guild.id)
        query = current.strip()
        choices: list[app_commands.Choice[str]] = []
        for cid in config.audit_channel_blacklist:
            label = f"#{cid}"
            channel = interaction.guild.get_channel(cid)
            if channel is not None:
                label = f"#{channel.name}"
            if query and query not in str(cid):
                continue
            choices.append(app_commands.Choice(name=label, value=str(cid)))
        return choices[:25]

    @audit_blacklist.command(
        name="list",
        description="List channels excluded from audit logging.",
    )
    async def audit_blacklist_list_command(
        self, interaction: discord.Interaction
    ) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if interaction.guild is None:
            return

        config = self.bot.get_config(interaction.guild.id)
        if not config.audit_channel_blacklist:
            await interaction.response.send_message(
                "No channels are excluded from audit logging.",
                ephemeral=True,
            )
            return
        lines = "\n".join(f"<#{cid}>" for cid in config.audit_channel_blacklist)
        await interaction.response.send_message(
            f"Channels excluded from audit logging:\n{lines}",
            ephemeral=True,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_audit_blacklist.py -v`
Expected: PASS (all tests, including Task 2's).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/audit.py tests/test_audit_blacklist.py
git commit -m "feat: add /audit blacklist add/remove/list commands"
```

---

## Task 4: Show blacklisted channels in the audit status embed

**Files:**
- Modify: `axitools/cogs/audit.py` (`get_config_status`, line 1406)
- Test: `tests/test_config_status.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_status.py`:

```python
def test_audit_cog_status_lists_blacklist():
    from axitools.cogs.audit import AuditCog

    config = MagicMock()
    config.audit_channel_id = 11111
    config.audit_gw2_guild_id = "abc-123"
    config.audit_channel_blacklist = [222, 333]
    cog = AuditCog.__new__(AuditCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert any(f.label == "Blacklisted Channels" for f in status.fields)
    field = next(f for f in status.fields if f.label == "Blacklisted Channels")
    assert "<#222>" in field.value and "<#333>" in field.value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_status.py::test_audit_cog_status_lists_blacklist -v`
Expected: FAIL — no "Blacklisted Channels" field.

- [ ] **Step 3: Add the status field**

In `axitools/cogs/audit.py`, in `get_config_status`, after the `audit_gw2_guild_id` block (after line 1432, before `return ConfigStatus(...)`), add:

```python
        blacklist = config.audit_channel_blacklist
        if isinstance(blacklist, list) and blacklist:
            fields.append(StatusField(
                label="Blacklisted Channels",
                value=", ".join(f"<#{cid}>" for cid in blacklist),
                state="ok",
            ))
```

Note: the `isinstance(blacklist, list)` guard ensures the existing `test_audit_cog_status_ok` / `_missing` tests (which use a `MagicMock` config whose `audit_channel_blacklist` is a non-list MagicMock) skip this field and keep passing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_status.py -k audit -v`
Expected: PASS (new test + the two existing audit status tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/audit.py tests/test_config_status.py
git commit -m "feat: show blacklisted channels in audit status embed"
```

---

## Task 5: Full suite verification

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest --maxWorkers=2 -q`
(If `--maxWorkers` is unsupported by this project's pytest config, run `python -m pytest -p no:xdist -q`.)
Expected: All tests pass.

- [ ] **Step 2: Final commit (only if anything was amended)**

```bash
git status
# clean tree expected; nothing to commit
```

---

## Self-Review Notes

- **Spec coverage:** data model (Task 1), all six channel-tied listener guards (Task 2), add/remove/list + raw-id fallback + autocomplete (Task 3), status embed (Task 4), tests in every task. ✅
- **Type consistency:** field name `audit_channel_blacklist` used identically across storage, cog, tests; helper `_is_channel_blacklisted(guild, channel_id)` signature consistent across all call sites; command callbacks referenced by their exact attribute names. ✅
- **MagicMock guard:** status embed guards with `isinstance(list)` so pre-existing audit status tests are unaffected. ✅
