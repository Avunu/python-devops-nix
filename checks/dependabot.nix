# This repository's own .github/dependabot.yml, through the same check the
# products run. The action pins reach the products through the reusable
# workflow and the composite actions, so they are `fix(deps)`; the flake inputs
# and the prek hook revs reach no product, so they are development tooling.
{ pkgs, self }:
self.lib.checks.dependabot {
  inherit pkgs;
  src = self;
  product = "python-devops-nix";
  development = [
    { ecosystem = "nix"; }
    { ecosystem = "pre-commit"; }
  ];
}
