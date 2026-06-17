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
    with patch("axitools.cogs.game_news.Path.exists", return_value=True), \
         patch("axitools.cogs.game_news.discord.File") as mock_file:
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
    with patch("axitools.cogs.game_news.Path.exists", return_value=True), \
         patch("axitools.cogs.game_news.discord.File") as mock_file:
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
