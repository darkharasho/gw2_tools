# AxiTools Command Normalization (Phase 2) — Design Spec

_Date: 2026-06-09 · Implements menu items A + C + E from `docs/command-audit-2026-06-09.md`_

## Decisions (locked)

- **Rollout: hard cutover + announce.** Rename outright in one release; no dual-registration. Old
  names stop working the moment it ships. We publish a changelog/announcement listing every change.
- **Fold `/guildrole` into `/apikey`** (it's personal GW2-account setup).
- **Fold `/status` into `/config status`.**
- **Full normalization** of the convention bot-wide, not just the worst offenders.
- Sequencing: **E (structural, non-breaking) → A + C (the rename release)**, so the rename diffs
  land in already-split files.

## Convention being applied

1. Lowercase; **no underscores or smushed multi-word** user-visible names — multi-word concepts
   become subgroups (`/alliance setup channel`, not `setchannel`).
2. **Every feature is a group.** No flat top-level commands except genuine one-shots (`/help`, `/reset`).
3. **Intent subgroups for discoverability.** Features that mix configuration + queries + actions use
   task-oriented subgroups so the structure teaches the user where things live:
   - `setup` — configuration of single settings (channel, guild, time).
   - `query` — read/search operations.
   - Anything with its own `add`/`list`/`remove` lifecycle keeps its **own** subgroup
     (`/audit apikey …`, `/audit blacklist …`) — see the nesting constraint below.
   - One-shot actions stay as direct verbs on the group (`/alliance post`, `/alliance refresh`).
4. **Threshold rule for intent subgroups.** A feature gets intent/area subgroups when it has
   **≥5 commands across mixed concerns** → `audit`, `alliance`, `config`, `guildroles`. A feature
   with ≤4 cohesive CRUD commands stays **flat** → `builds`, `stream`, `rss`, `apikey`, `gw2guild`,
   `select`, `comp`. This puts "type the group, discover the commands" exactly where it helps and
   avoids redundant names like `/stream view list`.
5. Verbs: `add` / `remove` / `edit` / `list` / `set` / `clear`. `set` may act as upsert for
   single-value config (`/rss set`).
6. Drop redundant prefixes inside an already-namespaced group (`/audit gw2_query` → `/audit query gw2`).
7. Leaf names carry **no underscores** and drop context-implied prefixes (`/audit setup guild`,
   not `/audit setup gw2_guild`).

## Discord nesting constraint

Discord allows exactly one subgroup level: `/group subgroup command` (3 tokens). `/audit setup
apikey add` (4 tokens) is impossible. Therefore: **single-setting config → under `setup`;
multi-operation areas (apikey, blacklist, query) → their own sibling subgroup.**

---

## Complete target taxonomy (before → after)

Discord nesting limit is one subgroup level (`/group subgroup command`); every entry below respects it.

### `/config` (ConfigCog becomes a group)
| Before | After | Note |
|--------|-------|------|
| `/config` | `/config setup` | bare command can't coexist with a subcommand; `setup` opens the settings UI |
| `/status` | `/config status` | |

### `/audit` (intent subgroups: setup / query, plus lifecycle subgroups)
| Before | After |
|--------|-------|
| `/audit channel` | `/audit setup channel` |
| `/audit gw2_guild` | `/audit setup guild` |
| `/audit query` (discord user) | `/audit query discord` |
| `/audit historical_query` | `/audit query historical` |
| `/audit gw2_query` | `/audit query gw2` |
| `/audit gw2_key add\|list\|remove\|migrate` | `/audit apikey add\|list\|remove\|migrate` |
| `/audit blacklist add\|remove\|list` | `/audit blacklist add\|remove\|list` _(unchanged)_ |

### `/apikey` (user; gains the folded role command)
| Before | After |
|--------|-------|
| `/apikey add\|remove\|list\|refresh\|help` | _(unchanged)_ |
| `/guildrole set` | `/apikey role set` |
| `/guildrole clear` | `/apikey role clear` |

### `/gw2guild`
| Before | After |
|--------|-------|
| `/gw2guild search` | `/gw2guild search` _(unchanged)_ |

### `/guildroles` (admin)
| Before | After |
|--------|-------|
| `/guildroles set\|list\|remove\|audit` | _(unchanged)_ |
| `/guildroles setalliance` | `/guildroles alliance set` |
| `/guildroles clearalliance` | `/guildroles alliance clear` |
| `/guildroles whitelist add\|remove\|list` | `/guildroles allowlist add\|remove\|list` |

### `/alliance` (intent subgroup: setup)
| Before | After |
|--------|-------|
| `/alliance setguild` | `/alliance setup guild` |
| `/alliance setchannel` | `/alliance setup channel` |
| `/alliance settime` | `/alliance setup time` |
| `/alliance postnow` | `/alliance post` |
| `/alliance status\|refresh` | _(unchanged)_ |
| `/alliance relink enable\|disable` | _(unchanged)_ |

### Unchanged feature groups (already conform)
`/builds add|edit|delete` · `/comp manage` + `/comp schedule manage|list` · `/rss list|set|delete` ·
`/stream add|list|remove|update` · `/select query|and|or|help|blanket|ai` · `/reset` · `/help`

### Dev-only commands (gated by `PRODUCTION`) → `/dev` group
| Before | After |
|--------|-------|
| `/arcdps_force_notification` | `/dev arcdps` |
| `/update_notes_force_notification` | `/dev updatenotes` |
| `/rss test` | `/dev rsstest` |

---

## E — Structural refactor (non-breaking, lands first)

1. **Split `accounts.py` (2,411 lines)** by audience:
   - `cogs/account_self.py` — user-facing: `/apikey` (incl. new `role` subgroup), `/gw2guild`.
   - `cogs/guild_roles.py` — admin: `/guildroles` (incl. `alliance`, `allowlist` subgroups).
   - Shared helpers (`_embed` delegating to `brand_embed`, formatting utils) move to the cog that
     owns them or to `utils.py` if used by both. Update `bot.py` extension loads accordingly.
2. **Extract RSS pagination** — the near-identical `_FeedDeleteView`/`_FeedTestView` (+ their page
   buttons) become one reusable paginated-select component (e.g. `cogs/_paginated_select.py` or
   `rendering.py`), parameterized by options + on-select callback.

E ships and is verified before any rename, so the A/C diffs apply to the already-split files.

---

## Migration / data considerations

- Stored config keys (e.g. `audit_gw2_guild_id`, `audit_channel_blacklist`, guild-role mappings)
  are **not** renamed — only the *command surface* changes. No data migration required.
- The folded `/apikey role` and renamed `/guildroles allowlist` operate on the same stored
  fields as before; only the command path changes.
- `/audit apikey migrate` (formerly `gw2_key migrate`) keeps its existing legacy-key migration
  behaviour untouched.

## Rollout artifacts

- A user-facing **CHANGELOG / announcement** block enumerating every before→after rename, suitable
  for posting to servers. Drafted as part of the implementation plan's final task.
- All command renames ship in **one release/PR** so users relearn once.

## Testing strategy

- Each renamed command keeps its existing tests; tests are updated to call the new callback
  names/paths. Behaviour assertions (config mutations, embed shape) stay identical.
- New tests: `/apikey role set|clear` (folded behaviour), `/config status` path, `/dev` group
  gating in non-production.
- Full suite green (currently 186 tests) after each task; `--maxWorkers=2` per machine policy.

## Open questions resolved
- `/config` bare command → `/config setup` (only way to fold `status` under a group; `setup` matches
  the intent-subgroup vocabulary).
- Intent/area subgroups apply per the **threshold rule** (≥5 mixed commands): `audit`, `alliance`,
  `config`, `guildroles`. Simple CRUD features (`builds`, `stream`, `rss`, `apikey`, `gw2guild`,
  `select`, `comp`) stay flat.
- Leaf names carry no underscores (`/audit setup guild`, not `gw2_guild`).
- API-key systems are NOT merged (user `/apikey` vs guild `/audit apikey`); they stay distinct but
  now share the `apikey` vocabulary, which the `/help` categories disambiguate.
