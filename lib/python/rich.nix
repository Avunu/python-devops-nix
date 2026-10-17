# A nixpkgs interpreter with rich, for a product's own tooling (a banner, a
# command line) that has nothing to do with the application's uv-managed
# environment and so does not belong in it. odoo-nix's lib/rich-python.nix,
# the superset of the two products' copies.
#
#   rich { python = pkgs.python312; }                                  # rich only
#   rich { python = pkgs.python312; extraPackages = ps: [ ps.click ]; }
#
# Two fixes on nixpkgs' rich:
#
#   - markdown-it-py, one of rich's runtime dependencies, builds with its own
#     test suite, which pulls in pytest-regressions and so numpy, and nixpkgs
#     has offered 3.11 a numpy that requires 3.12. Its checks are switched
#     off. overridePythonAttrs, not overrideAttrs: buildPythonPackage computes
#     the check inputs from doCheck before overrideAttrs runs, so overrideAttrs
#     leaves them in. rich's own doCheck would not help either, because
#     markdown-it-py is still built as rich's dependency.
#   - rich's test_brokenpipeerror races a closing pipe and fails on a busy
#     builder (`assert proc1.returncode == 1` got 0 on a GitHub runner) while
#     passing locally (odoo-nix#55). The fix above means this rich is never a
#     cache hit, so its suite runs on every cold build; that one test is
#     skipped, and the rest still run.
#
# The environment carries the rich it was built with as `.rich`.
{
  python,
  extraPackages ? (_ps: [ ]),
}:
let
  richFor =
    ps:
    let
      markdown-it-py = ps.markdown-it-py.overridePythonAttrs (_: {
        doCheck = false;
      });
    in
    (ps.rich.override { inherit markdown-it-py; }).overridePythonAttrs (old: {
      disabledTests = (old.disabledTests or [ ]) ++ [ "test_brokenpipeerror" ];
    });
in
python.withPackages (ps: [ (richFor ps) ] ++ extraPackages ps)
// {
  rich = richFor python.pkgs;
}
