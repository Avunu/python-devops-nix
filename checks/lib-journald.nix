# lib.journald: the formatter core's tests, and a stub product's graft writing
# to a JOURNAL_STREAM, on every interpreter the presets name.
{ pkgs, self }:
self.lib.journald.check {
  inherit pkgs;
  name = "lib-journald";
}
