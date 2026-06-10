# Command Normalization (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the normalized, intent-subgroup command taxonomy from the spec across the bot in one coordinated breaking release (hard cutover), after a non-breaking structural refactor.

**Architecture:** Refactor first (split `accounts.py`, extract pagination) so renames apply to focused files; then rename per-cog. ConfigCog and the dev commands become groups. No data migration — only the command surface changes.

**Spec:** `docs/superpowers/specs/2026-06-09-command-normalization-design.md` (source of truth for every before→after).

**Tech Stack:** Python 3.10+, discord.py `app_commands`, pytest (`--maxWorkers=2` per machine policy; run with `-p no:cacheprovider -p no:xdist`).

**Conventions for every rename task:**
- Discord nesting max is `/group subgroup command` (3 tokens).
- Each task: update command/group definitions → update the cog's existing tests to the new callback names/paths → run that cog's suite → commit. Behaviour assertions stay identical; only names/paths change.
- After each task the **full suite stays green** (baseline 186 tests).

---

## STAGE E — Structural refactor (non-breaking, lands first)

### Task E1: Split `accounts.py` into user + admin cogs

**Files:**
- Create: `axitools/cogs/account_self.py` (user: `/apikey`, `/gw2guild`)
- Create: `axitools/cogs/guild_roles.py` (admin: `/guildroles`)
- Delete: `axitools/cogs/accounts.py`
- Modify: `axitools/bot.py:44` (extension load), `tests/test_cogs_accounts.py`

`accounts.py` is `class AccountsCog(commands.Cog)` holding five `app_commands.Group` attributes:
`api_keys` (`apikey`), `guild_role_preferences` (`guildrole`), `guild_lookup` (`gw2guild`),
`guild_roles` (`guildroles`), `guild_role_allowlist` (`whitelist`, parent=guild_roles). Shared
helpers: `_embed` (delegates to `brand_embed`), `_send_embed`, `_format_list`, `_format_table`,
plus GW2 API client helpers.

- [ ] **Step 1: Identify the shared helpers.** Grep for cross-group helper usage:

Run: `rg -n "_send_embed|_format_table|_format_list|_resolve_record_details" axitools/cogs/accounts.py`
Any helper used by BOTH the apikey/gw2guild commands AND the guildroles commands moves to
`axitools/utils.py` as a free function (or a small shared mixin in a new
`axitools/cogs/_accounts_shared.py`). Helpers used by only one side move with that side.

- [ ] **Step 2: Create `account_self.py`** — `class AccountSelfCog(commands.Cog)` containing the
`api_keys` group (+ its `role` subgroup added in Task A3), `guild_lookup` group, their command
methods, autocompletes, and only the helpers they use. Keep the existing `extras` tags
(`{"public": True, "category": "GW2 Account"}`). Add `async def setup(bot)` →
`await bot.add_cog(AccountSelfCog(bot))`.

- [ ] **Step 3: Create `guild_roles.py`** — `class GuildRolesCog(commands.Cog)` containing the
`guild_roles` group (`extras={"category": "Moderation"}`), the `guild_role_allowlist` subgroup, their
commands/autocompletes and helpers. Add its own `setup`.

- [ ] **Step 4: Delete `accounts.py`.**

- [ ] **Step 5: Update `bot.py`** — replace the single load with both:

```python
        await self.load_extension("axitools.cogs.account_self")
        await self.load_extension("axitools.cogs.guild_roles")
```

- [ ] **Step 6: Split the tests** — `tests/test_cogs_accounts.py` imports `AccountsCog`. Split into
`tests/test_cogs_account_self.py` and `tests/test_cogs_guild_roles.py`, importing the new classes.
Update construction (`AccountSelfCog(bot)` / `GuildRolesCog(bot)`) and callback paths
(e.g. `cog.api_keys` group commands unchanged at this stage). Keep assertions identical.

- [ ] **Step 7: Run suites + sanity**

Run: `python -m pytest tests/test_cogs_account_self.py tests/test_cogs_guild_roles.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add axitools/cogs/account_self.py axitools/cogs/guild_roles.py axitools/bot.py tests/
git rm axitools/cogs/accounts.py
git commit -m "refactor: split accounts cog into account_self and guild_roles"
```

---

