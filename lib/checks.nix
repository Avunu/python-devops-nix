# Checks a product registers in its own flake.
{ lib }:
{
  # nixfmt (nixfmt-rs), statix and deadnix over every .nix file in `src`.
  #
  # `ignore` lists directories, relative to `src`, that hold files which are not
  # Nix that parses or are not the repository's own: scaffolding templates with
  # @PLACEHOLDERS@, fixtures that stand in for third-party trees. statix reads
  # statix.toml from the root of `src`.
  nixLint =
    {
      pkgs,
      src,
      ignore ? [ ],
      name ? "nix-lint",
    }:
    let
      prune = lib.concatMapStrings (dir: " -o -path ${lib.escapeShellArg "./${dir}"}") ignore;
      statixIgnore = lib.concatMapStrings (dir: " --ignore ${lib.escapeShellArg "${dir}/*"}") ignore;
    in
    pkgs.runCommand name
      {
        nativeBuildInputs = [
          pkgs.deadnix
          pkgs.findutils
          pkgs.nixfmt-rs
          pkgs.statix
        ];
      }
      ''
        cp -r ${src} src
        chmod -R u+w src
        cd src
        find . \( -path ./.git${prune} \) -prune -o -name '*.nix' -type f -print0 > ../nix-files
        xargs -0 --no-run-if-empty ${lib.getExe pkgs.nixfmt-rs} --check < ../nix-files
        statix check --format errfmt${statixIgnore} .
        xargs -0 --no-run-if-empty deadnix --fail < ../nix-files
        touch "$out"
      '';
}
