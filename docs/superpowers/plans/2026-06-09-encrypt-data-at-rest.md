# Encrypt Persistent Data At Rest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encrypt all persistent SQLite databases (`api_keys.sqlite` and per-guild `audit.sqlite`) at rest using SQLCipher, with a key held separately from the data.

**Architecture:** Whole-database encryption via the `sqlcipher3` driver. A single key-resolution module (`axitools/db_key.py`) supplies the key from an env var, an env-pointed file, or an auto-generated key file in the user's config dir (never inside the data dir). A shared `connect_encrypted` helper opens every connection with `PRAGMA key`; both stores route through it. An idempotent startup migration re-encrypts any pre-existing plaintext databases in place, keeping a `.plaintext.bak` backup.

**Tech Stack:** Python 3.10+, `sqlcipher3-binary` (SQLCipher driver), `pytest`, stdlib `sqlite3`/`secrets`/`shutil`.

**Spec:** `docs/superpowers/specs/2026-06-09-encrypt-data-at-rest-design.md`

**Notes for the implementer:**
- This is a pytest project. Run tests with `python -m pytest`. The global vitest worker-limit note does not apply here.
- Install the new dependency once before running tests: `pip install -r requirements.txt`.
- The 32-byte key is stored/transported as a 64-char hex string and applied as a *raw* SQLCipher key via `PRAGMA key = "x'<hex>'"` (no passphrase KDF).
- SQLCipher encrypts the file header, so a plaintext SQLite file is detectable by its first 16 bytes `b"SQLite format 3\x00"`; encrypted files never match this. This is what makes migration idempotent.

---

### Task 1: Add the SQLCipher dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the driver**

In `requirements.txt`, add after line 9 (`markdownify>=0.13.1`), before the `# Testing` block:

```
sqlcipher3-binary>=0.5.0
```

- [ ] **Step 2: Install it**

Run: `pip install -r requirements.txt`
Expected: `sqlcipher3-binary` installs successfully (manylinux wheel on this Linux host).

- [ ] **Step 3: Verify the module imports**

