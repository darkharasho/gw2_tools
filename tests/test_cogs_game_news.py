import pytest

from axitools.cogs.game_news import (
    GameNewsCog,
    GameNewsEntry,
    NewsSource,
)
from axitools.storage import GameNewsStatus


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


def test_select_new_entries_only_unseen_oldest_first():
    cog = _cog()
    new = cog._select_new_entries(GW3_PAGE, ["slug-a"])
    assert [e.entry_id for e in new] == ["slug-b", "slug-c"]


def test_select_new_entries_all_seen_returns_empty():
    cog = _cog()
    new = cog._select_new_entries(GW3_PAGE, ["slug-a", "slug-b", "slug-c"])
    assert new == []


def test_select_new_entries_ignores_reorder():
    # The site reorders its index (an old card pinned to the top) but adds no
    # new article. Every id is already seen, so nothing is re-posted. This is
    # the spam vector the seen-set dedup closes — slug-position is irrelevant.
    cog = _cog()
    reordered = [
        _entry("gw3", "slug-a"),  # old card jumped to the top
        _entry("gw3", "slug-c"),
        _entry("gw3", "slug-b"),
    ]
    new = cog._select_new_entries(reordered, ["slug-a", "slug-b", "slug-c"])
    assert new == []


def test_select_new_entries_seen_id_scrolled_off_is_non_event():
    # An already-seen id is no longer on the page; a genuinely new one appeared.
    # Only the new one is returned (no whole-page re-anchor flood).
    cog = _cog()
    page = [_entry("gw3", "slug-d"), _entry("gw3", "slug-c")]  # slug-a/-b scrolled off
    new = cog._select_new_entries(page, ["slug-a", "slug-b", "slug-c"])
    assert [e.entry_id for e in new] == ["slug-d"]


def test_remember_is_bounded_and_keeps_newest():
    from axitools.cogs.game_news import SEEN_IDS_LIMIT

    cog = _cog()
    status = GameNewsStatus()
    for i in range(SEEN_IDS_LIMIT + 5):
        cog._remember(status, "gw3", f"slug-{i}")
    seen = status.seen_entry_ids["gw3"]
    assert len(seen) == SEEN_IDS_LIMIT
    # Oldest ids were trimmed; the most recent id is retained at the tail.
    assert seen[-1] == f"slug-{SEEN_IDS_LIMIT + 4}"
    assert "slug-0" not in seen


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


def test_parse_gw3_url_ignores_root_relative_href():
    """The landing page writes hrefs relative to the site root.

    Joining "./en/news/<slug>" against the page's own /en/ URL would yield
    /en/en/news/<slug>, so the article URL is built from the slug instead.
    """
    cog = _cog()
    html = (
        '<a href="./en/news/playable-species-spotlight-kodan">'
        '<article class="news-article svelte-xvh6k6" '
        'id="article-playable-species-spotlight-kodan">'
        '<img src="https://cdn/k.png"/>'
        '<h2 class="title">Playable Species Spotlight: Kodan</h2>'
        "</article></a>"
    )
    entries = cog._parse_gw3_html(html)
    assert len(entries) == 1
    assert entries[0].url == (
        "https://www.guildwars3.com/en/news/playable-species-spotlight-kodan"
    )


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


def test_first_image_skips_leading_img_without_src():
    # A leading <img> with no usable src must not shadow a later valid one
    # in the same HTML block.
    cog = _cog()
    entry = {"summary": '<img alt="spacer"><img src="//cdn/real.jpg">'}
    assert cog._first_image_from_entry(entry) == "https://cdn/real.jpg"


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
    # Whole existing backlog recorded as seen; nothing posted.
    assert st.seen_entry_ids == {"gw2": ["n2"], "gw3": ["slug-b"]}


@pytest.mark.asyncio
async def test_process_guild_posts_new_entries_oldest_first():
    cog = _cog()
    status = GameNewsStatus(
        seen_entry_ids={"gw2": ["n1"], "gw3": ["slug-a"]},
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
    # Newly-posted ids appended to each source's seen set (newest last).
    assert st.seen_entry_ids["gw2"] == ["n1", "n2", "n3"]
    assert st.seen_entry_ids["gw3"] == ["slug-a", "slug-b"]


@pytest.mark.asyncio
async def test_process_guild_reorder_does_not_repost():
    # Already-seen GW3 slugs reappear reordered with no genuinely-new article:
    # nothing is posted (the seen-set hardening for finding I1).
    cog = _cog()
    status = GameNewsStatus(seen_entry_ids={"gw3": ["slug-a", "slug-b", "slug-c"]})
    bot, guild, config, saved = _poll_bot(status, MagicMock())
    cog.bot = bot
    cog._send_entry = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=MagicMock())

    # Old card "slug-a" pinned to the top; no new article.
    source_entries = {
        "gw3": [_entry("gw3", "slug-a"), _entry("gw3", "slug-c"), _entry("gw3", "slug-b")]
    }
    await cog._process_guild(guild, source_entries)

    cog._send_entry.assert_not_called()
    assert saved == {}  # nothing changed -> no write


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


@pytest.mark.asyncio
async def test_fetch_url_decodes_utf8_when_header_omits_charset():
    """guildwars3.com serves "text/html" with no charset.

    requests then defaults text/* to ISO-8859-1 (RFC 2616), which mangles
    UTF-8 punctuation in article titles.
    """
    from unittest.mock import MagicMock

    cog = _cog()
    body = "<h2>Guild Wars 3</h2>".encode("utf-8")

    response = MagicMock()
    response.headers = {"Content-Type": "text/html"}
    response.content = body
    response.raise_for_status = MagicMock()
    # Mimic requests: encoding defaults to latin-1, .text honours whatever
    # encoding is set at access time.
    response.encoding = "ISO-8859-1"
    response.apparent_encoding = "utf-8"
    type(response).text = property(lambda self: self.content.decode(self.encoding))

    cog._session = MagicMock()
    cog._session.get = MagicMock(return_value=response)

    html = await cog._fetch_url("https://www.guildwars3.com/en/")
    assert "Guild Wars 3" in html
    assert "Â" not in html


@pytest.mark.asyncio
async def test_fetch_url_respects_explicit_charset():
    """An explicit charset in the header wins; don't second-guess the server."""
    from unittest.mock import MagicMock

    cog = _cog()
    response = MagicMock()
    response.headers = {"Content-Type": "text/html; charset=ISO-8859-1"}
    response.content = "caf\xe9".encode("latin-1")
    response.raise_for_status = MagicMock()
    response.encoding = "ISO-8859-1"
    response.apparent_encoding = "utf-8"
    type(response).text = property(lambda self: self.content.decode(self.encoding))

    cog._session = MagicMock()
    cog._session.get = MagicMock(return_value=response)

    assert await cog._fetch_url("https://example/x") == "caf\xe9"
