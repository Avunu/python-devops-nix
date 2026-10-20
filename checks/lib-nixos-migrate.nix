# lib.nixos.migrate, unixSocket, nginx and hardening together, in a VM:
# tests/nixos-migrate.nix.
{ pkgs, self }:
let
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
if pkgs.stdenv.hostPlatform.isLinux then
  checks.nixosTest "lib-nixos-migrate" (import ../tests/nixos-migrate.nix { inherit self pkgs; })
else
  checks.script "lib-nixos-migrate" { } ''echo "NixOS VM tests are Linux only" > "$out"''
