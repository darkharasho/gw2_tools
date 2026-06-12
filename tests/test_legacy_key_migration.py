"""Regression: legacy api_keys.json must merge into SQLite even when the
guild already has rows — and must not resurrect deleted or overwrite newer
records. Previously `if existing: continue` skipped the whole file forever."""

import json

from axitools.storage import ApiKeyRecord, ApiKeyStore

GID = 555


def _write_legacy(tmp_path, payload):
    guild_dir = tmp_path / f"guild_{GID}"
    guild_dir.mkdir(parents=True, exist_ok=True)
    (guild_dir / "api_keys.json").write_text(json.dumps(payload), encoding="utf-8")
    return guild_dir / "api_keys.json"


def test_legacy_keys_merge_even_when_sqlite_already_has_rows(tmp_path):
    store = ApiKeyStore(tmp_path)
    # A registration that landed in sqlite before migration ran.
    store.upsert_api_key(GID, 1, ApiKeyRecord(name="main", key="NEW-1", account_name="One.1111"))
    path = _write_legacy(tmp_path, {
        "1": [{"name": "main", "key": "STALE-1", "account_name": "Stale.1111"}],
        "2": [{"name": "main", "key": "LEGACY-2", "account_name": "Two.2222"}],
        "3": [{"name": "main", "key": "LEGACY-3", "account_name": "Three.3333"}],
    })

    merged = ApiKeyStore(tmp_path)  # migration runs on construction

    # Legacy-only users imported.
    assert [r.account_name for r in merged.get_user_api_keys(GID, 2)] == ["Two.2222"]
    assert [r.account_name for r in merged.get_user_api_keys(GID, 3)] == ["Three.3333"]
    # Existing sqlite record NOT overwritten by the stale legacy copy.
    assert [r.key for r in merged.get_user_api_keys(GID, 1)] == ["NEW-1"]
    # File retired so deleted keys can't resurrect on later boots.
    assert not path.exists()
    assert path.with_suffix(".json.imported").exists()


def test_legacy_migration_still_runs_on_fresh_guild(tmp_path):
    path = _write_legacy(tmp_path, {
        "7": [{"name": "main", "key": "K7", "account_name": "Seven.7777"}],
    })
    store = ApiKeyStore(tmp_path)
    assert [r.account_name for r in store.get_user_api_keys(GID, 7)] == ["Seven.7777"]
    assert not path.exists()


def test_retired_file_not_reimported(tmp_path):
    path = _write_legacy(tmp_path, {
        "8": [{"name": "main", "key": "K8", "account_name": "Eight.8888"}],
    })
    store = ApiKeyStore(tmp_path)
    assert len(store.get_user_api_keys(GID, 8)) == 1
    store.delete_api_key(GID, 8, "main")

    again = ApiKeyStore(tmp_path)
    assert again.get_user_api_keys(GID, 8) == []
