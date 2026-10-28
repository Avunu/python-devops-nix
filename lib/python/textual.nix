# A nixpkgs interpreter with textual, for a product's interactive tooling (the
# development shell's TUI, lib.tui) that, like rich's, has nothing to do with
# the application's uv-managed environment.
#
#   textual { python = pkgs.python312; }
#   textual { python = pkgs.python312; extraPackages = ps: [ ps.click ]; }
#
# textual depends on markdown-it-py three ways (directly, through
# mdit-py-plugins and through rich), so lib/python/rich.nix's fixes are made
# once, in the package set, rather than on one package's arguments: every
# package in the environment then gets the same markdown-it-py, with its checks
# off, and the same rich, with test_brokenpipeerror skipped (rich.nix has why).
# textual's own suite is off as well: it is a snapshot suite that drives a
# terminal, and pulls in pytest-textual-snapshot and syrupy for a tool that
# only renders a menu.
#
# A package the overrides do not reach keeps its derivation, and so its cache
# hit. The environment carries the textual and rich it was built with as
# `.textual` and `.rich`.
{
  python,
  extraPackages ? (_ps: [ ]),
}:
let
  # `self` keeps withPackages on the overridden set, rather than the
  # interpreter's original one.
  fixed = python.override {
    self = fixed;
    packageOverrides = _final: prev: {
      markdown-it-py = prev.markdown-it-py.overridePythonAttrs (_: {
        doCheck = false;
      });
      # Its suite takes pytest-regressions too, and with it the numpy 3.11
      # cannot build.
      mdit-py-plugins = prev.mdit-py-plugins.overridePythonAttrs (_: {
        doCheck = false;
      });
      rich = prev.rich.overridePythonAttrs (old: {
        disabledTests = (old.disabledTests or [ ]) ++ [ "test_brokenpipeerror" ];
      });
      textual = prev.textual.overridePythonAttrs (_: {
        doCheck = false;
      });
    };
  };
in
fixed.withPackages (ps: [ ps.textual ] ++ extraPackages ps)
// {
  inherit (fixed.pkgs) textual rich;
}
