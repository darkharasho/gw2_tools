# WvW Lockout Reminder — Design

**Date:** 2026-07-31
**Status:** Approved (design), pending implementation plan

## Summary

Add a new configurable reminder that alerts a Discord channel ahead of the next
WvW team-assignment **lockout** — the moment rosters lock before World
Restructuring relinks. The lockout time comes from the GW2 API
(`/v2/wvw/timers/lockout`), so operators do not hand-configure the time; they
only choose whether it is on, which channel to post to, and how far in advance
to warn (default 24 hours).

## Background

- Endpoint: `GET https://api.guildwars2.com/v2/wvw/timers/lockout` — no auth,
  scope none. Returns two ISO 8601 timestamps for the *next* lockout:
  ```json
  { "na": "2025-03-04T07:59:00Z", "eu": "2025-03-04T07:59:00Z" }
  ```
  `na` and `eu` are usually equal but can differ.
- Existing pattern to mirror: `AllianceMatchupCog` in
  `axitools/cogs/wvw_alliance.py` — a `@tasks.loop(minutes=5)` (`_poster_loop`)
  that reads per-guild `GuildConfig`, compares a schedule to "now" (PST), posts
  an embed, and stamps a last-post timestamp to dedupe. The recent home-world
  guard established the rule: **only stamp the dedupe marker on a genuinely
  successful post**.
- Config is plain per-guild JSON (`GuildConfig` in `axitools/storage.py`,
  read/normalized in `get_config`, written via `save_config` using `asdict`).
  Not in the SQLCipher DB, so **no migration is required** — but every new field
  needs read-side normalization in `get_config`, or `GuildConfig(**payload)`
  raises on the unknown key.
- World IDs use the new Restructuring team-ID scheme: **NA = `11xxx`,
  EU = `12xxx`** (see `WVW_SERVER_NAMES` in `axitools/constants.py`), so region
  derives as `world_id // 1000` → `11` = na, `12` = eu.

## Requirements

1. Per-guild on/off toggle and a target Discord channel.
2. Configurable lead time — how far before the lockout to post. Default 24 hours.
3. Region resolution: explicit `na`/`eu` override, falling back to auto-derive
   from the guild's alliance home world.
4. Fire exactly once per distinct lockout event.
5. Configurable via both Discord slash commands and the web/dashboard API.

## Design

### Config fields (`GuildConfig`, `axitools/storage.py`)

```python
wvw_lockout_enabled: bool = False              # on/off toggle
wvw_lockout_channel_id: Optional[int] = None   # target channel
wvw_lockout_lead_minutes: int = 1440           # how far in advance (default 24h)
wvw_lockout_region: Optional[str] = None       # "na"/"eu"; None = auto-derive
wvw_lockout_last_fired_for: Optional[str] = None  # ISO of the lockout last alerted on (dedupe)
```

Read-side normalization added to `get_config` (mirroring the
`alliance_relink_enabled` / `alliance_channel_id` normalizers):

- `wvw_lockout_enabled` → `bool` coercion, default `False`.
- `wvw_lockout_channel_id` → int-or-None (accept int or numeric string).
- `wvw_lockout_lead_minutes` → int, clamped to a sane minimum (≥ 5 minutes);
  default 1440 when missing or invalid.
- `wvw_lockout_region` → lowercase, validated to `{"na","eu"}` else `None`.
- `wvw_lockout_last_fired_for` → str-or-None.

Lead time is stored in **minutes** so sub-hour values are possible; the slash
command accepts hours as a decimal for convenience (converted to minutes).

**Dedupe key is the lockout timestamp itself** (`wvw_lockout_last_fired_for`),
not "now". Each distinct lockout event fires exactly once, and the stamp is
written only after a successful post.

### Region resolution

```
region = config.wvw_lockout_region
         or derive_region(_resolve_guild_world(config.alliance_guild_id))
```

`derive_region(world_id)` = `"na"` if `world_id // 1000 == 11`, `"eu"` if `== 12`,
else `None`. If region cannot be resolved (no override and no derivable home
world), skip that guild and log — the reminder simply will not fire until a home
world is set or a region is chosen.

### Fetch + scheduler

- New constant in `wvw_alliance.py`:
  `GW2_WVW_LOCKOUT_URL = "https://api.guildwars2.com/v2/wvw/timers/lockout"`.
- New helper `_fetch_lockout()` — reuses the cog's aiohttp session and
  `_fetch_json` (with its 429 backoff); parses the `na`/`eu` ISO strings into
  timezone-aware datetimes. Returns `None`/partial on failure.
- **Reuse the existing `_poster_loop`** (5-minute tick) rather than adding a
  second timer/session. Once per tick, fetch the lockout payload once; then for
  each guild:
  1. Skip unless `wvw_lockout_enabled` and `wvw_lockout_channel_id` set.
  2. Resolve region (above); skip + log if unresolvable.
  3. `target = lockout[region]`; skip if `None` or already in the past.
  4. `fire_at = target - lead_minutes`.
  5. If `now >= fire_at` **and** `now < target` **and**
     `wvw_lockout_last_fired_for != target_iso`: resolve channel
     (`_resolve_channel`), post the embed, set
     `wvw_lockout_last_fired_for = target_iso`, `save_config`.

Firing resolution is within one tick (~5 min) of `fire_at`, which is acceptable
for a reminder configured hours ahead. The `now < target` guard prevents a
late/first-run fire after the lockout has already passed.

### Message

An embed, e.g.:

- Title: `⚔️ WvW Team Lockout Incoming`
- Lockout time as Discord dynamic timestamps: `<t:UNIX:F>` (absolute) and
  `<t:UNIX:R>` (relative, self-updating).
- Region label (NA/EU).
- Short note that roster / transfer changes lock at that time.

### Config surfaces

**Slash commands** on the existing `alliance` cog (a `lockout` subgroup):

- `lockout enable` / `lockout disable` → set `wvw_lockout_enabled`.
- `lockout set_channel <channel>` → set `wvw_lockout_channel_id`.
- `lockout set_lead <hours>` → set `wvw_lockout_lead_minutes` (hours × 60).
- `lockout set_region <na|eu|auto>` → set `wvw_lockout_region` (`auto` → `None`).
- Include lockout settings in the existing `status` embed.

**Web API** (`axitools/api/server.py`): extend `_alliance_to_json` and
`_handle_alliance_put` with the four configurable fields (bool / int / str
validation, `auto` → `None` for region), reusing `_merge_save`. Same
`GuildConfig` and dashboard section as alliance, so no new route.

## Testing

Mirror `tests/test_cogs_wvw_alliance.py` and `tests/test_config_status.py`:

- **Lead-time boundary:** does not fire before `target - lead`; fires at/after it
  while `now < target`.
- **Region:** derives NA from `11xxx` home world, EU from `12xxx`; explicit
  override wins over derivation.
- **Dedupe:** will not re-fire the same lockout timestamp; a new lockout
  timestamp fires again.
- **Skips:** disabled, no channel, lockout in the past, region unresolvable — all
  no-op (and do not stamp the dedupe marker).
- **Config normalization:** new fields round-trip through `get_config` /
  `save_config`; invalid region → `None`, invalid/short lead → default/min.
- **API:** `PUT` validates and persists the four fields; `GET` reflects them.

## Non-goals / YAGNI

- No per-guild custom message text or embed styling.
- No separate standalone cog (reuse `AllianceMatchupCog`'s loop + session).
- No firing for both regions in one message.
- No DB schema changes.
