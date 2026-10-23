# A development shell's welcome banner (docs/library.md, Development shell): one
# rich renderer taking JSON (title, commands, URLs, facts, guard state, notes)
# from Nix, in place of frappe-nix's flags and odoo-nix's nine positional
# arguments. python/banner.py has the JSON's shape.
#
#   banner = lib.banner.command { inherit pkgs; python = pkgs.python312; name = "odoo-nix-banner"; };
#
#   enterShell = ''
#     ${lib.banner.script { inherit pkgs banner; spec = { title = …; commands = [ … ]; … };
#                           vars.port = "$_port"; }}
#   '';
{ lib }:

let
  pythonLib = import ./python { inherit lib; };
in
rec {
  module = ../python/banner.py;

  # `<name> SPEC.json [--var NAME=VALUE]...`, on lib.python.rich for `python`
  # (a tool: docs/library.md, Python versions; never the application's
  # environment).
  command =
    {
      pkgs,
      name,
      python ? pkgs.python3,
    }:
    pythonLib.command {
      inherit pkgs name;
      python = pythonLib.rich { inherit python; };
      modules = builtins.path {
        name = "banner";
        path = ../python;
        filter = path: _type: baseNameOf path == "banner.py";
      };
      imports = "import banner";
      main = "banner.main(sys.argv[1:], prog=${builtins.toJSON name})";
    };

  # The shell that prints `spec` with `banner` (a `command`). `spec` is written
  # to the store as JSON at evaluation; `vars` fill its @name@ placeholders
  # when the shell runs, each value a piece of shell (expanded there, inside
  # double quotes), for what is only known then.
  script =
    {
      pkgs,
      banner,
      spec,
      vars ? { },
    }:
    let
      file = pkgs.writeText "${banner.name}.json" (builtins.toJSON spec);
    in
    lib.concatStringsSep " " (
      [
        (lib.getExe banner)
        "${file}"
      ]
      ++ lib.mapAttrsToList (name: value: "--var \"${name}=${value}\"") vars
    );

  # The base's tests: the parser and the rendering on every interpreter a
  # preset names, each with lib.python.rich, and the command run with a spec
  # in both products' shapes. Named by lib.checks.mk: <product>-<name>-check.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "banner",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      run =
        attr:
        let
          python = pkgs.${attr};
          rich = pythonLib.rich { inherit python; };
          banner = command {
            inherit pkgs python;
            name = "stub-nix-banner";
          };
          shell = pkgs.writeText "banner-shell" ''
            _port=8223
            ${script {
              inherit pkgs banner;
              spec = {
                title = "stub — dev environment";
                commands = [
                  {
                    command = "stub test <mod> [db]";
                    description = "run a module's tests";
                  }
                ];
                urls = [
                  {
                    label = "URL";
                    url = "http://127.0.0.1:@port@";
                  }
                ];
                guard = {
                  enable = true;
                  egress = {
                    enable = true;
                    allow = [ "api.example.com" ];
                  };
                };
              };
              vars.port = "$_port";
            }}
          '';
        in
        ''
          echo "== ${attr} (${python.version})"
          ${rich.interpreter} -I ${../tests/test_banner.py} ${../python} 2>&1
          got="$(COLUMNS=80 bash ${shell})"
          printf '%s\n' "$got"
          grep -qF 'URL: http://127.0.0.1:8223' <<< "$got"
          grep -qF 'stub test <mod> [db]' <<< "$got"
          grep -qF 'Egress: outbound connections blocked (allowed: api.example.com)' <<< "$got"
          printf '{"title": "t", "colour": 1}' > bad.json
          if ${lib.getExe banner} bad.json 2> err; then
            echo "an unknown key was accepted" >&2
            exit 1
          fi
          grep -qF 'unknown key(s): colour' err
        '';
    in
    checks.script name { } "{ set -e; ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
