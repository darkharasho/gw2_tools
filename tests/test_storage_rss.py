import json
from pathlib import Path

import pytest

from axitools.storage import StorageManager, RssFeedConfig, TrackedRelease


@pytest.fixture
def storage(tmp_path):
    return StorageManager(tmp_path)


def test_old_feed_json_loads_with_defaults(storage, tmp_path):
    # Simulate a pre-existing file without the new keys.
    guild_dir = storage._guild_path(123)
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


def test_feed_with_unknown_keys_loads(storage, tmp_path):
    guild_dir = storage._guild_path(7)
    guild_dir.mkdir(parents=True, exist_ok=True)
    (guild_dir / "rss_feeds.json").write_text(
        '[{"name":"TSA","url":"https://github.com/x/y/releases.atom","channel_id":5,'
        '"future_field":"ignore me",'
        '"tracked_releases":{"tag:1/v1":{"entry_id":"tag:1/v1","message_id":9,'
        '"future_subfield":"ignore me too"}}}]'
    )
    feeds = storage.get_rss_feeds(7)
    assert len(feeds) == 1
    assert feeds[0].tracked_releases["tag:1/v1"].message_id == 9
