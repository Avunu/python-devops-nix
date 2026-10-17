#!/usr/bin/env bash
# Checks for the db-nocow command (lib.dbNocow): keeping a development
# database's datadir off btrfs copy-on-write. Ported from frappe-nix's and
# odoo-nix's tests/db-nocow.sh, one per strategy.
#
# Usage: db-nocow.sh <path-to-the-command> <datadir|parent> <cmdline|pidfile> <label>
#
# What the tool does depends on the filesystem it runs on, and a build sandbox
# lives on whatever the builder's temp directory is (tmpfs, usually). So the
# assertions branch: on btrfs they check the attribute is really set and the
# copy really made; on anything else, that every subcommand is a silent no-op.
# Both branches check the refusals. To run the btrfs branch by hand, point
# TMPDIR at a btrfs directory and run this script directly.
set -euo pipefail

TOOL="$1"
STRATEGY="$2"
SERVER="$3"
LABEL="$4"
NAME="$(basename "$TOOL")"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}
check() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then ok "$desc"; else no "$desc"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
nocow() { lsattr -d -- "$1" 2> /dev/null | cut -d' ' -f1 | grep -q C; }

if [ "$(stat -f -c %T "$ROOT")" = btrfs ]; then
  btrfs=1
  echo "(running on btrfs: checking the attribute and the copy; $STRATEGY, $SERVER)"
else
  btrfs=0
  echo "(not on btrfs, $(stat -f -c %T "$ROOT"): checking that nothing is done; $STRATEGY, $SERVER)"
fi

if [ "$STRATEGY" = datadir ]; then
  echo "── prepare ──────────────────────────────────────────────────────"
  out=$("$TOOL" prepare "$ROOT/fresh" "$ROOT/tmpdir")
  check "a missing datadir is created" test -d "$ROOT/fresh"
  check "so is a second directory named alongside it" test -d "$ROOT/tmpdir"
  check "a fresh datadir is prepared silently" test -z "$out"
  if [ "$btrfs" = 1 ]; then
    check "a fresh datadir gets copy-on-write off" nocow "$ROOT/fresh"
    touch "$ROOT/fresh/ibdata1"
    check "files created in it inherit that" nocow "$ROOT/fresh/ibdata1"
  else
    check_not "no attribute is set off btrfs" nocow "$ROOT/fresh"
  fi
else
  echo "── prepare, before initdb ───────────────────────────────────────"
  # devenv runs initdb only when $PGDATA does not exist: it must not be created.
  out=$("$TOOL" prepare "$ROOT/state/postgres")
  check "a missing datadir is not created (devenv would skip initdb)" test ! -e "$ROOT/state/postgres"
  check "…its parent is" test -d "$ROOT/state"
  check "…silently" test -z "$out"
  # What initdb does: mkdir, then files.
  mkdir "$ROOT/state/postgres"
  touch "$ROOT/state/postgres/PG_VERSION"
  if [ "$btrfs" = 1 ]; then
    check "the parent gets copy-on-write off for initdb's mkdir" nocow "$ROOT/state"
    check "…marked as this tool's doing" test -e "$ROOT/state/.$LABEL-nocow-parent"
    check "…which the datadir inherits" nocow "$ROOT/state/postgres"
    check "…and its files with it" nocow "$ROOT/state/postgres/PG_VERSION"
  else
    check_not "no attribute is set off btrfs" nocow "$ROOT/state"
    check "…and no marker left" test ! -e "$ROOT/state/.$LABEL-nocow-parent"
  fi
  out=$("$TOOL" prepare "$ROOT/state/postgres")
  check "the next entry is silent" test -z "$out"
  check_not "…and clears the parent's attribute" nocow "$ROOT/state"
  check "…and its marker" test ! -e "$ROOT/state/.$LABEL-nocow-parent"
  if [ "$btrfs" = 1 ]; then
    check "…leaving the datadir's" nocow "$ROOT/state/postgres"
  fi

  echo "── prepare, existing datadirs ───────────────────────────────────"
  mkdir -p "$ROOT/empty"
  out=$("$TOOL" prepare "$ROOT/empty")
  check "an empty datadir is prepared silently" test -z "$out"
  if [ "$btrfs" = 1 ]; then
    check "…with copy-on-write off" nocow "$ROOT/empty"
  fi
fi

