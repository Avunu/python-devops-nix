# lib.devguard: the core's tests against the stub product, and the stub grafted
# into a virtualenv beside a second graft, on every interpreter the presets
# name.
{ pkgs, self }:
self.lib.devguard.check {
  inherit pkgs;
  name = "lib-devguard";
}
