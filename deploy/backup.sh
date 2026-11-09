#!/bin/sh
# Dump the database to a dated file and keep the newest 14 (a VPS has no managed backups).
#
#   deploy/backup.sh
#
# BACKUP_DIR (default ~/biosentry-backups) is made with mode 700; files are mode 600. KEEP
# (default 14) is how many dumps stay. The dump is pg_dump's custom format: restore it with
# pg_restore (deploy/README.md). It sits on the same disk as the database, so also copy it elsewhere.
set -eu
cd "$(dirname "$0")"

dir="${BACKUP_DIR:-$HOME/biosentry-backups}"
keep="${KEEP:-14}"
umask 077
mkdir -p "$dir"
chmod 700 "$dir"

name="biosentry-$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f "$dir/$name.partial"' EXIT  # a failed dump leaves nothing that looks like a backup
docker compose exec -T postgres pg_dump -U biosentry -d biosentry --format=custom > "$dir/$name.partial"
[ -s "$dir/$name.partial" ] || { echo "the dump is empty" >&2; exit 1; }
mv "$dir/$name.partial" "$dir/$name"

ls -1t "$dir"/biosentry-*.dump | tail -n +"$((keep + 1))" | while IFS= read -r old; do
  rm -f -- "$old"
done
echo "$dir/$name"