mkdir -p "$ROOT/used"
echo data > "$ROOT/used/PG_VERSION"
out=$("$TOOL" prepare "$ROOT/used")
check_not "a datadir already holding data is left as it is" nocow "$ROOT/used"
if [ "$btrfs" = 1 ]; then
  check "…and reported, with the command that fixes it" \
    grep -qF "run:  $NAME migrate \"$ROOT/used\"" <<< "$out"
else
  check "…and not reported off btrfs" test -z "$out"
fi

echo "── migrate ──────────────────────────────────────────────────────"
mkdir -p "$ROOT/db/base/1"
chmod 0700 "$ROOT/db"
echo one > "$ROOT/db/ibdata1"
echo two > "$ROOT/db/base/1/1259"

if [ "$SERVER" = cmdline ]; then
  # Anything carrying --datadir=<dir> counts as the server. The trailing `:`
  # keeps bash from exec'ing straight into sleep, which would drop the
  # --datadir argument from the command line pgrep reads.
  bash -c 'sleep 30; :' fake-mariadbd "--datadir=$ROOT/db" &
  server=$!
  # Wait until pgrep sees it, as the tool will. A busy builder can take seconds
  # to start a process, and carrying on before it is visible would test nothing.
  started=0
  for _ in $(seq 1 300); do
    if pgrep -f -- "--datadir=$ROOT/db" > /dev/null; then
      started=1
      break
    fi
    sleep 0.1
  done
  if [ "$started" != 1 ]; then
    echo "the stand-in server never appeared to pgrep" >&2
    exit 1
  fi
else
  # The server's lock file, naming a live process: the server is running.
  sleep 30 &
  server=$!
  printf '%s\n%s\n' "$server" "$ROOT/db" > "$ROOT/db/postmaster.pid"
fi
if [ "$btrfs" = 1 ]; then
  check_not "migrate refuses while a server is running on the datadir" "$TOOL" migrate "$ROOT/db"
  err=$("$TOOL" migrate "$ROOT/db" 2>&1 || true)
  check "…and says so" grep -qF "a database server is running on $ROOT/db" <<< "$err"
else
  # Off btrfs there is nothing to migrate, and the tool says so before it looks
  # for a server, so a live one does not matter (frappe-nix#92).
  check "off btrfs, migrate has nothing to do even with a server running" "$TOOL" migrate "$ROOT/db"
  got=$("$TOOL" migrate "$ROOT/db")
  check "…and says so" grep -qF "is not on btrfs; nothing to do." <<< "$got"
fi
kill "$server" 2> /dev/null || true
wait "$server" 2> /dev/null || true

# pidfile: a crash leaves the file behind, naming a dead pid: not running.
check "migrate succeeds once it is stopped" "$TOOL" migrate "$ROOT/db"
rm -f "$ROOT/db/postmaster.pid"
check "the data is all still there" \
  test "$(cat "$ROOT/db/ibdata1")-$(cat "$ROOT/db/base/1/1259")" = "one-two"
check "the datadir keeps its mode, 0700 (PostgreSQL requires it)" test "$(stat -c %a "$ROOT/db")" = 700
if [ "$btrfs" = 1 ]; then
  check "the datadir has copy-on-write off" nocow "$ROOT/db"
  check "and so do the files copied into it" nocow "$ROOT/db/base/1/1259"
  check "the original is kept beside it" \
    test "$(cat "$ROOT"/db.cow-backup-*/base/1/1259)" = "two"
  check "a second migrate has nothing to do" "$TOOL" migrate "$ROOT/db"
  check "and leaves no second backup" \
    test "$(find "$ROOT" -maxdepth 1 -name 'db.cow-backup-*' | wc -l)" -eq 1
else
  check "nothing is copied off btrfs" \
    test "$(find "$ROOT" -maxdepth 1 -name 'db.*' | wc -l)" -eq 0
fi
check_not "a missing datadir is an error" "$TOOL" migrate "$ROOT/nope"

echo "── usage ────────────────────────────────────────────────────────"
check_not "no arguments is an error" "$TOOL"
check_not "an unknown subcommand is an error" "$TOOL" frobnicate
check_not "prepare needs a directory" "$TOOL" prepare
check_not "migrate takes one datadir" "$TOOL" migrate "$ROOT/a" "$ROOT/b"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All db-nocow checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
