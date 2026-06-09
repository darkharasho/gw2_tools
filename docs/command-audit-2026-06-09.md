# AxiTools Command Audit & Consistency Report

_Date: 2026-06-09 · Scope: user-facing command surface + supporting structure_

The bot is live across real guilds, so any rename is a breaking change for users.
Discord has no alias/redirect for slash commands; the safe path for any rename is to
register old+new names together for a transition window, then drop the old one.

---

## 1. Full command tree (what users actually see)

There are **14 top-level roots**. Dev-only commands are gated behind `if not PRODUCTION`.

| Root | Subcommands | Cog | Auth |
|------|-------------|-----|------|
| `/config` | — | config | mod (inline `is_authorised`) |
| `/status` | — | config | mod (inline `is_authorised`) |
| `/help` | — | help | public |
| `/reset` | — | reset | **public (no check)** |
| `/audit` | `channel`, `gw2_guild`, `query`, `historical_query`, `gw2_query` | audit | mod |
| `/audit gw2_key` | `add`, `list`, `remove`, `migrate` | audit | mod |
| `/audit blacklist` | `add`, `remove`, `list` | audit | mod |
| `/apikey` | `add`, `remove`, `list`, `refresh`, `help` | accounts | **public (no check)** |
| `/guildroles` | `set`, `list`, `remove`, `setalliance`, `clearalliance`, `audit` | accounts | mod |
| `/guildroles whitelist` | `add`, `remove`, `list` | accounts | mod |
| `/guildrole` | `set`, `clear` | accounts | **public (no check)** |
| `/gw2guild` | `search` | accounts | **public (no check)** |
| `/select` | `query`, `and`, `or`, `help`, `blanket`, `ai` | select | mod |
| `/builds` | `add`, `edit`, `delete` | builds | mod |
| `/rss` | `list`, `set`, `delete`, `test`(dev) | rss | mixed — `list` is **public** |
| `/comp` | `manage` | comps | mod |
| `/comp schedule` | `manage`, `list` | comps | mod |
| `/alliance` | `setguild`, `setchannel`, `settime`, `status`, `refresh`, `postnow` | wvw_alliance | mixed — `status` is **public** |
| `/alliance relink` | `enable`, `disable` | wvw_alliance | mod |
| `/stream` | `add`, `list`, `remove`, `update` | streaming | mod |
| `/arcdps_force_notification` | — (dev) | arcdps | mod |
| `/update_notes_force_notification` | — (dev) | update_notes | mod |

---

## 2. Consistency findings (prioritized)

### P1 — Confusable / colliding names

- **`/guildroles` (plural, admin) vs `/guildrole` (singular, user preference).** Nearly identical
  names that do completely different things for different audiences. This is the single most
  confusing pair in the bot.
- **Two GW2 API-key systems with different names:** `/apikey *` (per-user account linking) and
  `/audit gw2_key *` (guild-wide audit syncing). Both store GW2 API keys; nothing tells a user
  why there are two or which to use.
- **`/config` vs `/status`** are two top-level roots for one concern. `/status` is logically
  `config status` (the `/status` embed even renders an "Open Config" button).

### P2 — Naming-style drift (no single convention)

- **Smushed words:** `apikey`, `gw2guild`, `setguild`, `setchannel`, `settime`.
- **snake_case:** `gw2_key`, `gw2_guild`, `gw2_query`, `historical_query`,
  `update_notes_force_notification`, `arcdps_force_notification`.
- These collide *inside the same group*: `/audit` has `channel` + `query` (plain) alongside
  `gw2_key`, `gw2_guild`, `gw2_query` (snake). No rule is being followed bot-wide.

### P2 — Verb / structure conventions vary per feature

- create+update merged (`/rss set`) vs split (`/builds add` + `/builds edit`).
- enable/disable (`/alliance relink`) vs set/clear (`/guildroles setalliance`/`clearalliance`)
  vs add/remove (most) vs toggle-via-`set`.
- verb-prefixed flat commands (`/alliance setguild`, `setchannel`, `settime`) where a
  `set` subgroup (`/alliance set guild|channel|time`) would be more discoverable.

### P2 — Authorization is inconsistent and partly invisible

- **Public read commands that `/help` hides:** `/alliance status` and `/rss list` have no auth
  check (anyone can run them) but are **not** in help's hardcoded `PUBLIC_COMMANDS` set, so
  non-moderators never see them in `/help` even though they work. Discoverability bug.
- Other `list`/`status` commands (e.g. `/stream list`, `/guildroles list`) *are* gated. No rule
  distinguishes which reads are public.
- **Two auth-check styles:** `await bot.ensure_authorised(interaction)` (most cogs) vs inline
  `bot.is_authorised(...)` conditionals (`/config`, `/status`).
