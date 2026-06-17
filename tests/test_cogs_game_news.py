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
