# AxiTools Command Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AxiTools' command surface consistent and discoverable, starting with the two non-breaking wins (help/auth visibility + unified response style), then the breaking renames as a coordinated later phase.

**Architecture:** Declarative command metadata via discord.py `extras` (no hardcoded lists), one shared embed builder in `branding.py`, and a phased rollout where breaking renames register old+new names together for a transition window before the old name is dropped.

**Tech Stack:** Python 3.10+, discord.py (`app_commands`), pytest (`--maxWorkers=2` / forks ≤2 per machine policy).

**Source of truth:** `docs/command-audit-2026-06-09.md` (the audit this plan implements).

---

## Rollout phases

- **Phase 1 — execute now (non-breaking):** B (help + auth visibility) and D (response style). Tasks 1–7 below, fully detailed.
- **Phase 2 — later, own plans (breaking / structural):** A (kill confusables), C (normalize names), E (structural refactor). Outlined at the end; each becomes its own `writing-plans` pass once Phase 1 is live and observed.

---

## File structure (Phase 1)

- `axitools/branding.py` — gains `brand_embed(...)`, the single embed factory (currently only holds `BRAND_COLOUR`).
- `axitools/cogs/help.py` — stops using the hardcoded `PUBLIC_COMMANDS` set; derives public/category from command `extras`.
- `axitools/cogs/accounts.py` — `_embed` delegates to `branding.brand_embed`; public groups tagged `extras={"public": True}` + category.
- `axitools/cogs/select.py` — `_embed` delegates to `branding.brand_embed`.
- `axitools/cogs/audit.py` — plain-text responses become branded embeds; query commands defer.
- `axitools/cogs/wvw_alliance.py`, `axitools/cogs/rss.py` — gate the two public-but-hidden reads; tag genuinely-public commands.
- `axitools/cogs/{config,builds,comps,streaming,reset}.py` — group `extras={"category": ...}` tags only.
- `tests/test_branding.py` (new), `tests/test_cogs_help.py`, `tests/test_cogs_audit*.py` — coverage.

---

## PHASE 1 — B: Help & Auth Visibility

### Task 1: Add `brand_embed` factory to branding.py

**Files:**
- Modify: `axitools/branding.py`
- Test: `tests/test_branding.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_branding.py
import discord
from axitools.branding import BRAND_COLOUR, brand_embed


def test_brand_embed_defaults():
    embed = brand_embed(title="Hello")
    assert embed.title == "Hello"
    assert embed.colour == BRAND_COLOUR
    assert embed.footer.text == "Guild Wars 2 Tools"
    assert embed.description == ""


def test_brand_embed_overrides():
    embed = brand_embed(title="T", description="D", colour=discord.Colour.red())
    assert embed.description == "D"
    assert embed.colour == discord.Colour.red()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_branding.py -v -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'brand_embed'`.

- [ ] **Step 3: Implement**

```python
# axitools/branding.py — append below BRAND_COLOUR
from typing import Optional


def brand_embed(
    *,
    title: str,
    description: Optional[str] = None,
    colour: discord.Colour = BRAND_COLOUR,
) -> discord.Embed:
    """Build an embed with the standard AxiTools branding footer."""
    embed = discord.Embed(title=title, description=description or "", colour=colour)
    embed.set_footer(text="Guild Wars 2 Tools")
    return embed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_branding.py -v -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add axitools/branding.py tests/test_branding.py
git commit -m "feat: add shared brand_embed factory"
```

---

### Task 2: Point the duplicated `_embed` helpers at `brand_embed`

**Files:**
- Modify: `axitools/cogs/accounts.py:64-73`
- Modify: `axitools/cogs/select.py:64-73`

These two methods are byte-identical. Make them delegate so there is one implementation.

- [ ] **Step 1: Update accounts.py `_embed`**

Replace the body of `_embed` (lines 64-73) with:

```python
    def _embed(
        self,
        *,
        title: str,
        description: Optional[str] = None,
        colour: discord.Colour = BRAND_COLOUR,
    ) -> discord.Embed:
        return brand_embed(title=title, description=description, colour=colour)
```