### Task E2: Extract reusable paginated-select component

**Files:**
- Create: `axitools/cogs/_paginated_select.py`
- Modify: `axitools/cogs/rss.py` (replace `_FeedDeleteView`/`_FeedTestView`)
- Test: `tests/test_paginated_select.py` (create)

`_FeedDeleteView` + `_FeedDeleteSelect` + `_FeedDeletePageButton` and the `_FeedTest*` trio are
near-identical (PAGE_SIZE=25 paginated dropdowns differing only in option source + on-select action).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paginated_select.py
from axitools.cogs._paginated_select import PaginatedSelectView


def test_pagination_splits_options_into_pages():
    options = [(str(i), f"item {i}") for i in range(60)]
    view = PaginatedSelectView(options=options, page_size=25, on_select=lambda v, i: None)
    assert view.page_count == 3
    assert len(view.current_options()) == 25
    view.next_page()
    assert view.current_index == 1
    view.next_page(); view.next_page()  # clamps at last page
    assert view.current_index == 2
    assert len(view.current_options()) == 10
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paginated_select.py -q -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `PaginatedSelectView`** — a `discord.ui.View` taking
`options: list[tuple[value, label]]`, `page_size`, and an async `on_select(value, interaction)`
callback, with prev/next page buttons and a `discord.ui.Select` rebuilt per page. Expose
`page_count`, `current_index`, `current_options()`, `next_page()`, `prev_page()` for testability.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_paginated_select.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Replace in rss.py** — `/rss delete` and the dev test feed view construct
`PaginatedSelectView` with their option lists + on-select actions; delete the four bespoke classes.

- [ ] **Step 6: Run rss suite**

Run: `python -m pytest tests/test_cogs_rss.py tests/test_paginated_select.py -q -p no:cacheprovider`
Expected: PASS (adjust rss tests that referenced the deleted view classes).

- [ ] **Step 7: Commit**

```bash
git add axitools/cogs/_paginated_select.py axitools/cogs/rss.py tests/
git commit -m "refactor: extract reusable PaginatedSelectView and use it in rss"
```

---

## STAGE A+C — The rename release (hard cutover)

Each task renames one cog's surface per the spec tables and updates that cog's tests.

### Task R1: `/config` → group (`/config setup`, `/config status`)

**Files:** `axitools/cogs/config.py`, `tests/test_cogs_config.py`, `tests/test_config_status.py`

- [ ] **Step 1:** Change `class ConfigCog(commands.Cog)` →
`class ConfigCog(commands.GroupCog, name="config", group_extras={"category": "Server Setup"})`.
Add `def __init__` calling `super().__init__()` if not present.

- [ ] **Step 2:** Rename the two commands to subcommands of the implicit group:
  - `@app_commands.command(name="config", ...)` → `@app_commands.command(name="setup", description="Open AxiTools settings for this server.")` (drop the now-redundant `extras` category — inherited from the group).
  - `@app_commands.command(name="status", ...)` → `@app_commands.command(name="status", description="View AxiTools configuration status for this server.")`.

- [ ] **Step 3:** Update tests — call `cog.setup_command.callback`/`cog.status.callback` per the new
method names; assert the commands now resolve under the `config` group.

- [ ] **Step 4:** Run

Run: `python -m pytest tests/test_cogs_config.py tests/test_config_status.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5:** Commit `feat: group config commands as /config setup and /config status`

---

### Task R2: `/audit` intent subgroups

**Files:** `axitools/cogs/audit.py`, `tests/test_audit_blacklist.py`, `tests/test_audit_embed_avatar.py`

Per spec: add a `setup` subgroup (`channel`, `guild`) and a `query` subgroup
(`discord`, `historical`, `gw2`); rename `gw2_key` subgroup → `apikey`.

- [ ] **Step 1:** Add subgroups next to the existing ones:

```python
    audit_setup = app_commands.Group(
        name="setup", description="Configure audit logging.", parent=audit
    )
    audit_query = app_commands.Group(
        name="query", description="Query audit entries.", parent=audit
    )
