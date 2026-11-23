#!/bin/sh
# Vaultwarden nightly backup (k8s/apps/vaultwarden/backup.yaml). POSIX sh, runs in
# the pinned postgres image (pg_dump 18 + gpg + tar). Writes to /backups (NAS
# /volume1/Artzima/backups/vaultwarden):
#   vaultwarden-<UTC timestamp>-db.dump.gpg     pg_dump --format=custom, encrypted
#   vaultwarden-<UTC timestamp>-data.tar.gpg    the /data dir, encrypted
# Encryption: gpg symmetric AES256, passphrase from Infisical (file /secrets/BACKUP_PASSPHRASE).
# Each new file is written as .part, decrypt-verified, then renamed. Only after BOTH
# succeeded are older copies rotated: the newest KEEP of each kind stay. It only
# ever deletes its own vaultwarden-*.gpg files in /backups.
# Usage: backup.sh --keep=N     (env: DATABASE_URL)
# NOTE: no dollar-brace syntax anywhere in this file: Flux substitutes it in
# ConfigMaps (k8s/apps/vaultwarden/kustomization.yaml), shell defaults included.
set -eu
umask 077

KEEP=""
for arg in "$@"; do
  case "$arg" in
    --keep=*) KEEP="$(printf '%s' "$arg" | sed 's/^--keep=//')" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
case "$KEEP" in '' | *[!0-9]*) echo "--keep=N (N >= 1) is required" >&2; exit 2 ;; esac
[ "$KEEP" -ge 1 ] || { echo "--keep must be >= 1" >&2; exit 2; }

OUT=/backups
PASS=/secrets/BACKUP_PASSPHRASE
[ -s "$PASS" ] || { echo "missing $PASS" >&2; exit 1; }
[ -d "$OUT" ] && [ -w "$OUT" ] || { echo "$OUT is not a writable directory (NAS folder missing?)" >&2; exit 1; }
[ -d /data ] || { echo "/data is not mounted" >&2; exit 1; }
export GNUPGHOME=/tmp/gnupg
mkdir -p "$GNUPGHOME"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DB="$OUT/vaultwarden-$TS-db.dump.gpg"
DATA="$OUT/vaultwarden-$TS-data.tar.gpg"

enc() { gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$PASS" \
          --symmetric --cipher-algo AES256 --compress-algo none --output "$1"; }
dec() { gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASS" --decrypt "$1"; }

echo "pg_dump -> $DB"
pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" | enc "$DB.part"
# Verify: decrypts (MDC integrity) and is a readable custom-format archive.
dec "$DB.part" | pg_restore --list >/dev/null
mv "$DB.part" "$DB"

echo "tar /data -> $DATA"
tar -C /data -cf - . | enc "$DATA.part"
dec "$DATA.part" | tar -tf - >/dev/null
mv "$DATA.part" "$DATA"

# Rotation: newest KEEP of each kind; names sort by timestamp.
for kind in db.dump data.tar; do
  # shellcheck disable=SC2012 # our own fixed file names: no spaces/newlines
  ls -1 "$OUT"/vaultwarden-*-"$kind".gpg 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
    echo "rotate: rm $old"
    rm -f -- "$old"
  done
done
rm -f "$OUT"/vaultwarden-*.gpg.part

echo "done:"
ls -l "$OUT"/vaultwarden-"$TS"-*.gpg
echo "copies kept per kind: $KEEP"
