# lib.banner: the spec parser and the rendering on every interpreter the
# presets name, each with lib.python.rich, and the command run from a shell
# with a placeholder filled at run time.
{ pkgs, self }:
self.lib.banner.check {
  inherit pkgs;
  name = "lib-banner";
}