```

- [ ] **Step 2:** Re-decorate commands:
  - `@audit.command(name="channel")` → `@audit_setup.command(name="channel")`
  - `@audit.command(name="gw2_guild")` → `@audit_setup.command(name="guild")`
  - `@audit.command(name="query")` → `@audit_query.command(name="discord")`
  - `@audit.command(name="historical_query")` → `@audit_query.command(name="historical")`
  - `@audit.command(name="gw2_query")` → `@audit_query.command(name="gw2")`
  - `audit_gw2_key = app_commands.Group(name="gw2_key", ...)` → `name="apikey"` (keep `parent=audit`); subcommands `add|list|remove|migrate` unchanged.

- [ ] **Step 3:** Update tests — blacklist tests reference `audit_blacklist_*` (unchanged); update any
test calling the renamed query/setup callbacks. Method names stay (only decorators/groups change), so
mostly the `.callback` calls still work; verify qualified names if asserted.

- [ ] **Step 4:** Run

Run: `python -m pytest tests/test_audit_blacklist.py tests/test_audit_embed_avatar.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5:** Commit `feat: reorganize /audit into setup/query/apikey subgroups`

---

### Task R3: Fold `/guildrole` into `/apikey role`

**Files:** `axitools/cogs/account_self.py`, `axitools/cogs/guild_roles.py`, `tests/test_cogs_account_self.py`

The `guild_role_preferences` group (`guildrole`) currently lives in the user half (account_self after E1).

- [ ] **Step 1:** In `account_self.py`, replace the standalone `guild_role_preferences` group with a
`role` subgroup parented to `api_keys`:

```python
    api_role = app_commands.Group(
        name="role", description="Set your preferred guild role for auto sync.", parent=api_keys
    )
```

- [ ] **Step 2:** Re-decorate the two commands: `@guild_role_preferences.command(name="set")` →
`@api_role.command(name="set")`; `name="clear"` likewise. Remove the old group attribute and its
`extras` (category/public inherited from `api_keys`). Keep the `preferred_guild_role_autocomplete`.

- [ ] **Step 3:** Update tests to call the commands via the new `api_role` group; assert qualified
names `apikey role set` / `apikey role clear`.

- [ ] **Step 4:** Run

Run: `python -m pytest tests/test_cogs_account_self.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5:** Commit `feat: fold /guildrole into /apikey role`

---

### Task R4: `/guildroles` — `alliance` subgroup + `whitelist`→`allowlist`

**Files:** `axitools/cogs/guild_roles.py`, `tests/test_cogs_guild_roles.py`

- [ ] **Step 1:** Add an `alliance` subgroup parented to `guild_roles`:

```python
    guild_roles_alliance = app_commands.Group(
        name="alliance", description="Alliance guild used for WvW membership checks.", parent=guild_roles
    )
```

- [ ] **Step 2:** Re-decorate: `setalliance` → `@guild_roles_alliance.command(name="set")`;
`clearalliance` → `@guild_roles_alliance.command(name="clear")`. Rename the
`guild_role_allowlist` group `name="whitelist"` → `name="allowlist"` and update its description to use
"allowlist" wording consistently. `set|list|remove|audit` unchanged.

- [ ] **Step 3:** Update tests for the new qualified names (`guildroles alliance set`,
`guildroles allowlist add`).

- [ ] **Step 4:** Run

Run: `python -m pytest tests/test_cogs_guild_roles.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5:** Commit `feat: /guildroles alliance subgroup and whitelist->allowlist`

---

### Task R5: `/alliance` — `setup` subgroup + `postnow`→`post`

**Files:** `axitools/cogs/wvw_alliance.py`, `tests/test_cogs_wvw_alliance.py`

- [ ] **Step 1:** Add a `setup` subgroup. Since `AllianceMatchupCog` is a `GroupCog`, declare the
subgroup as a class attribute (parent is the implicit cog group, so no explicit `parent=`):

```python
    setup_group = app_commands.Group(name="setup", description="Configure alliance matchup posts.")
```

- [ ] **Step 2:** Re-decorate: `setguild`→`@setup_group.command(name="guild")`,
`setchannel`→`@setup_group.command(name="channel")`, `settime`→`@setup_group.command(name="time")`,
`postnow`→`@app_commands.command(name="post", ...)`. Leave `status`, `refresh`, `relink` as-is.

- [ ] **Step 3:** Update tests (`test_settime_*` etc.) to the new callback group paths and qualified
names (`alliance setup guild`, `alliance post`).

