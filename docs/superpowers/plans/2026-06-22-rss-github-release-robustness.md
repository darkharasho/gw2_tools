# Robust GitHub Release Announcements (RSS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitHub release announcements reliable — never post empty/premature releases, edit-in-place when a release fills in late, and kill the re-announce storm — while making our own apps publish atomically so they only surface complete releases.

**Architecture:** Two parts. **Part B (consumer, this repo):** a GitHub-aware path in `axitools/cogs/rss.py` that discovers releases via the cheap atom feed, fetches ground truth from the GitHub Releases REST API, gates on completeness, posts once and edits the same Discord message within a grace window, and dedupes against a bounded seen-ID set persisted in `axitools/storage.py`. Non-GitHub feeds keep the existing feedparser path untouched. **Part A (producer, external repos):** roll out the existing axipulse/axibridge "golden" `release.yml` (draft → build/attach → notes → atomic publish) to apps missing it so drafts hide incomplete releases from the feed.

**Tech Stack:** Python 3.14, discord.py, aiohttp, feedparser, pytest + pytest-asyncio, `unittest.mock`. GitHub Actions (YAML), `gh` CLI, electron-builder (producer repos).

## Global Constraints

- All new `RssFeedConfig` / `TrackedRelease` fields MUST have defaults — `Storage.get_rss_feeds` drops any feed that raises `TypeError` on `RssFeedConfig(**item)`, so old JSON without the new keys must still load.
- The GitHub token is **optional**: read `AXITOOLS_GITHUB_TOKEN` from env; when absent, call the API unauthenticated. Never require it.
- Completeness gate: a GitHub release is postable iff `draft == False` AND (`len(assets) >= 1` OR non-empty `body`).
- Edit grace window default: **2 hours** from first post. After the window, finalize and stop tracking edits.
- Seen-ID set cap: **50** entries, FIFO.
- Non-GitHub feeds (e.g. `https://www.guildwars2.com/en/feed/`) MUST continue through the existing generic path unchanged.
- "Now" comes from `datetime.now(timezone.utc)` in cog methods only; pure helpers receive timestamps as arguments so they stay unit-testable.
- Run pytest with ≤2 workers per machine memory limits (`-p no:cacheprovider` is fine; if xdist present use `-n 2`).
- Brand colour for embeds: reuse `BRAND_COLOUR` (already imported as `EMBED_COLOR`).

---

## File Structure

- `axitools/cogs/rss.py` — add pure helpers (`_parse_github_repo`, `_github_tag_from_entry`, `_release_is_complete`, `_release_content_hash`, `_within_grace_window`, `_append_seen_id`), the API fetch method, the GitHub-release embed builder, and GitHub branching inside `_process_feed`.
- `axitools/storage.py` — add `TrackedRelease` dataclass, extend `RssFeedConfig`, coerce nested data on load.
- `tests/test_cogs_rss.py` — extend with unit + flow tests.
- `tests/test_storage_rss.py` — **new**, storage round-trip tests.
- Producer repos (external, via `gh`/GitHub MCP): `.github/workflows/release.yml` in `TopStatsAIO` and `gw2_arc_log_uploader`; `RELEASE_NOTES.md` if missing.

---

## Part B — Consumer hardening (this repo)

### Task 1: GitHub feed detection + tag extraction helpers

**Files:**
- Modify: `axitools/cogs/rss.py` (add helpers near `_entry_identifier`, ~line 31)
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Produces:
  - `_parse_github_repo(url: str) -> Optional[Tuple[str, str]]` → `(owner, repo)` for a GitHub releases feed URL, else `None`.
  - `_github_tag_from_entry(entry: Mapping) -> Optional[str]` → release tag from an atom entry's `link` or `id`.

- [ ] **Step 1: Write the failing tests**

```python
from axitools.cogs.rss import _parse_github_repo, _github_tag_from_entry


def test_parse_github_repo_matches_releases_atom():
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO/releases.atom") == ("darkharasho", "TopStatsAIO")
    assert _parse_github_repo("https://github.com/baaron4/GW2-Elite-Insights-Parser/releases") == ("baaron4", "GW2-Elite-Insights-Parser")


def test_parse_github_repo_rejects_non_release_urls():
    assert _parse_github_repo("https://www.guildwars2.com/en/feed/") is None
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO") is None
    assert _parse_github_repo("https://github.com/darkharasho/TopStatsAIO/commits.atom") is None


def test_github_tag_from_entry_prefers_link():
    entry = {
        "link": "https://github.com/darkharasho/TopStatsAIO/releases/tag/v3.4.4",
        "id": "tag:github.com,2008:Repository/954501083/v3.4.4",
    }
    assert _github_tag_from_entry(entry) == "v3.4.4"


def test_github_tag_from_entry_falls_back_to_id():
    entry = {"id": "tag:github.com,2008:Repository/954501083/v3.4.4"}
    assert _github_tag_from_entry(entry) == "v3.4.4"
    assert _github_tag_from_entry({}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cogs_rss.py -k "parse_github_repo or github_tag_from_entry" -v`
