#!/usr/bin/env bash
# Offline tests for the backup fetch command (lib.backups.fetch), built with the
# timestamp-folders layout (tests/fixtures/backup-layouts/timestamp_folders.py) for a stub product
# whose environment prefix is STUB.
#
# The fixture is a plain directory tree standing in for a bucket, read as a
# directory store: the whole discovery, selection, download, cache and
# retention path is exercised with no server, no credentials and no network.
# The S3 path is exercised twice: in test_backups.py against a stub rclone, and
# here (17) with the rclone the command was built with against a real S3
# endpoint, `rclone serve s3` over a copy of the same tree, on loopback.
#
# Ported from frappe-nix's tests/backup-fetch.sh. Deliberate changes: the
# variables are <P>_NIX_*, the messages for an unknown --at and an empty source
# are the base's, and --at also takes ISO 8601 and a leading part of a name.
#
# Usage: backups.sh <path-to-the-fetch-command>
set -euo pipefail

BF="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

pass=0
fail=0
ok() {
  printf '  ok   %s\n' "$1"
  pass=$((pass + 1))
}
no() {
  printf '  FAIL %s\n' "$1"
  printf '       %s\n' "${2:-}"
  fail=$((fail + 1))
}

# Run the fetcher, capturing stdout (manifest), stderr (progress) and status
# separately. Never `bf … | grep`: this script runs under pipefail and the
# fetcher exits non-zero by design on the refusal paths, which would invert the
# assertion.
OUT="" ERR="" RC=0
bf() {
  OUT="$("$BF" "$@" 2>"$WORK/.err")" && RC=0 || RC=$?
  ERR="$(cat "$WORK/.err")"
}
# No pipe here either: `printf … | grep -q` fails a match whenever grep exits
# before printf's last write (bash flushes per line), which then dies of SIGPIPE.
errsays() { [[ $ERR == *"$1"* ]]; }

# ── fixture ───────────────────────────────────────────────────────────────
SRC="$WORK/bucket"
mk() {
  mkdir -p "$(dirname "$1")"
  printf '%s' "$2" >"$1"
}

# A normal backup: database, the (never-encrypted) site config, both archives.
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-database.sql.gz" "jan-db"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-site_config_backup.json" \
  '{"encryption_key":"EK1","backup_encryption_key":"BK1"}'
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-files.tar" "public-archive"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-private-files.tar" "private-archive"

# An encrypted backup. Note the config JSON carries the -enc suffix but is
# plaintext — backup_encryption() never touches it.
mk "$SRC/20260202_020202/20260202_020202-prod_example_com-database-enc.sql.gz" "feb-db"
mk "$SRC/20260202_020202/20260202_020202-prod_example_com-site_config_backup-enc.json" \
  '{"backup_encryption_key":"BK2"}'

# Partial only — unusable for creating a site.
mk "$SRC/20260303_030303/20260303_030303-prod_example_com-partial-database.sql.gz" "mar-partial"

# A site slug ending in "l", which naive "not -partial-" matching gets wrong.
mk "$SRC/20260404_040404/20260404_040404-my_portal-database.sql.gz" "apr-db"

# Noise that must be ignored by the folder scan.
mkdir -p "$SRC/Backups" "$SRC/not-a-timestamp"
mk "$SRC/README.txt" "hello"

export STUB_NIX_BACKUP_SOURCE="$SRC"
export STUB_NIX_BACKUP_CACHE="$WORK/cache"

# ── 1. folder discovery ───────────────────────────────────────────────────
bf list
if [ "$RC" -eq 0 ] && [ "$OUT" = "20260101_010101
20260202_020202
20260303_030303
20260404_040404" ]; then
  ok "list finds timestamp folders and ignores everything else"
else
  no "list finds timestamp folders and ignores everything else" "rc=$RC out=$(printf '%s' "$OUT" | tr '\n' ' ')"
fi

# ── 2. newest wins, and the slug comes off the filename ──────────────────
bf fetch
if [ "$RC" -eq 0 ] &&
  [ "$(printf '%s' "$OUT" | jq -r .folder)" = "20260404_040404" ] &&
  [ "$(printf '%s' "$OUT" | jq -r .slug)" = "my_portal" ]; then
  ok "newest folder is chosen; a slug ending in 'l' is not mistaken for -partial-"
else
  no "newest folder is chosen; slug parsed" "rc=$RC $(printf '%s' "$OUT" | jq -c '{folder,slug}' 2>&1)"
fi

# ── 3. --at pins a specific backup ───────────────────────────────────────
bf fetch --at 20260101_010101
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .folder)" = "20260101_010101" ]; then
  ok "--at pins a specific folder"
