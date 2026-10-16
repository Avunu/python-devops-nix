# This repository's own Nix, through the same check the products run.
{ pkgs, self }:
self.lib.checks.nixLint {
  inherit pkgs;
  src = self;
}
