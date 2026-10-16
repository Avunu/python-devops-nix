# lib.backups: the fetch layer's unit tests on every interpreter the presets
# name, and the command driven from outside, with both layouts in use.
{ pkgs, self }:
self.lib.backups.check {
  inherit pkgs;
  name = "lib-backups";
}
