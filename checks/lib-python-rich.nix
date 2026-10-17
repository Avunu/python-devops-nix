# lib.python.rich on every interpreter the presets name: rich builds with its
# own suite run, test_brokenpipeerror skipped and markdown-it-py's checks off,
# and the interpreter imports rich and the extra packages it was given.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  envs = lib.genAttrs self.lib.python.defaultInterpreters (
    attr:
    self.lib.python.rich {
      python = pkgs.${attr};
      extraPackages = ps: [ ps.click ];
    }
  );

  # The markdown-it-py a rich was built with.
  markdownItPy =
    rich: lib.findFirst (p: (p.pname or null) == "markdown-it-py") null rich.dependencies;

  evaluated = checks.eval "lib-python-rich-eval" {
    expected = lib.genAttrs self.lib.python.defaultInterpreters (_: {
      richChecks = true;
      skipped = true;
      markdownItPyChecks = false;
    });
    actual = lib.mapAttrs (_: env: {
      richChecks = env.rich.doInstallCheck;
      skipped = lib.elem "test_brokenpipeerror" env.rich.disabledTests;
      markdownItPyChecks = (markdownItPy env.rich).doInstallCheck;
    }) envs;
  };
in
checks.script "lib-python-rich" { } ''
  : ${evaluated}
  ${lib.concatStrings (
    lib.mapAttrsToList (attr: env: ''
      echo "== ${attr}"
      ${env}/bin/python -I -c 'import click, markdown_it, rich; from rich.console import Console; Console().print("[bold]rich[/bold]", rich.__file__)'
    '') envs
  )}
''
