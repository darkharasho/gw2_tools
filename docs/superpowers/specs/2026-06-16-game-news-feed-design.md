# Game News Feed (GW2 + GW3) — Design

**Date:** 2026-06-16
**Status:** Approved (pending spec review)

## Summary

Add a **multi-source game-news feed** that posts new **Guild Wars 2** and
**Guild Wars 3** news articles to a single configurable Discord channel per
guild. The two sources are accessed differently — GW2 exposes an official RSS
feed, GW3 has none and is scraped from its server-rendered news index — but
both flow through one common entry model and post with their own logo
thumbnail and footer so they read as one cohesive feed.

This is **news articles**, distinct from the existing GW2 **patch-notes** feed
(`update_notes.py`, the wiki "Game updates" scraper), which is left untouched.

## Goals

- Post new GW2 and GW3 news articles to one shared, per-guild channel.
- Each post carries the correct source logo (GW2 / GW3) as its thumbnail and
  the article's hero/lead image as the embed image.
- Reuse the existing RSS/rendering helpers for the GW2 source; reuse the
  `update_notes` operational patterns (poll-once-then-iterate-guilds, retrying
  fetch, silent seed, silent re-anchor) for structure.
- Resilient to GW3's volatile Svelte markup.

## Non-Goals (YAGNI)

- Not touching the existing `update_notes` (patch notes) or generic `rss` cogs.
- No per-game channel routing (single shared channel — user decision).
- No full GW3 article-body scraping (its index has no excerpt/date anyway).
- No multi-language (English `/en/` only).

## Source Analysis

| | GW2 news | GW3 news |
|---|---|---|
| Access | RSS feed `https://www.guildwars2.com/en/feed/` | HTML scrape `https://www.guildwars3.com/en/news/` |
| Per item | title, link, **pubDate**, description, `content:encoded` (full HTML, images inline) | title, slug, hero image |
| Date | yes (pubDate) | **none** |
| Dedup key | guid/link + timestamp | slug only |
| Images | first `<img>` inside `content:encoded` (no media enclosures) | card `<img src>` |

### GW3 card markup (index)

```html
<a href="../../en/news/announcing-guild-wars-3">
  <article class="news-article" id="article-announcing-guild-wars-3">
    <img alt="Announcing Guild Wars 3" src="https://d169…/farmartboard.full.jpg"/>
    <h2 class="title">Announcing Guild Wars 3</h2>
  </article>
</a>
```

Class names carry a build-volatile Svelte hash (e.g. `news-article svelte-xvh6k6`).
**Select on the stable base class `news-article` and the `article-` id prefix —
never the hash.** Index DOM order is newest-first.

### Logos

No stable public logo URL exists for either site's branding in a form we
control, so logos are **bundled in the repo** and attached per-message via
Discord `attachment://` (self-hosted, never breaks on site rebuilds):
`axitools/assets/gw2_logo.png`, `axitools/assets/gw3_logo.png`.

## Architecture

New cog `axitools/cogs/game_news.py`, modeled on `update_notes.py`:

- Polls every **15 minutes** via `@tasks.loop`. Each cycle fetches **each
  source once** (shared across guilds), then iterates guilds.
- Registered in `axitools/bot.py` `setup_hook`, after `update_notes`.
- Single per-guild channel `game_news_channel_id`; feed is on when set.
- `get_config_status(guild_id)` implemented and registered for the config
  status display (parallel to `UpdateNotesCog`).
- `/dev gamenewstest` dev command (force-post latest per source, non-prod)
  parallel to the existing update-notes dev test. **Included.**

### Source adapters

A source registry keeps the two access strategies isolated behind one
interface. Each source is described by:

```python
@dataclass(frozen=True)
class NewsSource:
    key: str          # "gw2" | "gw3" — dedup + status key
    label: str        # footer text, e.g. "Guild Wars 2 – News"
    logo_asset: str   # filename under axitools/assets/, e.g. "gw2_logo.png"
```