- [ ] **Step 4:** Run

Run: `python -m pytest tests/test_cogs_wvw_alliance.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5:** Commit `feat: /alliance setup subgroup and postnow->post`

---

### Task R6: Dev commands → `/dev` group

**Files:** Create `axitools/cogs/dev.py`; modify `axitools/cogs/arcdps.py`, `axitools/cogs/update_notes.py`, `axitools/cogs/rss.py`, `axitools/bot.py`; create `tests/test_cogs_dev.py`

`/dev arcdps|updatenotes|rsstest` must live in one group, but the logic lives in three cogs. Solution:
extract each cog's force/test body into a public coroutine and have a single `DevCog` (gated by
`PRODUCTION`) delegate to them via `bot.get_cog(...)`.

- [ ] **Step 1:** In `arcdps.py`, refactor `arcdps_force_notification`'s body into
`async def run_force_notification(self, interaction)` and have the (now-removed) command deleted.
Repeat in `update_notes.py` (`run_force_notification`) and `rss.py` (`run_test_feed`). Remove the old
`if not PRODUCTION:` command registrations from those cogs.

- [ ] **Step 2:** Create `DevCog(commands.GroupCog, name="dev")` with a module-level
`if not PRODUCTION:` guard around registration. Subcommands:

```python
    @app_commands.command(name="arcdps", description="Send a test ArcDPS notification.")
    async def arcdps(self, interaction):
        if not await self.bot.ensure_authorised(interaction):
            return
        cog = self.bot.get_cog("ArcDpsUpdatesCog")
        await cog.run_force_notification(interaction)
```

…and `updatenotes` → `UpdateNotesCog.run_force_notification`, `rsstest` → `RssFeedsCog.run_test_feed`.
Tag `group_extras={"category": "Dev"}`.

- [ ] **Step 3:** In `bot.py`, load `axitools.cogs.dev` after the feature cogs (only registers when
`not PRODUCTION`).

- [ ] **Step 4:** Write `tests/test_cogs_dev.py` — assert each `/dev` subcommand calls the delegated
cog method (mock `bot.get_cog`).

- [ ] **Step 5:** Run

Run: `python -m pytest tests/test_cogs_dev.py tests/test_cogs_arcdps.py tests/test_cogs_update_notes.py tests/test_cogs_rss.py tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6:** Commit `feat: consolidate dev/test commands under /dev group`

---

### Task R7: Changelog/announcement + full verification

**Files:** Create `docs/command-rename-changelog-2026-06-09.md`; modify `README.md`, `docs/command-audit-2026-06-09.md`

- [ ] **Step 1:** Write the user-facing changelog enumerating every before→after from the spec
(grouped by feature), suitable for posting to servers. Include a one-line "why" (consistency +
discoverability) and the note that stored settings are unaffected.

- [ ] **Step 2:** Update `README.md` Features/commands references that name old commands
(`/config`, `/status`, `/rss test`, etc.).

- [ ] **Step 3:** Mark A, C, E done in `docs/command-audit-2026-06-09.md`.

- [ ] **Step 4:** Full suite

Run: `python -m pytest -q -p no:cacheprovider -p no:xdist`
Expected: all PASS.

- [ ] **Step 5:** Manual smoke — boot sanity asserts the tree builds with no duplicate/!invalid names:

Run: `python -m pytest tests/test_bot_sanity.py -q -p no:cacheprovider`
Expected: PASS (this catches Discord command-tree validation errors from the renames).

- [ ] **Step 6:** Commit `docs: command rename changelog and README/audit updates`

---

## Self-review notes
- Spec coverage: every spec table maps to a task — config→R1, audit→R2, apikey/role→R3,
  guildroles→R4, alliance→R5, dev→R6; E1/E2 structural; R7 docs. ✔
- Hard cutover: no dual-registration anywhere; old names simply cease to exist. ✔
- Ordering: E1 must precede R3/R4 (they edit the split files); E2 independent; R1/R2/R5/R6 independent
  of the split. Recommended order E1→E2→R1→R2→R3→R4→R5→R6→R7. ✔
- No data migration: confirmed — only command definitions change; storage keys untouched. ✔
- Nesting: every new subgroup is exactly one level under its top group (no 4-token paths). ✔
