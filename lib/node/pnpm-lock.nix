# pnpm-lock.yaml: what evaluation reads of it, and the install plan
# pnpm2nix-nzbr makes from it.
#
# The plan is pnpm2nix's (github:FliegendeWurst/pnpm2nix-nzbr, lockfile.nix,
# which the product passes in as a `flake = false` input, as it does uv2nix):
# one fetchurl per package from the lock's own `resolution.integrity`, git and
# codeload dependencies fetched by commit, the lock rewritten so each package
# resolves to its store path. Nothing to hash by hand, as with yarn.lock. Its
# reader is import-from-derivation (remarshal's yaml2json, in a derivation
# whose output is read back), so only a pnpm target's install pays for it;
# detection and workspace membership read the lock here, line by line.
#
# pnpm 11 and later write two YAML documents into one file when package.json
# pins pnpm through devEngines: an "env" document first (the pinned pnpm and
# configDependencies, under an `importers: .:` of its own), then the project's.
# yaml2json refuses a file with two documents, and the env document only says
# which pnpm to switch to, which the build does not (pnpm_config_pm_on_fail is
# `ignore`; the nixpkgs pnpm of the same major runs). So the plan is made from
# the last document alone, split off here without a derivation.
{ lib, label }:

rec {
  # The lock's lines, and the project document's: after the last `---` line,
  # or the whole file when there is none.
  lines = path: lib.splitString "\n" (builtins.readFile path);
  projectLines =
    path:
    let
      all = lines path;
      marks = lib.filter (i: builtins.elemAt all i == "---") (lib.range 0 (builtins.length all - 1));
    in
    if marks == [ ] then all else lib.drop (lib.last marks + 1) all;

  # The project document as a file of its own (builtins.toFile: no derivation,
  # so it is the same store path for the same text).
  projectDocument =
    path: builtins.toFile "pnpm-lock.yaml" (lib.concatStringsSep "\n" (projectLines path));

  # `lockfileVersion: '9.0'` → "9.0". pnpm 9 through 12 write 9.0.
  version =
    path:
    let
      m = lib.findFirst (x: x != null) null (
        map (builtins.match "lockfileVersion: '?([0-9.]+)'?[ \t]*") (projectLines path)
      );
    in
    if m == null then null else builtins.head m;

  assertVersion =
    path: x:
    let
      v = version path;
    in
    if v == null || lib.versions.major v != "9" then
      throw "${label}: ${toString path} is a pnpm lockfile version ${
        if v == null then "this cannot read" else v
      }; only version 9 (pnpm 9 and later) installs offline here. Regenerate it with a current pnpm: pnpm install --lockfile-only"
    else
      x;

  # The keys under `importers:` in the project document, unquoted: "." for the
  # root and each workspace member's path relative to it ("frontend",
  # "packages/a"). A two-space-indented `key:` line under an unindented
  # `importers:` is an importer; everything deeper is its dependency block.
  importers =
    path:
    let
      step =
        acc: line:
        if acc.done then
          acc
        else if line == "importers:" then
          acc // { inside = true; }
        else if !acc.inside then
          acc
        else if builtins.match "[^ \t].*" line != null then
          acc // { done = true; }
        else
          let
            m = builtins.match "  ([^ \t].*):[ \t]*" line;
          in
          if m == null then
            acc
          else
            acc
            // {
              keys = acc.keys ++ [ (lib.removeSuffix "'" (lib.removePrefix "'" (builtins.head m))) ];
            };
    in
    (builtins.foldl' step {
      inside = false;
      done = false;
      keys = [ ];
    } (projectLines path)).keys;

  # The members: every importer but the root.
  members = path: lib.filter (k: k != ".") (importers path);

  # The registry a target's .npmrc names, if any: `registry=<url>`.
  registryOf =
    src:
    let
      npmrc = src + "/.npmrc";
      found =
        if builtins.pathExists npmrc then
          lib.findFirst (x: x != null) null (
            map (builtins.match "[ \t]*registry[ \t]*=[ \t]*([^ \t]+)[ \t]*") (lines npmrc)
          )
        else
          null;
    in
    if found == null then "https://registry.npmjs.org" else lib.removeSuffix "/" (builtins.head found);

  # pnpm's names for the host, as pnpm2nix's derivation.nix derives them: the
  # lock's platform-specific optional packages for any other host are left out
  # of the plan (and out of the rewritten lock, so pnpm does not look for them).
  hostOs =
    platform:
    if platform.isLinux then
      "linux"
    else if platform.isDarwin then
      "darwin"
    else if platform.isFreeBSD then
      "freebsd"
    else
      null;
  hostCpu =
    platform:
    if platform.isx86_64 then
      "x64"
    else if platform.isAarch64 then
      "arm64"
    else
      null;

  # { patchedLockfile; dependencyTarballs; } for a lock: pnpm2nix's
  # processLockfile over the project document. devDependencies are always
  # kept: a build needs them, and lock version 9 marks none as dev anyway.
  plan =
    {
      pkgs,
      pnpm2nix,
      lockFile,
      registry ? "https://registry.npmjs.org",
    }:
    if pnpm2nix == null then
      throw "${label}: ${toString lockFile} is a pnpm lock, and pnpm targets need the pnpm2nix input (github:FliegendeWurst/pnpm2nix-nzbr, flake = false) passed to lib.node"
    else
      assertVersion lockFile (
        (pkgs.callPackage "${pnpm2nix}/lockfile.nix" { }).processLockfile {
          inherit registry;
          lockfile = projectDocument lockFile;
          noDevDependencies = false;
          targetOs = hostOs pkgs.stdenv.hostPlatform;
          targetCpu = hostCpu pkgs.stdenv.hostPlatform;
        }
      );
}
