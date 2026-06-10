
import pytest
import sqlite3
from unittest.mock import MagicMock
from axitools.storage import ApiKeyStore

@pytest.fixture(autouse=True)
def _db_key_env(monkeypatch):
    """Give every test a deterministic DB key so SQLCipher connections work."""
    monkeypatch.setenv("AXITOOLS_DB_KEY", "00" * 32)  # 64 hex chars

@pytest.fixture
def mock_db():
    """Create an in-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    # Enable foreign keys for correctness
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()

@pytest.fixture
def api_key_store(tmp_path):
    """Create a temporary ApiKeyStore."""
    # We use a temp directory for the database file
    store = ApiKeyStore(tmp_path)
    # Force the connection to be in-memory or just use the file
    # ApiKeyStore defaults to "api_keys.sqlite" in the root path.
    return store

@pytest.fixture
def mock_aioresponse():
    """Fixture for mocking aiohttp responses."""
    from aioresponses import aioresponses
    with aioresponses() as m:
        yield m