Expected: FAIL with `ImportError` / `cannot import name '_parse_github_repo'`.

- [ ] **Step 3: Implement the helpers**

Add near the top of `axitools/cogs/rss.py` (after `_entry_identifier`), and add `import re` and `Mapping` to imports:

```python
import re

_GITHUB_RELEASES_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/releases(?:\.atom)?/?$",
    re.IGNORECASE,
)


def _parse_github_repo(url: str) -> Optional[Tuple[str, str]]:
    if not url:
        return None
    match = _GITHUB_RELEASES_RE.match(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _github_tag_from_entry(entry: "feedparser.FeedParserDict") -> Optional[str]:
    link = entry.get("link")
    if link and "/releases/tag/" in link:
        return link.rsplit("/releases/tag/", 1)[1].strip("/") or None
    entry_id = entry.get("id")
    if entry_id and "/" in str(entry_id):
        candidate = str(entry_id).rsplit("/", 1)[1].strip()
        if candidate:
            return candidate
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_rss.py -k "parse_github_repo or github_tag_from_entry" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): detect GitHub release feeds and extract tags"
```

---

### Task 2: Completeness gate, content hash, grace window, seen-ID cap (pure helpers)

**Files:**
- Modify: `axitools/cogs/rss.py`
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Produces:
  - `_release_is_complete(release: Mapping) -> bool`
  - `_release_content_hash(release: Mapping) -> str`
  - `_within_grace_window(first_posted_at: Optional[str], now: datetime, hours: int = 2) -> bool`
  - `_append_seen_id(seen: List[str], entry_id: str, cap: int = 50) -> List[str]`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone
from axitools.cogs.rss import (
    _release_is_complete,
    _release_content_hash,
    _within_grace_window,
    _append_seen_id,
)


def test_release_is_complete_requires_published_and_content():
    assert _release_is_complete({"draft": False, "assets": [{"name": "App.exe"}], "body": ""}) is True
    assert _release_is_complete({"draft": False, "assets": [], "body": "## Notes"}) is True
    assert _release_is_complete({"draft": False, "assets": [], "body": "   "}) is False
    assert _release_is_complete({"draft": True, "assets": [{"name": "App.exe"}], "body": "x"}) is False


def test_release_content_hash_changes_with_assets_and_body():
    base = {"name": "v1", "body": "notes", "assets": [{"name": "a.exe"}]}
    changed_body = {"name": "v1", "body": "notes updated", "assets": [{"name": "a.exe"}]}
    changed_assets = {"name": "v1", "body": "notes", "assets": [{"name": "a.exe"}, {"name": "b.AppImage"}]}
    assert _release_content_hash(base) == _release_content_hash(dict(base))
    assert _release_content_hash(base) != _release_content_hash(changed_body)
    assert _release_content_hash(base) != _release_content_hash(changed_assets)


def test_release_content_hash_ignores_asset_order():
    a = {"name": "v1", "body": "n", "assets": [{"name": "a"}, {"name": "b"}]}
    b = {"name": "v1", "body": "n", "assets": [{"name": "b"}, {"name": "a"}]}
    assert _release_content_hash(a) == _release_content_hash(b)


def test_within_grace_window():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    assert _within_grace_window("2026-06-22T11:00:00Z", now, hours=2) is True
    assert _within_grace_window("2026-06-22T09:30:00Z", now, hours=2) is False
    assert _within_grace_window(None, now, hours=2) is False


def test_append_seen_id_dedupes_and_caps():
    seen = []
    for i in range(55):
        seen = _append_seen_id(seen, f"id-{i}", cap=50)
    assert len(seen) == 50
    assert "id-0" not in seen
    assert "id-54" in seen
    # re-adding an existing id moves it to the end, no duplicate
    seen2 = _append_seen_id(seen, "id-54", cap=50)
    assert seen2.count("id-54") == 1
    assert len(seen2) == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cogs_rss.py -k "release_is_complete or content_hash or grace_window or append_seen_id" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the helpers**

