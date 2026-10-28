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

  # The checks of the conventions over a product's modules. They read the
  # option sets the modules evaluate to, as `options`: `shell`, the flake-parts
  # perSystem options, which hold `<product>.*`, and `nixos`, the NixOS
  # options, which hold `services.<product>.*`. Nothing is built.
  modules =
    let
      roots = product: {
        shell = [ product ];
        nixos = [
          "services"
          product
        ];
      };

      # The option, or tree of options, at `path` under `node`, through
      # submodules; null if there is none. A path writes the names of an
      # attribute set and the items of a list as the manual does: `<name>` (or
      # the type's own placeholder) and `*`. The walk reads them from the
      # sub-options' `loc`, so it skips none and accepts no other.
      find =
        node: path:
        if path == [ ] then
          node
        else if lib.isOption node then
          let
            sub = node.type.getSubOptions node.loc;
            # Each submodule declares `_module.args`, whose loc is the
            # submodule's own followed by "_module" "args".
            within =
              if sub ? _module.args then
                lib.sublist (lib.length node.loc) (
                  lib.length sub._module.args.loc - 2 - lib.length node.loc
                ) sub._module.args.loc
              else
                [ ];
          in
          if lib.take (lib.length within) path == within then
            find sub (lib.drop (lib.length within) path)
          else
            null
        else if builtins.isAttrs node && node ? ${lib.head path} then
          find node.${lib.head path} (lib.tail path)
        else
          null;

      # Whether the manual shows the option. A removed option is hidden, and so
      # is an alias made by mkRenamedOptionModule.
      shown = opt: (opt.visible or true) != false && !(opt.internal or false);
      # The target of an alias, hidden or not (mkAliasOptionModule's is
      # visible), or null: lib.doRename describes each as "Alias of" it.
      aliasOf =
        opt:
        let
          target =
            if builtins.isString (opt.description or null) then
              builtins.match "Alias of \\{option}`(.*)`\\." opt.description
            else
              null;
        in
        if target == null then null else lib.head target;

      # "missing" or why the option at `path` is not the convention's, or null.
      declared =
        options: path:
        let
          opt = find options path;
        in
        if opt == null || !lib.isOption opt then
          "missing"
        else if aliasOf opt != null then
          "only an alias of ${aliasOf opt}"
        else if !shown opt then
          "hidden (a removed option, or visible = false or internal)"
        else
          null;

      # The lines of an eval check, from a list of { <option path> = { problem,
      # section }; } less `exceptions`, which fail once they are no longer
      # needed. A path two sections name is one line that names both.
      report =
        problems: exceptions:
        let
          found = lib.zipAttrsWith (_: ps: {
            inherit (lib.head ps) problem;
            section = lib.concatStringsSep ", " (lib.unique (map (p: p.section) ps));
          }) problems;
          broken = lib.filterAttrs (path: _: !(lib.elem path exceptions)) found;
          stale = lib.filter (path: !(found ? ${path})) exceptions;
        in
        lib.mapAttrsToList (path: p: "${path}: ${p.problem} (${p.section})") broken
        ++ map (path: "${path}: listed as an exception, but nothing is wrong with it") stale;
    in
    {
      # docs/conventions.md, Restore, devguard, The development shell option
      # tree, NixOS module path and Identity options: each option they name is
      # declared and visible (an alias or a removed option does not count), the
      # `ports` options are not read-only, and nothing visible is left in the
      # shell's `dev` tree. `tenant` names the default tenant's option, which
      # keeps the framework's word, in each: { shell = "defaultSite"; nixos =
      # "sites"; }. `exceptions` lists option paths, as the message shows them,
      # that may break the rule; one that no longer does fails the check.
      optionPaths =
        {
          pkgs,
          product,
          options,
          tenant,
          exceptions ? [ ],
        }:
        let
          sharedGuards = [
            "egress"
            "mail"
            "jobs"
            "backups"
            "objectstore"
            "webhooks"
          ];
          tree = "The development shell option tree";
          required = [
            {
              section = "Migrate after restore";
              paths = [ "restore.migrate" ];
            }
            {
              section = "Neutralise";
              paths = [ "restore.neutralize" ];
            }
            {
              section = "Files";
              paths = [ "restore.files" ];
            }
            {
              section = "Unguarded restore";
              paths = [ "restore.requireDevguard" ];
            }
            {
              section = "SMTP port";
              paths = [ "mailpit.port" ];
            }
            {
              section = "Job denylist";
              paths = [
                "devguard.jobs.enable"
                "devguard.jobs.extraBlocked"
              ];
            }
            {
              section = "Shared guard names";
              paths = map (guard: "devguard.${guard}.enable") sharedGuards;
            }
            {
              section = tree;
              paths = [
                "watch.python"
                "devguard.enable"
                "devguard.egress.allowHosts"
                "devguard.mail.sender"
                "devguard.mail.unmute"
                "mailpit.enable"
                "mailpit.host"
                "mailpit.httpPort"
                "mailpit.pop3.enable"
                "mailpit.pop3.port"
                "mailpit.pop3.user"
                "mailpit.pop3.password"
              ];
            }
            {
              section = tree;
              writable = true;
              paths = map (port: "ports.${port}") [
                "base"
                "offset"
                "worktreeSalt"
                "http"
                "db"
                "smtp"
                "mailUi"
              ];
            }
            {
              section = "Identity options";
              paths = [
                "name"
                tenant.shell
              ];
            }
            {
              section = "Identity options";
              module = "nixos";
              paths = [ tenant.nixos ];
            }
            {
              section = "NixOS module path";
              module = "nixos";
              paths = [
                "enable"
                "package"
              ];
            }
          ];

          # { <option path> = { problem, section }; } for each required option
          # that is not as the conventions have it.
          problems = lib.concatMap (
            entry:
            let
              module = entry.module or "shell";
            in
            lib.concatMap (
              relative:
              let
                path = (roots product).${module} ++ lib.splitString "." relative;
                missing = declared options.${module} path;
                problem =
                  if missing != null then
                    missing
                  else if entry.writable or false && (find options.${module} path).readOnly or false then
                    "read-only, so lib.mkForce cannot set it"
                  else
                    null;
              in
              lib.optional (problem != null) {
                ${lib.showOption path} = {
                  inherit problem;
                  inherit (entry) section;
                };
              }
            ) entry.paths
          ) required;

          # Whatever is still visible in the removed `dev` tree.
          dev =
            let
              node = find options.shell ((roots product).shell ++ [ "dev" ]);
            in
            if node == null then
              [ ]
            else
              lib.filter shown (if lib.isOption node then [ node ] else lib.collect lib.isOption node);
          lines = report (
            problems
            ++ map (opt: {
              ${lib.showOption opt.loc} = {
                problem = "declared in the dev tree, which the conventions removed";
                section = tree;
              };
            }) dev
          ) exceptions;
        in
        assert lib.assertMsg (options ? shell && options ? nixos)
          "lib.checks.optionPaths: pass options = { shell = <perSystem options>; nixos = <NixOS options>; }";
        (mk { inherit pkgs product; }).eval "option-paths" {
          assertion = lines == [ ];
          message = lib.concatStringsSep "; " lines + " (docs/conventions.md)";
        };

      # docs/conventions.md, Framework config blocks: each option `settings`
      # lists is named `settings` and is a submodule whose freeformType is
      # `pkgs.formats.<format> args`'s type. Each entry is { module, path,
      # format, args ? { } }: `module` is "shell" or "nixos", and `path` is
      # under the product's root in it, e.g. { module = "nixos"; path = [
      # "sites" "<name>" "settings" ]; format = "json"; }. Types cannot be
      # compared, so the freeformType is compared with the format's type by
      # name and description, and `args` are the ones the description shows
      # (ini's `listToValue`, say).
      configBlocks =
        {
          pkgs,
          product,
          options,
          settings,
        }:
        let
          problem =
            block:
            let
              path = (roots product).${block.module} ++ block.path;
              opt = find options.${block.module} path;
              missing = declared options.${block.module} path;
              want = (pkgs.formats.${block.format} (block.args or { })).type;
              freeform = (opt.type.getSubOptions opt.loc)._module.freeformType.value;
            in
            assert lib.assertMsg (lib.elem block.module [
              "shell"
              "nixos"
            ]) "lib.checks.configBlocks: module is ${block.module}, not shell or nixos";
            assert lib.assertMsg (
              pkgs.formats ? ${block.format}
            ) "lib.checks.configBlocks: pkgs.formats has no ${block.format}";
            {
              name = lib.showOption path;
              value =
                if lib.last block.path != "settings" then
                  "named ${lib.last block.path}, not settings"
                else if missing != null then
                  missing
                else if opt.type.name != "submodule" then
                  "its type is ${opt.type.description}, not a submodule"
                else if freeform == null then
                  "a submodule with no freeformType"
                else if freeform.name != want.name || freeform.description != want.description then
                  "its freeformType is ${freeform.description}, not pkgs.formats.${block.format}'s ${want.description}"
                else
                  null;
            };
          lines =
            lib.optional (settings == [ ]) "no settings option is listed"
            ++ map (p: "${p.name}: ${p.value}") (lib.filter (p: p.value != null) (map problem settings));
        in
        (mk { inherit pkgs product; }).eval "config-blocks" {
          assertion = lines == [ ];
          message = lib.concatStringsSep "; " lines + " (docs/conventions.md, Framework config blocks)";
        };
    };

  # The checks of the conventions over what a product's users meet: the
  # environment variables its tools read and set, and the flags its commands
  # take. interface.py has the rules.
  interface = {
    # docs/conventions.md, Tool-owned variables: every variable in the
    # product's namespace, `<prefix>_*`, that its Python, shell and Nix use is
    # `<prefix>_NIX_*` or devguard's `<prefix>_DEVGUARD_*`, and
    # `<prefix>_NIX_DEVGUARD` and `<prefix>_NIX_RESTORE_ALLOW_UNGUARDED` are
    # among them. `ignore` lists directories, relative to `src`, that are not
    # scanned (fixtures, vendored trees). `exceptions` lists the names that may
    # break the rule: the framework's own, the old names read until 2.0.0, and
    # names that are not variables, such as a template's keys. One that is not
    # found, or that follows the rule, fails the check. The Nix list that
    # declares them, if `src` holds it, is not scanned: a list of just those
    # strings, in order.
    envVars =
      {
        pkgs,
        src,
        product,
        prefix ? lib.toUpper (lib.replaceStrings [ "-" ] [ "_" ] (lib.removeSuffix "-nix" product)),
        ignore ? [ ],
        exceptions ? [ ],
      }:
      let
        spec = builtins.toJSON {
          inherit prefix ignore exceptions;
          allowed = [
            "${prefix}_NIX_"
            "${prefix}_DEVGUARD_"
          ];
          required = {
            "${prefix}_NIX_DEVGUARD" = "Tool-owned variables";
            "${prefix}_NIX_RESTORE_ALLOW_UNGUARDED" = "Unguarded restore";
          };
        };
      in
      (mk { inherit pkgs product; }).script "env-vars" { } ''
        ${lib.getExe pkgs.python3} ${./interface.py} env ${src} ${lib.escapeShellArg spec}
      '';

    # docs/conventions.md, Restore and Scaffolder: each command, run with
    # `--help` in the build sandbox, lists the flags the conventions name for
    # it. `commands` is { restore, scaffolder }, each the command's words, e.g.
    # { restore = [ (lib.getExe' cli "odoo") "db" "restore" ]; scaffolder =
    # lib.getExe odoo-init; }. A command that needs its shell's environment to
    # print its help is a wrapper that sets it. `exceptions` lists flags, as
    # "<command> <flag>" ("restore --no-force"), that may be missing; one that
    # is not fails the check.
    commandFlags =
      {
        pkgs,
        product,
        commands,
        exceptions ? [ ],
      }:
      let
        required = {
          restore = {
            "--no-force" = "Overwrite";
            "--no-migrate" = "Migrate after restore";
            "--neutralize" = "Neutralise";
            "--no-neutralize" = "Neutralise";
            "--files" = "Files";
            "--at" = "Point in time";
          };
          scaffolder = {
            "--version" = "Version flag";
          }
          // lib.genAttrs [
            "--dry-run"
            "--skip-lock"
            "-y"
            "--yes"
            "--commit"
            "--force"
            "--name"
            "--migrate"
          ] (_: "Flags");
        };
        spec = builtins.toJSON {
          inherit required exceptions;
          commands = lib.mapAttrs (_: words: map toString (lib.toList words)) commands;
        };
      in
      assert lib.assertMsg (lib.attrNames commands == lib.attrNames required)
        "lib.checks.commandFlags: pass commands = { ${lib.concatStringsSep "; " (lib.attrNames required)}; }, each the command's words";
      (mk { inherit pkgs product; }).script "command-flags" { } ''
        ${lib.getExe pkgs.python3} ${./interface.py} flags ${lib.escapeShellArg spec}
      '';
  };

  # Every conformance check a product registers, by check name, from one set of
  # arguments. The names depend on which arguments are given and never on
  # their values, so `checks` can be the set this one is merged into:
  # `option-paths` and `config-blocks` come with `options`, which needs
  # `tenant` and `settings` too, and `command-flags` with `commands`.
  conventions =
    args@{
      pkgs,
      src,
      product,
      checks,
      checkNames ? { },
      dependabot ? { },
      envVars ? { },
      optionPaths ? { },
      options ? null,
      tenant ? null,
      settings ? null,
      commandFlags ? { },
      commands ? null,
    }:
    let
      tree = { inherit pkgs src product; };
      modular = { inherit pkgs product options; };
    in
    assert lib.assertMsg (
      args ? options -> args ? tenant && args ? settings
    ) "lib.checks.conventions: options needs tenant and settings too";
    {
      ci-pins = repository.ciPins tree;
      dependabot = repository.dependabot (tree // dependabot);
      check-names = repository.checkNames ({ inherit pkgs product checks; } // checkNames);
      env-vars = interface.envVars (tree // envVars);
    }
    // lib.optionalAttrs (args ? options) {
      option-paths = modules.optionPaths (modular // { inherit tenant; } // optionPaths);
      config-blocks = modules.configBlocks (modular // { inherit settings; });
    }
    // lib.optionalAttrs (args ? commands) {
      command-flags = interface.commandFlags ({ inherit pkgs product commands; } // commandFlags);
    };
in
{
  inherit mk conventions;
  inherit (repository) ciPins dependabot checkNames;
  inherit (modules) optionPaths configBlocks;
  inherit (interface) envVars commandFlags;

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
