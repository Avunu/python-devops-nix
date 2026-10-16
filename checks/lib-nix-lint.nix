# lib.checks.nixLint against the stub product: it passes on a conforming tree
# with its templates ignored, and fails on a statix.toml that drifted from the
# base's and on a file nixfmt would change.
{ pkgs, self }:
let
  stub = import ../tests/stub-product.nix { inherit pkgs self; };
  lint =
    src: extra:
    self.lib.checks.nixLint (
      {
        inherit pkgs src;
        inherit (stub) product;
        ignore = [ "templates" ];
      }
      // extra
    );
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
checks.script "lib-nix-lint" { } ''
  echo "a conforming product passes: ${lint stub.tree { }}"
  ${stub.expectFailure (lint (stub.variant "statix" "echo 'disabled = []' > statix.toml") { }) [
    "statix.toml: disabled: [] (base: [\"repeated_keys\"])"
  ]}
  ${stub.expectFailure (lint (stub.variant "no-statix" "rm statix.toml") { }) [
    "statix.toml: missing from the product"
  ]}
  echo "compareStatix = false skips it: ${
    lint (stub.variant "statix-off" "echo 'disabled = []' > statix.toml") { compareStatix = false; }
  }"
  ${stub.expectFailure (lint (stub.variant "unformatted" "echo '{x=1;}' > bad.nix") { }) [
    "bad.nix"
  ]}
''