Add to `axitools/cogs/rss.py` (add `import hashlib` and `from datetime import ... datetime, timezone` already present):

```python
import hashlib


def _release_is_complete(release: "feedparser.FeedParserDict") -> bool:
    if release.get("draft"):
        return False
    has_assets = bool(release.get("assets"))
    body = (release.get("body") or "").strip()
    return has_assets or bool(body)


def _release_content_hash(release: "feedparser.FeedParserDict") -> str:
    name = str(release.get("name") or "")
    body = str(release.get("body") or "")
    asset_names = sorted(
        str(asset.get("name") or "")
        for asset in (release.get("assets") or [])
        if isinstance(asset, dict)
    )
    payload = " ".join([name, body, *asset_names])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _within_grace_window(
    first_posted_at: Optional[str], now: datetime, hours: int = 2
) -> bool:
    if not first_posted_at:
        return False
    try:
        posted = datetime.fromisoformat(first_posted_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = now - posted
    return 0 <= delta.total_seconds() <= hours * 3600


def _append_seen_id(seen: List[str], entry_id: str, cap: int = 50) -> List[str]:
    result = [item for item in seen if item != entry_id]
    result.append(entry_id)
    if len(result) > cap:
        result = result[-cap:]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_rss.py -k "release_is_complete or content_hash or grace_window or append_seen_id" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): completeness gate, content hash, grace window, seen-id cap"
```

---

### Task 3: Storage schema — `TrackedRelease` + extended `RssFeedConfig` with safe load

**Files:**
- Modify: `axitools/storage.py` (dataclass ~line 478; `get_rss_feeds` ~line 2308)
- Test: `tests/test_storage_rss.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TrackedRelease(entry_id: str, message_id: Optional[int]=None, content_hash: Optional[str]=None, first_posted_at: Optional[str]=None, finalized: bool=False)`
  - `RssFeedConfig` gains `seen_entry_ids: List[str] = []` and `tracked_releases: Dict[str, TrackedRelease] = {}`.
  - `Storage.get_rss_feeds` coerces nested dicts into `TrackedRelease`; `save_rss_feeds` round-trips via `asdict`.

- [ ] **Step 1: Write the failing tests**

```python
import tempfile
from pathlib import Path

import pytest

from axitools.storage import Storage, RssFeedConfig, TrackedRelease


@pytest.fixture
def storage(tmp_path):
    return Storage(data_dir=tmp_path)


def test_old_feed_json_loads_with_defaults(storage, tmp_path):
    # Simulate a pre-existing file without the new keys.
    guild_dir = tmp_path / "guild_123"
    guild_dir.mkdir(parents=True)
    (guild_dir / "rss_feeds.json").write_text(
        '[{"name": "TSA", "url": "https://github.com/x/y/releases.atom", '
        '"channel_id": 5, "last_entry_id": "tag:1/v1"}]'
    )
    feeds = storage.get_rss_feeds(123)
    assert len(feeds) == 1
    assert feeds[0].seen_entry_ids == []
    assert feeds[0].tracked_releases == {}


def test_tracked_release_roundtrips(storage):
    feed = RssFeedConfig(
        name="TSA",
        url="https://github.com/x/y/releases.atom",
        channel_id=5,
        seen_entry_ids=["tag:1/v1"],
        tracked_releases={
            "tag:1/v1": TrackedRelease(
                entry_id="tag:1/v1",
                message_id=999,
                content_hash="abc",
                first_posted_at="2026-06-22T12:00:00Z",
                finalized=False,
            )
        },
    )
    storage.save_rss_feeds(42, [feed])
    loaded = storage.get_rss_feeds(42)
    assert loaded[0].seen_entry_ids == ["tag:1/v1"]
    tracked = loaded[0].tracked_releases["tag:1/v1"]
    assert isinstance(tracked, TrackedRelease)
    assert tracked.message_id == 999
    assert tracked.content_hash == "abc"
    assert tracked.finalized is False
```

