# This repository's own check names, through the same check the products run.
{ pkgs, self }:
self.lib.checks.checkNames {
  inherit pkgs;
  product = "python-devops-nix";
  checks = self.checks.${pkgs.stdenv.hostPlatform.system};
}
