# lib.nixos.migrate: the flow's unit tests against stub commands, on every
# interpreter the presets name (a product's migrate tool may import it).
{ pkgs, self }:
self.lib.nixos.migrate.check {
  inherit pkgs;
  name = "lib-nixos-migrate-flow";
}
