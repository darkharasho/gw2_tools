# Audit Channel Blacklist — Design

**Date:** 2026-06-08
**Status:** Approved

## Problem

Server admins want to exclude specific channels from audit logging (e.g. spammy
or bot-heavy channels) without disabling audit logging entirely.

## Behavior

Blacklisting a channel suppresses **all audit events tied to that channel**:

- `on_message_delete` — when `message.channel` is blacklisted
- `on_message_edit` — when `after.channel` is blacklisted
- `on_voice_state_update` — when either `before.channel` or `after.channel` is blacklisted
- `on_guild_channel_create` / `on_guild_channel_delete` / `on_guild_channel_update` —
  when the channel's own id is blacklisted

Guild-wide events not tied to a channel (roles, emojis, member updates, server
settings) continue to log normally.

## Data model (`axitools/storage.py`)

Add to `GuildConfig`:

```python
audit_channel_blacklist: List[int] = field(default_factory=list)
```

- Add normalization in the load path (near `audit_channel_id`, ~line 1443) and
  the save path (~line 1683): coerce entries to `int`, drop invalid values,
  and dedupe while preserving order.

## Suppression logic (`axitools/cogs/audit.py`)

Add a helper:

```python
def _is_channel_blacklisted(self, guild: discord.Guild, channel_id: Optional[int]) -> bool:
    if channel_id is None:
        return False
    config = self.bot.get_config(guild.id)
    return channel_id in config.audit_channel_blacklist
```

Guard each channel-tied listener early, returning before building/sending the
event when the relevant channel id is blacklisted. For `on_voice_state_update`,
check both `before.channel` and `after.channel`.

## Commands (`/audit blacklist` subgroup)

Mirror the existing `audit_gw2_key` subgroup patterns.

- `/audit blacklist add channel:#x` — idempotent add; ephemeral confirmation.
- `/audit blacklist remove channel:#x channel_id:<optional str>` — remove by
  picker or, as a fallback for already-deleted channels, by raw id string.
  Autocomplete on currently-blacklisted channels (mirrors `gw2-key remove`).
- `/audit blacklist list` — lists current blacklist as `<#id>` mentions, or
  "No channels are blacklisted." when empty.

All commands gated by `self.bot.ensure_authorised` and persisted via
`self.bot.save_config`.

## Status embed (`axitools/cogs/audit.py`, ~line 1409)

Add a "Blacklisted channels" field listing `<#id>` mentions when the list is
non-empty.

## Testing

- `tests/test_storage.py`: round-trip the new field (save → load preserves the
  list; invalid entries dropped; dedupe).
- `tests/` audit tests: assert a blacklisted channel suppresses message edit/
  delete, voice, and channel create/delete/update events, while a
  non-blacklisted channel still logs.

## Out of scope

- Category-level blacklisting (only individual channels for now).
- Per-event-type granularity (blacklist is all-or-nothing per channel).
