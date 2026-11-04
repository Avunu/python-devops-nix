# standards-core (standards/, standards/DESIGN.md §10.2): the engine products
# build their standards tools on, as a Python package, and its own test suite.
#
#   package { pkgs, python ? pkgs.python3, rev ? null }
#       buildPythonPackage over standards/ (minus its tests). With `rev`, the
#       commit it was built from is written to standards_core/_source.json,
#       which standards_core.revision() reads for the version-skew row; a
#       product passes rev = inputs.python-devops-nix.rev.
#   conformance { pkgs, product, package, module }
#       a check (named after `product`) that runs
#       `python -m standards_core.testing.conformance <module>` with the
#       product's Python `package` installed: module is the product object,
#       "kit_nix_tools.product:PRODUCT". Any problem fails it.
#   pins { pkgs, src, product, owner, input ? "python-devops-nix", pyprojects ? [ ] }
#       the product's `standards-core-pins` check: `src`'s flake.lock locks the root
#       input `input` at the commit its tools' pyproject.toml names in
#       `standards-core @ git+https://github.com/<owner>/<input>@<sha>#subdirectory=standards`
#       (`py/**/pyproject.toml`, or the `pyprojects` globs) and that every
#       `<owner>/<input>/...@<sha>` in its .github/** uses
#       (python -m standards_core.testing.pins). Reachability needs the network, so the
#       product's CI runs the module with --reachable instead.
#   check { pkgs, interpreters ? <the presets' interpreters> }
#       pytest standards/tests on every interpreter, with git on PATH and no
#       global git configuration.
{ lib }:
let
  pythonLib = import ./python { inherit lib; };
  root = ../standards;
  testsRoot = root + "/tests";

  bytecode = dir: lib.fileset.fileFilter (f: f.hasExt "pyc") dir;

  # The distribution: standards/ without its tests or byte-compiled files.
  source = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.difference root (
      lib.fileset.unions [
        testsRoot
        (bytecode root)
      ]
    );
  };

  # The suite, with its two stub products, which it imports from this tree.
  tests = lib.fileset.toSource {
    root = testsRoot;
    fileset = lib.fileset.difference testsRoot (bytecode testsRoot);
  };

  inherit ((lib.importTOML (root + "/pyproject.toml")).project) version;
in
rec {
  package =
    {
      pkgs,
      python ? pkgs.python3,
      rev ? null,
    }:
    python.pkgs.buildPythonPackage {
      pname = "standards-core";
      inherit version;
      pyproject = true;
      src = source;
      build-system = [ python.pkgs.flit-core ];
      dependencies = with python.pkgs; [
        jinja2
        tomlkit
        packaging
        pyyaml
      ];
      pythonImportsCheck = [ "standards_core" ];
      postInstall = lib.optionalString (rev != null) ''
        printf '%s\n' ${lib.escapeShellArg (builtins.toJSON { inherit rev; })} \
          > "$out/${python.sitePackages}/standards_core/_source.json"
      '';
    };

  conformance =
    {
      pkgs,
      product,
      package,
      module,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      env = package.pythonModule.withPackages (_: [ package ]);
    in
    checks.script "standards-conformance"
      {
        env.PYTHONDONTWRITEBYTECODE = "1";
      }
      "${env}/bin/python -m standards_core.testing.conformance ${lib.escapeShellArg module} | tee \"$out\"";

  pins =
    {
      pkgs,
      src,
      product,
      owner,
      input ? "python-devops-nix",
      pyprojects ? [ ],
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      python = pkgs.python3;
      env = python.withPackages (_: [ (package { inherit pkgs python; }) ]);
      args = lib.escapeShellArgs (
        [
          "--root"
          "${src}"
          "--owner"
          owner
          "--input"
          input
        ]
        ++ lib.concatMap (glob: [
          "--pyproject"
          glob
        ]) pyprojects
      );
    in
    checks.script "standards-core-pins" {
      env.PYTHONDONTWRITEBYTECODE = "1";
    } "${env}/bin/python -m standards_core.testing.pins ${args} | tee \"$out\"";

  check =
    {
      pkgs,
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk {
        inherit pkgs;
        product = "python-devops-nix";
      };
      run =
        attr:
        let
          python = pkgs.${attr};
          env = python.withPackages (ps: [
            (package { inherit pkgs python; })
            ps.pytest
          ]);
        in
        ''
          echo "== ${attr} (${python.version})"
          ${env}/bin/python -m pytest -q -p no:cacheprovider .
        '';
    in
    checks.script "lib-standards" {
      src = tests;
      tools = [ pkgs.git ];
      env = {
        GIT_CONFIG_GLOBAL = "/dev/null";
        PYTHONDONTWRITEBYTECODE = "1";
      };
    } "{ set -e; ${lib.concatMapStrings run interpreters} } 2>&1 | tee \"$out\"";
}
