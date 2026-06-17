from pathlib import Path

from axitools.storage import StorageManager, GameNewsStatus


def _storage(tmp_path: Path) -> StorageManager:
    return StorageManager(tmp_path)


def test_game_news_status_round_trip(tmp_path):
    storage = _storage(tmp_path)
    assert storage.get_game_news_status(42) is None

    status = GameNewsStatus(
        seen_entry_ids={
            "gw2": ["https://gw2/news/a", "https://gw2/news/b"],
            "gw3": ["announcing-guild-wars-3"],
        },
    )
    storage.save_game_news_status(42, status)

    loaded = storage.get_game_news_status(42)
    assert loaded == status
    assert loaded.seen_entry_ids["gw3"] == ["announcing-guild-wars-3"]
    assert loaded.seen_entry_ids["gw2"][-1] == "https://gw2/news/b"


def test_game_news_status_defaults_are_independent(tmp_path):
    storage = _storage(tmp_path)
    a = GameNewsStatus()
    a.seen_entry_ids["gw2"] = ["x"]
    b = GameNewsStatus()
    assert b.seen_entry_ids == {}
