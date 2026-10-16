# This repository's own copies of the shared files, through the same check the
# products run. It compares them with themselves, so what it catches here is a
# copy the check can no longer parse.
{ pkgs, self }:
self.lib.checks.sharedConfig {
  inherit pkgs;
  src = self;
  product = "python-devops-nix";
}