else
  no "--at pins a specific folder" "rc=$RC"
fi

bf fetch --at 29990101_000000
if [ "$RC" -ne 0 ] && errsays "no backup matches" && errsays "20260404_040404"; then
  ok "--at on a folder that does not exist fails and lists what does"
else
  no "--at on a folder that does not exist fails" "rc=$RC"
fi

# ── 4. the site config always comes along, encrypted or not ──────────────
# It is what carries encryption_key and backup_encryption_key, and it is
# readable even in an -enc backup, so a restore is self-describing.
bf fetch --at 20260202_020202
if [ "$RC" -eq 0 ] &&
  [ "$(printf '%s' "$OUT" | jq -r .encrypted)" = "true" ] &&
  [ "$(jq -r .backup_encryption_key "$(printf '%s' "$OUT" | jq -r .site_config)")" = "BK2" ]; then
  ok "-enc is detected and the plaintext site config is still readable"
else
  no "-enc is detected and the site config is readable" "rc=$RC $(printf '%s' "$OUT" | jq -c '{encrypted,site_config}' 2>&1)"
fi

# ── 5. a partial dump is refused before anything is downloaded ───────────
bf fetch --at 20260303_030303
if [ "$RC" -ne 0 ] && errsays "PARTIAL"; then
  if find "$STUB_NIX_BACKUP_CACHE" -name '*mar-partial*' -o -name '*partial-database*' | grep -q .; then
    no "partial refused before download" "it downloaded the file anyway"
  else
    ok "partial backup is refused, and nothing was downloaded"
  fi
else
  no "partial backup is refused" "rc=$RC"
fi

# ── 6. public vs private archives ────────────────────────────────────────
# "-files.tar" is a suffix of "-private-files.tar", so a naive match picks the
# private archive as the public one and restores it into the wrong place.
bf fetch --at 20260101_010101 --files --private-files
pub="$(printf '%s' "$OUT" | jq -r .files)"
priv="$(printf '%s' "$OUT" | jq -r .private_files)"
if [ "$RC" -eq 0 ] && [ "$(cat "$pub")" = "public-archive" ] && [ "$(cat "$priv")" = "private-archive" ]; then
  ok "public and private archives are told apart"
else
  no "public and private archives are told apart" "pub=$(basename "$pub") priv=$(basename "$priv")"
fi

# ── 7. archives are opt-in, but their availability is reported ───────────
bf fetch --at 20260101_010101
if [ "$(printf '%s' "$OUT" | jq -r .files)" = "null" ] &&
  [ "$(printf '%s' "$OUT" | jq -r .available.files)" = "true" ]; then
  ok "archives are skipped by default and reported as available"
else
  no "archives are skipped by default and reported as available" "$(printf '%s' "$OUT" | jq -c '{files,available}')"
fi

# ── 8. cache reuse ───────────────────────────────────────────────────────
bf fetch --at 20260101_010101
if errsays "cached" && ! errsays "fetch   "; then
  ok "a second fetch of the same folder is served from cache"
else
  no "a second fetch of the same folder is served from cache" "$(printf '%s' "$ERR" | tr '\n' ' ')"
fi

# ── 9. a truncated cache entry is not trusted ────────────────────────────
db="$(printf '%s' "$OUT" | jq -r .database)"
printf 'x' >"$db"
bf fetch --at 20260101_010101
if errsays "fetch   " && [ "$(cat "$(printf '%s' "$OUT" | jq -r .database)")" = "jan-db" ]; then
  ok "a size mismatch invalidates the cache entry"
else
  no "a size mismatch invalidates the cache entry" "$(printf '%s' "$ERR" | tr '\n' ' ')"
