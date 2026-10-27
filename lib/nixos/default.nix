# The production pieces inside both products' NixOS modules (docs/library.md,
# Production). The modules themselves stay in the products; these are the parts
# that are the same security posture in both, and should not differ.
#
#   hardening   a systemd sandboxing baseline, applied per unit, with named
#               profiles for what a unit legitimately needs (lib/nixos/hardening.nix)
#   migrate     the migrate.* options and the snapshot flow (lib/nixos/migrate.nix)
#   unixSocket  a socket in its own 0750 or 0770 directory (lib/nixos/unix-socket.nix)
#   nginx       the journal_json access log, the error log to syslog, the
#               real-IP block for a unix-socket listener, and the service,
#               upstream, virtual host and location builders (lib/nixos/nginx.nix)
#
# The journald field vocabulary units carry is lib.journald.fields.
{ lib }:
{
  hardening = import ./hardening.nix { inherit lib; };
  migrate = import ./migrate.nix { inherit lib; };
  unixSocket = import ./unix-socket.nix { inherit lib; };
  nginx = import ./nginx.nix { inherit lib; };
}