- The `permissions=` parameter and `REQUIRED_PERMISSIONS` constants imply per-permission
  granularity, but the gate only ever checks **administrator OR a configured moderator role**.
  The granularity is dead code — worth either removing or actually wiring up.

### P3 — Response style is split down the middle

- **Embed + `BRAND_COLOUR`:** accounts, select, comp, reset, update_notes, arcdps,
  `/alliance status`, `/rss list`.
- **Plain text:** the entire `/audit` cog, `/rss set|delete`, `/alliance set*`,
  `/stream remove|update`.
- `/stream` itself mixes both (embed for `list`, plain text + ✓ emoji elsewhere).
- Net: `/audit` looks like a different bot. No shared embed/result builder.

### P3 — `defer()` usage is inconsistent

Some commands `defer()` before slow work (autocomplete-heavy, network calls), others reply
directly. Direct replies on slow paths risk Discord's 3-second "interaction failed" timeout.
No shared pattern (e.g. a decorator or helper) enforces deferring.

### P3 — Help is fragile and uneven

- `cogs/help.py` keeps a **hand-maintained `PUBLIC_COMMANDS` set**. It's already out of sync
  (missing `/alliance status`, `/rss list`). Every new public command must be remembered here.
- Only 2 of 14 features have a `help` subcommand (`/apikey help`, `/select help`); the rest have
  none. No consistent in-feature help convention.
- `/help` flattens 14 roots into one wall of fields with no categorization (e.g.
  "GW2 accounts", "Moderation", "Announcements", "WvW", "Server setup").

### P3 — Dev/test commands are ad-hoc

`/rss test`, `/arcdps_force_notification`, `/update_notes_force_notification` are each gated by
`if not PRODUCTION` with bespoke flat names. No unified dev namespace (e.g. `/dev …`) or shared
gating helper.

### P4 — Structural / maintainer notes

- `accounts.py` (2,411 lines) declares 5 command groups and mixes user-facing account linking
  with admin guild-role config — two audiences in one file. Natural split:
  user (`apikey`, `guildrole`, `gw2guild`) vs admin (`guildroles`).
- `comps.py` (2,391) and `select.py` (1,955) are large but each cohesive (one feature).
- Pagination logic is duplicated (`_FeedDeleteView`/`_FeedTestView` in rss are near-identical;
  several cogs reimplement page buttons).
- Terminology drift: a subgroup named `whitelist` whose own description says "allowlist".

---

## 3. Suggested convention (for whatever we choose to fix)

A single rule set the bot can be measured against:

1. **Names:** lowercase, no underscores in user-visible command/group names; multi-word concepts
   become subgroups, not smushed words (`/alliance set channel`, not `setchannel`).
2. **Grouping:** every feature is a group; no flat top-level commands except genuine one-shots
   (`/help`, `/reset`).
3. **Verbs:** `add` / `remove` / `edit` / `list` / `set` / `clear`. Pick split vs merged
   create+update once and apply everywhere.
4. **Auth:** one helper (`ensure_authorised`) everywhere; an explicit, documented list of
   intentionally-public commands that `/help` derives from automatically (not a hand-kept set).
5. **Responses:** one shared embed/result builder; brand colour everywhere; `defer()` on any
   command that does network/IO.
6. **Help:** auto-generated from the tree + a category tag per group; drop the hardcoded set.

---

## 4. Decision menu

Pick any subset; each is independently shippable.

- **A. Kill the confusables (P1):** rename `/guildrole`→`/myrole` (or fold into `/apikey`),
  unify the two API-key systems' naming, fold `/status` into `/config status`. _Breaking; needs
  transition window._
- **B. Fix help + auth visibility (P3, low risk, non-breaking):** auto-derive public commands,
  add categories, fix the two hidden public commands. _No user-visible command renames._
  ✅ **DONE (2026-06-09)** — help now derives visibility/category from command `extras`
  (hardcoded `PUBLIC_COMMANDS` removed); `/rss list` and `/alliance status` are now mod-gated
  for consistency with their siblings (the one intentional behaviour change).
- **C. Normalize names to the convention (P2):** the big rename pass (`setguild`→`set guild`,
  snake_case→subgroups). _Most breaking; biggest consistency win._
- **D. Unify response style (P3):** shared embed/result builder, brand `/audit`, fix `defer()`.
  _Non-breaking._ ✅ **DONE (2026-06-09)** — `brand_embed` factory in `branding.py` (duplicated
  `_embed` helpers now delegate); all `/audit` text replies are branded embeds; the three
  `/audit` query commands defer before DB/API work.
- **E. Structural refactor (P4):** split `accounts.py`, dedupe pagination. _Non-breaking,
  maintainer-only._

Recommended order: **B → D → A → C → E** (ship the safe, high-visibility wins first; do the
breaking renames as one coordinated transition).
