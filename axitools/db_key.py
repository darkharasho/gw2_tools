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
