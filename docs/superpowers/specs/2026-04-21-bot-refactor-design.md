# AxiTools Bot Refactor Design

**Date:** 2026-04-21  
**Scope:** Code quality refactor + unified admin UX  
**Approach:** Cog-by-cog integrated redesign (Option C)

---

## Problem Statement

AxiTools has a solid modular architecture, but three friction points compound each other:

1. **No unified status view.** Admins have no way to see what's configured and what's missing at a glance. Each cog has its own config commands but there's no overview.
2. **Discovery is rough.** An admin setting up a new guild must know which commands exist across all cogs — nothing guides them through what's needed.
3. **Monster cogs.** `select.py` (~2K lines) does member search, AI query building, filtering, and CSV export in one file. `rss.py` and `update_notes.py` each carry duplicate HTML/markdown logic.

---

## Goals

- A `/status` command that shows the bot's full configuration health at a glance with actionable jump-points
- A first-run hint for new guilds with nothing configured
- `select.py` decomposed into focused, independently testable modules
- Shared formatting logic consolidated into `axitools/rendering.py`
- All changes delivered cog-by-cog so the bot stays working throughout

## Non-Goals

- Refactoring `comps.py` or `accounts.py` (large but internally coherent)
- Encrypting API keys at rest (separate concern)
- Changing any existing command names or signatures
- Rewriting the storage layer

---

## Architecture

### Config Summary Interface

Each cog that has configurable state implements an async `get_config_status()` method. The method returns a `ConfigStatus` describing the cog's current health. The `/status` command collects these and renders them into a unified embed.

```python
# axitools/config_status.py

from dataclasses import dataclass, field
from typing import Literal

@dataclass
class StatusField:
    label: str
    value: str                           # human-readable current value or "not set"
    state: Literal["ok", "warn", "missing"]

@dataclass
class ConfigStatus:
    title: str
    fields: list[StatusField]
    setup_command: str | None = None     # e.g. "/config" — shown on action button
```

Cogs opt in by implementing:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    ...
```

Cogs that don't implement it are silently skipped. No breaking changes to existing cogs.

### `/status` Command (in `config` cog)

- Admin/moderator-only, ephemeral
- Iterates all loaded cogs, collects `ConfigStatus` from those that implement it
- Renders a single embed: one inline section per cog
- State indicators: ✅ ok / ⚠️ warn / ❌ missing
- Buttons below the embed for each cog with `setup_command` set and at least one non-ok field; button label is the `setup_command` value (e.g. `/config`, `/rss set`)
- "Getting Started" header shown when no moderator roles and no build channel are configured (first-run detection)

**Example embed:**

```
AxiTools Status — My Server

⚙️ Core
  ✅ Moderator roles: @Officers, @Mods
  ❌ Build channel: not set

📋 Builds
  ✅ Build channel: #gw2-builds
  ✅ 14 builds registered

📡 RSS
  ✅ 3 feeds active
  ⚠️ 1 feed failing (last error 2h ago)

🎯 Compositions
  ✅ 2 presets  |  ✅ 3 scheduled comps

🔔 ArcDPS
  ❌ Channel not set

👤 Accounts
  ✅ API key sync active  |  ✅ 47 keys registered
```

### First-Run Detection

If `ConfigStatus` fields from the core config cog show both moderator roles and build channel as missing, `/status` prepends a "Getting Started" block explaining the recommended setup order:

1. `/config` — set moderator roles and build channel
2. `/builds add` — add your first build
3. Feature-specific setup as needed (RSS, ArcDPS, etc.)

---

## `select.py` Decomposition

Current `cogs/select.py` (~2K lines) is split into four focused units:

### `axitools/member_filter.py`
Core filtering engine. No Discord imports.

- `MemberFilter` class: accepts a list of member records + filter predicates
- `build_predicates(query: FilterQuery) -> list[Predicate]`
- `apply(members, predicates) -> list[MemberRecord]`
- Handles: role membership, join date ranges, API data fields, account name matching

### `axitools/query_schema.py`
AI schema generation and natural language query parsing. No Discord imports.

- `generate_schema(available_roles: list[str]) -> dict` — builds the JSON schema sent to the AI
- `parse_natural_query(text: str, schema: dict) -> FilterQuery` — calls AI and returns structured query
- `FilterQuery` dataclass — typed representation of parsed filter criteria

### `axitools/export.py`
CSV formatting and file construction. No Discord imports.

- `members_to_csv(members: list[MemberRecord]) -> io.BytesIO`
- Returns a file-like object ready to pass to `discord.File`
- Reusable by other cogs (e.g. audit) without modification

### `cogs/select.py` (after split)
Thin cog: Discord command definitions and handler methods only. Each handler:
1. Parses Discord interaction inputs
2. Delegates to the appropriate module
3. Formats and returns the Discord response

Target size: under 300 lines.

---

## Shared Rendering Utilities

New file: `axitools/rendering.py`

Consolidates duplicated logic from `rss.py` and `update_notes.py`:

```python
def clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""

def to_discord_markdown(text: str) -> str:
    """Convert HTML/RSS content to Discord-friendly markdown."""

def ensure_bullet_prefix(lines: list[str]) -> list[str]:
    """Normalise lines to start with a bullet character."""

def truncate_embed_field(text: str, limit: int = 1024) -> str:
    """Safely truncate to Discord embed field limit with ellipsis."""
```

`rss.py` and `update_notes.py` drop their local copies and import from `rendering.py`.

---

## Delivery Order

Work is done cog-by-cog. The bot remains functional after each step.

1. **`axitools/rendering.py`** — extract shared formatting, update `rss.py` + `update_notes.py`
2. **`select.py` decomposition** — extract `member_filter.py`, `query_schema.py`, `export.py`; slim down `cogs/select.py`; add `get_config_status()` to the new thin cog
3. **`config_status.py`** — define the `ConfigStatus` interface
4. **Wire remaining cogs** — add `get_config_status()` to `builds`, `rss`, `comps`, `accounts`, `arcdps`, `update_notes`, `audit`
5. **`/status` command** — implement in `config` cog, render the unified embed + buttons
6. **First-run detection** — add "Getting Started" block logic to `/status`

---

## Testing

- `member_filter.py`, `query_schema.py`, and `export.py` are pure Python — unit testable without Discord mocks
- `rendering.py` functions are pure — straightforward unit tests
- `ConfigStatus` shape tested by asserting each cog's `get_config_status()` returns valid fields
- No changes to existing command signatures — existing tests remain valid

---

## Files Changed

| File | Change |
|------|--------|
| `axitools/rendering.py` | New — shared formatting utilities |
| `axitools/config_status.py` | New — `ConfigStatus` + `StatusField` dataclasses |
| `axitools/member_filter.py` | New — extracted from `select.py` |
| `axitools/query_schema.py` | New — extracted from `select.py` |
| `axitools/export.py` | New — extracted from `select.py` |
| `cogs/select.py` | Refactored — thin cog, delegates to new modules |
| `cogs/config.py` | Extended — adds `/status` command |
| `cogs/rss.py` | Updated — uses `rendering.py`, adds `get_config_status()` |
| `cogs/update_notes.py` | Updated — uses `rendering.py`, adds `get_config_status()` |
| `cogs/builds.py` | Updated — adds `get_config_status()` |
| `cogs/comps.py` | Updated — adds `get_config_status()` |
| `cogs/accounts.py` | Updated — adds `get_config_status()` |
| `cogs/arcdps.py` | Updated — adds `get_config_status()` |
| `cogs/audit.py` | Updated — adds `get_config_status()` |
