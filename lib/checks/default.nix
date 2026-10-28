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

  # Python with PyYAML, for the scripts that read the repository's files.
  python = pkgs: pkgs.python3.withPackages (ps: [ ps.pyyaml ]);

  # Shell that compares `files` under the current directory with the base's
  # copies, by the rule in shared_config.py.
  compareShared =
    pkgs:
    {
      files,
      exceptions ? { },
    }:
    let
      unknown = lib.subtractLists (lib.attrNames baseFiles) files;
      base = pkgs.linkFarm "python-devops-nix-shared-config" (lib.getAttrs files baseFiles);
      spec = builtins.toJSON { inherit files exceptions; };
    in
    assert lib.assertMsg (unknown == [ ])
      "lib.checks: the base has no copy of ${lib.concatStringsSep ", " unknown}; it shares ${lib.concatStringsSep ", " (lib.attrNames baseFiles)}";
    ''
      ${lib.getExe (python pkgs)} ${./shared_config.py} ${base} . ${lib.escapeShellArg spec}
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

  # The checks of the conventions over a product's repository: its .github and
  # its check names. Each is exported on its own, and `conventions` registers
  # them all.
  repository = {
    # docs/conventions.md, CI: every `uses:` in .github/workflows and
    # .github/actions is pinned by a full commit SHA, or a `docker://` image by
    # its sha256 digest, with a trailing comment. A local `./` path is exempt.
    ciPins =
      {
        pkgs,
        src,
        product,
      }:
      (mk { inherit pkgs product; }).script "ci-pins" { } ''
        ${lib.getExe (python pkgs)} ${./repository.py} pins ${src}
      '';

    # docs/conventions.md, Dependabot: every ecosystem in .github/dependabot.yml
    # has the commit prefix `prefix`, `cooldown.default-days: 7` and a group of
    # every dependency (`patterns: ["*"]`). `development` lists the entries for
    # development-only tooling, as { ecosystem, directory ? "/" }, which take
    # `developmentPrefix` instead. An entry listed there that the file does not
    # have fails the check.
    dependabot =
      {
        pkgs,
        src,
        product,
        prefix ? "fix(deps)",
        developmentPrefix ? "chore(deps)",
        development ? [ ],
      }:
      let
        spec = builtins.toJSON {
          inherit prefix developmentPrefix;
          development = map (d: { directory = "/"; } // d) development;
        };
      in
      (mk { inherit pkgs product; }).script "dependabot" { } ''
        ${lib.getExe (python pkgs)} ${./repository.py} dependabot ${src} ${lib.escapeShellArg spec}
      '';

    # docs/conventions.md, Tests: each of `checks`' attribute names is kebab-case
    # and has no kind prefix. Only the names are read, so a product passes its
    # own checks.<system>, this check among them. `exceptions` lists names that
    # may break the rule; one that no longer needs to fails the check.
    checkNames =
      {
        pkgs,
        product,
        checks,
        exceptions ? [ ],
      }:
      let
        names = lib.attrNames checks;
        kinds = [
          "eval-"
          "module-"
          "vm-"
        ];
        problem =
          name:
          let
            kind = lib.findFirst (k: lib.hasPrefix k name) null kinds;
          in
          if builtins.match "[a-z0-9]+(-[a-z0-9]+)*" name == null then
            "not kebab-case"
          else if kind != null then
            "starts with the kind prefix ${kind}"
          else
            null;
        broken = lib.filter (name: !(lib.elem name exceptions) && problem name != null) names;
        stale = lib.filter (name: !(lib.elem name names) || problem name == null) exceptions;
        lines =
          map (name: "${name}: ${problem name}") broken
          ++ map (name: "${name}: listed as an exception, but it is not a check that breaks the rule") stale;
      in
      (mk { inherit pkgs product; }).eval "check-names" {
        assertion = lines == [ ];
        message =
          lib.concatStringsSep "; " lines
          + " (docs/conventions.md, Tests: <x> or <x>-<major>, kebab-case, no eval-, module- or vm- prefix)";
      };
  };

  # Every conformance check a product registers, by check name, from one set of
  # arguments. A later family adds its checks here. The names depend on which
  # arguments are given and never on their values, so `checks` can be the set
  # this one is merged into.
  conventions =
    {
      pkgs,
      src,
      product,
      checks,
      checkNames ? { },
      dependabot ? { },
    }:
    let
      tree = { inherit pkgs src product; };
    in
    {
      ci-pins = repository.ciPins tree;
      dependabot = repository.dependabot (tree // dependabot);
      check-names = repository.checkNames ({ inherit pkgs product checks; } // checkNames);
    };
in
{
  inherit mk conventions;
  inherit (repository) ciPins dependabot checkNames;

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
