# standards-core's suite (standards/tests) on every interpreter the presets
# name: lib.standards.check (standards/DESIGN.md §10.2).
{ pkgs, self }:
self.lib.standards.check { inherit pkgs; }