The cog holds `SOURCES = [GW2, GW3]` and a fetch dispatch:
`_fetch_gw2_entries()` (RSS) and `_fetch_gw3_entries()` (scrape), each returning
`List[GameNewsEntry]` newest-first. Adding a future source = one `NewsSource` +
one fetch method, no change to the poll/post loop.

### Constants

- `GW2_FEED_URL = "https://www.guildwars2.com/en/feed/"`
- `GW3_NEWS_PAGE_URL = "https://www.guildwars3.com/en/news/"`
- `GW3_BASE_URL = "https://www.guildwars3.com"` (resolve relative hrefs)
- `ASSETS_DIR = <package>/assets`

## Data Model

```python
@dataclass
class GameNewsEntry:
    source_key: str           # "gw2" | "gw3"
    entry_id: str             # gw2: guid/link; gw3: slug
    title: str
    url: str                  # absolute article URL
    image_url: Optional[str]  # hero/lead image (set_image)
    published_at: Optional[str] = None  # ISO; None for GW3
    summary: Optional[str] = None        # markdown; None for GW3
```

New persisted status in `axitools/storage.py` (per-source maps so the two
boundaries are independent):

```python
@dataclass
class GameNewsStatus:
    last_entry_ids: Dict[str, str] = field(default_factory=dict)      # source_key -> entry_id
    last_published_at: Dict[str, str] = field(default_factory=dict)   # source_key -> ISO
```

Storage helpers + `data/guild_<id>/game_news.json`:
- `get_game_news_status(guild_id) -> Optional[GameNewsStatus]`
- `save_game_news_status(guild_id, status) -> None`

New `GuildConfig` field: `game_news_channel_id: Optional[int] = None`.

## Fetching & Parsing

### GW2 (RSS adapter)

Reuse the existing `rss` cog helpers (import from `axitools.cogs.rss`, or lift
the shared ones into a small `axitools/news_sources.py` / keep importing — see
Open Decisions):
- `feedparser.parse` the feed (fetched via aiohttp or the shared session).
- `_entry_identifier(entry)` → `entry_id`.
- `_convert_struct_time(entry.published_parsed)` → `published_at` (ISO).
- `_extract_entry_description(entry)` → `summary` (markdown, truncated).
- Image: `_extract_entry_thumbnail(entry)` first (returns None for GW2 — no
  media tags), then fall back to **the first `<img src>` parsed from
  `content:encoded`/`summary` HTML** via BeautifulSoup. Absolute URL only.
- Map each into a `GameNewsEntry(source_key="gw2", …)`, newest-first.

### GW3 (scrape adapter)

`_fetch_gw3_entries()`:
1. `_fetch_url(GW3_NEWS_PAGE_URL)` (retrying fetch reused from update_notes).
2. `soup.select("article.news-article")`.
3. Per card → `GameNewsEntry(source_key="gw3", …)`:
   - **slug:** `article["id"]` minus `article-` prefix; fallback to the wrapping
     `<a href>` last path segment. Skip card if no slug.
   - **title:** `h2.title` text, fallback any `h2`; skip if empty.
   - **url:** resolve anchor href against `GW3_BASE_URL` (handles `../../`);
     fallback `{GW3_BASE_URL}/en/news/{slug}`.
   - **image_url:** `img[src]` if present.
   - `published_at=None`, `summary=None`.
4. DOM order (newest-first). Per-card errors are skipped (debug log), never
   aborting the parse.

## Dedup / Boundary Logic (shared)

One resolver used by both sources, generalizing the `update_notes` method:

`_resolve_new_entries(entries, last_entry_id, last_published_at) -> (new_oldest_first, boundary_found)`
- Walk newest-first; stop at the entry whose `entry_id == last_entry_id`
  (boundary). For sources with timestamps (GW2), also stop when
  `entry.published_at <= last_published_at` (handles guid churn). GW3 passes
  `None` timestamps, so only the id branch applies.