Run: `python -c "from sqlcipher3 import dbapi2; print(dbapi2.sqlite_version)"`
Expected: prints a version string, no ImportError.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add sqlcipher3-binary for at-rest encryption"
```

---

### Task 2: Key-resolution module

**Files:**
- Create: `axitools/db_key.py`
- Test: `tests/test_db_key.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_key.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db_key.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'axitools.db_key'`.

- [ ] **Step 3: Write the module**

Create `axitools/db_key.py`:

```python
"""Resolution of the SQLCipher key used to encrypt persistent data at rest.

The key is resolved in precedence order:
  1. ``AXITOOLS_DB_KEY`` env var (preferred; nothing written to disk).
  2. ``AXITOOLS_DB_KEY_FILE`` env var pointing at a key file.
  3. An auto-generated key file in the user's config dir, kept **separate from
     the data directory** so a leaked/backed-up ``data/`` never includes the key.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _default_key_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "axitools" / "db_key"


def resolve_db_key(config_path: Optional[Path] = None) -> str:
    """Return the SQLCipher key as a hex string, generating one if needed."""

    env_key = os.environ.get("AXITOOLS_DB_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    key_file = os.environ.get("AXITOOLS_DB_KEY_FILE")
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()

    path = Path(config_path) if config_path is not None else _default_key_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    key = secrets.token_hex(32)  # 32 bytes -> 64 hex chars (AES-256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    os.chmod(path, 0o600)
    logger.warning(
        "Generated a new database key at %s. Move it to the AXITOOLS_DB_KEY "
        "environment variable and back it up. If this key is lost, the encrypted "
        "data is UNRECOVERABLE.",
        path,
    )
    return key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db_key.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add axitools/db_key.py tests/test_db_key.py
git commit -m "feat: add db_key resolution module"
```

---

### Task 3: Test fixture so the suite has a key

**Files:**
- Modify: `tests/conftest.py`

This must land before Task 4 switches connections to SQLCipher, otherwise every
storage test fails for lack of a key. The fixture is `autouse`, so individual
`db_key` tests still override it with `monkeypatch.delenv`.

- [ ] **Step 1: Add the autouse fixture**

In `tests/conftest.py`, after the existing imports (after line 5,
`from axitools.storage import ApiKeyStore`), add:

```python
@pytest.fixture(autouse=True)
def _db_key_env(monkeypatch):
    """Give every test a deterministic DB key so SQLCipher connections work."""
    monkeypatch.setenv("AXITOOLS_DB_KEY", "00" * 32)  # 64 hex chars
```

- [ ] **Step 2: Verify the suite still passes (pre-encryption baseline)**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS — the fixture is inert until connections use the key in Task 4.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: provide deterministic DB key to all tests"
```

---

### Task 4: Encrypted connection helper, routed through both stores

**Files:**
- Modify: `axitools/storage.py` (imports near line 1-14; `ApiKeyStore.__init__`/`_connect` near 556-566; `AuditStore.__init__`/`_connect` near 1095-1103; `StorageManager.__init__` near 1314-1318; `get_audit_store` near 1328-1333)
- Test: `tests/test_storage_encryption.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage_encryption.py`:

```python
import sqlite3

import pytest

from axitools.storage import StorageManager, ApiKeyRecord

PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def test_database_is_not_plaintext_on_disk(tmp_path):
    storage = StorageManager(tmp_path)
    record = ApiKeyRecord(name="main", key="SECRET-GW2-KEY", account_name="Foo.1234")
    storage.api_key_store.upsert_api_key(1, 2, record)

    db_path = tmp_path / "api_keys.sqlite"
    with db_path.open("rb") as fh:
        header = fh.read(16)
    assert header != PLAINTEXT_MAGIC  # encrypted, not a plain SQLite file

    # A plain sqlite3 driver cannot read an encrypted file.
    with pytest.raises(sqlite3.DatabaseError):
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT name FROM api_keys").fetchall()


def test_round_trip_through_encrypted_store(tmp_path):
    storage = StorageManager(tmp_path)
    record = ApiKeyRecord(name="main", key="SECRET-GW2-KEY", account_name="Foo.1234")
    storage.api_key_store.upsert_api_key(1, 2, record)

    fetched = storage.api_key_store.find_api_key(1, 2, "main")
    assert fetched is not None
    assert fetched.key == "SECRET-GW2-KEY"
    assert fetched.account_name == "Foo.1234"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage_encryption.py -v`
Expected: FAIL — `test_database_is_not_plaintext_on_disk` fails because the file
still begins with `SQLite format 3\x00` (connections still use stdlib `sqlite3`).

- [ ] **Step 3: Add the encrypted-connection helper and imports**

In `axitools/storage.py`, after the existing `import sqlite3` line (line 7), add:

```python
from sqlcipher3 import dbapi2 as sqlcipher
```

And after the import block (after line 14, the `from typing import ...` line), add the `from .db_key import resolve_db_key` import on its own line:

```python
from .db_key import resolve_db_key
```

Then add this module-level helper immediately after the `logger = logging.getLogger(__name__)` line (line 17):

```python
def connect_encrypted(path, key: str, *, foreign_keys: bool = False):
    """Open a SQLCipher connection to ``path`` keyed with the raw hex ``key``."""

    connection = sqlcipher.connect(str(path))
    connection.execute(f"PRAGMA key = \"x'{key}'\"")
    connection.row_factory = sqlcipher.Row
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    return connection
```

- [ ] **Step 4: Route `ApiKeyStore` through the helper**

In `ApiKeyStore.__init__` (line 556), change the signature and store the key.
Replace:

```python
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "api_keys.sqlite"
        self._ensure_schema()
        self._migrate_json_stores()
```

with:

```python
    def __init__(self, root: Path, key: Optional[str] = None) -> None:
        self.root = root
        self.path = root / "api_keys.sqlite"
        self._key = key or resolve_db_key()
        self._ensure_schema()
        self._migrate_json_stores()
```

Replace `ApiKeyStore._connect` (lines 562-566):

```python
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
```

with:

```python
    def _connect(self):
        return connect_encrypted(self.path, self._key, foreign_keys=True)
```

- [ ] **Step 5: Route `AuditStore` through the helper**

In `AuditStore.__init__` (line 1095), replace:

```python
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "audit.sqlite"
        self._ensure_schema()
```

with:

```python
    def __init__(self, root: Path, key: Optional[str] = None) -> None:
        self.root = root
        self.path = root / "audit.sqlite"
        self._key = key or resolve_db_key()
        self._ensure_schema()
```

Replace `AuditStore._connect` (lines 1100-1103):

```python
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
```

with:

```python
    def _connect(self):
        return connect_encrypted(self.path, self._key)
```

- [ ] **Step 6: Resolve the key once in `StorageManager` and pass it down**

In `StorageManager.__init__` (lines 1314-1318), replace:

```python
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.api_key_store = ApiKeyStore(self.root)
        self._audit_stores: Dict[int, AuditStore] = {}
```

with:

```python
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_key = resolve_db_key()
        self.api_key_store = ApiKeyStore(self.root, self._db_key)
        self._audit_stores: Dict[int, AuditStore] = {}
```

In `get_audit_store` (lines 1328-1333), replace the `AuditStore(self._guild_path(guild_id))` construction:

```python
            store = AuditStore(self._guild_path(guild_id))
```

with:

```python
            store = AuditStore(self._guild_path(guild_id), self._db_key)
```

- [ ] **Step 7: Run the new test and the full storage suite**

Run: `python -m pytest tests/test_storage_encryption.py tests/test_storage.py -v`
Expected: PASS — encryption test passes and all existing storage tests still pass (the `_db_key_env` fixture supplies the key).

- [ ] **Step 8: Commit**

```bash
git add axitools/storage.py tests/test_storage_encryption.py
git commit -m "feat: encrypt persistent SQLite databases at rest via SQLCipher"
```

---

### Task 5: Startup migration of pre-existing plaintext databases

**Files:**
- Modify: `axitools/storage.py` (add `migrate_data_dir`/`_encrypt_in_place` helpers near the new `connect_encrypted` helper; call from `StorageManager.__init__`)
- Test: `tests/test_storage_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage_migration.py`:

```python
import sqlite3

import pytest

from axitools.storage import migrate_data_dir, connect_encrypted

KEY = "00" * 32
PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def _make_plaintext_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def test_migrates_plaintext_db_in_place(tmp_path):
    db = tmp_path / "api_keys.sqlite"
    _make_plaintext_db(db)
    assert db.open("rb").read(16) == PLAINTEXT_MAGIC  # sanity: starts plaintext

    migrate_data_dir(tmp_path, KEY)

    # File is now encrypted and a backup was kept.
    assert db.open("rb").read(16) != PLAINTEXT_MAGIC
    assert (tmp_path / "api_keys.sqlite.plaintext.bak").exists()

    # Data survived and is readable through an encrypted connection.
    conn = connect_encrypted(db, KEY)
    assert conn.execute("SELECT val FROM t").fetchone()[0] == "hello"
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "api_keys.sqlite"
    _make_plaintext_db(db)
    migrate_data_dir(tmp_path, KEY)
    first = db.read_bytes()

    migrate_data_dir(tmp_path, KEY)  # second run must be a no-op
    assert db.read_bytes() == first
    # No second backup of the backup.
    assert not (tmp_path / "api_keys.sqlite.plaintext.bak.plaintext.bak").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_data_dir'`.

- [ ] **Step 3: Add the migration helpers**

In `axitools/storage.py`, add these imports if not already present: `import os` (already at line 6) and `import shutil` (add after `import os`). Then add immediately after the `connect_encrypted` helper:

```python
_PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def _encrypt_in_place(path: Path, key: str) -> None:
    """Re-encrypt a plaintext SQLite file at ``path``, keeping a backup."""

    backup = path.with_name(path.name + ".plaintext.bak")
    shutil.copy2(path, backup)

    tmp = path.with_name(path.name + ".enc.tmp")
    if tmp.exists():
        tmp.unlink()

    # Open the plaintext database (no key on main), attach an encrypted target,
    # and export the full schema + data into it.
    connection = sqlcipher.connect(str(path))
    try:
        connection.execute(
            f"ATTACH DATABASE '{tmp}' AS encrypted KEY \"x'{key}'\""
        )
        connection.execute("SELECT sqlcipher_export('encrypted')")
        connection.execute("DETACH DATABASE encrypted")
    finally:
        connection.close()

    os.replace(tmp, path)


def migrate_data_dir(root: Path, key: str) -> None:
    """Encrypt any plaintext ``*.sqlite`` files under ``root`` in place.

    Idempotent: SQLCipher-encrypted files do not carry the plaintext header,
    so they are skipped on subsequent runs.
    """

    for path in Path(root).rglob("*.sqlite"):
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            header = handle.read(16)
        if header != _PLAINTEXT_MAGIC:
            continue
        logger.warning("Encrypting pre-existing plaintext database at %s", path)
        _encrypt_in_place(path, key)
```

- [ ] **Step 4: Call migration from `StorageManager.__init__`**

In `StorageManager.__init__`, add the migration call between resolving the key and constructing `ApiKeyStore` (it must run before any encrypted connection opens an existing plaintext file). Replace:

```python
        self._db_key = resolve_db_key()
        self.api_key_store = ApiKeyStore(self.root, self._db_key)
```

with:

```python
        self._db_key = resolve_db_key()
        migrate_data_dir(self.root, self._db_key)
        self.api_key_store = ApiKeyStore(self.root, self._db_key)
```

- [ ] **Step 5: Run the migration tests**

Run: `python -m pytest tests/test_storage_migration.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add axitools/storage.py tests/test_storage_migration.py
git commit -m "feat: auto-migrate pre-existing plaintext databases to SQLCipher on startup"
```

---

### Task 6: Ignore backups/keys and update docs

**Files:**
- Modify: `.gitignore`
- Modify: `DATABASE_SCHEMA.md`

- [ ] **Step 1: Ignore migration backups and stray key files**

Append to `.gitignore`:

```
# At-rest encryption: never commit key material or plaintext backups
*.db_key
*.plaintext.bak
```

- [ ] **Step 2: Document encryption in the schema doc**

In `DATABASE_SCHEMA.md`, replace the first two lines:

```markdown
The bot stores persistent data in SQLite at `axitools/data/api_keys.sqlite`. The tables below outline the current schema.

Audit logging data is stored per Discord guild in `axitools/data/guild_<guild_id>/audit.sqlite`.
```

with:

```markdown
The bot stores persistent data in SQLite at `axitools/data/api_keys.sqlite`. The tables below outline the current schema.

Audit logging data is stored per Discord guild in `axitools/data/guild_<guild_id>/audit.sqlite`.

## Encryption at rest

All persistent SQLite databases are encrypted at rest with SQLCipher. The key is
resolved (in order) from the `AXITOOLS_DB_KEY` env var, the `AXITOOLS_DB_KEY_FILE`
env var, or an auto-generated key file at `${XDG_CONFIG_HOME:-~/.config}/axitools/db_key`
(kept separate from the data directory). **Back up this key** — if it is lost, the
encrypted data is unrecoverable. On startup, any pre-existing plaintext database is
re-encrypted in place, leaving a `<name>.plaintext.bak` backup you should delete once
you have confirmed reads work.

This protects against a leaked database file (backup, stray copy, accidental commit,
stolen disk). It does **not** protect against an attacker with host access, nor does it
prevent the operator from decrypting; see `docs/superpowers/specs/2026-06-09-encrypt-data-at-rest-design.md`.
```

- [ ] **Step 3: Verify nothing is broken**

Run: `python -m pytest -v`
Expected: PASS — full suite green.

- [ ] **Step 4: Commit**

```bash
git add .gitignore DATABASE_SCHEMA.md
git commit -m "docs: document at-rest encryption; ignore key/backup files"
```

---

## Self-Review

**Spec coverage:**
- Key resolution (env / env-file / auto-gen in config dir, 0600, loud warning, separate from data dir) → Task 2.
- Raw-hex SQLCipher key via `PRAGMA key` → Task 4 (`connect_encrypted`).
- Both stores encrypted, no query changes → Task 4.
- Idempotent startup migration with `.plaintext.bak`, atomic replace, born-encrypted lazy DBs → Task 5.
- `sqlcipher3-binary` dependency → Task 1.
- Tests: round-trip, at-rest (header + plain-sqlite-fails), migration (intact + backup + idempotent), key resolution (precedence, first-run gen, 0600) → Tasks 2, 4, 5.
- `.gitignore` for key/backups; honest non-goals documented → Task 6.

**Placeholder scan:** none — every code/test step shows full content.

**Type/name consistency:** `resolve_db_key(config_path=None)`, `connect_encrypted(path, key, *, foreign_keys=False)`, `migrate_data_dir(root, key)`, `_encrypt_in_place(path, key)`, `self._db_key`, `self._key`, env vars `AXITOOLS_DB_KEY` / `AXITOOLS_DB_KEY_FILE`, and the `b"SQLite format 3\x00"` magic are used identically across all tasks.
