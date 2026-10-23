# Keeps a development shell's database datadir off btrfs copy-on-write
# (docs/library.md, Development shell): frappe-nix's lib/db-nocow.nix (MariaDB)
# and odoo-nix's port of it (PostgreSQL), as one command. Glue, so bash
# (docs/library.md, design rule 5).
#
# A database rewrites its pages in place all day (InnoDB 16 KB, PostgreSQL
# 8 KB). On a copy-on-write filesystem no write lands in place: each one
# allocates a new extent, rewrites the checksum and extent trees above it, and,
# on a filesystem mounted with compression (as btrfs desktops usually are),
# compresses the page first. The table files fragment within days of a
# restore, and every fsync the server issues commits a btrfs transaction that
# other programs' fsyncs then queue behind. Restores, migrations and test
# suites are mostly fsyncs.
#
# The standard remedy is the NOCOW attribute (`chattr +C`), which turns off
# copy-on-write, data checksums and compression for the files it covers. It is
# only honoured on a file that is still empty, so it is set on a *directory*
# before anything is written there, and every file created inside inherits it.
#
# `prepare <dir>...` does that on shell entry. Which directory it marks is the
# product's `strategy`:
#
#   datadir  (MariaDB) a missing or empty directory is created and marked.
#   parent   (PostgreSQL) devenv runs `initdb` only when $PGDATA does not
#            exist, so creating it empty to mark it would leave it never
#            initialized. A missing datadir's *parent* is marked instead, and
#            initdb's mkdir inherits the attribute; the first entry that finds
#            the datadir there clears the parent's again (a marker file,
#            .<label>-nocow-parent, records that it was this tool's doing).
#            Only what is created in the parent meanwhile is NOCOW besides,
#            which for development state costs nothing that matters. An
#            existing, empty datadir is marked as with `datadir`.
#
# A datadir that already holds a database cannot be fixed in place, since the
# attribute does not reach existing data, so `prepare` only says so, and
# `migrate <datadir>` does the copy, with the server stopped. Which process
# counts as the server is the product's `server`:
#
#   cmdline  any process of this user with --datadir=<dir> on its command line
#            (frappe-nix's mariadbd reaper selects the same way), never by
#            process name, so a neighbouring project's server does not block
#   pidfile  the server's own lock, <dir>/<pidFile> (PostgreSQL's
#            postmaster.pid), naming a live process on its first line; a stale
#            one left by a crash does not count
#
# Not btrfs: both subcommands are silent no-ops, and `migrate` says so before it
# looks for a server, so a live one off btrfs does not matter (frappe-nix#92).
{ lib }:

{
  pkgs,
  label,
  strategy,
  server,
  pidFile ? "postmaster.pid",
  name ? "${label}-db-nocow",
}:
assert lib.assertMsg
  (lib.elem strategy [
    "datadir"
    "parent"
  ])
  "lib.dbNocow: strategy is \"datadir\" (MariaDB) or \"parent\" (PostgreSQL), not ${builtins.toJSON strategy}";
