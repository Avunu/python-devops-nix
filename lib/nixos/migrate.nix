# Migrate on deploy (PLAN.md, Phase 6; CONVENTIONS.md, Migrate on deploy):
# the migrate.* options both products already name the same, and the snapshot
# flow behind them as one command (python/migrate.py). The product supplies
# what is specific to its database and framework: how to dump, verify and
# restore, how to migrate, and what a failed migration leaves running.
{ lib }:
let
  inherit (lib) mkOption types;
  pythonLib = import ../python { inherit lib; };
  checksLib = import ../checks { inherit lib; };

  modules = builtins.path {
    name = "python-devops-nix-migrate";
    path = ../../python;
    filter =
      path: _type:
      lib.elem (baseNameOf path) [
        "journald.py"
        "migrate.py"
      ];
  };
in
rec {
  # python/migrate.py and python/journald.py, which it imports, for a product
  # whose own migrate tool takes the snapshot in-process (odoo-nix's `odoo db
  # migrate` decides whether anything changed before it snapshots). Vendor
  # both, side by side, as migrate.py and journald.py.
  module = ../../python/migrate.py;
  inherit modules;

  # The options, as `migrate = python-devops-nix.lib.nixos.migrate.options { ... };`
  # in the product's option tree. The names and defaults are the ones both
  # products shipped before this; a product adds its own beside them
  # (frappe-nix's maintenanceMode and offline, odoo-nix's full and timeout).
  #
  #   what       the migration, in prose ("`bench migrate` for each site")
  #   dumpTool   the tool the snapshot is taken with ("mysqldump", "pg_dump")
  #   where      where snapshots are kept, in prose
  options =
    {
      what,
      dumpTool,
      where,
    }:
    {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Run ${what} when a new build is deployed, behind a verified snapshot.
          Set to false to run migrations by hand.
        '';
      };
      snapshot = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Take a snapshot of the database (${dumpTool}) immediately before
          migrating, and verify it before anything relies on it, so a failed
          migration can be rolled back. Schema changes are DDL, which commits
          on its own and cannot be undone in a transaction: a snapshot is the
          only way back. Snapshots are private: their directory is 0700 and
          each file 0600.
        '';
      };
      rollbackOnFailure = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Restore the pre-migrate snapshot if the migration fails, returning
          the database to its state before it. No effect without
          migrate.snapshot.
        '';
      };
      snapshotRetention = mkOption {
        type = types.ints.positive;
        default = 3;
        description = "How many pre-migrate snapshots to keep ${where}, newest first.";
      };
    };

  # The migrate command for one target: `<name> [run|take|restore SNAPSHOT|prune|list]`,
  # `run` by default: the unit's `ExecStart = lib.getExe (... .script { ... })`. Its steps and its
  # messages are python/migrate.py's.
  #
  #   label        the message prefix, "<label>(<target>): ..."
  #   target       what is migrated: a site, a database
  #   cfg          the values of the `options` above
  #   migrate      the migration (an executable)
  #   check        exit 0 to migrate, 100 when there is nothing to do; null to
  #                always migrate
  #   onSuccess    after a successful migration (a failure is a warning)
  #   onFailure    after a failed migration and its rollback: what the product
  #                leaves running. frappe-nix says the site stays in
  #                maintenance mode; odoo-nix needs none, since odoo.service
  #                Requires= the unit and does not start.
  #   snapshotDir  where snapshots go; made and kept 0700
  #   prefix       snapshot names: <prefix>-YYYYmmdd-HHMMSS<extension>
  #   extension    ".sql.gz", ".dump"
  #   dump         writes the dump to stdout (e.g. mysqldump | gzip)
  #   verify       `verify SNAPSHOT` exits 0 when the dump is whole; see
  #                `verifiers` for both products' databases
  #   restore      `restore SNAPSHOT` puts the database back as the dump has it
  #   python       the interpreter it runs on: the application's, in a
  #                production closure (PLAN.md, Python versions)
  #
  # The executables run with the unit's user, environment and sandbox.
  script =
    {
      pkgs,
      label,
      target,
      cfg,
      migrate,
      check ? null,
      onSuccess ? null,
      onFailure ? null,
      snapshotDir ? null,
      prefix ? "premigrate-${target}",
      extension ? null,
      dump ? null,
      verify ? null,
      restore ? null,
      python ? pkgs.python3,
      name ? "${label}-${lib.strings.sanitizeDerivationName target}",
    }:
    let
      exe = value: if value == null then null else "${value}";
      snapshotOn = cfg.snapshot or true;
      missing = lib.filter (arg: arg.value == null) [
        {
          name = "snapshotDir";
          value = snapshotDir;
        }
        {
          name = "extension";
          value = extension;
        }
        {
          name = "dump";
          value = dump;
        }
        {
          name = "verify";
          value = verify;
        }
        {
          name = "restore";
          value = restore;
        }
      ];
      config = {
        inherit label target;
        migrate = exe migrate;
        check = exe check;
        onSuccess = exe onSuccess;
        onFailure = exe onFailure;
        rollbackOnFailure = cfg.rollbackOnFailure or true;
        snapshot =
          if snapshotOn then
            {
              dir = snapshotDir;
              inherit prefix extension;
              retention = cfg.snapshotRetention or 3;
              dump = exe dump;
              verify = exe verify;
              restore = exe restore;
            }
          else
            null;
      };
      configFile = pkgs.writeText "${name}.json" (builtins.toJSON config);
    in
    assert lib.assertMsg (!snapshotOn || missing == [ ])
      "lib.nixos.migrate.script ${name}: a snapshot needs ${
        lib.concatMapStringsSep ", " (arg: arg.name) missing
      }";
    pythonLib.command {
      inherit
        pkgs
        name
        modules
        python
        ;
      imports = "import migrate";
      main = "migrate.main([${builtins.toJSON "${configFile}"}, *sys.argv[1:]], prog=${builtins.toJSON name})";
      passthru = {
        inherit config configFile;
      };
    };

  # Snapshot verifiers for both products' databases: `verify SNAPSHOT`.
  verifiers = {
    # A mysqldump (or mariadb-dump) is whole when it ends with the
    # "-- Dump completed" comment it writes last (on by default; not with
    # --skip-comments or --compact). A dump cut short by a lost connection or a
    # full disk does not. `compressed`: the dump is gzipped, and gzip's own
    # check runs too.
    mysqldump =
      {
        pkgs,
        compressed ? true,
      }:
      pkgs.writeShellScript "verify-mysqldump" ''
        set -euo pipefail
        last=$(${
          if compressed then "${pkgs.gzip}/bin/gzip -cd -- \"$1\"" else "${pkgs.coreutils}/bin/cat -- \"$1\""
        } | ${pkgs.coreutils}/bin/tail -n 1)
        case "$last" in
          "-- Dump completed"*) ;;
          *) echo "verify: $1 does not end with mysqldump's '-- Dump completed' line" >&2; exit 1 ;;
        esac
      '';

    # A pg_dump custom-format archive is whole when pg_restore can read its
    # table of contents (odoo-nix's check). `postgresql`: the server's package,
    # whose pg_restore matches the dump's format version.
    pgRestoreList =
      { pkgs, postgresql }:
      pkgs.writeShellScript "verify-pg-restore-list" ''
        set -euo pipefail
        ${postgresql}/bin/pg_restore --list -- "$1" > /dev/null
      '';
  };

  # The flow's unit tests (tests/test_migrate.py), on every interpreter a
  # preset names: a product's migrate tool may import the module in-process.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "migrate",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = checksLib.mk { inherit pkgs product; };
      run = attr: ''
        echo "== ${attr} (${pkgs.${attr}.version})"
        ${pkgs.${attr}.interpreter} -I ${../../tests/test_migrate.py} ${modules} 2>&1
      '';
    in
    checks.script name { } "{ ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