Add `brand_embed` to the existing branding import at the top of the file (it already imports `BRAND_COLOUR` from `..branding`).

- [ ] **Step 2: Update select.py `_embed`** identically (replace lines 64-73 with the same delegating body and add `brand_embed` to its `..branding` import).

- [ ] **Step 3: Run the affected suites**

Run: `python -m pytest tests/test_cogs_accounts.py tests/test_cogs_select.py -v -p no:cacheprovider`
Expected: PASS (existing tests unaffected — behaviour identical).

- [ ] **Step 4: Commit**

```bash
git add axitools/cogs/accounts.py axitools/cogs/select.py
git commit -m "refactor: dedupe _embed via brand_embed"
```

---

### Task 3: Tag command visibility & category via `extras`; gate the two hidden public reads

**Decision (from audit):** `/rss list` and `/alliance status` are currently public-but-hidden. Their siblings (`/rss set|delete`, `/alliance set*`) are mod-only and they expose server config, so we **gate them** for consistency rather than expose them. This changes them from public→mod-only (noted as the one intentional behaviour change in Phase 1).

The genuinely-public set after gating: `apikey*`, `guildrole*`, `gw2guild search`, `help`, `reset`.

**Files:**
- Modify: `axitools/cogs/wvw_alliance.py` (status command), `axitools/cogs/rss.py` (list command)
- Modify: `axitools/cogs/accounts.py` (group declarations), `axitools/cogs/reset.py`, `axitools/cogs/help.py`
- Modify: group declarations in `config.py`, `audit.py`, `select.py`, `builds.py`, `comps.py`, `streaming.py` (category tags)

- [ ] **Step 1: Gate `/alliance status`** — add at the top of its callback (mirroring siblings):

```python
        if not await self.bot.ensure_authorised(interaction):
            return
```

- [ ] **Step 2: Gate `/rss list`** — add the same two lines at the top of the `list` callback.

- [ ] **Step 3: Run the affected suites to confirm nothing else relied on them being public**

Run: `python -m pytest tests/test_cogs_wvw_alliance.py tests/test_cogs_rss.py -v -p no:cacheprovider`
Expected: PASS (update any test that asserted public access — set `bot.ensure_authorised` mock to return `True`).

- [ ] **Step 4: Tag public commands with `extras`.** On the genuinely-public group/command declarations add `extras={"public": True}`:
  - `accounts.py`: `api_keys = app_commands.Group(name="apikey", description=..., extras={"public": True})`, same for `guild_role_preferences` (`guildrole`) and `guild_lookup` (`gw2guild`).
  - `help.py`: `@app_commands.command(name="help", description=..., extras={"public": True})`.
  - `reset.py`: add `extras={"public": True}` to the `@app_commands.command(...)` for reset.

- [ ] **Step 5: Tag categories** on each top-level group/command via `extras={"category": "<Category>"}` (merge with any existing extras dict):
  - GW2 Account: `apikey`, `guildrole`, `gw2guild`
  - Server Setup: `config`, `status`, `audit`
  - Moderation: `select`, `guildroles`
  - Builds & Comps: `builds`, `comp`
  - Announcements: `rss`, `stream`
  - WvW: `alliance`, `reset`

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/
git commit -m "feat: declare command visibility and category via extras; gate /rss list and /alliance status"
```

---

### Task 4: Rewrite help.py to derive from `extras`

**Files:**
- Modify: `axitools/cogs/help.py`
- Test: `tests/test_cogs_help.py`

- [ ] **Step 1: Write failing tests** — extend `tests/test_cogs_help.py`:

```python
@pytest.mark.asyncio
async def test_help_uses_extras_for_public(mock_bot_help):
    cog = HelpCog(mock_bot_help)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()
    mock_bot_help.is_authorised.return_value = False

    public_cmd = MagicMock()
    public_cmd.qualified_name = "apikey add"
    public_cmd.description = "Add a key"
    public_cmd.extras = {"public": True, "category": "GW2 Account"}
    public_cmd.parent = None

    gated_cmd = MagicMock()
    gated_cmd.qualified_name = "stream list"
    gated_cmd.description = "List"
    gated_cmd.extras = {}
    gated_cmd.parent = None

    mock_bot_help.tree.get_commands.return_value = [public_cmd, gated_cmd]
    await cog.help_command.callback(cog, interaction)

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    body = "\n".join(f.value for f in embed.fields)
    assert "/apikey add" in body
    assert "/stream list" not in body
