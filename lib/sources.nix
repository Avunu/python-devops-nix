# Source repositories as git submodules (PLAN.md, Phase 5): the first clone a
# scaffolder makes, and what shell entry does about them. The logic is
# python/sources.py; its docstring has the reasoning (paired branch, partial
# clone, no pack pile-up), ported from frappe-nix's lib/apps-report.nix and
# odoo-nix's lib/submodules-init.nix.
{ lib }:

let
  pythonLib = import ./python { inherit lib; };
in
rec {
  # The module itself, for a product that imports it in-process (frappe-nix's
  # scaffolder): copy it into the product's package (PLAN.md, design rule 6)
  # and call `clone(...)` or `Sources(Config(...)).enter(root)`.
  module = ../python/sources.py;

  # The command: `<name> enter [--treeless PATH]... [ROOT]` for shell entry
  # (it always exits 0: every problem is reported, and the next entry tries
  # again), and `<name> clone [--branch B] [--treeless] [--require-branch] URL
  # PATH` for a scaffolder's first clone (exit 1 when git fails, 3 when
  # --require-branch and the remote has no such branch).
  #
  #   label          the message prefix ("<p>-nix")
  #   noun           what the superproject is called ("bench", "project")
  #   scope          directories whose direct children are the source
  #                  submodules ([ "apps" ]), or [ ] for every submodule in
  #                  .gitmodules. With a scope, a repository in one of them that
  #                  is not a registered submodule is reported
  #   recursive      whether a checkout takes the submodule's own submodules
  #   treeless       paths cloned commits-only (tree:0); `enter --treeless`
  #                  adds more at run time
  #   updateCommand  the command that moves submodules to their branches' tips
  #   removeCommand  the command that finishes a half-done removal; {name},
  #                  {path} and {base} are filled in. Default: removing the
  #                  .gitmodules section
  #   nestedHint     the lines (each indented by two spaces) after "<path> is a
  #                  git repository but not a registered submodule —"
  #   python         the interpreter it runs on (a tool: PLAN.md, Python
  #                  versions)
  #   git            put first on PATH
  command =
    {
      pkgs,
      label,
      noun ? "project",
      scope ? [ ],
      recursive ? false,
      treeless ? [ ],
      updateCommand ? "",
      removeCommand ? "",
      nestedHint ? null,
      python ? pkgs.python3,
      git ? pkgs.git,
      name ? "${label}-sources",
    }:
    let
      config = {
        inherit
          label
          noun
          scope
          recursive
          treeless
          ;
        update_command = updateCommand;
        remove_command = removeCommand;
      }
      // lib.optionalAttrs (nestedHint != null) { nested_hint = nestedHint; };
    in
    pythonLib.command {
      inherit pkgs name python;
      modules = builtins.path {
        name = "sources";
        path = ../python;
        filter = path: _type: baseNameOf path == "sources.py";
      };
      imports = ''
        import json
        import os

        import sources

        os.environ["PATH"] = ${builtins.toJSON "${git}/bin"} + os.pathsep + os.environ.get("PATH", "")
      '';
      main = ''
        sources.main(
            sys.argv[1:],
            config=json.loads(${builtins.toJSON (builtins.toJSON config)}),
            prog=${builtins.toJSON name},
        )'';
      passthru = { inherit config; };
    };

  # The base's tests: the unit tests on every interpreter a preset names, and
  # the command driven against file:// remotes (with uploadpack.allowFilter, as
  # GitHub has it) in both products' shapes, frappe-nix's apps under apps/ and
  # odoo-nix's submodules anywhere with a treeless core, on each interpreter.
  # Named by lib.checks.mk: <product>-<name>-check.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "sources",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      shapes = {
        apps = {
          label = "stub-nix";
          noun = "bench";
          scope = [ "apps" ];
          updateCommand = "stub update --pull";
          removeCommand = "stub remove-app {base}";
        };
        modules = {
          label = "stub-nix";
          recursive = true;
          treeless = [ "core" ];
          updateCommand = "stub project update";
        };
      };
      run =
        attr:
        let
          python = pkgs.${attr};
          tool = shape: lib.getExe (command ({ inherit pkgs python; } // shapes.${shape}));
        in
        ''
          echo "== ${attr} (${python.version})"
          ${python.interpreter} -I ${../tests/test_sources.py} ${../python} 2>&1
          ${lib.concatMapStrings (shape: ''
            echo "-- shape: ${shape}"
            bash ${../tests/sources.sh} ${tool shape} ${shape}
          '') (lib.attrNames shapes)}
        '';
    in
    checks.script name {
      tools = [
        pkgs.git
        pkgs.findutils
        pkgs.gawk
      ];
    } "{ set -e; ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
