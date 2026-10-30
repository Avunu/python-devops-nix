# lib.node: detection, discovery and the lock readers at evaluation time, an
# offline node_modules and build per manager (npm, yarn 1, pnpm, a pnpm 12
# workspace), the yarn.lock reader, the command, and python/node.py's unit
# tests on every interpreter the presets name. pnpm2nix is a product's input;
# here it is pinned by hash, so the base's lock stays nixpkgs and flake-parts.
{ pkgs, self }:
self.lib.node.check {
  inherit pkgs;
  name = "lib-node";
  pnpm2nix = builtins.fetchTree {
    type = "github";
    owner = "FliegendeWurst";
    repo = "pnpm2nix-nzbr";
    rev = "9b15300b711a50bb96e553b66aad5cad94df9c7e";
    narHash = "sha256-NPXKyp40QNCk9r6kavSwqi/zv7OcLgrSRtPmHW+e74w=";
  };
}
