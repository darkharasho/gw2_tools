# Game News Feed (GW2 + GW3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post new Guild Wars 2 and Guild Wars 3 news articles to one shared, per-guild Discord channel via a new multi-source `game_news` cog.

**Architecture:** A single cog (`axitools/cogs/game_news.py`) polls two sources every 15 minutes — GW2 via its official RSS feed (reusing `cogs/rss.py` helpers), GW3 via HTML scraping its news index — maps both into a common `GameNewsEntry`, dedups each source independently against per-source boundaries stored in a new `GameNewsStatus`, and posts one embed per article with the source's bundled logo as thumbnail and the article's hero image via `set_image`.

**Tech Stack:** Python, discord.py (`commands`, `tasks`, `app_commands`, `discord.ui`), `requests`, `feedparser`, `BeautifulSoup` (`bs4`), `markdownify`, pytest.

## Global Constraints

- Test runner: `pytest` (the repo's existing suite). Run targeted tests with `pytest <path>::<test> -v`.
- Reuse existing helpers verbatim where the spec says so — import from `axitools.cogs.rss`: `_entry_identifier`, `_extract_entry_description`, `_extract_entry_thumbnail`, `_convert_struct_time`.
- Reuse the `update_notes` operational patterns: poll-once-then-iterate-guilds, retrying `_fetch_url`, silent first-run seed, silent re-anchor when the boundary scrolled off.
- Brand colour: `from axitools.branding import BRAND_COLOUR`.
- Logos are bundled assets attached via `attachment://`; when an asset file is absent, the embed must still send (thumbnail + file omitted).
- GW3 parsing must NOT depend on the volatile Svelte hash class suffix (e.g. `svelte-xvh6k6`). Select on `article.news-article` and the `article-` id prefix only.
- Single shared channel: `GuildConfig.game_news_channel_id`. No per-game channels.
- Leave `update_notes.py` and `rss.py` cogs functionally untouched (importing their module-level helpers is fine).

---

## File Structure

- `axitools/storage.py` — add `GameNewsStatus` dataclass + get/save helpers + `GuildConfig.game_news_channel_id` field.
- `axitools/cogs/game_news.py` (new) — the cog: entry model, source registry, fetch adapters, shared resolver, embed/send, poll loop, force-notification.
- `axitools/bot.py` — load the new extension.
- `axitools/cogs/config.py` — "More channels…" button → secondary view with `GameNewsChannelSelect`; add `GameNewsCog` to the config-status cog list.
- `axitools/cogs/dev.py` — `/dev gamenewstest` command.
- `axitools/assets/gw2_logo.png`, `axitools/assets/gw3_logo.png` (new bundled assets; graceful fallback if absent).
- `tests/fixtures/gw2_feed.xml`, `tests/fixtures/gw3_news.html` (new captured fixtures).
- `tests/test_cogs_game_news.py` (new).

---

## Task 1: Storage — status model, persistence, and config field

**Files:**
- Modify: `axitools/storage.py` (near `UpdateNotesStatus` ~line 531; helpers ~line 2067; `GuildConfig` ~line 276)
- Test: `tests/test_storage_game_news.py` (new)

**Interfaces:**
- Produces:
  - `GameNewsStatus(last_entry_ids: Dict[str, str] = {}, last_published_at: Dict[str, str] = {})`
  - `Storage.get_game_news_status(guild_id: int) -> Optional[GameNewsStatus]`
  - `Storage.save_game_news_status(guild_id: int, status: GameNewsStatus) -> None`
  - `GuildConfig.game_news_channel_id: Optional[int] = None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage_game_news.py`:

```python
from pathlib import Path

from axitools.storage import StorageManager, GameNewsStatus


def _storage(tmp_path: Path) -> StorageManager:
    return StorageManager(tmp_path)


def test_game_news_status_round_trip(tmp_path):
    storage = _storage(tmp_path)
    assert storage.get_game_news_status(42) is None

    status = GameNewsStatus(
        last_entry_ids={"gw2": "https://gw2/news/a", "gw3": "announcing-guild-wars-3"},
        last_published_at={"gw2": "2026-06-06T16:00:00+00:00"},
    )
    storage.save_game_news_status(42, status)

    loaded = storage.get_game_news_status(42)
    assert loaded == status
    assert loaded.last_entry_ids["gw3"] == "announcing-guild-wars-3"
    assert "gw3" not in loaded.last_published_at


def test_game_news_status_defaults_are_independent(tmp_path):
    storage = _storage(tmp_path)
    a = GameNewsStatus()
    a.last_entry_ids["gw2"] = "x"
    b = GameNewsStatus()
    assert b.last_entry_ids == {}
```

`StorageManager(root: Path)` is the persistence class (confirmed: `axitools/bot.py` does `self.storage = StorageManager(storage_root)`). The new get/save helpers go inside `StorageManager`, immediately after `save_update_notes_status`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_game_news.py -v`
Expected: FAIL with `ImportError: cannot import name 'GameNewsStatus'`.

- [ ] **Step 3: Add the dataclass**

In `axitools/storage.py`, immediately after the `UpdateNotesStatus` dataclass (~line 536), add:

```python
@dataclass
class GameNewsStatus:
    """Per-source boundaries for the multi-source game news feed.

    Keys are source identifiers ("gw2", "gw3"). Stored independently so one
    source advancing never disturbs another.
    """

    last_entry_ids: Dict[str, str] = field(default_factory=dict)
    last_published_at: Dict[str, str] = field(default_factory=dict)
```

Verify `Dict` and `field` are already imported at the top of `storage.py` (they are used by `GuildConfig`). If not:

Run: `grep -n "from dataclasses import\|from typing import" axitools/storage.py | head`
Ensure `field` (from `dataclasses`) and `Dict` (from `typing`) are present; add to the existing import lines if missing.

- [ ] **Step 4: Add the get/save helpers**

In `axitools/storage.py`, immediately after `save_update_notes_status` (~line 2076), add:

```python
    def get_game_news_status(self, guild_id: int) -> Optional[GameNewsStatus]:
        path = self._guild_path(guild_id) / "game_news.json"
        payload = self._read_json(path, None)
        if not payload:
            return None
        return GameNewsStatus(
            last_entry_ids=dict(payload.get("last_entry_ids", {})),
            last_published_at=dict(payload.get("last_published_at", {})),
        )

    def save_game_news_status(self, guild_id: int, status: GameNewsStatus) -> None:
        path = self._guild_path(guild_id) / "game_news.json"
        self._write_json(path, asdict(status))
```

- [ ] **Step 5: Add the config field**

In `axitools/storage.py`, in the `GuildConfig` dataclass, add directly below `update_notes_channel_id` (~line 285):

```python
    game_news_channel_id: Optional[int] = None
```

(It is a defaulted field, so it is safe to add among the other defaulted fields. Confirm it is placed after the last non-defaulted field to avoid a dataclass ordering error.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_storage_game_news.py -v`
Expected: PASS (2 passed).

Run the broader storage/config suite to catch `GuildConfig` deserialization regressions:
Run: `pytest tests/ -k "storage or config" -q`
Expected: PASS (no failures introduced).

- [ ] **Step 7: Commit**

```bash
git add axitools/storage.py tests/test_storage_game_news.py
git commit -m "feat(storage): GameNewsStatus + game_news_channel_id config field"
```

---

## Task 2: Cog scaffold — entry model, source registry, shared helpers

**Files:**
- Create: `axitools/cogs/game_news.py`
- Test: `tests/test_cogs_game_news.py` (new)

**Interfaces:**
- Consumes: `GameNewsStatus` (Task 1); `BRAND_COLOUR`.
- Produces:
  - `GameNewsEntry(source_key, entry_id, title, url, image_url=None, published_at=None, summary=None)`
  - `NewsSource(key, label, logo_asset)`
  - `GameNewsCog.SOURCES: list[NewsSource]` with keys `"gw2"`, `"gw3"`
  - `GameNewsCog._resolve_new_entries(entries, last_entry_id, last_published_at) -> tuple[list[GameNewsEntry], bool]`
  - `GameNewsCog._parse_timestamp(value) -> Optional[datetime]`
  - `GameNewsCog._truncate(value, limit) -> str`
  - `ASSETS_DIR: Path`, constants `GW2_FEED_URL`, `GW3_NEWS_PAGE_URL`, `GW3_BASE_URL`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cogs_game_news.py`:

```python
import pytest

from axitools.cogs.game_news import (
    GameNewsCog,
    GameNewsEntry,
    NewsSource,
)


def _cog() -> GameNewsCog:
    # Bypass __init__ so we don't start the polling task loop.
    return GameNewsCog.__new__(GameNewsCog)


def _entry(source: str, entry_id: str, iso=None) -> GameNewsEntry:
    return GameNewsEntry(
        source_key=source,
        entry_id=entry_id,
        title=entry_id,
        url=f"https://example/{entry_id}",
        image_url=None,
        published_at=iso,
        summary=None,
    )


def test_sources_registered():
    keys = {s.key for s in GameNewsCog.SOURCES}
    assert keys == {"gw2", "gw3"}
    for s in GameNewsCog.SOURCES:
        assert isinstance(s, NewsSource)
        assert s.logo_asset.endswith(".png")


# GW2 entries carry timestamps (newest first).
GW2_PAGE = [
    _entry("gw2", "n3", "2026-06-06T16:00:00+00:00"),
    _entry("gw2", "n2", "2026-06-05T16:00:00+00:00"),
    _entry("gw2", "n1", "2026-06-01T16:00:00+00:00"),
]

# GW3 entries have no timestamps (newest first).
GW3_PAGE = [
    _entry("gw3", "slug-c"),
    _entry("gw3", "slug-b"),
    _entry("gw3", "slug-a"),
]


def test_resolve_boundary_on_page_ids_only():
    cog = _cog()
    new, found = cog._resolve_new_entries(GW3_PAGE, "slug-a", None)
    assert found is True
    assert [e.entry_id for e in new] == ["slug-b", "slug-c"]


def test_resolve_up_to_date():
    cog = _cog()
    new, found = cog._resolve_new_entries(GW3_PAGE, "slug-c", None)
    assert found is True
    assert new == []


def test_resolve_boundary_scrolled_off():
    cog = _cog()
    new, found = cog._resolve_new_entries(GW3_PAGE, "slug-gone", None)
    assert found is False


def test_resolve_timestamp_fallback_for_gw2():
    # entry_id changed but timestamp says we already have everything up to n2.
    cog = _cog()
    new, found = cog._resolve_new_entries(GW2_PAGE, "missing-id", "2026-06-05T16:00:00+00:00")
    assert found is True
    assert [e.entry_id for e in new] == ["n3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_game_news.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'axitools.cogs.game_news'`.

- [ ] **Step 3: Create the cog scaffold**

Create `axitools/cogs/game_news.py`:

```python
"""Multi-source game news feed (Guild Wars 2 + Guild Wars 3)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import discord
import feedparser
import requests
from bs4 import BeautifulSoup
from discord.ext import commands, tasks

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR
from ..config_status import ConfigStatus, StatusField
from ..storage import GameNewsStatus
from .rss import (
    _convert_struct_time,
    _entry_identifier,
    _extract_entry_description,
    _extract_entry_thumbnail,
)

LOGGER = logging.getLogger(__name__)

GW2_FEED_URL = "https://www.guildwars2.com/en/feed/"
GW3_NEWS_PAGE_URL = "https://www.guildwars3.com/en/news/"
GW3_BASE_URL = "https://www.guildwars3.com"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class GameNewsEntry:
    """A news article from any source, normalized for posting."""

    source_key: str
    entry_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    published_at: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class NewsSource:
    """Describes one news source: its dedup key, footer label, and logo asset."""

    key: str
    label: str
    logo_asset: str


class GameNewsCog(commands.Cog):
    """Poll GW2 (RSS) and GW3 (scrape) news and post new articles."""

    CHECK_INTERVAL_MINUTES = 15

    SOURCES: List[NewsSource] = [
        NewsSource(key="gw2", label="Guild Wars 2 – News", logo_asset="gw2_logo.png"),
        NewsSource(key="gw3", label="Guild Wars 3 – News", logo_asset="gw3_logo.png"),
    ]

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)
        self._poll_news.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_news.cancel()
        self._session.close()

    # ---- shared helpers ---------------------------------------------------

    def _resolve_new_entries(
        self,
        entries: Sequence[GameNewsEntry],
        last_entry_id: Optional[str],
        last_published_at: Optional[str],
    ) -> "Tuple[List[GameNewsEntry], bool]":
        """Return ``(new_entries_oldest_first, boundary_found)``.

        ``boundary_found`` is ``False`` when the recorded entry can no longer be
        located (it scrolled off). The caller re-anchors instead of re-posting
        everything. Mirrors update_notes; the timestamp branch is skipped for
        sources without dates (GW3 passes ``None``).
        """
        if not entries:
            return [], True

        collected: List[GameNewsEntry] = []
        cutoff = self._parse_timestamp(last_published_at)
        boundary_found = not last_entry_id
        for entry in entries:
            if last_entry_id and entry.entry_id == last_entry_id:
                boundary_found = True
                break
            entry_timestamp = self._parse_timestamp(entry.published_at)
            if cutoff and entry_timestamp and entry_timestamp <= cutoff:
                boundary_found = True
                break
            collected.append(entry)

        return list(reversed(collected)), boundary_found

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        candidate = value
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            LOGGER.debug("Unable to parse game news timestamp: %s", value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(GameNewsCog(bot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_game_news.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/game_news.py tests/test_cogs_game_news.py
git commit -m "feat(game-news): cog scaffold, entry model, source registry, resolver"
```

---

## Task 3: GW3 scraper adapter

**Files:**
- Modify: `axitools/cogs/game_news.py`
- Create: `tests/fixtures/gw3_news.html`
- Modify: `tests/test_cogs_game_news.py`

**Interfaces:**
- Consumes: `GameNewsEntry`, `GW3_NEWS_PAGE_URL`, `GW3_BASE_URL`.
- Produces:
  - `GameNewsCog._parse_gw3_card(card) -> Optional[GameNewsEntry]`
  - `GameNewsCog._parse_gw3_html(html: str) -> List[GameNewsEntry]`

- [ ] **Step 1: Capture the fixture**

Run:
```bash
mkdir -p tests/fixtures
curl -sL -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36" \
  https://www.guildwars3.com/en/news/ -o tests/fixtures/gw3_news.html
grep -o 'news-article' tests/fixtures/gw3_news.html | wc -l
```
Expected: a positive count (the page minifies markup onto wrapped lines, so use `grep -o ... | wc -l`, NOT line-based `grep -c`). If `0`, the markup changed — inspect and update the selector in Step 3 before continuing.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_cogs_game_news.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_gw3_html():
    cog = _cog()
    html = (FIXTURES / "gw3_news.html").read_text(encoding="utf-8")
    entries = cog._parse_gw3_html(html)
    assert entries, "expected at least one GW3 article"
    first = entries[0]
    assert first.source_key == "gw3"
    assert first.entry_id and "/" not in first.entry_id  # a slug, not a path
    assert first.title
    assert first.url.startswith("https://www.guildwars3.com/en/news/")
    assert first.published_at is None
    assert first.summary is None


def test_parse_gw3_ignores_svelte_hash():
    # Hash suffix differs from capture time; parser must still find the card.
    cog = _cog()
    html = (
        '<a href="../../en/news/some-post">'
        '<article class="news-article svelte-DIFFERENT" id="article-some-post">'
        '<img src="https://cdn/x.jpg"/><h2 class="title">Some Post</h2>'
        "</article></a>"
    )
    entries = cog._parse_gw3_html(html)
    assert len(entries) == 1
    e = entries[0]
    assert e.entry_id == "some-post"
    assert e.title == "Some Post"
    assert e.url == "https://www.guildwars3.com/en/news/some-post"
    assert e.image_url == "https://cdn/x.jpg"


def test_parse_gw3_skips_cards_without_slug_or_title():
    cog = _cog()
    html = (
        '<article class="news-article"><h2 class="title">No Id No Anchor</h2></article>'
        '<a href="/en/news/has-title"><article class="news-article" id="article-has-title">'
        '<h2 class="title">Has Title</h2></article></a>'
    )
    entries = cog._parse_gw3_html(html)
    assert [e.entry_id for e in entries] == ["has-title"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_cogs_game_news.py -k gw3 -v`
Expected: FAIL with `AttributeError: 'GameNewsCog' object has no attribute '_parse_gw3_html'`.

- [ ] **Step 4: Implement the parser**

In `axitools/cogs/game_news.py`, add these methods to `GameNewsCog` (after `_truncate`):

```python
    def _parse_gw3_html(self, html: str) -> List[GameNewsEntry]:
        soup = BeautifulSoup(html, "html.parser")
        entries: List[GameNewsEntry] = []
        for card in soup.select("article.news-article"):
            try:
                entry = self._parse_gw3_card(card)
            except Exception:  # pragma: no cover - defensive per-card guard
                LOGGER.debug("Failed to parse a GW3 news card", exc_info=True)
                continue
            if entry:
                entries.append(entry)
        return entries

    def _parse_gw3_card(self, card) -> Optional[GameNewsEntry]:
        slug: Optional[str] = None
        card_id = card.get("id") or ""
        if card_id.startswith("article-"):
            slug = card_id[len("article-"):].strip()

        anchor = card.find_parent("a")
        href = anchor.get("href") if anchor else None
        if not slug and href:
            slug = href.rstrip("/").split("/")[-1].strip()
        if not slug:
            return None

        heading = card.select_one("h2.title") or card.find("h2")
        title = heading.get_text(strip=True) if heading else ""
        if not title:
            return None

        if href:
            url = urljoin(GW3_NEWS_PAGE_URL, href)
        else:
            url = f"{GW3_BASE_URL}/en/news/{slug}"

        image = card.find("img")
        image_url = image.get("src") if image and image.get("src") else None

        return GameNewsEntry(
            source_key="gw3",
            entry_id=slug,
            title=title,
            url=url,
            image_url=image_url,
            published_at=None,
            summary=None,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cogs_game_news.py -k gw3 -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/game_news.py tests/test_cogs_game_news.py tests/fixtures/gw3_news.html
git commit -m "feat(game-news): GW3 news index scraper adapter"
```

---

## Task 4: GW2 RSS adapter

**Files:**
- Modify: `axitools/cogs/game_news.py`
- Create: `tests/fixtures/gw2_feed.xml`
- Modify: `tests/test_cogs_game_news.py`

**Interfaces:**
- Consumes: `GameNewsEntry`, rss helpers (`_entry_identifier`, `_extract_entry_description`, `_extract_entry_thumbnail`, `_convert_struct_time`).
- Produces:
  - `GameNewsCog._parse_gw2_feed(raw: str) -> List[GameNewsEntry]`
  - `GameNewsCog._first_image_from_entry(entry) -> Optional[str]`

- [ ] **Step 1: Capture the fixture**

Run:
```bash
curl -sL -A "Mozilla/5.0" https://www.guildwars2.com/en/feed/ -o tests/fixtures/gw2_feed.xml
grep -c "<item>" tests/fixtures/gw2_feed.xml
```
Expected: a positive count (the feed has multiple items).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_cogs_game_news.py`:

```python
def test_parse_gw2_feed():
    cog = _cog()
    raw = (FIXTURES / "gw2_feed.xml").read_text(encoding="utf-8")
    entries = cog._parse_gw2_feed(raw)
    assert entries, "expected GW2 feed items"
    first = entries[0]
    assert first.source_key == "gw2"
    assert first.entry_id
    assert first.url.startswith("https://www.guildwars2.com/")
    assert first.title
    # GW2 feed items carry pubDate -> ISO timestamp.
    assert first.published_at and first.published_at.endswith("+00:00")


def test_first_image_from_entry_reads_content_html():
    cog = _cog()

    class _E(dict):
        pass

    entry = _E()
    entry["content"] = [{"value": '<p>hi</p><img src="//cdn/x.jpg"><img src="y.jpg">'}]
    assert cog._first_image_from_entry(entry) == "https://cdn/x.jpg"


def test_first_image_from_entry_none_when_no_img():
    cog = _cog()
    entry = {"summary": "<p>no images here</p>"}
    assert cog._first_image_from_entry(entry) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_cogs_game_news.py -k gw2 -v`
Expected: FAIL with `AttributeError: ... '_parse_gw2_feed'`.

- [ ] **Step 4: Implement the adapter**

In `axitools/cogs/game_news.py`, add to `GameNewsCog`:

```python
    def _first_image_from_entry(self, entry) -> Optional[str]:
        """Pull the first <img src> from the entry's content/summary HTML.

        The GW2 feed embeds images inline in content:encoded (no media tags),
        so _extract_entry_thumbnail returns None and we fall back to this.
        """
        html_candidates: List[str] = []
        contents = entry.get("content")
        if isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict) and item.get("value"):
                    html_candidates.append(str(item["value"]))
        summary = entry.get("summary") or entry.get("description")
        if summary:
            html_candidates.append(str(summary))

        for html in html_candidates:
            soup = BeautifulSoup(html, "html.parser")
            img = soup.find("img")
            src = img.get("src") if img else None
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http"):
                return src
        return None

    def _parse_gw2_feed(self, raw: str) -> List[GameNewsEntry]:
        parsed = feedparser.parse(raw)
        entries: List[GameNewsEntry] = []
        for entry in parsed.entries:
            entry_id = _entry_identifier(entry)
            link = entry.get("link")
            if not entry_id or not link:
                continue
            title = entry.get("title") or "Guild Wars 2 News"
            published = _convert_struct_time(entry.get("published_parsed"))
            published_at = published.isoformat() if published else None
            summary = _extract_entry_description(entry)
            image = _extract_entry_thumbnail(entry) or self._first_image_from_entry(entry)
            entries.append(
                GameNewsEntry(
                    source_key="gw2",
                    entry_id=entry_id,
                    title=str(title),
                    url=str(link),
                    image_url=image,
                    published_at=published_at,
                    summary=summary,
                )
            )
        return entries
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cogs_game_news.py -k gw2 -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/game_news.py tests/test_cogs_game_news.py tests/fixtures/gw2_feed.xml
git commit -m "feat(game-news): GW2 RSS adapter with inline-image extraction"
```

---

## Task 5: Embed building and send (logo attachment + graceful fallback)

**Files:**
- Modify: `axitools/cogs/game_news.py`
- Modify: `tests/test_cogs_game_news.py`

**Interfaces:**
- Consumes: `GameNewsEntry`, `NewsSource`, `ASSETS_DIR`, `BRAND_COLOUR`.
- Produces:
  - `GameNewsCog._build_embed(source, entry) -> discord.Embed`
  - `GameNewsCog._build_file(source) -> Optional[discord.File]`
  - `GameNewsCog._send_entry(channel, source, entry) -> None` (async)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cogs_game_news.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


def _source(key="gw2"):
    return next(s for s in GameNewsCog.SOURCES if s.key == key)


def test_build_embed_gw2_has_description_image_timestamp_footer():
    cog = _cog()
    src = _source("gw2")
    entry = GameNewsEntry(
        source_key="gw2",
        entry_id="id1",
        title="Title",
        url="https://gw2/news/x",
        image_url="https://cdn/hero.jpg",
        published_at="2026-06-06T16:00:00+00:00",
        summary="Body text",
    )
    embed = cog._build_embed(src, entry)
    assert embed.title == "Title"
    assert embed.url == "https://gw2/news/x"
    assert embed.description == "Body text"
    assert embed.image.url == "https://cdn/hero.jpg"
    assert embed.footer.text == "Guild Wars 2 – News"
    assert embed.timestamp is not None


def test_build_embed_gw3_no_description_no_timestamp():
    cog = _cog()
    src = _source("gw3")
    entry = GameNewsEntry(
        source_key="gw3",
        entry_id="slug",
        title="Hello",
        url="https://gw3/news/slug",
        image_url="https://cdn/hero.jpg",
    )
    embed = cog._build_embed(src, entry)
    assert embed.description in (None, "")
    assert embed.timestamp is None
    assert embed.image.url == "https://cdn/hero.jpg"
    assert embed.footer.text == "Guild Wars 3 – News"


def test_build_file_present_sets_thumbnail():
    cog = _cog()
    src = _source("gw2")
    with patch("axitools.cogs.game_news.Path.exists", return_value=True):
        file = cog._build_file(src)
    assert file is not None
    # Embed thumbnail references the attachment by filename.
    entry = GameNewsEntry(source_key="gw2", entry_id="i", title="t", url="https://u")
    with patch("axitools.cogs.game_news.Path.exists", return_value=True):
        embed = cog._build_embed(src, entry)
    assert embed.thumbnail.url == "attachment://gw2_logo.png"


def test_build_file_absent_returns_none_and_no_thumbnail():
    cog = _cog()
    src = _source("gw2")
    entry = GameNewsEntry(source_key="gw2", entry_id="i", title="t", url="https://u")
    with patch("axitools.cogs.game_news.Path.exists", return_value=False):
        assert cog._build_file(src) is None
        embed = cog._build_embed(src, entry)
    assert embed.thumbnail.url is None


@pytest.mark.asyncio
async def test_send_entry_with_logo_sends_file():
    cog = _cog()
    src = _source("gw2")
    entry = GameNewsEntry(source_key="gw2", entry_id="i", title="t", url="https://u")
    channel = MagicMock()
    channel.send = AsyncMock()
    with patch("axitools.cogs.game_news.Path.exists", return_value=True):
        await cog._send_entry(channel, src, entry)
    _, kwargs = channel.send.call_args
    assert "embed" in kwargs and kwargs.get("file") is not None


@pytest.mark.asyncio
async def test_send_entry_without_logo_omits_file():
    cog = _cog()
    src = _source("gw3")
    entry = GameNewsEntry(source_key="gw3", entry_id="s", title="t", url="https://u")
    channel = MagicMock()
    channel.send = AsyncMock()
    with patch("axitools.cogs.game_news.Path.exists", return_value=False):
        await cog._send_entry(channel, src, entry)
    _, kwargs = channel.send.call_args
    assert "embed" in kwargs and "file" not in kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_game_news.py -k "embed or send or file" -v`
Expected: FAIL with `AttributeError: ... '_build_embed'`.

- [ ] **Step 3: Implement embed/send**

In `axitools/cogs/game_news.py`, add to `GameNewsCog`:

```python
    def _build_embed(self, source: NewsSource, entry: GameNewsEntry) -> discord.Embed:
        embed = discord.Embed(
            title=self._truncate(entry.title, 256),
            url=entry.url,
            color=BRAND_COLOUR,
        )
        if entry.summary:
            embed.description = self._truncate(entry.summary, 4000)
        if entry.image_url:
            embed.set_image(url=entry.image_url)
        timestamp = self._parse_timestamp(entry.published_at)
        if timestamp:
            embed.timestamp = timestamp
        if (ASSETS_DIR / source.logo_asset).exists():
            embed.set_thumbnail(url=f"attachment://{source.logo_asset}")
        embed.set_footer(text=source.label)
        return embed

    def _build_file(self, source: NewsSource) -> Optional[discord.File]:
        path = ASSETS_DIR / source.logo_asset
        if not path.exists():
            return None
        return discord.File(str(path), filename=source.logo_asset)

    async def _send_entry(
        self, channel: discord.abc.Messageable, source: NewsSource, entry: GameNewsEntry
    ) -> None:
        embed = self._build_embed(source, entry)
        file = self._build_file(source)
        if file is not None:
            await channel.send(embed=embed, file=file)
        else:
            await channel.send(embed=embed)
```

Note: `_build_embed` checks `(ASSETS_DIR / source.logo_asset).exists()`; the tests patch `axitools.cogs.game_news.Path.exists`, which covers this call because `ASSETS_DIR` is a `Path`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_game_news.py -k "embed or send or file" -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/game_news.py tests/test_cogs_game_news.py
git commit -m "feat(game-news): embed building and send with logo attachment fallback"
```

---

## Task 6: Fetch dispatch, poll loop, force-notification, channel resolution, config status, bot wiring

**Files:**
- Modify: `axitools/cogs/game_news.py`
- Modify: `axitools/bot.py:51` (load extension)
- Modify: `tests/test_cogs_game_news.py`

**Interfaces:**
- Consumes: all of the above; `GameNewsStatus`, `Storage.get_game_news_status`/`save_game_news_status`.
- Produces:
  - `GameNewsCog._fetch_url(url, retries=3) -> Optional[str]` (async)
  - `GameNewsCog._fetch_entries(source_key) -> List[GameNewsEntry]` (async)
  - `GameNewsCog._resolve_channel(guild, channel_id) -> Optional[discord.TextChannel]` (async)
  - `GameNewsCog._process_guild(guild, source_entries) -> None` (async)
  - `GameNewsCog._poll_news` task loop (async)
  - `GameNewsCog.run_force_notification(interaction) -> None` (async)
  - `GameNewsCog.get_config_status(guild_id) -> ConfigStatus`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cogs_game_news.py`:

```python
def _poll_bot(status, channel):
    bot = MagicMock()
    guild = MagicMock()
    guild.id = 42
    bot.guilds = [guild]
    config = MagicMock()
    config.game_news_channel_id = 999
    bot.get_config.return_value = config
    bot.storage.get_game_news_status.return_value = status
    saved = {}
    bot.storage.save_game_news_status.side_effect = lambda gid, st: saved.update({gid: st})
    return bot, guild, config, saved


@pytest.mark.asyncio
async def test_process_guild_first_run_seeds_silently():
    cog = _cog()
    bot, guild, config, saved = _poll_bot(None, MagicMock())
    cog.bot = bot
    cog._send_entry = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=MagicMock())

    source_entries = {
        "gw2": [_entry("gw2", "n2", "2026-06-06T16:00:00+00:00")],
        "gw3": [_entry("gw3", "slug-b")],
    }
    await cog._process_guild(guild, source_entries)

    cog._send_entry.assert_not_called()
    st = saved[42]
    assert st.last_entry_ids == {"gw2": "n2", "gw3": "slug-b"}


@pytest.mark.asyncio
async def test_process_guild_posts_new_entries_oldest_first():
    cog = _cog()
    status = GameNewsStatus(
        last_entry_ids={"gw2": "n1", "gw3": "slug-a"},
        last_published_at={"gw2": "2026-06-01T16:00:00+00:00"},
    )
    bot, guild, config, saved = _poll_bot(status, MagicMock())
    cog.bot = bot
    cog._send_entry = AsyncMock()
    channel = MagicMock()
    cog._resolve_channel = AsyncMock(return_value=channel)

    source_entries = {
        "gw2": [
            _entry("gw2", "n3", "2026-06-06T16:00:00+00:00"),
            _entry("gw2", "n2", "2026-06-05T16:00:00+00:00"),
            _entry("gw2", "n1", "2026-06-01T16:00:00+00:00"),
        ],
        "gw3": [_entry("gw3", "slug-b"), _entry("gw3", "slug-a")],
    }
    await cog._process_guild(guild, source_entries)

    posted = [c.args[2].entry_id for c in cog._send_entry.call_args_list]
    assert posted == ["n2", "n3", "slug-b"]
    st = saved[42]
    assert st.last_entry_ids["gw2"] == "n3"
    assert st.last_entry_ids["gw3"] == "slug-b"


@pytest.mark.asyncio
async def test_process_guild_reanchors_when_boundary_scrolled_off():
    cog = _cog()
    status = GameNewsStatus(last_entry_ids={"gw3": "slug-gone"})
    bot, guild, config, saved = _poll_bot(status, MagicMock())
    cog.bot = bot
    cog._send_entry = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=MagicMock())

    source_entries = {"gw3": [_entry("gw3", "slug-c"), _entry("gw3", "slug-b")]}
    await cog._process_guild(guild, source_entries)

    cog._send_entry.assert_not_called()
    assert saved[42].last_entry_ids["gw3"] == "slug-c"


@pytest.mark.asyncio
async def test_process_guild_skips_when_no_channel_configured():
    cog = _cog()
    bot, guild, config, saved = _poll_bot(None, MagicMock())
    config.game_news_channel_id = None
    cog.bot = bot
    cog._send_entry = AsyncMock()
    await cog._process_guild(guild, {"gw2": [_entry("gw2", "n", "2026-06-06T16:00:00+00:00")]})
    cog._send_entry.assert_not_called()
    assert saved == {}


def test_get_config_status_configured():
    cog = _cog()
    bot = MagicMock()
    config = MagicMock()
    config.game_news_channel_id = 555
    bot.get_config.return_value = config
    cog.bot = bot
    status = cog.get_config_status(42)
    assert status.fields[0].state == "ok"
    assert "555" in status.fields[0].value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_game_news.py -k "process_guild or config_status" -v`
Expected: FAIL with `AttributeError: ... '_process_guild'`.

- [ ] **Step 3: Implement fetch dispatch, channel resolution, per-guild processing, poll loop, force-notify, status**

In `axitools/cogs/game_news.py`, add to `GameNewsCog`:

```python
    async def _fetch_url(self, url: str, retries: int = 3) -> Optional[str]:
        last_error: Optional[BaseException] = None
        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(self._session.get, url, timeout=30)
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except requests.RequestException as error:
                last_error = error
                LOGGER.warning(
                    "Failed to fetch %s (attempt %s/%s)", url, attempt + 1, retries,
                    exc_info=True,
                )
                if attempt + 1 < retries:
                    await asyncio.sleep(min(5, 2 ** attempt))
        if last_error is not None:
            LOGGER.warning("Giving up on %s after repeated failures", url, exc_info=last_error)
        return None

    async def _fetch_entries(self, source_key: str) -> List[GameNewsEntry]:
        if source_key == "gw2":
            raw = await self._fetch_url(GW2_FEED_URL)
            return self._parse_gw2_feed(raw) if raw else []
        if source_key == "gw3":
            html = await self._fetch_url(GW3_NEWS_PAGE_URL)
            return self._parse_gw3_html(html) if html else []
        return []

    async def _resolve_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel is not None:
            return None
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.warning(
                "Unable to fetch game news channel %s for guild %s", channel_id, guild.id
            )
            return None
        return fetched if isinstance(fetched, discord.TextChannel) else None

    def _seed(self, status: GameNewsStatus, source_key: str, latest: GameNewsEntry) -> None:
        status.last_entry_ids[source_key] = latest.entry_id
        if latest.published_at:
            status.last_published_at[source_key] = latest.published_at

    async def _process_guild(
        self, guild: discord.Guild, source_entries: Dict[str, List[GameNewsEntry]]
    ) -> None:
        config = self.bot.get_config(guild.id)
        channel_id = config.game_news_channel_id
        if not channel_id:
            return

        status = self.bot.storage.get_game_news_status(guild.id) or GameNewsStatus()
        channel: Optional[discord.TextChannel] = None
        changed = False

        for source in self.SOURCES:
            entries = source_entries.get(source.key) or []
            if not entries:
                continue

            last_id = status.last_entry_ids.get(source.key)
            last_pub = status.last_published_at.get(source.key)

            if not last_id:
                self._seed(status, source.key, entries[0])
                changed = True
                continue

            new_entries, boundary_found = self._resolve_new_entries(entries, last_id, last_pub)

            if not boundary_found:
                self._seed(status, source.key, entries[0])
                changed = True
                LOGGER.info(
                    "Game news boundary for guild %s source %s scrolled off; re-anchored to %s",
                    guild.id, source.key, entries[0].entry_id,
                )
                continue

            if not new_entries:
                continue

            if channel is None:
                channel = await self._resolve_channel(guild, channel_id)
            if not channel:
                break

            for entry in new_entries:
                try:
                    await self._send_entry(channel, source, entry)
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Failed to post game news in channel %s for guild %s",
                        channel_id, guild.id,
                    )
                    break
                status.last_entry_ids[source.key] = entry.entry_id
                if entry.published_at:
                    status.last_published_at[source.key] = entry.published_at
                changed = True

        if changed:
            self.bot.storage.save_game_news_status(guild.id, status)

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poll_news(self) -> None:
        if not self.bot.guilds:
            return
        source_entries: Dict[str, List[GameNewsEntry]] = {
            source.key: await self._fetch_entries(source.key) for source in self.SOURCES
        }
        if not any(source_entries.values()):
            return
        for guild in self.bot.guilds:
            await self._process_guild(guild, source_entries)

    @_poll_news.before_loop
    async def _before_poll_news(self) -> None:  # pragma: no cover - discord.py lifecycle
        await self.bot.wait_until_ready()

    async def run_force_notification(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return
        if not await self.bot.ensure_authorised(interaction):
            return

        config = self.bot.get_config(interaction.guild.id)
        channel_id = config.game_news_channel_id
        if not channel_id:
            await interaction.response.send_message(
                "Game news notifications are disabled for this server.", ephemeral=True
            )
            return

        channel = await self._resolve_channel(interaction.guild, channel_id)
        if not channel:
            await interaction.response.send_message(
                "Unable to locate the configured game news channel.", ephemeral=True
            )
            return

        posted = 0
        status = self.bot.storage.get_game_news_status(interaction.guild.id) or GameNewsStatus()
        for source in self.SOURCES:
            entries = await self._fetch_entries(source.key)
            if not entries:
                continue
            entry = entries[0]
            await self._send_entry(channel, source, entry)
            self._seed(status, source.key, entry)
            posted += 1
        self.bot.storage.save_game_news_status(interaction.guild.id, status)

        if posted:
            await interaction.response.send_message(
                f"Posted the latest game news ({posted} source(s)) in {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Unable to fetch any game news right now.", ephemeral=True
            )

    def get_config_status(self, guild_id: int) -> ConfigStatus:
        config = self.bot.get_config(guild_id)
        if config.game_news_channel_id:
            field = StatusField(
                label="Game News Channel",
                value=f"<#{config.game_news_channel_id}>",
                state="ok",
            )
        else:
            field = StatusField(
                label="Game News Channel",
                value="Not configured — use /config setup",
                state="missing",
            )
        return ConfigStatus(
            title="Game News (GW2 + GW3)",
            fields=[field],
            setup_command="/config setup",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_game_news.py -k "process_guild or config_status" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Register the extension**

In `axitools/bot.py`, after the `update_notes` load line (`axitools/bot.py:51`), add:

```python
        await self.load_extension("axitools.cogs.game_news")
```

- [ ] **Step 6: Run the full cog test module + import smoke check**

Run: `pytest tests/test_cogs_game_news.py -v`
Expected: PASS (all tests in the module).

Run: `python -c "import axitools.cogs.game_news"`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add axitools/cogs/game_news.py axitools/bot.py tests/test_cogs_game_news.py
git commit -m "feat(game-news): poll loop, force-notify, config status, bot wiring"
```

---

## Task 7: Config UI — secondary "More channels…" view with the game-news select

**Files:**
- Modify: `axitools/cogs/config.py` (selects ~line 90; `ConfigView` ~line 139; status cog list ~line 283)
- Test: `tests/test_config_game_news.py` (new)

**Background:** The primary `ConfigView` already holds 4 channel selects + a button row = Discord's 5-action-row maximum. A 5th select cannot be added to it. Instead add a "More channels…" button that opens a small secondary view holding `GameNewsChannelSelect`.

**Interfaces:**
- Consumes: `GameNewsCog.get_config_status` (Task 6).
- Produces: `GameNewsChannelSelect`, `MoreChannelsView`, `MoreChannelsButton`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_game_news.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.config import GameNewsChannelSelect, MoreChannelsView


def _view():
    view = MagicMock()
    view.config = MagicMock()
    view.config.game_news_channel_id = None
    view.persist = MagicMock()
    return view


@pytest.mark.asyncio
async def test_game_news_select_sets_channel():
    view = _view()
    select = GameNewsChannelSelect(view, None)
    channel = MagicMock()
    channel.id = 777
    channel.mention = "#news"
    select._values = [channel]  # discord.py stores resolved values internally
    # Patch the `values` property access used in callback:
    type(select).values = property(lambda self: [channel])
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await select.callback(interaction)
    assert view.config.game_news_channel_id == 777
    view.persist.assert_called_once()


@pytest.mark.asyncio
async def test_game_news_select_clears_channel():
    view = _view()
    view.config.game_news_channel_id = 5
    select = GameNewsChannelSelect(view, None)
    type(select).values = property(lambda self: [])
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await select.callback(interaction)
    assert view.config.game_news_channel_id is None
    view.persist.assert_called_once()


def test_more_channels_view_holds_game_news_select():
    parent = _view()
    view = MoreChannelsView(parent, default_channel=None)
    assert any(isinstance(item, GameNewsChannelSelect) for item in view.children)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_game_news.py -v`
Expected: FAIL with `ImportError: cannot import name 'GameNewsChannelSelect'`.

- [ ] **Step 3: Add the select and secondary view**

In `axitools/cogs/config.py`, after the `UpdateNotesChannelSelect` class (~line 116), add:

```python
class GameNewsChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self, view: "discord.ui.View", default_channel: Optional[discord.abc.GuildChannel]
    ) -> None:
        super().__init__(
            placeholder="Select the channel for GW2 + GW3 news",
            channel_types=(discord.ChannelType.text, discord.ChannelType.news),
            min_values=0,
            max_values=1,
            default_values=[default_channel] if default_channel else None,
        )
        self.config_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values:
            channel = self.values[0]
            self.config_view.config.game_news_channel_id = channel.id
            message = f"Game news channel set to {channel.mention}."
        else:
            self.config_view.config.game_news_channel_id = None
            message = "Game news notifications disabled."
        self.config_view.persist()
        await interaction.response.send_message(message, ephemeral=True)


class MoreChannelsView(discord.ui.View):
    """Secondary config page for channels that overflow the primary view's
    5-action-row limit."""

    def __init__(
        self,
        parent_view: "ConfigView",
        default_channel: Optional[discord.abc.GuildChannel],
    ) -> None:
        super().__init__(timeout=300)
        # Reuse the parent's config + persist so edits save to the same place.
        self.config = parent_view.config
        self.persist = parent_view.persist
        self.add_item(GameNewsChannelSelect(self, default_channel))
        self.add_item(CloseButton())
```

`CloseButton` is defined later in the file; if Python name resolution complains at class-definition time, note that `MoreChannelsView.__init__` references it only at call time, so ordering is fine as long as `CloseButton` is defined in the module (it is).

- [ ] **Step 4: Add the "More channels…" button and wire it into `ConfigView`**

In `axitools/cogs/config.py`, add this button class near `CloseButton`:

```python
class MoreChannelsButton(discord.ui.Button["ConfigView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="More channels…")

    async def callback(self, interaction: discord.Interaction) -> None:
        config = self.view.config
        default_channel = (
            interaction.guild.get_channel(config.game_news_channel_id)
            if config.game_news_channel_id
            else None
        )
        await interaction.response.send_message(
            "Additional channels:",
            view=MoreChannelsView(self.view, default_channel),
            ephemeral=True,
        )
```

In `ConfigView.__init__`, add the button to the button row (after `ResetRolesButton()`, before/after `CloseButton()` — buttons share one action row, so this does not add a row):

```python
        self.add_item(MoreChannelsButton())
```

- [ ] **Step 5: Register `GameNewsCog` in the config-status list**

In `axitools/cogs/config.py`, in `_build_status_embed` `cog_names` (~line 283), add `"GameNewsCog"` after `"UpdateNotesCog"`:

```python
            "UpdateNotesCog",
            "GameNewsCog",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config_game_news.py -v`
Expected: PASS (3 passed).

Confirm `Optional` is imported in `config.py` (used in the new select signature):
Run: `grep -n "from typing import" axitools/cogs/config.py`
If `Optional` is absent, add it to the existing typing import.

- [ ] **Step 7: Run the config test suite for regressions**

Run: `pytest tests/ -k config -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add axitools/cogs/config.py tests/test_config_game_news.py
git commit -m "feat(config): secondary view + game news channel select"
```

---

## Task 8: Dev command `/dev gamenewstest`

**Files:**
- Modify: `axitools/cogs/dev.py`
- Test: `tests/test_dev_game_news.py` (new)

**Interfaces:**
- Consumes: `GameNewsCog.run_force_notification` (Task 6).

- [ ] **Step 1: Write the failing test**

Create `tests/test_dev_game_news.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.dev import DevCog


@pytest.mark.asyncio
async def test_gamenewstest_delegates_to_cog():
    bot = MagicMock()
    game_news = MagicMock()
    game_news.run_force_notification = AsyncMock()
    bot.get_cog.return_value = game_news

    cog = DevCog.__new__(DevCog)
    cog.bot = bot
    interaction = MagicMock()

    await DevCog.gamenewstest.callback(cog, interaction)

    bot.get_cog.assert_called_with("GameNewsCog")
    game_news.run_force_notification.assert_awaited_once_with(interaction)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dev_game_news.py -v`
Expected: FAIL with `AttributeError: type object 'DevCog' has no attribute 'gamenewstest'`.

- [ ] **Step 3: Add the command**

In `axitools/cogs/dev.py`, add inside `DevCog` after the `updatenotes` command:

```python
    @app_commands.command(
        name="gamenewstest",
        description="Post the latest GW2 + GW3 news to the configured channel.",
    )
    async def gamenewstest(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("GameNewsCog")
        await cog.run_force_notification(interaction)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dev_game_news.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/dev.py tests/test_dev_game_news.py
git commit -m "feat(dev): /dev gamenewstest force-post command"
```

---

## Task 9: Full-suite verification and logo assets note

**Files:**
- (No source changes unless a regression surfaces.)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -q -p no:randomly` (drop `-p no:randomly` if the plugin is not installed)
Expected: PASS, no new failures. Investigate and fix any failure traced to this feature before proceeding.

- [ ] **Step 2: Import smoke check for the whole bot wiring**

Run: `python -c "import axitools.bot, axitools.cogs.game_news, axitools.cogs.config, axitools.cogs.dev"`
Expected: exit 0.

- [ ] **Step 3: Document the logo-asset follow-up**

The feature ships and runs without the logo files (thumbnail is omitted via graceful fallback). To enable thumbnails, drop two PNGs in (recommended ~128–256px square):
- `axitools/assets/gw2_logo.png`
- `axitools/assets/gw3_logo.png`

Confirm the directory is created so the path resolves even when empty:

```bash
mkdir -p axitools/assets
ls axitools/assets
```

If a real logo asset is added in this branch, commit it:

```bash
git add axitools/assets/gw2_logo.png axitools/assets/gw3_logo.png
git commit -m "assets: bundle GW2/GW3 news logos for embed thumbnails"
```

Otherwise leave a short note in the PR description that the logos are a pending drop-in.

- [ ] **Step 4: Final commit (if any verification fixes were made)**

```bash
git add -A
git commit -m "test(game-news): full-suite verification fixes"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** GW2 RSS adapter (Task 4), GW3 scraper (Task 3), common entry model + registry + resolver (Task 2), per-source independent status (Tasks 1 & 6), shared channel config (Tasks 1 & 7), logo thumbnail + hero image + graceful fallback (Task 5), poll loop / seed / re-anchor (Task 6), config UI (Task 7), dev command (Task 8), tests with captured fixtures (Tasks 3, 4, plus unit tests throughout).
- **Volatile markup:** GW3 parsing uses `article.news-article` + `article-` id prefix only; `test_parse_gw3_ignores_svelte_hash` guards it.
- **Independent boundaries:** `GameNewsStatus` uses per-source dicts; `test_process_guild_posts_new_entries_oldest_first` asserts both sources advance independently in one cycle.
- **Type consistency:** `_send_entry(channel, source, entry)`, `_resolve_new_entries(entries, last_entry_id, last_published_at) -> (list, bool)`, `_build_embed(source, entry)`, `_build_file(source)` used identically across tasks.
- **Discord row limit:** addressed by the secondary `MoreChannelsView` (Task 7 background).
