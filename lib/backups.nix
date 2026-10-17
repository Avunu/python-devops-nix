# The backup fetch, list and cache layer behind each product's restore command
# (PLAN.md, Phase 2). The logic is python/backups.py; see its docstring for the
# store, the overrides and the cache, and its `Layout` for what a product
# supplies.
{ lib }:

let
  pythonLib = import ./python { inherit lib; };
in
rec {
  # The module itself, for a product that imports it in-process: copy it into
  # the product's package (PLAN.md, design rule 6), e.g. as `_backups.py`, and
  # construct `Backups(label=…, env_prefix=…, layout=…)`.
  module = ../python/backups.py;

  # The command: `<label>-backup-fetch {list,folders,fetch}`.
  #
  #   label      the product's name: message prefix, mc alias, command name
  #   envPrefix  <P>: the overrides are <P>_NIX_BACKUP_SOURCE, <P>_NIX_MC and
  #              <P>_NIX_BACKUP_CACHE
  #   layout     a Python file implementing `Layout` (ROLES, parse, and
  #              optionally OPTIONAL_ROLES and HINT). It is copied next to the
  #              module as backup_layout.py, so it must stand on its own: the
  #              standard library and nothing of the product's.
  #   legacyEnv  { <suffix> = "<old name>"; }: an old variable read, with a
  #              warning, while a rename is in its deprecation window, e.g.
  #              { BACKUP_SOURCE = "OLD_BACKUP_SOURCE"; }
  #   python     the interpreter it runs on
  #   mc         minio-client; <P>_NIX_MC overrides it at run time
  fetch =
    {
      pkgs,
      label,
      envPrefix,
      layout,
      legacyEnv ? { },
      python ? pkgs.python3,
      mc ? pkgs.minio-client,
      name ? "${label}-backup-fetch",
    }:
    let
      modules = pkgs.runCommandLocal "${name}-modules" { } ''
        mkdir -p "$out"
        cp ${module} "$out/backups.py"
        cp ${layout} "$out/backup_layout.py"
      '';
      mcVar = "${envPrefix}_NIX_MC";
      legacyMc = legacyEnv.MC or null;
    in
    pythonLib.command {
      inherit
        pkgs
        name
        modules
        python
        ;
      imports = ''
        import os

        import backup_layout
        import backups

        if not os.environ.get(${builtins.toJSON mcVar})${
          lib.optionalString (legacyMc != null) " and not os.environ.get(${builtins.toJSON legacyMc})"
        }:
            os.environ[${builtins.toJSON mcVar}] = ${builtins.toJSON (lib.getExe' mc "mc")}
      '';
      main = ''
        backups.main(
            sys.argv[1:],
            label=${builtins.toJSON label},
            env_prefix=${builtins.toJSON envPrefix},
            layout=backup_layout,
            legacy_env=${builtins.toJSON legacyEnv},
            prog=${builtins.toJSON name},
        )'';
      passthru = { inherit modules; };
    };

  # The base's tests of this layer, as a check a product can register with its
  # presets' interpreters: `interpreters` is a list of nixpkgs attribute names
  # (lib.python.interpreters { presets = [ ./lib/<p>-presets.json ]; }). The
  # module is imported in-process by a product's command line, so the unit
  # tests run on every interpreter a preset names, and the command is built
  # and driven on each as well. Named by lib.checks.mk: <product>-<name>-check.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "backups",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      layouts = ../tests/fixtures/backup-layouts;
      run =
        attr:
        let
          python = pkgs.${attr};
          command = fetch {
            inherit pkgs python;
            label = "stub-nix";
            envPrefix = "STUB";
            layout = layouts + "/timestamp_folders.py";
            legacyEnv.BACKUP_SOURCE = "STUB_BACKUP_SOURCE";
          };
        in
        ''
          echo "== ${attr} (${python.version})"
          ${python.interpreter} -I ${../tests/test_backups.py} ${../python} ${layouts} 2>&1
          bash ${../tests/backups.sh} ${lib.getExe command}
        '';
    in
    checks.script name {
      tools = [
        pkgs.jq
        pkgs.findutils
      ];
    } "{ ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