Note: confirm the `Storage` constructor signature first — if it is not
`Storage(data_dir=...)`, adapt the fixture to the real constructor (grep
`class Storage` in `axitools/storage.py`). Keep the fixture pointing at
`tmp_path`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage_rss.py -v`
Expected: FAIL with `ImportError: cannot import name 'TrackedRelease'`.

- [ ] **Step 3: Implement the schema + safe load**

In `axitools/storage.py`, ensure `from dataclasses import dataclass, field, asdict` and `from typing import Dict, List, Optional` are imported. Add above `RssFeedConfig`:

```python
@dataclass
class TrackedRelease:
    """A GitHub release the bot has posted and may still edit in place."""

    entry_id: str
    message_id: Optional[int] = None
    content_hash: Optional[str] = None
    first_posted_at: Optional[str] = None  # ISO8601 UTC
    finalized: bool = False
```

Extend `RssFeedConfig`:

```python
@dataclass
class RssFeedConfig:
    """Persisted configuration for an RSS or Atom feed subscription."""

    name: str
    url: str
    channel_id: int
    last_entry_id: Optional[str] = None
    last_entry_published_at: Optional[str] = None
    seen_entry_ids: List[str] = field(default_factory=list)
    tracked_releases: Dict[str, TrackedRelease] = field(default_factory=dict)
```

Replace the body of `get_rss_feeds` so nested dicts become `TrackedRelease`:

```python
    def get_rss_feeds(self, guild_id: int) -> List[RssFeedConfig]:
        path = self._guild_path(guild_id) / "rss_feeds.json"
        payload = self._read_json(path, [])
        feeds: List[RssFeedConfig] = []
        for item in payload:
            try:
                tracked_raw = item.pop("tracked_releases", {}) or {}
                feed = RssFeedConfig(**item)
            except TypeError:
                continue
            feed.tracked_releases = {
                key: TrackedRelease(**value) if isinstance(value, dict) else value
                for key, value in tracked_raw.items()
            }
            feeds.append(feed)
        return feeds
```

`save_rss_feeds` already uses `asdict(feed)`, which recursively converts nested
`TrackedRelease` dataclasses to dicts — no change needed there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage_rss.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/storage.py tests/test_storage_rss.py
git commit -m "feat(storage): TrackedRelease + GitHub-aware RssFeedConfig fields"
```

---

### Task 4: GitHub Releases API fetch

**Files:**
- Modify: `axitools/cogs/rss.py` (new method on `RssFeedsCog`)
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Consumes: `_get_session()` (existing aiohttp session helper).
- Produces: `async def _fetch_github_release(self, owner: str, repo: str, tag: str) -> Optional[dict]` — returns the release JSON dict or `None` on any failure. Reads `AXITOOLS_GITHUB_TOKEN` from env for the `Authorization` header when present.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            import aiohttp
            raise aiohttp.ClientResponseError(MagicMock(), (), status=self.status)

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_fetch_github_release_returns_json(mock_bot_rss, monkeypatch):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(200, {"name": "v1", "draft": False}))
    cog._get_session = AsyncMock(return_value=session)

    release = await cog._fetch_github_release("darkharasho", "TopStatsAIO", "v1")
    assert release == {"name": "v1", "draft": False}
    url = session.get.call_args[0][0]
    assert url == "https://api.github.com/repos/darkharasho/TopStatsAIO/releases/tags/v1"


@pytest.mark.asyncio
async def test_fetch_github_release_handles_404(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(404, {}))
    cog._get_session = AsyncMock(return_value=session)

    assert await cog._fetch_github_release("o", "r", "v9") is None


@pytest.mark.asyncio
async def test_fetch_github_release_sends_token_when_present(mock_bot_rss, monkeypatch):
    monkeypatch.setenv("AXITOOLS_GITHUB_TOKEN", "ghp_test")
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()

    session = MagicMock()
    session.get = MagicMock(return_value=_FakeResp(200, {"name": "v1"}))
    cog._get_session = AsyncMock(return_value=session)

    await cog._fetch_github_release("o", "r", "v1")
    headers = session.get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer ghp_test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cogs_rss.py -k fetch_github_release -v`
Expected: FAIL with `AttributeError: ... no attribute '_fetch_github_release'`.

- [ ] **Step 3: Implement the method**

Add `import os` to `axitools/cogs/rss.py`, then add to `RssFeedsCog`:

```python
    GITHUB_API_BASE = "https://api.github.com"

    async def _fetch_github_release(
        self, owner: str, repo: str, tag: str
    ) -> Optional[dict]:
        session = await self._get_session()
        url = f"{self.GITHUB_API_BASE}/repos/{owner}/{repo}/releases/tags/{tag}"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("AXITOOLS_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, ValueError):
            LOGGER.warning("Failed to fetch GitHub release %s/%s@%s", owner, repo, tag, exc_info=True)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_rss.py -k fetch_github_release -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): fetch GitHub release detail via REST API"