```

(The existing `test_help_command_public`/`_authorised` need their MagicMock commands given `.extras = {"public": True/...}` and `.parent = None` so the new logic sees them. Update `cmd_public.extras = {"public": True}`, `cmd_private.extras = {}`.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cogs_help.py -v -p no:cacheprovider`
Expected: FAIL (current code keys off the hardcoded set, not extras).

- [ ] **Step 3: Implement.** Replace the `PUBLIC_COMMANDS` set and the per-command public check in `help.py` with extras-derived helpers:

```python
# remove the PUBLIC_COMMANDS set entirely

DEFAULT_CATEGORY = "Other"


def _walk_extras(command: app_commands.Command, key: str):
    node = command
    while node is not None:
        value = getattr(node, "extras", {}).get(key)
        if value is not None:
            return value
        node = getattr(node, "parent", None)
    return None


def _is_public(command: app_commands.Command) -> bool:
    return bool(_walk_extras(command, "public"))


def _category(command: app_commands.Command) -> str:
    return _walk_extras(command, "category") or DEFAULT_CATEGORY
```

In `help_command`, group by `_category(command)` instead of the root name, and replace the `is_public` check with `_is_public(command)`:

```python
        lines_by_category: dict[str, list[str]] = defaultdict(list)
        for command in command_entries:
            if not is_authorised and not _is_public(command):
                continue
            lines_by_category[_category(command)].append(
                f"/{command.qualified_name} — {command.description or 'No description provided.'}"
            )
        ...
        for category in sorted(lines_by_category.keys()):
            entries = "\n".join(sorted(lines_by_category[category]))
            embed.add_field(name=category, value=entries, inline=False)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_cogs_help.py -v -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/help.py tests/test_cogs_help.py
git commit -m "feat: derive /help visibility and categories from command extras"
```

---

## PHASE 1 — D: Unified Response Style

### Task 5: Convert `/audit` text responses to branded embeds

**Files:**
- Modify: `axitools/cogs/audit.py`
- Test: `tests/test_cogs_audit*.py` (existing audit tests)

