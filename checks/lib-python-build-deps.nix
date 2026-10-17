# lib.python.buildDeps: python/uv_build_deps.py's unit tests against both
# products' lock shapes, and the command driven from outside, on every
# interpreter the presets name.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  fixtures = ../tests/fixtures/build-deps;

  run =
    attr:
    let
      python = pkgs.${attr};
      command = self.lib.python.buildDeps {
        inherit pkgs python;
        label = "odoo-nix";
        always = lib.genAttrs [ "docopt" "ofxparse" "python-ldap" "rjsmin" "vobject" ] (_: [
          "setuptools"
        ]);
        name = "stub-build-deps";
      };
    in
    ''
      echo "== ${attr} (${python.version})"
      ${python.interpreter} -I ${../tests/test_uv_build_deps.py} ${../python} ${fixtures} 2>&1
      rm -rf work && cp -r ${fixtures}/odoo-shape work && chmod -R u+w work
      (
        cd work
        status=0; ${lib.getExe command} check || status=$?
        [ "$status" = 1 ] || { echo "check on a stale block exited $status, not 1"; exit 1; }
        ${lib.getExe command} update
        diff -u expected.toml pyproject.toml
        ${lib.getExe command} check
      )
    '';
in
checks.script "lib-python-build-deps" { }
  "{ ${lib.concatMapStrings run self.lib.python.defaultInterpreters} } | tee \"$out\""
