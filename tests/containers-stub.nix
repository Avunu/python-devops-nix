# A stub product's container images (docs/library.md, design rule 8), built
# from lib.containers the way frappe-nix and odoo-nix build theirs: a web
# image that serves HTTP and has a healthcheck, and a worker image that serves
# nothing and has none. checks/lib-containers.nix evaluates them and reads
# each image's passthru.imageConfig, so nothing is built.
#
#   import ./containers-stub.nix self { inherit pkgs; nonRoot = true; … }
#
# Any argument besides pkgs goes to lib.containers.mk, over the stub's own.
base:
{ pkgs, ... }@args:
let
  inherit (pkgs) lib;

  containers = base.lib.containers.mk (
    {
      label = "stub-nix";
      name = "acme";
      user = "stub";
      home = "/home/stub";
      nonRoot = false;
      source = "https://example.com/stub-nix";
      version = "1.2.3";
      licenses = "MIT";
    }
    // args
  );

  # A shell's /dev/tcp asking for a status line, as the products' nginx and
  # Odoo probes do.
  probe = pkgs.writeShellScript "stub-healthcheck" ''
    exec 3<>/dev/tcp/127.0.0.1/8000 || exit 1
    printf 'GET / HTTP/1.0\r\nHost: localhost\r\n\r\n' >&3
    IFS= read -r -t 8 status <&3 || exit 1
    [[ $status == HTTP/* ]]
  '';

  image =
    {
      role,
      cmd,
      exposedPorts ? { },
      healthcheck ? null,
    }:
    containers.buildImage {
      name = containers.imageName role;
      tag = "latest";
      contents = [
        (pkgs.buildEnv {
          name = "stub-${role}-env";
          paths = [
            pkgs.bashInteractive
            pkgs.coreutils
            pkgs.python3
          ];
          pathsToLink = [ "/bin" ];
        })
        containers.users
      ];
      enableFakechroot = true;
      fakeRootCommands = containers.fakeRootCommands {
        dirs = [ "/secrets" ];
        writable = [
          "/srv/stub"
          "/home/stub"
        ];
      };
      config = {
        User = containers.user;
        Cmd = cmd;
        WorkingDir = "/srv/stub";
        ExposedPorts = exposedPorts;
        Env = [ "LANG=C.UTF-8" ] ++ containers.homeEnv;
        Labels = containers.labels role;
      }
      // lib.optionalAttrs (healthcheck != null) { Healthcheck = containers.healthcheck healthcheck; };
    };
in
{
  inherit containers probe;

  web = image {
    role = "web";
    cmd = [
      "${pkgs.python3}/bin/python"
      "-m"
      "http.server"
      "8000"
    ];
    exposedPorts."8000/tcp" = { };
    healthcheck = [ "${probe}" ];
  };

  worker = image {
    role = "worker";
    cmd = [
      "${pkgs.coreutils}/bin/sleep"
      "infinity"
    ];
  };
}
