#!/usr/bin/env bash
# The backup fetch command (lib.backups.fetch) built with the layout the base
# ships for WordPress sites (lib.backups.layouts.wordpress), driven from
# outside against a directory store: the sites under wordpress/, one site's
# dumps, --at, and the manifest. test_backups.py covers the layout itself.
#
# Usage: backups-wordpress.sh <path-to-the-fetch-command>
set -euo pipefail

BF="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
check() {
  if [ "$2" = "$3" ]; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s\n       want %q\n       got  %q\n' "$1" "$3" "$2"
    fail=1
  fi
}

SRC="$WORK/bucket/wordpress"
mkdir -p "$SRC/example" "$SRC/other"
printf 'nov 1' >"$SRC/example/20261101-030000.sql.gz"
printf 'nov 2' >"$SRC/example/20261102-030000.sql.gz"
printf 'x' >"$SRC/example/notes.txt"
printf 'dec 1' >"$SRC/other/20261201-030000.sql.gz"
export STUB_NIX_BACKUP_SOURCE="$WORK/bucket"

check "folders lists the sites" "$("$BF" folders wordpress | tr '\n' ' ')" "example other "
check "list lists one site's dumps" "$("$BF" list wordpress/example | tr '\n' ' ')" \
  "20261101-030000.sql.gz 20261102-030000.sql.gz "

manifest="$("$BF" fetch wordpress/example --at 2026-11-01 --dest "$WORK/cache" 2>/dev/null)"
check "fetch --at picks the dump" "$(jq -r .name <<<"$manifest")" "20261101-030000.sql.gz"
check "the manifest names the scope" "$(jq -r .scope <<<"$manifest")" "wordpress/example"
check "the dump is downloaded" "$(cat "$(jq -r .database <<<"$manifest")")" "nov 1"

if "$BF" list wordpress >/dev/null 2>"$WORK/err"; then
  check "the sites' parent holds no dumps, and says where they are" "exit 0" "exit 1"
else
  hinted=no
  if grep -q 'give the site.s folder (wordpress/<slug>) as the scope' "$WORK/err"; then hinted=yes; fi
  check "the sites' parent holds no dumps, and says where they are" "$hinted" "yes"
fi

exit "$fail"
