# AxiTools Command Rename — 2026-06-09

**Heads up: several slash commands were renamed.** We reorganised the command
surface so related actions live under shared groups with consistent, predictable
names — this makes commands easier to discover in Discord's autocomplete and
easier to remember.

> **Your data is safe.** Nothing about your stored settings, API keys, build
> posts, schedules, or guild-role mappings changed. **Only the command names
> changed** — every existing configuration carries over untouched.

Old command names no longer exist, so update any saved macros, pinned
instructions, or server docs to the new names below.

---

## What changed

### Server configuration
| Before | After |
|--------|-------|
| `/config` | `/config setup` |
| `/status` | `/config status` |

### Audit
| Before | After |
|--------|-------|
| `/audit channel` | `/audit setup channel` |
| `/audit gw2_guild` | `/audit setup guild` |
| `/audit query` | `/audit query discord` |
| `/audit historical_query` | `/audit query historical` |
| `/audit gw2_query` | `/audit query gw2` |
| `/audit gw2_key add` | `/audit apikey add` |
| `/audit gw2_key list` | `/audit apikey list` |
| `/audit gw2_key remove` | `/audit apikey remove` |
| `/audit gw2_key migrate` | `/audit apikey migrate` |

### Personal guild role
| Before | After |
|--------|-------|
| `/guildrole set` | `/apikey role set` |
| `/guildrole clear` | `/apikey role clear` |

### Guild role administration
| Before | After |
|--------|-------|
| `/guildroles setalliance` | `/guildroles alliance set` |
| `/guildroles clearalliance` | `/guildroles alliance clear` |
| `/guildroles whitelist add` | `/guildroles allowlist add` |
| `/guildroles whitelist remove` | `/guildroles allowlist remove` |
| `/guildroles whitelist list` | `/guildroles allowlist list` |

### WvW alliance
| Before | After |
|--------|-------|
| `/alliance setguild` | `/alliance setup guild` |
| `/alliance setchannel` | `/alliance setup channel` |
| `/alliance settime` | `/alliance setup time` |
| `/alliance postnow` | `/alliance post` |

### Developer / test helpers (non-production only)
| Before | After |
|--------|-------|
| `/arcdps_force_notification` | `/dev arcdps` |
| `/update_notes_force_notification` | `/dev updatenotes` |
| `/rss test` | `/dev rsstest` |

---

## Why

Consistency and discoverability: multi-word actions are now subcommands under a
shared group (so Discord autocomplete groups them together), smushed and
snake_case names follow one convention, and the confusing `/guildrole` vs
`/guildroles` and dual API-key naming have been untangled.

**Reminder: this is a name change only — all stored settings and data are
unaffected.**
