# A development shell's TUI (docs/library.md, Development shell): the banner,
# devenv's processes as process-compose runs them, the product's commands, and
# a store for what the product can add. python/devtui has the Python; the spec
# it reads is devtui.spec's, written here from Nix as the banner's is.
#
#   tui = lib.tui.command {
#     inherit pkgs; python = pkgs.python312; name = "odoo-nix-tui";
#     plugins = [ { name = "odoo_nix_cli"; path = ./lib/odoo_nix_cli; } ];
#   };
#   run = lib.tui.script { inherit pkgs tui; spec = { product = "odoo-nix"; cli = "odoo"; banner = bannerSpec; … };
#                          vars.port = "$(…)"; };
#   # the product's `odoo ui` execs (lib.getExe run)
{ lib }:

let
  pythonLib = import ./python { inherit lib; };

  devtui = builtins.path {
    name = "devtui";
    path = ../python/devtui;
    filter = path: _type: baseNameOf path != "__pycache__";
  };
in
rec {
  module = devtui;

  # The modules the command runs: devtui/, banner.py beside it (devtui
  # renders the banner), and each of `plugins` ({ name, path }: a package
  # directory, copied in as <name>/), whose module the spec's "plugin" names.
  tree =
    {
      pkgs,
      name ? "devtui",
      plugins ? [ ],
    }:
    let
      names = map (p: p.name) plugins;
    in
    assert lib.assertMsg
      (lib.unique names == names && !(lib.elem "devtui" names) && !(lib.elem "banner" names))
      "lib.tui.tree: plugin names must be unique, and neither devtui nor banner: ${lib.concatStringsSep ", " names}";
    pkgs.runCommandLocal "${name}-modules" { } ''
      mkdir -p $out
      cp -r --no-preserve=mode ${devtui} $out/devtui
      cp --no-preserve=mode ${../python/banner.py} $out/banner.py
      ${lib.concatMapStrings (p: ''
        cp -rL --no-preserve=mode ${p.path} $out/${p.name}
      '') plugins}
      find $out -name __pycache__ -prune -exec rm -rf {} +
    '';

  # `<name> SPEC.json [--vars-command PROGRAM] [--var NAME=VALUE]...`, on
  # lib.python.textual for `python` (a tool: docs/library.md, Python versions;
  # never the application's environment), isolated (-I).
  command =
    {
      pkgs,
      name,
      python ? pkgs.python3,
      plugins ? [ ],
      extraPackages ? (_ps: [ ]),
    }:
    let
      env = pythonLib.textual { inherit python extraPackages; };
      modules = tree { inherit pkgs name plugins; };
    in
    pythonLib.command {
      inherit pkgs name modules;
      python = env;
      imports = "import devtui";
      main = "devtui.main(sys.argv[1:], prog=${builtins.toJSON name})";
      # For a product's own tests of its plugin: the interpreter and the
      # modules the command runs.
      passthru = {
        inherit plugins modules;
        python = env;
      };
    };

  # The program that runs `tui` (a `command`) with `spec`. `spec` is written to
  # the store as JSON at evaluation; `vars` fill its @name@ placeholders, each
  # value a piece of shell (expanded inside double quotes) run when the TUI
  # starts and again whenever the stack does, so a port leased at `devenv up`
  # stays current. The program passes its own arguments on to `tui`.
  script =
    {
      pkgs,
      tui,
      spec,
      vars ? { },
      name ? "${tui.name}-run",
    }:
    let
      file = pkgs.writeText "${tui.name}.json" (builtins.toJSON spec);
      varsCommand = pkgs.writeShellScript "${name}-vars" (
        lib.concatStrings (
          lib.mapAttrsToList (var: value: ''
            printf '%s=%s\n' ${var} "${value}"
          '') vars
        )
      );
    in
    assert lib.assertMsg (lib.all (var: builtins.match "[A-Za-z_][A-Za-z0-9_]*" var != null) (
      lib.attrNames vars
    )) "lib.tui.script: a var name is not an identifier";
    (pkgs.writeShellScriptBin name ''
      exec ${lib.getExe tui} ${file} ${
        lib.optionalString (vars != { }) "--vars-command ${varsCommand}"
      } "$@"
    '').overrideAttrs
      (old: {
        # The spec as written, and the command, for a product's own tests.
        passthru = (old.passthru or { }) // {
          inherit tui;
          spec = file;
        };
      });

  # The arguments of devtui's commands.py `dump`, shared by `commands` and
  # `probe`.
  dumpArgs =
    {
      target,
      cli,
      source ? "",
      interactive ? [ ],
    }:
    [
      "dump"
      target
      "--name"
      cli
      "--source"
      source
    ]
    ++ lib.concatMap (path: [
      "--interactive"
      path
    ]) interactive;

  # A command catalog built with the shell: the click group `target`
  # (module:attr) of a command line called `cli`, as JSON, made by `python`
  # (an environment with click and whatever `target` imports; `pythonPath`
  # directories go on PYTHONPATH). `interactive` names the commands (paths,
  # space-separated) that need the terminal. The spec lists it as
  # { file = "${…}"; }.
  commands =
    {
      pkgs,
      name,
      python,
      target,
      cli,
      source ? "",
      interactive ? [ ],
      pythonPath ? [ ],
    }:
    pkgs.runCommandLocal "${name}.json" { } ''
      export HOME="$TMPDIR"
      PYTHONPATH=${lib.escapeShellArg (lib.concatStringsSep ":" (map toString pythonPath))} \
        ${python.interpreter} ${devtui}/commands.py ${
          lib.escapeShellArgs (dumpArgs {
            inherit
              target
              cli
              source
              interactive
              ;
          })
        } > $out
    '';

  # The same catalog made when the TUI first needs it, by `interpreter` (a
  # path to the application's python, as a string: the framework's own
  # commands take the framework to import), and cached under `cache` by this
  # command line. For the spec's "commands": { probe = lib.tui.probe { … };
  # cache = "…"; cwd = "…"; }. The JSON follows a marker line, so whatever
  # the framework prints on import is skipped.
  probe =
    {
      interpreter,
      target,
      cli,
      source ? "",
      interactive ? [ ],
    }:
    [
      interpreter
      "${devtui}/commands.py"
      "dump"
      "--marked"
    ]
    ++ lib.drop 1 (dumpArgs {
      inherit
        target
        cli
        source
        interactive
        ;
    });

  # The base's tests: the spec, the process-compose client against a fake
  # server, the command catalogs, the store's search, and the app itself driven
  # headless (textual's run_test), on every interpreter a preset names, each
  # with lib.python.textual; and the command run without a terminal. Named by
  # lib.checks.mk: <product>-<name>-check.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "tui",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      run =
        attr:
        let
          python = pkgs.${attr};
          env = pythonLib.textual {
            inherit python;
            extraPackages = ps: [ ps.click ];
          };
          tui = command {
            inherit pkgs python;
            name = "stub-nix-tui";
          };
          runner = script {
            inherit pkgs tui;
            spec = {
              product = "stub-nix";
              cli = "stub";
              banner.title = "stub — dev environment @port@";
              site.url = "http://127.0.0.1:@port@";
            };
            vars.port = "8223";
          };
        in
        ''
          echo "== ${attr} (${python.version})"
          ${env.interpreter} -I ${../tests/test_tui.py} ${../python} 2>&1
          ${env.interpreter} -I ${../tests/test_tui_app.py} ${../python} 2>&1
          if ${lib.getExe runner} < /dev/null > out 2> err; then
            echo "the TUI started without a terminal" >&2
            exit 1
          fi
          grep -qF 'the menu needs a terminal. Run `stub --help`' err
          if ${lib.getExe runner} --only store < /dev/null 2> err; then
            echo "the store started for a spec without one" >&2
            exit 1
          fi
          grep -qF 'has no store here' err
          printf '{"product": "p", "cli": "c", "banner": {"title": "t"}, "colour": 1}' > bad.json
          if ${lib.getExe tui} bad.json < /dev/null 2> err; then
            echo "an unknown key was accepted" >&2
            exit 1
          fi
          grep -qF 'unknown key(s): colour' err
        '';
    in
    checks.script name { } "{ set -e; ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
