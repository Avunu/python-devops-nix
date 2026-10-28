# lib.python.textual on every interpreter the presets name: textual, rich and
# markdown-it-py (and mdit-py-plugins) are one copy each, with the fixes
# lib/python/rich.nix makes, and the interpreter imports textual and the extra
# packages it was given.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  envs = lib.genAttrs self.lib.python.defaultInterpreters (
    attr:
    self.lib.python.textual {
      python = pkgs.${attr};
      extraPackages = ps: [ ps.click ];
    }
  );

  dependency = drv: name: lib.findFirst (p: (p.pname or null) == name) null (drv.dependencies or [ ]);

  evaluated = checks.eval "lib-python-textual-eval" {
    expected = lib.genAttrs self.lib.python.defaultInterpreters (_: {
      textualChecks = false;
      richSkipped = true;
      sameRich = true;
      markdownItPyChecks = false;
      mditPyPluginsChecks = false;
    });
    actual = lib.mapAttrs (_: env: {
      textualChecks = env.textual.doInstallCheck;
      richSkipped = lib.elem "test_brokenpipeerror" env.rich.disabledTests;
      sameRich = (dependency env.textual "rich") == env.rich;
      markdownItPyChecks = (dependency env.textual "markdown-it-py").doInstallCheck;
      mditPyPluginsChecks = (dependency env.textual "mdit-py-plugins").doInstallCheck;
    }) envs;
  };
in
checks.script "lib-python-textual" { } ''
  : ${evaluated}
  ${lib.concatStrings (
    lib.mapAttrsToList (attr: env: ''
      echo "== ${attr}"
      ${env}/bin/python -I -c 'import click, markdown_it, rich, textual; from textual.app import App; print("textual", textual.__version__)'
    '') envs
  )}
''
