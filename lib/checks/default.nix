# Checks a product registers in its own flake, and the helper it registers
# them with. docs/conventions.md, Tests, has the naming scheme `mk` applies.
{ lib }:
let
  # The base's copies of the shared repository files (docs/conventions.md,
  # Repository files), which `sharedConfig` and `nixLint` compare a product's
  # copies with.
  baseFiles = {
    ".editorconfig" = ../../.editorconfig;
    ".pre-commit-config.yaml" = ../../.pre-commit-config.yaml;
    "ruff.toml" = ../../ruff.toml;
    "statix.toml" = ../../statix.toml;
  };

  # Shell that compares `files` under the current directory with the base's
  # copies, by the rule in shared_config.py.
  compareShared =
    pkgs:
    {
      files,
      exceptions ? { },
    }:
    let
      python = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
      unknown = lib.subtractLists (lib.attrNames baseFiles) files;
      base = pkgs.linkFarm "python-devops-nix-shared-config" (lib.getAttrs files baseFiles);
      spec = builtins.toJSON { inherit files exceptions; };
    in
    assert lib.assertMsg (unknown == [ ])
      "lib.checks: the base has no copy of ${lib.concatStringsSep ", " unknown}; it shares ${lib.concatStringsSep ", " (lib.attrNames baseFiles)}";
    ''
      ${lib.getExe python} ${./shared_config.py} ${base} . ${lib.escapeShellArg spec}
    '';

  mk =
    {
      pkgs,
      product,
    }:
    let
      checkName = name: "${product}-${name}-check";
    in
    {
      inherit checkName;

      # A shell script, run with `tools` on PATH. With `src`, it runs in a
      # writable copy of it; otherwise in an empty directory. HOME is a
      # writable directory, and pipefail is on. The check passes when the
      # script exits 0. The script may write a report to $out, a file or a
      # directory; if it leaves none, $out is an empty file. `env` sets
      # environment variables, and `drvArgs` any other derivation attribute
      # (requiredSystemFeatures, passthru, ...).
      script =
        name:
        {
          src ? null,
          tools ? [ ],
          env ? { },
          drvArgs ? { },
        }:
        text:
        pkgs.runCommand (checkName name)
          (
            {
              nativeBuildInputs = tools;
              inherit env;
            }
            // drvArgs
          )
          ''
            set -o pipefail
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            ${lib.optionalString (src != null) ''
              cp -r ${src} src
              chmod -R u+w src
              cd src
            ''}
            ${text}
            [ -e "$out" ] || touch "$out"
          '';

      # A Nix expression, judged at evaluation time and reported at build
      # time, so a failure fails this one check and not the whole flake's
      # evaluation. Either `{ assertion, message }`: passes when `assertion`
      # is true; or `{ expected, actual, message ? "" }`: passes when the two
      # serialize to the same JSON, and prints a diff of them when not.
      eval =
        name: args:
        let
          message = args.message or "";
          compares = args ? expected || args ? actual;
          expected = builtins.toJSON args.expected;
          actual = builtins.toJSON args.actual;
          ok = if compares then expected == actual else args.assertion;
        in
        assert lib.assertMsg (
          compares || (args ? assertion && args ? message)
        ) "lib.checks.mk eval ${name}: pass { assertion, message } or { expected, actual }";
        assert lib.assertMsg (
          compares || builtins.isBool args.assertion
        ) "lib.checks.mk eval ${name}: assertion is not a boolean";
        pkgs.runCommand (checkName name)
          (lib.optionalAttrs (compares && !ok) {
            inherit expected actual;
            passAsFile = [
              "expected"
              "actual"
            ];
            nativeBuildInputs = [
              pkgs.diffutils
              pkgs.jq
            ];
          })
          (
            if ok then
              ''touch "$out"''
            else
              ''
                {
                  printf '%s\n' ${lib.escapeShellArg "eval check ${checkName name} failed${lib.optionalString (message != "") ": ${message}"}"}
                  ${lib.optionalString compares ''
                    diff -u --label expected --label actual \
                      <(jq -S . "$expectedPath") <(jq -S . "$actualPath") || true
                  ''}
                } >&2
                exit 1
              ''
          );

      # A NixOS VM test (pkgs.testers.runNixOSTest). The helper names it
      # `<product>-<name>`, so the test module sets no `name` of its own.
      nixosTest =
        name: test:
        pkgs.testers.runNixOSTest {
          imports = [ test ];
          name = "${product}-${name}";
        };
    };
in
{
  inherit mk;

  # nixfmt (nixfmt-rs), statix and deadnix over every .nix file in `src`, and
  # statix.toml at its root compared with the base's copy (`sharedConfig`'s
  # rule), unless `compareStatix` is false.
  #
  # `ignore` lists directories, relative to `src`, that hold files which are not
  # Nix that parses or are not the repository's own: scaffolding templates with
  # @PLACEHOLDERS@, fixtures that stand in for third-party trees. statix reads
  # statix.toml from the root of `src`.
  nixLint =
    {
      pkgs,
      src,
      product,
      ignore ? [ ],
      compareStatix ? true,
    }:
    let
      prune = lib.concatMapStrings (dir: " -o -path ${lib.escapeShellArg "./${dir}"}") ignore;
      statixIgnore = lib.concatMapStrings (dir: " --ignore ${lib.escapeShellArg "${dir}/*"}") ignore;
    in
    (mk { inherit pkgs product; }).script "nix-lint"
      {
        inherit src;
        tools = [
          pkgs.deadnix
          pkgs.findutils
          pkgs.nixfmt-rs
          pkgs.statix
        ];
      }
      ''
        find . \( -path ./.git${prune} \) -prune -o -name '*.nix' -type f -print0 > ../nix-files
        xargs -0 --no-run-if-empty ${lib.getExe pkgs.nixfmt-rs} --check < ../nix-files
        statix check --format errfmt${statixIgnore} .
        xargs -0 --no-run-if-empty deadnix --fail < ../nix-files
        ${lib.optionalString compareStatix (compareShared pkgs { files = [ "statix.toml" ]; })}
      '';

  # The product's copies of the shared repository files, compared with the
  # base's: each must make every setting the base's copy makes, with the same
  # value, and may add its own (docs/conventions.md, Repository files).
  # `exceptions` maps a file to the key paths of base settings the product
  # sets differently, each one documented in the product, e.g.
  # { ".editorconfig" = [ [ "*.py" "indent_style" ] ]; }. statix.toml is
  # compared by `nixLint`.
  sharedConfig =
    {
      pkgs,
      src,
      product,
      files ? [
        ".editorconfig"
        ".pre-commit-config.yaml"
        "ruff.toml"
      ],
      exceptions ? { },
    }:
    (mk { inherit pkgs product; }).script "shared-config" { inherit src; } (
      compareShared pkgs { inherit files exceptions; }
    );
}
