# This repository's own workflows and actions, through the same check the
# products run.
{ pkgs, self }:
self.lib.checks.ciPins {
  inherit pkgs;
  src = self;
  product = "python-devops-nix";
}
