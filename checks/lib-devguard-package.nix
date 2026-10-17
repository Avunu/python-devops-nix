# lib.devguard.mkPackage, lib.python.mkGraft and lib.python.withGrafts: what
# the merged package holds, the baked settings file, the names products rely
# on, and each refusal: a file in both trees, a tree carrying a generated file,
# a name that is not a package name, a module grafted twice.
{ pkgs, self }:
let
  inherit (self.lib) devguard python;
  stub = import ../tests/stub-product.nix { inherit pkgs self; };
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  fixture = ../tests/fixtures/devguard/stub_devguard;

  # The stub product's tree, with `script` run in a writable copy of it.
  variant =
    name: script:
    pkgs.runCommand "stub-devguard-${name}" { } ''
      cp -r ${fixture} "$out"
      chmod -R u+w "$out"
      cd "$out"
      ${script}
    '';

  package = devguard.stub pkgs;

  mkPackage =
    name: src:
    devguard.mkPackage {
      inherit pkgs name src;
    };

  fails = value: !(builtins.tryEval (builtins.seq value.drvPath true)).success;

  env = pkgs.stdenvNoCC.mkDerivation {
    name = "stub-venv";
    dontUnpack = true;
    installPhase = "mkdir -p $out";
  };

  names = checks.eval "devguard-names" {
    expected = {
      module = "stub_devguard";
      pth = "zzz-stub-devguard.pth";
      settings.guards.mail.port = 2525;
      badName = true;
      graftedTwice = true;
      noGrafts = true;
    };
    actual = {
      inherit (package) module;
      pth = python.pthName package.module;
      settings.guards.mail = { inherit (package.settings.guards.mail) port; };
      badName = fails (mkPackage "stub-devguard" fixture);
      graftedTwice = fails (
        python.withGrafts {
          inherit env;
          python = pkgs.python3;
          grafts = [
            package
            package
          ];
        }
      );
      noGrafts =
        (python.withGrafts {
          inherit env;
          python = pkgs.python3;
          grafts = [ ];
        }) == env;
    };
  };
in
checks.script "lib-devguard-package" { tools = [ pkgs.jq ]; } ''
  echo "names: ${names}"

  # The merged package: every core file, every product file, the baked
  # settings, and nothing else.
  (cd ${package}/stub_devguard && find . -type f | sort) > files
  diff -u - files <<'EOF'
  ./__init__.py
  ./_baked.json
  ./_hook.py
  ./_patch.py
  ./_product.py
  ./_settings.py
  ./guards/__init__.py
  ./guards/egress.py
  ./guards/jobs.py
  ./guards/mail_stdlib.py
  EOF
  jq -e '.guards.mail.port == 2525 and .guards.jobs.extra_blocked == ["stub.sync.push"]' \
    ${package}/stub_devguard/_baked.json

  # __pycache__ in a product's checkout is left out, not merged.
  pkg=${mkPackage "stub_devguard" (variant "pycache" "mkdir __pycache__ guards/__pycache__; touch __pycache__/_hook.cpython-314.pyc guards/__pycache__/egress.cpython-314.pyc")}
  test -f "$pkg/stub_devguard/_product.py"
  test ! -e "$pkg/stub_devguard/__pycache__"
  test ! -e "$pkg/stub_devguard/guards/__pycache__"

  # A graft: the product's package and the shared _hook.py.
  graft=${
    python.mkGraft {
      inherit pkgs;
      name = "stub_other";
      src = ../tests/fixtures/devguard/stub_other;
    }
  }
  cmp "$graft/stub_other/_hook.py" ${python.hook}
  test -f "$graft/stub_other/__init__.py"

  # A product file that is also the core's, at the top or nested.
  ${stub.expectFailure (mkPackage "stub_devguard" (variant "settings" "touch _settings.py")) [
    "lib.devguard.mkPackage stub_devguard: these files are in more than one tree, and one would overwrite the other:"
    "  _settings.py, in the devguard core (python-devops-nix, python/devguard) and in the product's tree"
  ]}
  ${stub.expectFailure
    (mkPackage "stub_devguard" (variant "egress" "cp guards/jobs.py guards/egress.py; touch _hook.py"))
    [
      "  _hook.py, in the devguard core"
      "  guards/egress.py, in the devguard core"
    ]
  }
  # A product tree carrying the generated file.
  ${stub.expectFailure (mkPackage "stub_devguard" (variant "baked" "echo '{}' > _baked.json")) [
    "lib.devguard.mkPackage stub_devguard: _baked.json is written by lib.devguard.mkPackage; remove it from"
    "  the product's tree"
  ]}
  # A graft with a _hook.py of its own.
  ${stub.expectFailure (python.mkGraft {
    inherit pkgs;
    name = "stub_other";
    src = pkgs.runCommand "stub-other-hook" { } ''
      mkdir "$out"
      touch "$out/__init__.py" "$out/_hook.py"
    '';
  }) [ "lib.python.mkGraft stub_other: _hook.py is written by lib.python.mkGraft; remove it from" ]}
''
