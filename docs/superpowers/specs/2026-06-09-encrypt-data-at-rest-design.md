# Encrypt persistent data at rest — design

**Date:** 2026-06-09
**Status:** Approved (pending implementation plan)

## Problem

AxiTools stores personal data in plaintext SQLite. The most sensitive is the
Guild Wars 2 API key (`api_keys.sqlite`, `key` column), which grants read access
to a player's entire GW2 account. Per-guild `audit.sqlite` files also hold
personal data: Discord and GW2 account names, user IDs, and audit event details.

Anyone who opens these files — via a SQLite browser, a leaked backup, a stolen
disk, or a stray commit — reads everything in cleartext.

## Goal

Encrypt all persistent SQLite data at rest so that the database files are useless
without a separate key. This is a deliberate **first rung**: it converts
*everything-plaintext* into *useless-without-the-key*.

### Threats addressed
- **Leaked database file** (backup, stray copy, accidental commit, stolen disk):
  fully mitigated — the file is ciphertext and the key lives elsewhere.
- **Casual operator/host reading:** raised from "double-click in a DB browser"
  to "deliberately locate the key and decrypt."

### Explicit non-goals (honest scope)
- Does **not** protect against an attacker with host access — they obtain both
  the key and the data. Accepted.
- Does **not** make the operator unable to decrypt. The running bot must use the
  data (e.g. call the GW2 API with stored keys), so the key is reachable on the
  host by design.
- Operator-blind encryption for display-only data, hardware enclaves (TEE), and
  external KMS are **future rungs**, not built here. See "Future work."

## Approach chosen: whole-database encryption (SQLCipher)

Rejected alternative: field-level column encryption (`cryptography` AES-GCM).
It avoids a native dependency but **breaks the audit substring search** —
`query_discord_events` / `query_gw2_events` run `LIKE '%name%'` over normalized
name columns, and you cannot `LIKE` over ciphertext (blind-index HMAC only does
exact match). Whole-DB encryption decrypts transparently in memory at runtime,
so every existing query — `LIKE` searches and the `api_key_guilds` joins —
keeps working unchanged.

## Design

### 1. Key resolution — `axitools/db_key.py` (new)

A single `resolve_db_key(data_dir) -> str` (or bytes) consulted by all stores,
in precedence order:

1. `AXITOOLS_DB_KEY` (env) — **preferred**; nothing written to disk.
2. `AXITOOLS_DB_KEY_FILE` (env) — optional explicit path to a key file (e.g. a
   secrets-mounted file).
3. `${XDG_CONFIG_HOME:-~/.config}/axitools/db_key` — auto-generated on first run
   if neither env var is set: 32 random bytes, file mode `0600`, **separate from
   the data directory** so a leaked/backed-up/committed `data/` never includes
   the key. On generation, log a loud one-time warning:
   *"Generated a new DB key at <path> — move it to AXITOOLS_DB_KEY and back it
   up. If you lose this key, the encrypted data is unrecoverable."*

The 32-byte value is applied as a **raw** SQLCipher key via
`PRAGMA key = "x'<hex>'"`, skipping the passphrase KDF.

**Key/data separation is a hard requirement** — the key file must never default
to a location inside `data_dir`. Add the default key path and `*.db_key` to
`.gitignore`.

### 2. Encrypted connection layer — `axitools/storage.py`

Add one helper, `connect_encrypted(path, key)`, that:
- opens via `from sqlcipher3 import dbapi2 as sqlcipher`,
- runs `PRAGMA key = "x'<hex>'"` immediately after connect,
- then applies the existing `row_factory = sqlite3.Row` and, where currently
  used, `PRAGMA foreign_keys = ON`.

Both `ApiKeyStore._connect` and `AuditStore._connect` route through it. **No
query code changes anywhere else.**

### 3. Auto-migration on startup — `migrate_data_dir(root, key)`

Called once from `StorageManager.__init__`, before stores are used:
- Glob `**/*.sqlite` under `root`, skipping `*.bak`.
- A file needs migration iff its first 16 bytes equal `SQLite format 3\0`.
  SQLCipher encrypts the file header, so already-encrypted files fail this test
  and are skipped — startup is **idempotent**.
- For each plaintext file:
  1. copy to `<name>.sqlite.plaintext.bak`,
  2. `sqlcipher_export` into a temp encrypted file,
  3. `os.replace` the temp file into the original path (atomic).
  The `.plaintext.bak` is retained for the operator to delete after confirming
  reads work. Export-to-temp-then-replace is crash-safe.
- Lazily-created per-guild `audit.sqlite` files are **born encrypted** (they go
  through `connect_encrypted`), so they never require migration.

### 4. Dependency

Add `sqlcipher3-binary` to `requirements.txt` (ships manylinux wheels; fine for
the Linux host). Provides the `sqlcipher3` module.

### 5. Tests

- **Round-trip:** write/read an API key and an audit event; values unchanged.
- **At-rest:** raw-read a written DB file, assert the header is *not*
  `SQLite format 3\0`, and that a plain `sqlite3.connect` cannot read it.
- **Migration:** seed a plaintext DB with known rows, run `migrate_data_dir`,
  assert (a) data intact through the encrypted store, (b) `.plaintext.bak`
  exists, (c) the file is now encrypted, (d) a second run is a no-op.
- **Key resolution:** env-var precedence; first-run generation writes to the
  config dir (not the data dir) with mode `0600`; `AXITOOLS_DB_KEY_FILE` honored.

## Future work (not in scope)

- **Pile B operator-blind:** encrypt display-only personal data (audit logs)
  under a per-guild/community-held key the bot never persists usable, so the
  operator genuinely cannot read it.
- **Confidential computing (enclave/TEE):** the only way to use a credential
  unattended while the operator cannot read it.
- **External KMS / off-host key custody:** adds auditable, revocable access.
