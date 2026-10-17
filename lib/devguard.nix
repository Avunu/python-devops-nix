# The devguard core (PLAN.md, Phase 3): the post-import hook, settings
# resolution, the patch helpers, and the two transport-level guards, egress and
# mail_stdlib. The Python is python/devguard/; a product supplies the rest of
# its package (_product.py and the guards that patch its framework), and
# `mkPackage` merges the two. CONVENTIONS.md, devguard, has the contract.
{ lib }:

let
  pythonLib = import ./python.nix { inherit lib; };
in
rec {
  # The core's tree, package-relative: __init__.py, _hook.py, _settings.py,
  # _patch.py, guards/__init__.py, guards/egress.py, guards/mail_stdlib.py.
  core = ../python/devguard;

  # The product's devguard package, $out/<name>/: the core's tree and the
  # product's merged, with `settings` baked in as _baked.json. Graft it into
  # the development virtualenv with lib.python.withGrafts.
  #
  #   name      the package name, which is also its log prefix
  #             (`<name>[egress] ...`) and, through withGrafts, its .pth file
  #             (zzz-<name, _ as ->.pth): frappe_devguard, odoo_devguard
  #   src       the product's own tree, package-relative: _product.py and
  #             guards/<guard>.py for each guard of its own. A file the core
  #             also has fails the build, naming it; so does a _baked.json.
  #   settings  the baked layer: { enabled, guards.<guard>.<key> }, below the
  #             <P>_DEVGUARD_* environment and the runtime file, above the
  #             defaults (python/devguard/_settings.py)
  #
  # DEVELOPMENT ONLY. It stops a development copy reaching production, so
  # being absent from every production environment is the property that
  # matters: graft it into the development virtualenv, never the production
  # one, and check that it is absent there (PLAN.md, design rule 7).
  mkPackage =
    {
      pkgs,
      name,
      src,
      settings ? { },
    }:
    pythonLib.mergeTrees {
      inherit pkgs name;
      what = "lib.devguard.mkPackage";
      trees = [
        {
          label = "the devguard core (python-devops-nix, python/devguard)";
          path = core;
        }
        {
          label = "the product's tree (${toString src})";
          path = src;
        }
      ];
      files."_baked.json" = pkgs.writeText "${name}-baked.json" (builtins.toJSON settings);
      passthru = { inherit settings; };
    };

  # The stub product the base's tests run against (PLAN.md, design rule 8),
  # with the settings `check` expects baked in.
  stub =
    pkgs:
    mkPackage {
      inherit pkgs;
      name = "stub_devguard";
      src = ../tests/fixtures/devguard/stub_devguard;
      settings.guards = {
        mail.port = 2525;
        egress.allow_hosts = [ "baked.allowed.test" ];
        jobs.extra_blocked = [ "stub.sync.push" ];
      };
    };

  # The core's tests, as a check a product can register with its presets'
  # interpreters: `interpreters` is a list of nixpkgs attribute names
  # (lib.python.interpreters { presets = [ ./lib/<p>-presets.json ]; }). The
  # core loads into the application's interpreter, so it is tested on every
  # one a preset names: tests/test_devguard.py against the stub product, then
  # the stub grafted into a virtualenv beside a second graft
  # (tests/devguard_graft.py). Named by lib.checks.mk: <product>-<name>-check.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "devguard-core",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      package = stub pkgs;
      other = pythonLib.mkGraft {
        inherit pkgs;
        name = "stub_other";
        src = ../tests/fixtures/devguard/stub_other;
      };
      tree = pkgs.symlinkJoin {
        name = "stub-devguard-tree";
        paths = [
          package
          other
        ];
      };
      run =
        attr:
        let
          python = pkgs.${attr};
          # What mkVirtualEnv leaves for withGrafts: a site-packages directory
          # created by the install phase.
          env = pkgs.stdenvNoCC.mkDerivation {
            name = "stub-venv-${attr}";
            dontUnpack = true;
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              mkdir -p "$out/${python.sitePackages}"
              runHook postInstall
            '';
          };
          grafted = pythonLib.withGrafts {
            inherit python env;
            grafts = [
              package
              other
            ];
          };
        in
        ''
          echo "== ${attr} (${python.version})"
          ${python.interpreter} -I ${../tests/test_devguard.py} ${tree} 2>&1
          ${python.interpreter} -I ${../tests/devguard_graft.py} ${grafted}/${python.sitePackages} 2>&1
        '';
    in
    checks.script name { } "{ ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
