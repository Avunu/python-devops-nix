# lib.dbNocow in both strategies: frappe-nix's (MariaDB: mark the datadir; a
# server is a process with --datadir=<dir>) and odoo-nix's (PostgreSQL: mark
# the parent before initdb; a server is a live postmaster.pid). Branches on the
# sandbox's own filesystem, so it checks the real attribute on a btrfs builder
# and the no-op path everywhere else; tests/db-nocow.sh runs the btrfs branch
# by hand (TMPDIR=<a btrfs directory>). Linux only: chattr and lsattr are
# e2fsprogs', and both products enable the tool on Linux alone.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  shapes = {
    mariadb = {
      strategy = "datadir";
      server = "cmdline";
    };
    postgresql = {
      strategy = "parent";
      server = "pidfile";
    };
  };
  tool =
    shape:
    self.lib.dbNocow (
      {
        inherit pkgs;
        label = "stub-nix";
        name = "stub-nix-db-nocow-${shape}";
      }
      // shapes.${shape}
    );
in
checks.script "lib-db-nocow"
  {
    tools = [
      pkgs.e2fsprogs
      pkgs.procps
    ];
  }
  (
    if pkgs.stdenv.hostPlatform.isLinux then
      lib.concatStrings (
        lib.mapAttrsToList (shape: args: ''
          echo "== ${shape}"
          bash ${../tests/db-nocow.sh} ${lib.getExe (tool shape)} ${args.strategy} ${args.server} stub-nix 2>&1 | tee -a "$out"
        '') shapes
      )
    else
      ''echo "db-nocow is Linux only" > "$out"''
  )
