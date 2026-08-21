
import pytest
from unittest.mock import AsyncMock, MagicMock
from axitools.cogs.update_notes import UpdateNotesCog, PatchNotesEntry
from axitools.storage import UpdateNotesStatus

@pytest.fixture
def mock_bot_update_notes():
    return MagicMock()

@pytest.mark.asyncio
async def test_update_notes_init(mock_bot_update_notes):
    cog = UpdateNotesCog(mock_bot_update_notes)
    assert cog is not None


def _entry(date_anchor: str, iso: str) -> PatchNotesEntry:
    """Build a PatchNotesEntry for an ``Update - <date>`` wiki heading."""
    anchor = f"Update_-_{date_anchor}"
    url = f"https://wiki.guildwars2.com/wiki/Game_updates#{anchor}"
    return PatchNotesEntry(
        entry_id=anchor,
        title=f"Update - {date_anchor.replace('_', ' ')}",
        url=url,
        published_at=iso,
        summary="",
        content="",
        legacy_entry_ids=(url,),
    )


# The wiki "Game updates" page only lists the most recent handful of updates,
# newest first. Older updates scroll off over time.
PAGE_ENTRIES = [
    _entry("June_3,_2026", "2026-06-03T00:00:00+00:00"),
    _entry("June_2,_2026", "2026-06-02T00:00:00+00:00"),
    _entry("May_18,_2026", "2026-05-18T00:00:00+00:00"),
    _entry("May_14,_2026", "2026-05-14T00:00:00+00:00"),
    _entry("May_13,_2026", "2026-05-13T00:00:00+00:00"),
]


def _cog() -> UpdateNotesCog:
    # Bypass __init__ so we don't start the polling task loop.
    return UpdateNotesCog.__new__(UpdateNotesCog)


def test_resolve_new_entries_boundary_on_page():
    """Boundary still visible -> only genuinely-newer entries are returned."""
    cog = _cog()
    new, boundary_found = cog._resolve_new_entries(
        PAGE_ENTRIES, "Update_-_May_18,_2026", "2026-05-18T00:00:00+00:00"
    )
    assert boundary_found is True
    assert [e.entry_id for e in new] == [
        "Update_-_June_2,_2026",
        "Update_-_June_3,_2026",
    ]


def test_resolve_new_entries_up_to_date():
    """Boundary is the newest entry -> nothing new, boundary found."""
    cog = _cog()
    new, boundary_found = cog._resolve_new_entries(
        PAGE_ENTRIES, "Update_-_June_3,_2026", "2026-06-03T00:00:00+00:00"
    )
    assert boundary_found is True
    assert new == []


def test_resolve_new_entries_boundary_scrolled_off():
    """Regression: a boundary that scrolled off the page must NOT be treated
    as 'post the entire page'. This is the spam bug."""
    cog = _cog()
    new, boundary_found = cog._resolve_new_entries(
        PAGE_ENTRIES, "Update_-_April_16,_2026", "2026-04-16T00:00:00+00:00"
    )
    # The recorded update is no longer on the page; we can't know what was
    # already posted, so the caller must re-anchor rather than flood.
    assert boundary_found is False


def _poll_bot(status):
    """Minimal bot double for exercising _poll_updates against one guild."""
    bot = MagicMock()
    guild = MagicMock()
    guild.id = 42
    bot.guilds = [guild]
    config = MagicMock()
    config.update_notes_channel_id = 999
    bot.get_config.return_value = config
    bot.storage.get_update_notes_status.return_value = status
    bot.storage.save_update_notes_status = MagicMock()
    return bot, guild


@pytest.mark.asyncio
async def test_poll_reanchors_silently_on_stale_boundary():
    """A stale (off-page) boundary re-anchors to the latest entry and posts nothing."""
    cog = _cog()
    bot, _ = _poll_bot(
        UpdateNotesStatus(
            last_entry_id="Update_-_April_16,_2026",
            last_entry_published_at="2026-04-16T00:00:00+00:00",
        )
    )
    cog.bot = bot
    cog._fetch_entries = AsyncMock(return_value=PAGE_ENTRIES)
    channel = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=channel)

    await cog._poll_updates.coro(cog)

    channel.send.assert_not_called()
    saved = bot.storage.save_update_notes_status.call_args[0][1]
    assert saved.last_entry_id == "Update_-_June_3,_2026"


@pytest.mark.asyncio
async def test_poll_posts_only_new_when_boundary_on_page():
    """With a visible boundary, only the genuinely-new updates are posted."""
    cog = _cog()
    bot, _ = _poll_bot(
        UpdateNotesStatus(
            last_entry_id="Update_-_May_18,_2026",
            last_entry_published_at="2026-05-18T00:00:00+00:00",
        )
    )
    cog.bot = bot
    cog._fetch_entries = AsyncMock(return_value=PAGE_ENTRIES)
    channel = AsyncMock()
    cog._resolve_channel = AsyncMock(return_value=channel)

    await cog._poll_updates.coro(cog)

    assert channel.send.await_count == 2
    saved = bot.storage.save_update_notes_status.call_args[0][1]
    assert saved.last_entry_id == "Update_-_June_3,_2026"


def test_build_embeds_brands_with_gw2_logo():
    """The embed must read as GW2 news, not a generic wiki post."""
    cog = _cog()
    embed = cog._build_embeds(PAGE_ENTRIES[0], "Bug Fix")[0]
    assert embed.author.name == "Guild Wars 2 · Game Update Notes"
    assert embed.author.icon_url == "attachment://gw2_logo.png"
    assert embed.footer.text == "Guild Wars 2 Wiki – Game Updates"


def test_build_embeds_thumbnail_is_attached_not_hotlinked():
    """Wiki-hosted images can die in Discord's image proxy; attach instead."""
    cog = _cog()
    embed = cog._build_embeds(PAGE_ENTRIES[0], "Bug Fix")[0]
    assert embed.thumbnail.url == "attachment://gw2_expansion_logo.png"


@pytest.mark.asyncio
async def test_send_embed_attaches_a_fresh_logo_each_time():
    """discord.File objects are single-use, so each send needs its own."""
    cog = _cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    embed = cog._build_embeds(PAGE_ENTRIES[0], "Bug Fix")[0]

    await cog._send_embed(channel, embed)
    await cog._send_embed(channel, embed)

    sends = [call.kwargs["files"] for call in channel.send.call_args_list]
    assert len(sends) == 2
    assert [f.filename for f in sends[0]] == [
        "gw2_logo.png",
        "gw2_expansion_logo.png",
    ]
    # Second send must get its own File objects, not the already-consumed ones.
    assert not set(map(id, sends[0])) & set(map(id, sends[1]))
