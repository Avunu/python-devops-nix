# lib.nixos.hardening, in a VM: tests/nixos-hardening.nix.
{ pkgs, self }:
let
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
if pkgs.stdenv.hostPlatform.isLinux then
  checks.nixosTest "lib-nixos-hardening" (import ../tests/nixos-hardening.nix { inherit self pkgs; })
else
  checks.script "lib-nixos-hardening" { } ''echo "NixOS VM tests are Linux only" > "$out"''