```

---

### Task 5: GitHub release embed builder

**Files:**
- Modify: `axitools/cogs/rss.py`
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Consumes: `RssFeedConfig`, `truncate_embed_field` (already imported).
- Produces: `_build_github_release_embed(self, feed_config: RssFeedConfig, release: dict) -> discord.Embed`.

- [ ] **Step 1: Write the failing test**

```python
def test_build_github_release_embed(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    feed = RssFeedConfig(name="TSA", url="https://github.com/darkharasho/TopStatsAIO/releases.atom", channel_id=1)
    release = {
        "name": "v3.4.4",
        "tag_name": "v3.4.4",
        "html_url": "https://github.com/darkharasho/TopStatsAIO/releases/tag/v3.4.4",
        "body": "## What's new\n- Fixed crash",
        "assets": [
            {"name": "TopStatsAIO-Setup.exe", "browser_download_url": "https://example/exe"},
        ],
        "published_at": "2026-06-22T12:00:00Z",
    }
    embed = cog._build_github_release_embed(feed, release)
    assert embed.title == "v3.4.4"
    assert embed.url == release["html_url"]
    assert "Fixed crash" in embed.description
    # asset listed
    field_text = "\n".join(f.value for f in embed.fields)
    assert "TopStatsAIO-Setup.exe" in field_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cogs_rss.py -k build_github_release_embed -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the builder**

```python
    def _build_github_release_embed(
        self, feed_config: RssFeedConfig, release: dict
    ) -> discord.Embed:
        title = release.get("name") or release.get("tag_name") or "New release"
        url = release.get("html_url") or feed_config.url
        embed = discord.Embed(title=title, url=url, color=self.EMBED_COLOR)

        body = (release.get("body") or "").strip()
        if body:
            embed.description = truncate_embed_field(body, 1800)

        published = release.get("published_at")
        if published:
            parsed = _convert_iso8601(published)
            if parsed:
                embed.timestamp = parsed

        assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
        if assets:
            lines = []
            for asset in assets[:10]:
                name = asset.get("name") or "download"
                link = asset.get("browser_download_url")
                lines.append(f"[{name}]({link})" if link else name)
            embed.add_field(name="Downloads", value="\n".join(lines)[:1024], inline=False)

        if release.get("prerelease"):
            embed.set_footer(text=f"{feed_config.name} · pre-release")
        else:
            embed.set_footer(text=f"{feed_config.name} · release")
        return embed
```

Add the small ISO helper near `_convert_struct_time`:

```python
def _convert_iso8601(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cogs_rss.py -k build_github_release_embed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): build GitHub release embed from API data"
```

---

### Task 6: GitHub processing path — discover, gate, post once, track

**Files:**
- Modify: `axitools/cogs/rss.py` (`_process_feed` branches; new `_process_github_feed`)
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Consumes: `_parse_github_repo`, `_github_tag_from_entry`, `_fetch_github_release`, `_release_is_complete`, `_release_content_hash`, `_append_seen_id`, `_build_github_release_embed`, `_resolve_channel`, `TrackedRelease`.
- Produces: `async def _process_github_feed(self, guild, feed_config, parsed_feed, owner, repo) -> Optional[RssFeedConfig]`. `_process_feed` routes GitHub feeds here.

Behaviour per poll:
1. For each atom entry (newest first), compute `entry_id` via `_entry_identifier`.
2. Skip if `entry_id in seen_entry_ids` AND its tracked release is finalized (or no tracking needed).
3. For un-posted entries: fetch the release via API; if not `_release_is_complete`, skip (re-evaluated next poll).
4. If complete and not yet posted: build embed, post, record `TrackedRelease(message_id, content_hash, first_posted_at=now)`, add to `seen_entry_ids`.
5. Return updated `feed_config` if anything changed, else `None`.

(Edit-in-place for already-posted releases is Task 7.)

- [ ] **Step 1: Write the failing tests**

```python
import types
from datetime import datetime, timezone
from axitools.storage import RssFeedConfig, TrackedRelease


def _atom_entry(tag, repo_id="1"):
    return {
        "id": f"tag:github.com,2008:Repository/{repo_id}/{tag}",
        "link": f"https://github.com/o/r/releases/tag/{tag}",
        "title": tag,
    }


@pytest.mark.asyncio
async def test_github_feed_skips_incomplete_release(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=777))
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock(return_value={"draft": False, "assets": [], "body": "   "})

    feed = RssFeedConfig(name="r", url="https://github.com/o/r/releases.atom", channel_id=1)
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    channel.send.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_github_feed_posts_complete_release_once(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    msg = MagicMock(id=777)
    channel = MagicMock()
    channel.send = AsyncMock(return_value=msg)
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock(
        return_value={"name": "v1", "tag_name": "v1", "draft": False,
                      "assets": [{"name": "a.exe"}], "body": "notes",
                      "html_url": "https://github.com/o/r/releases/tag/v1"}
    )

    feed = RssFeedConfig(name="r", url="https://github.com/o/r/releases.atom", channel_id=1)
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    channel.send.assert_awaited_once()
    assert result is not None
    entry_id = _atom_entry("v1")["id"]
    assert entry_id in result.seen_entry_ids
    tracked = result.tracked_releases[entry_id]
    assert tracked.message_id == 777
    assert tracked.content_hash

    # Second poll with same feed state => no re-post.
    channel.send.reset_mock()
    result2 = await cog._process_github_feed(MagicMock(), result, parsed, "o", "r")
    channel.send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cogs_rss.py -k github_feed -v`
Expected: FAIL with `AttributeError: ... '_process_github_feed'`.

- [ ] **Step 3: Implement the processing path**

Add to `RssFeedsCog`:

```python
    EDIT_GRACE_HOURS = 2

    async def _process_github_feed(
        self,
        guild: "discord.Guild",
        feed_config: RssFeedConfig,
        parsed_feed: "feedparser.FeedParserDict",
        owner: str,
        repo: str,
    ) -> Optional[RssFeedConfig]:
        entries = list(getattr(parsed_feed, "entries", []) or [])
        if not entries:
            return None

        channel = await self._resolve_channel(guild, feed_config.channel_id)
        if not channel:
            LOGGER.warning(
                "Configured RSS channel %s for guild %s is not accessible",
                feed_config.channel_id, guild.id,
            )
            return None

        seen = list(feed_config.seen_entry_ids)
        tracked = dict(feed_config.tracked_releases)
        changed = False
        now = datetime.now(timezone.utc)

        # Oldest -> newest so posts arrive in chronological order.
        for entry in reversed(entries):
            entry_id = _entry_identifier(entry)
            if not entry_id:
                continue
            existing = tracked.get(entry_id)
            if existing and existing.finalized:
                continue
            if existing and existing.message_id:
                # Edit-in-place handled in Task 7.
                continue
            if entry_id in seen and not existing:
                continue

            tag = _github_tag_from_entry(entry)
            if not tag:
                continue
            release = await self._fetch_github_release(owner, repo, tag)
            if not release or not _release_is_complete(release):
                continue

            embed = self._build_github_release_embed(feed_config, release)
            try:
                message = await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "Failed to post GitHub release '%s' to channel %s in guild %s",
                    tag, feed_config.channel_id, guild.id,
                )
                continue

            tracked[entry_id] = TrackedRelease(
                entry_id=entry_id,
                message_id=getattr(message, "id", None),
                content_hash=_release_content_hash(release),
                first_posted_at=now.isoformat(),
                finalized=False,
            )
            seen = _append_seen_id(seen, entry_id)
            changed = True

        if not changed:
            return None
        return replace(feed_config, seen_entry_ids=seen, tracked_releases=tracked)
```

Route GitHub feeds in `_process_feed` (replace its body):

```python
    async def _process_feed(self, guild: discord.Guild, feed_config: RssFeedConfig) -> Optional[RssFeedConfig]:
        parsed = await self._fetch_feed(feed_config.url)
        if not parsed or not parsed.entries:
            return None

        repo = _parse_github_repo(feed_config.url)
        if repo:
            return await self._process_github_feed(guild, feed_config, parsed, repo[0], repo[1])

        new_entries = _resolve_new_entries(parsed.entries, feed_config.last_entry_id)
        if not new_entries:
            return None
        return await self._post_entries(guild, feed_config, new_entries, parsed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_rss.py -k github_feed -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): GitHub-aware processing — gate and post complete releases once"
```

---

### Task 7: Edit-in-place within the grace window

**Files:**
- Modify: `axitools/cogs/rss.py` (`_process_github_feed`)
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Consumes: `_within_grace_window`, `_release_content_hash`, `channel.fetch_message`.
- Produces: updated `_process_github_feed` that edits the stored message when a tracked, non-finalized release within the grace window has a changed content hash; finalizes past the window or on `NotFound`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_github_feed_edits_message_when_content_changes(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    edited = AsyncMock()
    message = MagicMock(id=777)
    message.edit = edited
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock(return_value=message)
    cog._resolve_channel = AsyncMock(return_value=channel)
    # New body => new hash vs the stored one.
    cog._fetch_github_release = AsyncMock(
        return_value={"name": "v1", "tag_name": "v1", "draft": False,
                      "assets": [{"name": "a.exe"}], "body": "notes UPDATED",
                      "html_url": "https://github.com/o/r/releases/tag/v1"}
    )

    entry_id = _atom_entry("v1")["id"]
    now = datetime.now(timezone.utc)
    feed = RssFeedConfig(
        name="r", url="https://github.com/o/r/releases.atom", channel_id=1,
        seen_entry_ids=[entry_id],
        tracked_releases={entry_id: TrackedRelease(
            entry_id=entry_id, message_id=777, content_hash="STALE",
            first_posted_at=now.isoformat(), finalized=False)},
    )
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    edited.assert_awaited_once()
    channel.send.assert_not_called()
    assert result.tracked_releases[entry_id].content_hash != "STALE"


@pytest.mark.asyncio
async def test_github_feed_finalizes_after_grace_window(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=channel)
    cog._fetch_github_release = AsyncMock()

    entry_id = _atom_entry("v1")["id"]
    old = "2026-06-20T00:00:00Z"  # well past 2h
    feed = RssFeedConfig(
        name="r", url="https://github.com/o/r/releases.atom", channel_id=1,
        seen_entry_ids=[entry_id],
        tracked_releases={entry_id: TrackedRelease(
            entry_id=entry_id, message_id=777, content_hash="x",
            first_posted_at=old, finalized=False)},
    )
    parsed = types.SimpleNamespace(entries=[_atom_entry("v1")], feed={})

    result = await cog._process_github_feed(MagicMock(), feed, parsed, "o", "r")
    cog._fetch_github_release.assert_not_called()  # no API call once past window
    channel.fetch_message.assert_not_called()
    assert result.tracked_releases[entry_id].finalized is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cogs_rss.py -k "edits_message or finalizes_after" -v`
Expected: FAIL (currently the tracked+message_id branch just `continue`s; no edit, no finalize).

- [ ] **Step 3: Implement edit-in-place**

In `_process_github_feed`, replace the `if existing and existing.message_id:` branch with:

```python
            if existing and existing.message_id:
                if not _within_grace_window(existing.first_posted_at, now, self.EDIT_GRACE_HOURS):
                    if not existing.finalized:
                        tracked[entry_id] = replace(existing, finalized=True)
                        changed = True
                    continue
                tag = _github_tag_from_entry(entry)
                if not tag:
                    continue
                release = await self._fetch_github_release(owner, repo, tag)
                if not release or not _release_is_complete(release):
                    continue
                new_hash = _release_content_hash(release)
                if new_hash == existing.content_hash:
                    continue
                try:
                    message = await channel.fetch_message(existing.message_id)
                    await message.edit(embed=self._build_github_release_embed(feed_config, release))
                except discord.NotFound:
                    tracked[entry_id] = replace(existing, finalized=True)
                    changed = True
                    continue
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Failed to edit GitHub release message for %s", tag)
                    continue
                tracked[entry_id] = replace(existing, content_hash=new_hash)
                changed = True
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cogs_rss.py -k "edits_message or finalizes_after" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full cog + storage suites**

Run: `pytest tests/test_cogs_rss.py tests/test_storage_rss.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/rss.py tests/test_cogs_rss.py
git commit -m "feat(rss): edit GitHub release embed in place within grace window"
```

---

### Task 8: Regression guard — non-GitHub feed still uses the generic path

**Files:**
- Test: `tests/test_cogs_rss.py`

**Interfaces:**
- Consumes: `_process_feed`.

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_non_github_feed_uses_generic_path(mock_bot_rss):
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    cog._fetch_feed = AsyncMock(return_value=types.SimpleNamespace(
        entries=[{"id": "x", "title": "GW2 news", "link": "https://www.guildwars2.com/x"}],
        feed={},
    ))
    cog._post_entries = AsyncMock(return_value=None)
    github_path = AsyncMock()
    cog._process_github_feed = github_path

    feed = RssFeedConfig(name="GW2", url="https://www.guildwars2.com/en/feed/", channel_id=1)
    await cog._process_feed(MagicMock(), feed)
    github_path.assert_not_called()
    cog._post_entries.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it passes (behaviour already implemented in Task 6)**

Run: `pytest tests/test_cogs_rss.py -k non_github_feed -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cogs_rss.py
git commit -m "test(rss): guard non-GitHub feeds keep the generic path"
```

---

## Part A — Producer: golden release workflow rollout (external repos)

These tasks operate on **separate repositories**, not `axitools`. Use the `gh`
CLI or GitHub MCP. Each repo is independent; verify by cutting a throwaway tag
on a branch and confirming the release only appears once it is complete.

The reference workflow is `darkharasho/axipulse` `.github/workflows/release.yml`
(draft → test → build & upload to draft → set notes from `RELEASE_NOTES.md`
(fail if missing) → `gh release edit --draft=false` → Discord webhook).

### Task A1: Add golden `release.yml` to TopStatsAIO

**Files (in `darkharasho/TopStatsAIO`):**
- Create: `.github/workflows/release.yml`
- Create (if missing): `RELEASE_NOTES.md` with a `Version v<x> — <date>` section format matching axipulse's `awk` extractor.

- [ ] **Step 1: Fetch the reference workflow**

```bash
gh api repos/darkharasho/axipulse/contents/.github/workflows/release.yml \
  --jq '.content' | base64 -d > /tmp/axipulse-release.yml
```

- [ ] **Step 2: Adapt for TopStatsAIO**

Copy `/tmp/axipulse-release.yml`, then adjust: build matrix / `electron-builder`
args to TopStatsAIO's targets, app display name (`TopStatsAIO`), icon URL
(`https://raw.githubusercontent.com/darkharasho/TopStatsAIO/main/<icon path>` —
confirm the real path), and embed colour. Keep the draft→publish ordering and
the "fail if no RELEASE_NOTES section" guard **exactly**.

- [ ] **Step 3: Confirm `RELEASE_NOTES.md` exists with the expected header format**

The extractor matches lines like `Version v1.2.3 — 2026-06-22`. If the repo has
no `RELEASE_NOTES.md`, create one with at least the current version's section.

- [ ] **Step 4: Open a PR**

```bash
# from a clone/worktree of TopStatsAIO
git checkout -b ci/golden-release-workflow
git add .github/workflows/release.yml RELEASE_NOTES.md
git commit -m "ci: atomic draft->publish release workflow (golden pattern)"
gh pr create --title "ci: golden release workflow" --body "Draft->build->notes->atomic publish so releases.atom only sees complete releases."
```

- [ ] **Step 5: Verify on a throwaway tag**

Push a pre-release tag on the branch (e.g. `v0.0.0-test`), watch the run create
a draft, upload artifacts, set notes, then flip to published. Confirm
`https://github.com/darkharasho/TopStatsAIO/releases.atom` shows the release
only after publish. Delete the test release/tag afterward.

### Task A2: Audit & add golden `release.yml` to gw2_arc_log_uploader and other darkharasho apps

**Files (per repo):** same as A1.

- [ ] **Step 1: Enumerate tracked darkharasho repos**

From this repo, list feeds and filter to `github.com/darkharasho/...`:

```bash
grep -rhoE 'https://github.com/darkharasho/[^"/]+' axitools/data/guild_*/rss_feeds.json | sort -u
```

- [ ] **Step 2: For each, check for a release workflow**

```bash
gh api repos/darkharasho/<repo>/contents/.github/workflows --jq '.[].name' 2>/dev/null
```

- [ ] **Step 3: For repos missing `release.yml`, repeat Task A1 steps 2–5** for that repo (adapt build args/name/icon).

- [ ] **Step 4: Record results**

Note in the PR description (or a short comment on this plan) which repos already
had the golden pattern, which were patched, and which are non-Electron (may need
a "no build" variant of the workflow — drop the build matrix, keep
draft→notes→publish).

---

## Verification (whole feature)

- [ ] `pytest tests/test_cogs_rss.py tests/test_storage_rss.py -v` — all pass.
- [ ] `pytest -q` (full suite, ≤2 workers) — no regressions.
- [ ] Manual: point a test guild feed at a third-party publish-then-edit repo and confirm: no empty post, one post once complete, message edits if notes/assets land within 2h.
- [ ] Producer: at least TopStatsAIO patched and verified via throwaway tag.
- [ ] Prod env: document `AXITOOLS_GITHUB_TOKEN` as optional in deploy notes (piclock pm2 env) — set it for rate-limit headroom.
```
