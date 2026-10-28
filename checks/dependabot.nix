# This repository's own .github/dependabot.yml, through the same check the
# products run. It has no releases, so its bumps are `chore`, not `fix(deps)`.
{ pkgs, self }:
self.lib.checks.dependabot {
  inherit pkgs;
  src = self;
  product = "python-devops-nix";
  prefix = "chore";
}