assert lib.assertMsg (lib.elem server [
  "cmdline"
  "pidfile"
]) "lib.dbNocow: server is \"cmdline\" or \"pidfile\", not ${builtins.toJSON server}";
pkgs.writeShellApplication {
  inherit name;
  runtimeInputs = [
    pkgs.coreutils
    pkgs.e2fsprogs # chattr, lsattr
    pkgs.gnugrep
  ]
  ++ lib.optional (server == "cmdline") pkgs.procps; # pgrep
  passthru = { inherit strategy server; };
  text = ''
    usage() {
      echo "usage: ${name} prepare <dir>..." >&2
      echo "       ${name} migrate <datadir>" >&2
      exit 2
    }

    on_btrfs() {
      [ "$(stat -f -c %T "$1" 2>/dev/null)" = btrfs ]
    }

    # lsattr prints the flags as one word ahead of the name; C is NOCOW.
    has_nocow() {
      lsattr -d -- "$1" 2>/dev/null | cut -d' ' -f1 | grep -q C
    }

    is_empty() {
      [ -z "$(ls -A -- "$1" 2>/dev/null)" ]
    }

    ${
      if server == "cmdline" then
        ''
          # By --datadir and uid, never by process name.
          server_running() {
            pgrep -u "$(id -u)" -f -- "--datadir=$1" >/dev/null 2>&1
          }
        ''
      else
        ''
          # The server's own lock names its pid on the first line. Stale after
          # a crash, so the pid has to be alive too.
          server_running() {
            local pid
            [ -f "$1/${pidFile}" ] || return 1
            pid=$(head -n 1 -- "$1/${pidFile}")
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
          }
        ''
    }

    # An existing directory: marked when empty, reported when it holds data.
    mark_or_report() {
      local dir=$1
      on_btrfs "$dir" || return 0
      has_nocow "$dir" && return 0
      if is_empty "$dir"; then
        chattr +C -- "$dir"
        return 0
      fi
      echo "⚠  $dir holds a database on btrfs with copy-on-write on."
      echo "   Every database write is copied and compressed, which makes restores,"
      echo "   migrations and tests slow and stalls other programs' saves. To fix it,"
      echo "   stop \`devenv up\` and run:  ${name} migrate \"$dir\""
    }

    ${
      if strategy == "datadir" then
        ''
          prepare_one() {
            mkdir -p -- "$1"
            mark_or_report "$1"
          }
        ''
      else
        ''
          prepare_one() {
            local dir=$1 parent marker
            parent=$(dirname -- "$dir")
            marker="$parent/.${label}-nocow-parent"

            if [ ! -e "$dir" ]; then
              mkdir -p -- "$parent"
              on_btrfs "$parent" || return 0
              has_nocow "$parent" && return 0
              chattr +C -- "$parent"
              : > "$marker"
              return 0
            fi

            # The parent was marked for initdb's sake, and the datadir has it now.
            if [ -e "$marker" ]; then
              chattr -C -- "$parent" 2>/dev/null || true
              rm -f -- "$marker"
            fi
            mark_or_report "$dir"
          }
        ''
    }

    prepare() {
      [ "$#" -ge 1 ] || usage
      local dir
      for dir in "$@"; do
        prepare_one "''${dir%/}"
      done
    }

    migrate() {
      [ "$#" -eq 1 ] || usage
      local dir
      dir=$(realpath -- "$1")

      if [ ! -d "$dir" ]; then
        echo "${name}: $dir is not a directory" >&2
        exit 1
      fi
      if ! on_btrfs "$dir"; then
        echo "$dir is not on btrfs; nothing to do."
        exit 0
      fi
      if has_nocow "$dir"; then
        echo "$dir already has copy-on-write off; nothing to do."
        exit 0
      fi
      if server_running "$dir"; then
        echo "${name}: a database server is running on $dir — stop \`devenv up\` first." >&2
        exit 1
      fi

      local need avail
      need=$(du -sb -- "$dir" | cut -f1)
      avail=$(df -B1 --output=avail -- "$dir" | tail -n 1 | tr -d ' ')
      # The copy and the original coexist until the swap at the end, and a
      # btrfs that fills completely is far harder to recover than a refusal.
      if [ "$avail" -lt $((need + need / 10 + 1073741824)) ]; then
        echo "${name}: need about $((need / 1048576)) MiB free beside $dir, have $((avail / 1048576)) MiB." >&2
        exit 1
      fi

      # Not local: the EXIT trap below has to see it however the script ends.
      tmp="$dir.nocow-$$"
      backup="$dir.cow-backup-$(date +%Y%m%d-%H%M%S)"

      trap 'rm -rf -- "$tmp"' EXIT
      mkdir -- "$tmp"
      chattr +C -- "$tmp"

      echo "Copying $((need / 1048576)) MiB into a copy-on-write-free directory…"
      # --reflink=never: a reflink would share the original's copy-on-write
      # extents, which is the one thing this copy exists to get rid of (btrfs
      # refuses to clone between COW and NOCOW files anyway). cp does not carry
      # inode flags across, so every copied file takes +C from $tmp. The
      # datadir keeps its own mode (PostgreSQL insists on 0700).
      cp -a --reflink=never -- "$dir/." "$tmp/"
      chmod --reference="$dir" -- "$tmp"
      sync -f -- "$tmp"

      mv -- "$dir" "$backup"
      mv -- "$tmp" "$dir"
      trap - EXIT

      echo "Done. The original is kept at:"
      echo "  $backup"
      echo "Start \`devenv up\`, check that everything works, then remove it:"
      echo "  rm -rf \"$backup\""
    }

    [ "$#" -ge 1 ] || usage
    cmd=$1
    shift
    case $cmd in
      prepare) prepare "$@" ;;
      migrate) migrate "$@" ;;
      *) usage ;;
    esac
  '';
}
