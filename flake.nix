{
  description = "Shared building blocks and conventions for frappe-nix and odoo-nix";

  # nixpkgs and flake-parts only (PLAN.md, design rule 3). Products `follows`
  # both, so adopting this flake adds one node to their lock. Anything heavier
  # is taken as an argument from the product, never as an input here.
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };
  };

  outputs =
    { flake-parts, nixpkgs, ... }@inputs:
    let
      inherit (nixpkgs) lib;

      # Every <dir>/<name>.nix and <dir>/<name>/default.nix, as { <name> = path; }.
      # Each output below is built from one directory, so a later phase adds a
      # piece by adding a file, and never edits this one.
      entries =
        dir:
        lib.optionalAttrs (builtins.pathExists dir) (
          lib.mapAttrs' (name: _: lib.nameValuePair (lib.removeSuffix ".nix" name) (dir + "/${name}")) (
            lib.filterAttrs (
              name: type:
              if type == "directory" then
                builtins.pathExists (dir + "/${name}/default.nix")
              else
                lib.hasSuffix ".nix" name && name != "default.nix"
            ) (builtins.readDir dir)
          )
        );

      # db-nocow -> dbNocow.
      camel =
        name:
        let
          words = lib.splitString "-" name;
        in
        lib.concatStrings ([ (lib.head words) ] ++ map lib.toSentenceCase (lib.tail words));
    in
    flake-parts.lib.mkFlake { inherit inputs; } (
      { self, ... }:
      {
        # The systems both products build for. x86_64-darwin is gone from
        # nixpkgs 26.11 and from both products (CONVENTIONS.md, Systems).
        systems = [
          "x86_64-linux"
          "aarch64-linux"
          "aarch64-darwin"
        ];

        flake = {
          # lib/<name>.nix is `{ lib }: { … }` and becomes lib.<name>: pure
          # functions that take `pkgs` and the product's names as arguments.
          # A kebab-case file name is a camelCase attribute: lib/ci-mode.nix
          # is lib.ciMode.
          lib = lib.mapAttrs' (name: path: lib.nameValuePair (camel name) (import path { inherit lib; })) (
            entries ./lib
          );

          # modules/<name>.nix becomes flakeModules.<name>, a flake-parts module
          # a product imports. None ships yet.
          flakeModules = entries ./modules;
        };

        perSystem =
          { pkgs, ... }:
          {
            # checks/<name>.nix is `{ pkgs, self }: derivation` and becomes
            # checks.<system>.<name>. CI builds every one of them.
            checks = lib.mapAttrs (_: path: import path { inherit pkgs self; }) (entries ./checks);

            # `nix fmt`: nixfmt-rs, as both products use. Files the nix driver
            # passes go straight through; a bare `nix fmt` formats the tree.
            formatter = pkgs.writeShellScriptBin "nixfmt" ''
              [ "$#" -gt 0 ] && exec ${lib.getExe pkgs.nixfmt-rs} "$@"
              exec ${pkgs.findutils}/bin/find . \
                \( -path ./.git -o -path ./.direnv -o -name result -o -name 'result-*' \) -prune -o \
                -name '*.nix' -type f -exec ${lib.getExe pkgs.nixfmt-rs} {} +
            '';

            # What `.envrc` enters: the linters CI runs, and nothing else.
            devShells.default = pkgs.mkShellNoCC {
              packages = [
                pkgs.actionlint
                pkgs.deadnix
                pkgs.nixfmt-rs
                pkgs.prek
                pkgs.statix
              ];
            };
          };
      }
    );
}
