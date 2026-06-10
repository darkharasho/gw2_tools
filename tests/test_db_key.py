import os
import pytest

from axitools.db_key import resolve_db_key


def test_env_var_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("AXITOOLS_DB_KEY", "abc123")
    monkeypatch.delenv("AXITOOLS_DB_KEY_FILE", raising=False)
    key = resolve_db_key(config_path=tmp_path / "db_key")
    assert key == "abc123"
    assert not (tmp_path / "db_key").exists()  # nothing written when env is set


def test_key_file_env_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("AXITOOLS_DB_KEY", raising=False)
    key_file = tmp_path / "mounted_key"
    key_file.write_text("deadbeef\n")
    monkeypatch.setenv("AXITOOLS_DB_KEY_FILE", str(key_file))
    assert resolve_db_key(config_path=tmp_path / "db_key") == "deadbeef"


def test_generates_key_on_first_run(tmp_path, monkeypatch):
    monkeypatch.delenv("AXITOOLS_DB_KEY", raising=False)
    monkeypatch.delenv("AXITOOLS_DB_KEY_FILE", raising=False)
    path = tmp_path / "db_key"
    key = resolve_db_key(config_path=path)
    assert path.exists()
    assert len(key) == 64  # 32 bytes as hex
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    # second call reuses the same key, does not regenerate
    assert resolve_db_key(config_path=path) == key
