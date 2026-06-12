#!/usr/bin/env bash
# Nightly snapshot of the AxiTools data directory (encrypted sqlite + guild
# JSON). Safe to run while the bot is up: sqlite files are copied with a
# stability check, and the archive is built from a staged copy via a single
# tar invocation so an entry can never appear twice (a duplicated
# api_keys.sqlite entry in a hand-rolled tarball once wiped the key store —
# the empty copy extracted last and won).
set -euo pipefail

DATA_DIR="${AXITOOLS_DATA_DIR:-$HOME/axitools/axitools/data}"
DEST_DIR="${AXITOOLS_BACKUP_DIR:-$HOME/backups/axitools}"
KEEP="${AXITOOLS_BACKUP_KEEP:-14}"

[ -d "$DATA_DIR" ] || { echo "data dir not found: $DATA_DIR" >&2; exit 1; }
mkdir -p "$DEST_DIR"
chmod 700 "$DEST_DIR"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Copy everything except backup artifacts; sqlite files get a stability check
# (retry while the file is being written mid-copy).
rsync -a --exclude '*.bak' --exclude '*.plaintext.bak' "$DATA_DIR/" "$STAGE/data/"
for db in $(find "$DATA_DIR" -name '*.sqlite'); do
  rel="${db#"$DATA_DIR"/}"
  for _ in 1 2 3; do
    before=$(md5sum "$db" | cut -d' ' -f1)
    cp "$db" "$STAGE/data/$rel"
    after=$(md5sum "$db" | cut -d' ' -f1)
    [ "$before" = "$after" ] && break
    sleep 2
  done
done

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$DEST_DIR/axitools_data_$STAMP.tar.gz"
tar -czf "$OUT" -C "$STAGE" data
chmod 600 "$OUT"

# Guards: no duplicate entries, exactly one key store.
if tar -tzf "$OUT" | sort | uniq -d | grep -q .; then
  echo "FATAL: duplicate entries in $OUT" >&2; exit 1
fi
keystores=$(tar -tzf "$OUT" | grep -c '^data/api_keys\.sqlite$' || true)
if [ "$keystores" != "1" ]; then
  echo "FATAL: expected exactly 1 data/api_keys.sqlite in $OUT, found $keystores" >&2; exit 1
fi

# Rotate: keep the newest $KEEP.
ls -1t "$DEST_DIR"/axitools_data_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "ok: $OUT ($(du -h "$OUT" | cut -f1)) — $(ls -1 "$DEST_DIR"/axitools_data_*.tar.gz | wc -l)/$KEEP retained"