The audit cog replies in plain text everywhere. Convert each `interaction.response.send_message("...", ephemeral=True)` / `followup.send("...")` that sends **text** into a `brand_embed`. Leave `discord.File` query results as-is (file dumps can't be embedded), but send any accompanying message as an embed.

- [ ] **Step 1: Import the factory** — add to audit.py's `..branding` import: `from ..branding import BRAND_COLOUR, brand_embed` (add `brand_embed`; keep existing imports).

- [ ] **Step 2: Write/adjust a failing test** — pick one representative command (e.g. `/audit channel`). Add to the relevant audit test file:

```python
@pytest.mark.asyncio
async def test_audit_channel_replies_with_embed(...):
    # arrange interaction + authorised mock as in sibling tests
    await cog.set_channel.callback(cog, interaction, channel=None)
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("embed") is not None
    assert kwargs["embed"].footer.text == "Guild Wars 2 Tools"
```

(Match the real callback name and signature from `audit.py`.)

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_audit_blacklist.py tests/test_audit_embed_avatar.py -k channel -v -p no:cacheprovider`
Expected: FAIL (currently sends `content=`, not `embed=`).

- [ ] **Step 4: Implement** — for each text reply in audit.py, replace e.g.

```python
await interaction.response.send_message("Audit channel updated.", ephemeral=True)
```

with

```python
await interaction.response.send_message(
    embed=brand_embed(title="Audit", description="Audit channel updated."),
    ephemeral=True,
)
```

Apply to every text reply in the cog (config setters, blacklist add/remove/list, gw2_key add/list/remove/migrate, "no rows" messages). Use a sensible `title` per command group ("Audit", "Audit blacklist", "GW2 audit keys").

- [ ] **Step 5: Run the full audit suites**

Run: `python -m pytest tests/test_audit_blacklist.py tests/test_audit_embed_avatar.py tests/test_cogs_help.py -v -p no:cacheprovider`
Expected: PASS (update any sibling test still asserting `content=`).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/audit.py tests/
git commit -m "feat: brand /audit responses with embeds"
```

---

### Task 6: Defer `/audit` query commands

**Files:**
- Modify: `axitools/cogs/audit.py` (`query`, `historical_query`, `gw2_query`)

These do DB + GW2 API work and currently reply without deferring — timeout risk.

- [ ] **Step 1: Implement** — at the top of each of the three query callbacks add:

```python
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
```

and change their terminal `interaction.response.send_message(... file=...)` to `interaction.followup.send(... file=..., ephemeral=True)`. (The auth check uses `interaction.response.send_message` for the failure path — keep that BEFORE the defer so the rejection still works.)

- [ ] **Step 2: Run the suites**

Run: `python -m pytest tests/test_audit_blacklist.py tests/test_audit_embed_avatar.py -v -p no:cacheprovider`
Expected: PASS (adjust mocks to use `interaction.followup.send` where a query test asserts the send).

- [ ] **Step 3: Commit**

```bash
git add axitools/cogs/audit.py tests/
git commit -m "fix: defer slow /audit query commands to avoid interaction timeout"
```

---

### Task 7: Full-suite green + audit doc update

**Files:**
- Modify: `docs/command-audit-2026-06-09.md` (mark B and D done)

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -p no:cacheprovider --maxprocesses=2` (or `-p no:xdist` if xdist absent; honour the ≤2 worker machine policy)
Expected: all PASS.

- [ ] **Step 2: Tick B and D** in the audit doc's decision menu (note `/rss list` and `/alliance status` are now mod-gated).

- [ ] **Step 3: Commit**

```bash
git add docs/command-audit-2026-06-09.md
git commit -m "docs: mark help/auth and response-style cleanup complete"
```

---

## PHASE 2 — Outline only (each gets its own plan later)

These are **breaking or structural** and should be planned individually once Phase 1 is live and observed. Listed here so the sequence is recorded; do NOT implement from this outline.

### A — Kill the confusables (breaking; needs transition window)
- Rename `/guildrole` → a clearly user-scoped name (candidate: `/myrole`); register both names for one release, announce, then drop `/guildrole`.
- Unify the two API-key systems' naming so `/apikey` (user) vs `/audit gw2_key` (guild audit) are obviously distinct (candidate: `/audit apikey` to match the user-facing `/apikey` verb set).
- Fold `/status` into `/config status` (the status embed already links to config); keep `/status` as a transitional alias.
- Transition mechanism: register old+new together, log usage of the old name, remove after the window.

### C — Normalize all names to the convention (most breaking)
- Apply the convention from the audit §3: no smushed/snake names in user-visible commands; multi-word concepts become subgroups.
- Examples: `/alliance setguild|setchannel|settime` → `/alliance set guild|channel|time`; `/audit gw2_guild|gw2_query` → consistent subgroup form.
- Same old+new transition mechanism as A; batch into one coordinated rename so users relearn once.

### E — Structural refactor (non-breaking, maintainer-only)
- Split `accounts.py` (2,411 lines) by audience: user (`apikey`, `guildrole`, `gw2guild`) vs admin (`guildroles`).
- Extract shared pagination view (the near-identical `_FeedDeleteView`/`_FeedTestView` in rss, plus reimplemented page buttons elsewhere) into one reusable component.
- Rename the `whitelist` subgroup/terminology to match its "allowlist" description.

---

## Self-review notes
- Spec coverage: B → Tasks 3,4 (+1,2 enabling). D → Tasks 1,2,5,6. A/C/E → Phase 2 outline (deferred per user). ✔
- The one intentional behaviour change in Phase 1 (gating `/rss list`, `/alliance status`) is called out in Task 3 and Task 7. ✔
- Type/name consistency: `brand_embed(title=..., description=..., colour=...)` keyword signature is used identically in Tasks 1, 2, 5. ✔