fi

# ── 10. retention never eats the folder this run just wrote ──────────────
# `sort | head` takes the oldest, and fetching an older backup while newer ones
# are cached makes the fresh directory exactly that.
bf fetch --at 20260404_040404
bf fetch --at 20260202_020202
bf fetch --at 20260101_010101 --keep 1
db="$(printf '%s' "$OUT" | jq -r .database)"
if [ "$RC" -eq 0 ] && [ -f "$db" ]; then
  ok "retention keeps the folder just downloaded"
else
  no "retention keeps the folder just downloaded" "rc=$RC manifest points at a deleted file"
fi

# ── 11. a prefix inside the bucket ───────────────────────────────────────
mk "$SRC/Backups/20260505_050505/20260505_050505-prod_example_com-database.sql.gz" "may-db"
STUB_NIX_BACKUP_SOURCE="$SRC/Backups" bf list
if [ "$OUT" = "20260505_050505" ]; then
  ok "a prefix inside the bucket is honoured"
else
  no "a prefix inside the bucket is honoured" "out=$OUT"
fi

# ── 12. actionable errors ────────────────────────────────────────────────
STUB_NIX_BACKUP_SOURCE="$WORK/empty" bf list || true
if [ "$RC" -ne 0 ] && errsays "no backups under" && errsays "YYYYMMDD_HHMMSS" && errsays "BACKUPS_PREFIX"; then
  ok "an empty source explains itself and mentions the prefix"
else
  no "an empty source explains itself" "rc=$RC"
fi

# With no source at all, the message has to name the secret, not just the var.
(
  unset STUB_NIX_BACKUP_SOURCE
  "$BF" list 2>"$WORK/.err" >/dev/null || true
)
if grep -q "backup-access" "$WORK/.err" && grep -q "BACKUPS_URL" "$WORK/.err"; then
  ok "missing credentials name the agenix secret, not just the variable"
else
  no "missing credentials name the agenix secret" "$(head -3 "$WORK/.err")"
fi

# ── 13. --at takes ISO 8601 and any leading part of a name ───────────────
# docs/conventions.md, Restore, --at: ISO 8601 in both products, and each
# product's own backup naming as well. A partial time is the newest backup
# within it.
for at in 2026-01-01 2026-01-01T01:01:01 "2026-01-01 01:01" 2026-01-01T01:01:01Z 20260101 20260101_01; do
  bf fetch --at "$at"
  if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .name)" = "20260101_010101" ]; then
    ok "--at $at picks 20260101_010101"
  else
    no "--at $at picks 20260101_010101" "rc=$RC $(printf '%s' "$ERR" | head -2)"
  fi
done
bf fetch --at 2026-02
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .name)" = "20260202_020202" ]; then
  ok "--at a month picks the newest backup in it"
else
  no "--at a month picks the newest backup in it" "rc=$RC"
fi
bf fetch --at yesterday
if [ "$RC" -ne 0 ] && errsays "not a point in time"; then
  ok "--at that is neither a time nor a name is refused"
else
  no "--at that is neither a time nor a name is refused" "rc=$RC"
fi

# ── 14. the manifest ─────────────────────────────────────────────────────
bf fetch --at 20260101_010101
if [ "$(printf '%s' "$OUT" | jq -c '{name, at, folder, available}')" = \
  '{"name":"20260101_010101","at":"2026-01-01T01:01:01","folder":"20260101_010101","available":{"database":true,"site_config":true,"files":true,"private_files":true}}' ]; then
  ok "the manifest names the set, its time, and what it holds"
else
  no "the manifest names the set, its time, and what it holds" "$(printf '%s' "$OUT" | jq -c .)"
fi

# ── 15. an old variable name still works, with a warning ─────────────────
(
  unset STUB_NIX_BACKUP_SOURCE
  STUB_BACKUP_SOURCE="$SRC" "$BF" list >"$WORK/.out" 2>"$WORK/.err" || true
)
if [ "$(tail -n1 "$WORK/.out")" = "20260404_040404" ] && grep -q "STUB_BACKUP_SOURCE is deprecated" "$WORK/.err"; then
  ok "a legacy variable is read, and named as deprecated"
