# Guild Wars 3 News Feed — Design

**Date:** 2026-06-16
**Status:** Approved (pending spec review)

## Summary

Add a dedicated game-update-style feed that posts new **Guild Wars 3 news
articles** (from `https://www.guildwars3.com/en/news/`) to a configurable
Discord channel per guild. Modeled directly on the existing GW2
`update_notes` cog. The source has **no RSS/Atom feed**, so it is scraped from
the server-rendered news index. Lightweight: the index page is the only page
parsed.

## Goals

- Post new GW3 news articles (title, link, hero image) to a per-guild channel.
- Mirror the operational behavior and code patterns of `update_notes.py`.
- Be resilient to the site's volatile markup (it is a brand-new Svelte site).
- Never spam: seed silently on first run; re-anchor silently if the boundary
  scrolls off the index.

## Non-Goals (YAGNI)

- No full-article body scraping (index-only; the index has no excerpt anyway).
- No per-source add/remove management commands (single hardcoded source —
  that is what the generic `rss` cog is for).
- No multi-language support (English `/en/news/` only).

## Source Analysis

News index: `https://www.guildwars3.com/en/news/` — server-rendered (plain
`requests` + BeautifulSoup works; confirmed). Each article is a card:

```html
<a href="../../en/news/announcing-guild-wars-3">
  <article class="news-article" id="article-announcing-guild-wars-3">
    <img alt="Announcing Guild Wars 3" src="https://d169…/farmartboard.full.jpg"/>
    <h2 class="title">Announcing Guild Wars 3</h2>
  </article>
</a>
```

Key facts:
- Class names carry a build-volatile Svelte hash suffix (e.g.
  `news-article svelte-xvh6k6`). **Select on the stable base class
  `news-article` and the `article-` id prefix — never the hash.**
- The index carries **no publication date** — only slug, title, and image.
  This shapes dedup (no timestamp fallback).
- Article order in the DOM is newest-first.

There is **no stable public GW3 logo URL** (only a build-hashed webp; the
favicon is an inline data-URI). The thumbnail logo is therefore self-hosted
(see Embed section).

## Architecture

New cog `axitools/cogs/gw3_news.py`, modeled on `update_notes.py`:

- Polls the news index every **15 minutes** via `@tasks.loop`.
- Shared `requests.Session` with the same browser-like `REQUEST_HEADERS`
  pattern used by `update_notes`; `_fetch_url` with retry/backoff reused.
- Registered in `axitools/bot.py` `setup_hook`, immediately after the
  `update_notes` extension load.
- `get_config_status(guild_id)` implemented and registered for the config
  status display (parallel to `UpdateNotesCog`).
- Optional `/dev gw3newstest` dev command (force-post latest, non-prod),
  parallel to the existing update-notes dev test in `axitools/cogs/dev.py`.

### Constants

- `GW3_NEWS_PAGE_URL = "https://www.guildwars3.com/en/news/"`
- `GW3_NEWS_BASE_URL = "https://www.guildwars3.com"` (for resolving relative
  `../../en/news/<slug>` hrefs to absolute).
- `LOGO_ASSET_PATH` → `axitools/assets/gw3_logo.png`
- `LOGO_ATTACHMENT_NAME = "gw3_logo.png"`

## Data Model

```python
@dataclass
class Gw3NewsEntry:
    entry_id: str            # slug, e.g. "announcing-guild-wars-3"
    title: str
    url: str                 # absolute https://www.guildwars3.com/en/news/<slug>
    image_url: Optional[str] # article hero image src
```

New persisted status in `axitools/storage.py`:

```python
@dataclass
class Gw3NewsStatus:
    last_entry_id: Optional[str] = None
```

(No `last_entry_published_at` — the source exposes no date.)

Storage helpers mirroring the update-notes pair:
- `get_gw3_news_status(guild_id) -> Optional[Gw3NewsStatus]`
- `save_gw3_news_status(guild_id, status) -> None`
- Persisted to `data/guild_<id>/gw3_news.json`.

New `GuildConfig` field: `gw3_news_channel_id: Optional[int] = None`.

## Parsing

