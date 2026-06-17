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
