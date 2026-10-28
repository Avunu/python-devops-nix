# lib.tui: the spec, the process-compose client, the command catalogs, the
# store's search and the app driven headless, on every interpreter the presets
# name, each with lib.python.textual; and the command run without a terminal.
{ pkgs, self }:
self.lib.tui.check {
  inherit pkgs;
  name = "lib-tui";
}
