# Alliance Relink Announcement Feature

**Date:** 2026-06-07
**Status:** Approved

## Overview

When enabled, the relink feature polls the GW2 alliance spreadsheet and posts a full roster announcement when the configured guild's server assignment changes — i.e., when a new WvW restructuring season is announced and the guild appears in a different sheet tab than before.

## Architecture

This feature lives entirely within the existing `AllianceMatchupCog` in `axitools/cogs/wvw_alliance.py`. No new cog or utility module is needed — the relink detection reuses the existing session, sheet cache, guild-matching logic, and embed style.

## Data Model

Two new fields added to `GuildConfig` in `axitools/storage.py`:

```python
alliance_relink_enabled: bool = False
alliance_relink_last_server: Optional[str] = None
```

- `alliance_relink_enabled` — the on/off toggle, set by `/alliance relink enable/disable`
- `alliance_relink_last_server` — the sheet tab name (server short name, e.g. `"HoJ"`) where the guild was last found; `None` means not yet primed

## Commands

A `relink` subgroup is added under the existing `alliance` command group:

### `/alliance relink enable`

- Requires `ensure_authorised`
- Fails with an ephemeral error if `alliance_guild_id` or `alliance_channel_id` are not configured
- Sets `alliance_relink_enabled = True`
- Runs an immediate silent scan to prime `alliance_relink_last_server` (prevents a false-positive announcement on first enable)
- Responds ephemerally with confirmation

### `/alliance relink disable`

- Requires `ensure_authorised`
- Sets `alliance_relink_enabled = False`
- Leaves `alliance_relink_last_server` intact (so re-enabling doesn't re-announce the current state)
- Responds ephemerally with confirmation

### `/alliance status` (update)

Updated to include relink state: enabled/disabled and last known server.

## Detection Logic

Added to the existing `_poster_loop` (5-minute interval). For each guild with `alliance_relink_enabled = True`:

1. Iterate through all tabs in `WVW_ALLIANCE_SHEET_GIDS` (server short name → sheet GID)
2. Fetch each tab's roster via `_fetch_alliances(sheet_name)` — uses the existing 6-hour cache
3. Search each roster for the configured guild using the existing `_match_guild` normalization logic
4. On match in tab `X`:
   - If `alliance_relink_last_server is not None` and `X != alliance_relink_last_server` → post announcement
   - Set `alliance_relink_last_server = X` and save config

The `last_server is not None` guard prevents announcing on first-ever detection in case priming failed due to a transient error at enable time.

## Announcement Post

Posted to `alliance_channel_id` as a Discord embed:

| Field | Value |
|---|---|
| Title | `🔗 New Server Link Announced` |
| Description | New server name (e.g., "Hall of Judgment") |
| Fields | One field per alliance: alliance name → newline-separated guild list |
| "Independent Guilds" field | Solo guilds, if any present on that tab |
| Footer | Link to sheet tab via `SHEET_EDIT_URL#gid=<gid>` |
| Color | Same blue as existing alliance embeds |

No role ping — the alliance channel is assumed to have appropriate notification settings.

## Error Handling

- If the sheet is unreachable during a poll cycle, log the error and skip — do not clear `last_server`
- If the configured guild is not found in any tab, log a warning and skip — do not clear `last_server`
- Both cases are transient; the next poll cycle retries normally

## Testing

- Unit test `_match_guild` normalization with the guild found in a tab roster
- Integration test: mock sheet returning guild in tab "A", then tab "B" → verify announcement posted
- Test enable with no `alliance_guild_id` configured → verify error response
- Test priming on enable → verify `last_server` set, no announcement posted