else
  no "a legacy variable is read, and named as deprecated" "$(head -2 "$WORK/.err")"
fi

# ── 16. usage ────────────────────────────────────────────────────────────
bf fetch --help
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q -- "--private-files"; then
  ok "--help lists the layout's optional parts as flags"
else
  no "--help lists the layout's optional parts as flags" "rc=$RC"
fi
bf
if [ "$RC" -eq 2 ]; then
  ok "no subcommand is a usage error"
else
  no "no subcommand is a usage error" "rc=$RC"
fi

# ── 17. a real S3 endpoint ───────────────────────────────────────────────
# `rclone serve s3` over a directory in which each top-level directory is a
# bucket. The secret holds '/' and '+', which minio-client's MC_HOST_ form
# signed wrongly; the remote rclone is given must sign it as written.
S3="$WORK/s3"
mkdir -p "$S3/b"
cp -r "$SRC" "$S3/b/Backups-s3"
# A realistic key id: rclone serve s3 does not find one as short as "AK".
KEY=AKIAEXAMPLE SECRET='s/e+c' PORT=$((20000 + RANDOM % 20000))
rclone serve s3 "$S3" --addr "127.0.0.1:$PORT" --auth-key "$KEY,$SECRET" >"$WORK/.s3log" 2>&1 &
S3PID=$!
trap 'kill "$S3PID" 2>/dev/null || true; rm -rf "$WORK"' EXIT
for _ in $(seq 100); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  sleep 0.1
done
s3() {
  env -u STUB_NIX_BACKUP_SOURCE BACKUPS_URL="http://127.0.0.1:$PORT" BACKUPS_ACCESS_KEY="$KEY" \
    BACKUPS_SECRET_KEY="$SECRET" BACKUPS_BUCKET=b BACKUPS_PREFIX=/Backups-s3/ "$@"
}
OUT="$(s3 "$BF" list 2>"$WORK/.err")" && RC=0 || RC=$?
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | head -n1)" = "20260101_010101" ]; then
  ok "S3: a real endpoint lists the backups"
else
  no "S3: a real endpoint lists the backups" "rc=$RC $(head -3 "$WORK/.err")"
fi
OUT="$(s3 "$BF" fetch --at 20260101_010101 --private-files 2>"$WORK/.err")" && RC=0 || RC=$?
if [ "$RC" -eq 0 ] && [ "$(cat "$(printf '%s' "$OUT" | jq -r .database)")" = "jan-db" ] &&
  [ "$(cat "$(printf '%s' "$OUT" | jq -r .private_files)")" = "private-archive" ]; then
  ok "S3: a real endpoint serves the download, byte for byte"
else
  no "S3: a real endpoint serves the download" "rc=$RC $(head -3 "$WORK/.err")"
fi
OUT="$(s3 env BACKUPS_SECRET_KEY=wrong "$BF" list 2>"$WORK/.err")" && RC=0 || RC=$?
if [ "$RC" -ne 0 ] && grep -qi "rclone lsjson failed" "$WORK/.err"; then
  ok "S3: wrong credentials fail with rclone's own message"
else
  no "S3: wrong credentials fail" "rc=$RC $(head -3 "$WORK/.err")"
fi
OUT="$(s3 env BACKUPS_PREFIX=nowhere "$BF" list 2>"$WORK/.err")" && RC=0 || RC=$?
if [ "$RC" -ne 0 ] && grep -q "no backups under" "$WORK/.err"; then
  ok "S3: an empty prefix is no backups, not an rclone failure"
else
  no "S3: an empty prefix is no backups" "rc=$RC $(head -3 "$WORK/.err")"
fi
OUT="$(s3 env STUB_NIX_MC=/usr/bin/mc "$BF" list 2>"$WORK/.err")" && RC=0 || RC=$?
if [ "$RC" -ne 0 ] && grep -q "STUB_NIX_MC names a minio-client binary" "$WORK/.err"; then
  ok "S3: minio-client's variable is refused with its replacement"
else
  no "S3: minio-client's variable is refused" "rc=$RC $(head -3 "$WORK/.err")"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