- Per source, per guild:
  - **No status for this source:** seed silently to newest entry, post nothing.
  - **Boundary found:** post collected entries oldest-first; advance
    `last_entry_ids[key]` / `last_published_at[key]` after each successful send.
  - **Boundary not found** (recorded id scrolled off): re-anchor silently to
    newest, post nothing (spam prevention). Log at info.

## Discord Embed

`_send_entry(channel, source, entry)` builds one embed and sends:
- `title` = article title, `url` = article URL, `color` = `BRAND_COLOUR`.
- `set_image(url=entry.image_url)` when present (hero/lead image).
- Thumbnail = source logo via `attachment://<logo_asset>`:
  - If `assets/<logo_asset>` exists, attach `discord.File(path,
    filename=logo_asset)` and `embed.set_thumbnail(url="attachment://<logo_asset>")`.
  - **Graceful fallback:** asset missing → omit thumbnail and file (hero still
    shows). Ships before logos are added; dropping a PNG in later just works.
- `description` = `entry.summary` truncated to 4000 (GW2 only; GW3 has none).
- `embed.timestamp` from `published_at` when present.
- `set_footer(text=source.label)` (e.g. "Guild Wars 2 – News").

The poll loop and `/dev gamenewstest` share `_send_entry`.

## Config UI

`axitools/cogs/config.py`:
- Add `GameNewsChannelSelect` parallel to `UpdateNotesChannelSelect`, wired to
  `config.game_news_channel_id`, added to the setup view with default resolved
  the same way.
- Include `game_news_channel_id` wherever the config summary enumerates channel
  fields.

## Error Handling

- GW2 feed / GW3 page fetch failure: log warning, that source yields `[]` this
  cycle (the other source still processes).
- `discord.Forbidden` / `HTTPException` on send: log warning, stop advancing
  that source's boundary past the failed entry (matches update_notes).
- Channel resolution failure: skip the guild this cycle.
- Parse anomalies (GW3): per-card skip; (GW2): per-entry skip.

## Testing

`tests/test_cogs_game_news.py` with fixtures captured from live sources
(`tests/fixtures/gw2_feed.xml`, `tests/fixtures/gw3_news.html`):
- GW2: parse feed → entries with id, title, absolute URL, pubDate, summary,
  and first-`<img>` image extraction from `content:encoded`.
- GW3: parse index → entries (slug, title, absolute URL, hero image);
  parsing is independent of the volatile Svelte hash.
- Shared resolver: first-run seeds silently (per source); new entries posted
  oldest-first and advance status; boundary-scrolled-off re-anchors silently.
- Independent boundaries: advancing GW2 does not disturb GW3 status and vice
  versa.
- Embed: correct logo thumbnail per source; thumbnail+file omitted when the
  asset is missing; GW2 carries description+timestamp, GW3 does not.

Run with the repo's existing pytest suite; follow `tests/test_cogs_rss.py` and
`tests/test_cogs_update_notes.py` for structure.

## Resolved Decisions

1. **Helper reuse:** import the module-level helpers directly from
   `axitools.cogs.rss` (`_entry_identifier`, `_extract_entry_*`,
   `_convert_struct_time`). Only extract into a neutral module if the import
   reads poorly during implementation.
2. **`/dev gamenewstest`:** included.

## Files Touched

- `axitools/cogs/game_news.py` (new)
- `axitools/storage.py` — `GameNewsStatus`, get/save helpers,
  `GuildConfig.game_news_channel_id`
- `axitools/bot.py` — load the new extension
- `axitools/cogs/config.py` — `GameNewsChannelSelect` + summary field
- `axitools/cogs/dev.py` — optional `/dev gamenewstest`
- `axitools/assets/gw2_logo.png`, `axitools/assets/gw3_logo.png` (new; feature
  degrades gracefully if absent)
- `tests/test_cogs_game_news.py`, `tests/fixtures/gw2_feed.xml`,
  `tests/fixtures/gw3_news.html` (new)
