# lib.sources: the unit tests on every interpreter the presets name, and the
# command driven against file:// remotes in frappe-nix's shape (apps/) and
# odoo-nix's (every submodule, a treeless core, recursive) on each.
{ pkgs, self }:
self.lib.sources.check {
  inherit pkgs;
  name = "lib-sources";
}