`_fetch_entries()`:
1. Fetch the index HTML (`_fetch_url`, returns `None` on failure → log + `[]`).
2. `soup.select("article.news-article")`.
3. For each card build a `Gw3NewsEntry`:
   - **slug:** from `article["id"]` stripped of the `article-` prefix; if
     absent/empty, fall back to the last path segment of the wrapping
     `<a href>`. Skip the card if no slug.
   - **title:** `card.select_one("h2.title")` text, falling back to any `h2`;
     skip if empty.
   - **url:** resolve the wrapping anchor's href against `GW3_NEWS_BASE_URL`
     (handles the `../../en/news/<slug>` relative form) → absolute URL. If no
     anchor href, synthesize `{GW3_NEWS_BASE_URL}/en/news/{slug}`.
   - **image_url:** `card.select_one("img")["src"]` if present, else `None`.
4. Return entries in DOM order (newest first).

Defensive: any card that raises or lacks slug/title is skipped (logged at
debug), never aborting the whole parse.

## Dedup / Boundary Logic

Pure slug-based (no timestamp fallback, unlike `update_notes`):

- **First run / no status (or empty `last_entry_id`):** seed silently to the
  newest entry's slug, post nothing.
- **Normal:** walk entries newest-first, collecting until the entry whose slug
  equals `last_entry_id` (the boundary). Post the collected set **oldest-first**,
  advancing `last_entry_id` after each successful send.
- **Boundary not found** (recorded slug scrolled off the index): re-anchor
  silently to the newest slug and post nothing — prevents re-posting the whole
  index. Log at info, matching the update_notes re-anchor behavior.

`_resolve_new_entries(entries, last_entry_id) -> (new_oldest_first, boundary_found)`,
a simplified version of the update_notes method (id-only).

## Discord Embed

One embed per article:
- `title` = article title, `url` = article URL.
- `color` = `BRAND_COLOUR`.
- `set_image(url=entry.image_url)` — the article hero banner (when present).
- Thumbnail = bundled GW3 logo via `attachment://gw3_logo.png`:
  - If `LOGO_ASSET_PATH` exists, send a `discord.File(LOGO_ASSET_PATH,
    filename=LOGO_ATTACHMENT_NAME)` with the message and
    `embed.set_thumbnail(url="attachment://gw3_logo.png")`.
  - **Graceful fallback:** if the asset file is missing, omit the thumbnail
    and the file attachment entirely (hero image still shows). Feature ships
    and works before the logo asset is added; dropping the PNG in later lights
    up the thumbnail with no code change.
- `set_footer(text="Guild Wars 3 – News")`.
- No description (the index provides no excerpt).

A small `_send_entry(channel, entry)` helper builds the embed + optional file
and sends, so the poll loop and the dev command share one path.

## Config UI

In `axitools/cogs/config.py`:
- Add `Gw3NewsChannelSelect` parallel to `UpdateNotesChannelSelect`, wired to
  `config.gw3_news_channel_id`, added to the setup view with its default
  resolved the same way.
- Include `gw3_news_channel_id` wherever the config summary enumerates channel
  fields (alongside `update_notes_channel_id`).

## Error Handling

- Network failures: `_fetch_url` retries with backoff (reused pattern); returns
  `None` → loop logs a warning and skips this cycle.
- `discord.Forbidden` / `discord.HTTPException` on send: log a warning, break
  out of the per-guild send loop (do not advance status past the failed entry),
  matching update_notes.
- Channel resolution failure: skip the guild for this cycle.
- Parse anomalies: per-card skip, never a hard failure.

## Testing

`tests/test_cogs_gw3_news.py` (capture the current index HTML as a fixture):
- Parse fixture → expected `Gw3NewsEntry` list (slug, title, absolute URL,
  image URL); volatile-hash independence (parse still works if the svelte hash
  differs).
- First-run seeding posts nothing and records newest slug.
- New-article detection posts only entries above the boundary, oldest-first,
  advancing status.
- Boundary-scrolled-off → silent re-anchor, posts nothing.
- Embed building: hero via `set_image`; thumbnail present when asset exists,
  omitted (with no file attachment) when it does not.

Run with the repo's existing pytest suite; follow the structure of
`tests/test_cogs_update_notes.py`.

## Files Touched

- `axitools/cogs/gw3_news.py` (new)
- `axitools/storage.py` — `Gw3NewsStatus`, get/save helpers,
  `GuildConfig.gw3_news_channel_id`
- `axitools/bot.py` — load the new extension
- `axitools/cogs/config.py` — `Gw3NewsChannelSelect` + summary field
- `axitools/cogs/dev.py` — optional `/dev gw3newstest`
- `axitools/assets/gw3_logo.png` (new asset; feature degrades gracefully if
  absent)
- `tests/test_cogs_gw3_news.py` (new)
- `tests/fixtures/` — captured GW3 news index HTML
