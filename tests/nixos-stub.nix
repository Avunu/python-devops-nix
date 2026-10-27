# A stub product's NixOS module (docs/library.md, design rule 8):
# services.stub-nix, built from lib.nixos and lib.journald the way frappe-nix
# and odoo-nix use them. checks/lib-nixos.nix evaluates it and asserts what it
# renders; tests/nixos-migrate.nix runs it.
#
# Each target is one database with a web unit in front of it, in one of the
# two products' shapes:
#
#   mariadb     frappe-nix's: a gzipped mysqldump, verified by its last line,
#               and a failed migration leaves the web unit serving its
#               maintenance page (the migrate command turns maintenance on,
#               the success step off). The migrate unit RemainAfterExit, and
#               the web unit only orders after it.
#   postgresql  odoo-nix's: a pg_dump custom-format archive, verified with
#               pg_restore --list, and the web unit Requires= the migrate unit,
#               which runs on every start of it: a failed migration keeps it
#               down.
#
# The "migration" is a plan file, <stateDir>/<target>/plan, holding a number
# and ok or fail: migration N creates table m_N and adds N to table data, then
# fails after its DDL if the plan says so. A marker file of the last plan
# migrated stands in for frappe-nix's build marker (the check). A
# corrupt-dump file makes the dump truncated, so it fails to verify.
base:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkOption types;
  cfg = config.services.stub-nix;
  inherit (base.lib) nixos journald;

  targetDir = name: "${cfg.stateDir}/${name}";
  mariadbTargets = lib.filterAttrs (_: t: t.kind == "mariadb") cfg.targets;
  postgresqlTargets = lib.filterAttrs (_: t: t.kind == "postgresql") cfg.targets;

  fields = args: journald.fields ({ prefix = "stub"; } // args);

  sockets = nixos.unixSocket {
    inherit (cfg) user group;
    sockets = lib.concatLists (
      lib.mapAttrsToList (name: t: [
        {
          option = "services.stub-nix.targets.\"${name}\".web.socketPath";
          path = t.web.socketPath;
        }
        {
          option = "services.stub-nix.targets.\"${name}\".nginx.socketPath";
          path = t.nginx.socketPath;
          peerCreates = true;
          example = "/run/stub-nix/nginx.sock";
        }
      ]) cfg.targets
    );
  };

  mysql = "${config.services.mysql.package}/bin/mariadb";
  psql = "${config.services.postgresql.package}/bin/psql";

  # Double-quoted, so the shell expands $n in a statement.
  sql =
    name: t: statement:
    if t.kind == "mariadb" then
      "${mysql} -N -B ${name} -e \"${statement}\""
    else
      "${psql} -X -q -v ON_ERROR_STOP=1 -d ${name} -c \"${statement}\"";

  steps =
    name: t:
    let
      dir = targetDir name;
      script = step: text: pkgs.writeShellScript "stub-${step}-${name}" "set -euo pipefail\n${text}";
    in
    {
      check = script "check" ''
        if [ "$(cat ${dir}/marker 2>/dev/null || true)" = "$(cat ${dir}/plan)" ]; then
          echo "stub-migrate(${name}): plan unchanged since the last successful migrate; skipping."
          exit 100
        fi
      '';
      migrate = script "migrate" ''
        # tmpfiles writes the first plan without a newline.
        { read -r n outcome || [ -n "$n" ]; } < ${dir}/plan
        ${lib.optionalString (t.kind == "mariadb") "touch ${dir}/maintenance"}
        ${sql name t "CREATE TABLE IF NOT EXISTS data (n int)"}
        ${sql name t "CREATE TABLE m_$n (id int)"}
        ${sql name t "INSERT INTO data VALUES ($n)"}
        [ "$outcome" = ok ]
      '';
      onSuccess = script "success" ''
        cp ${dir}/plan ${dir}/marker
        rm -f ${dir}/maintenance
      '';
      onFailure = script "failure" (
        if t.kind == "mariadb" then
          ''
            echo "<3>stub-migrate(${name}): left in maintenance mode (rollback: $MIGRATE_ROLLBACK, exit $MIGRATE_EXIT)" >&2
          ''
        else
          ''
            echo "<3>stub-migrate(${name}): stub-web-${name} stays down (rollback: $MIGRATE_ROLLBACK, exit $MIGRATE_EXIT)" >&2
          ''
      );
      dump =
        let
          full =
            if t.kind == "mariadb" then
              "${config.services.mysql.package}/bin/mariadb-dump --single-transaction --quick --routines --triggers ${name} | ${pkgs.gzip}/bin/gzip"
            else
              "${config.services.postgresql.package}/bin/pg_dump --format=custom ${name}";
        in
        script "dump" ''
          if [ -e ${dir}/corrupt-dump ]; then
            ${full} | ${pkgs.coreutils}/bin/head -c 64 || true
          else
            ${full}
          fi
        '';
      verify =
        if t.kind == "mariadb" then
          nixos.migrate.verifiers.mysqldump { inherit pkgs; }
        else
          nixos.migrate.verifiers.pgRestoreList {
            inherit pkgs;
            postgresql = config.services.postgresql.package;
          };
      restore =
        if t.kind == "mariadb" then
          script "restore" ''
            { echo "SET FOREIGN_KEY_CHECKS=0;"
              ${mysql} -N -e "SELECT CONCAT('DROP ', IF(TABLE_TYPE='VIEW','VIEW','TABLE'), ' IF EXISTS \`', TABLE_NAME, '\`;') FROM information_schema.TABLES WHERE TABLE_SCHEMA = '${name}';"
            } | ${mysql} ${name}
            ${pkgs.gzip}/bin/gunzip -c "$1" | ${mysql} ${name}
          ''
        else
          script "restore" ''
            ${sql name t "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"}
            ${config.services.postgresql.package}/bin/pg_restore --no-owner --exit-on-error -d ${name} "$1"
          '';
    };

  migrateScript =
    name: t:
    let
      s = steps name t;
    in
    nixos.migrate.script {
      inherit pkgs;
      label = "stub-migrate";
      target = name;
      cfg = cfg.migrate;
      inherit (s)
        check
        migrate
        onSuccess
        onFailure
        dump
        verify
        restore
        ;
      snapshotDir = "${targetDir name}/snapshots";
      extension = if t.kind == "mariadb" then ".sql.gz" else ".dump";
    };

  # The web unit: answers "maintenance" or "ok <rows of data>" on its socket.
  webServer = pkgs.writeText "stub-web.py" ''
    import http.server, os, socketserver, subprocess, sys

    path, flag = sys.argv[1], sys.argv[2]

    class Handler(http.server.BaseHTTPRequestHandler):
        def address_string(self):
            return "unix"

        def do_GET(self):
            body = b"maintenance\n" if os.path.exists(flag) else b"ok\n"
            self.send_response(503 if body == b"maintenance\n" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        pass

    if os.path.exists(path):
        os.unlink(path)
    with Server(path, Handler) as server:
        os.chmod(path, 0o660)
        server.serve_forever()
  '';
in
{
  options.services.stub-nix = {
    enable = lib.mkEnableOption "the stub product";
    user = mkOption {
      type = types.str;
      default = "stub";
    };
    group = mkOption {
      type = types.str;
      default = "stub";
    };
    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/stub-nix";
    };
    migrate = nixos.migrate.options {
      what = "the stub migration for each target";
      dumpTool = "mysqldump or pg_dump";
      where = "per target under <stateDir>/<target>/snapshots";
    };
    logging.accessLog = mkOption {
      type = types.bool;
      default = true;
    };
    # Hardening profiles for the web units (an application that renders PDFs).
    profiles = mkOption {
      type = types.listOf types.str;
      default = [ ];
    };
    # The migrate commands, for checks/lib-nixos.nix to inspect.
    migrateScripts = mkOption {
      type = types.attrsOf types.package;
      internal = true;
      readOnly = true;
      default = lib.mapAttrs migrateScript cfg.targets;
    };
    targets = mkOption {
      default = { };
      type = types.attrsOf (
        types.submodule {
          options = {
            kind = mkOption {
              type = types.enum [
                "mariadb"
                "postgresql"
              ];
            };
            web.socketPath = mkOption {
              type = types.str;
              default = "";
            };
            nginx.enable = lib.mkEnableOption "an nginx virtual host";
            nginx.socketPath = mkOption {
              type = types.str;
              default = "";
            };
          };
        }
      );
    };
  };

  config = lib.mkIf cfg.enable {
    inherit (sockets) assertions;

    users.users.${cfg.user} = {
      isSystemUser = true;
      inherit (cfg) group;
    };
    users.groups.${cfg.group} = { };

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0750 ${cfg.user} ${cfg.group} -"
    ]
    ++ lib.concatLists (
      lib.mapAttrsToList (name: _: [
        "d ${targetDir name} 0750 ${cfg.user} ${cfg.group} -"
        "f ${targetDir name}/plan 0640 ${cfg.user} ${cfg.group} - 1 ok"
      ]) cfg.targets
    )
    ++ sockets.tmpfiles;

    systemd.services = lib.mkMerge (
      lib.mapAttrsToList (
        name: t:
        let
          odooShape = t.kind == "postgresql";
          dbUnit = if t.kind == "mariadb" then "mysql.service" else "postgresql.target";
          migrateUnit = "stub-migrate-${name}.service";
        in
        {
          "stub-migrate-${name}" = lib.mkIf cfg.migrate.enable {
            description = "Stub migration for ${name}";
            # The VM test deploys more often than systemd's start limit allows.
            startLimitIntervalSec = 0;
            wantedBy = lib.optional (!odooShape) "multi-user.target";
            after = [ dbUnit ];
            requires = [ dbUnit ];
            serviceConfig = {
              Type = "oneshot";
              RemainAfterExit = !odooShape;
              User = cfg.user;
              Group = cfg.group;
              ExecStart = lib.getExe (migrateScript name t);
            }
            // fields {
              role = "migrate";
              site = name;
            }
            // nixos.hardening {
              readWritePaths = [ (targetDir name) ];
              # The database is on its unix socket.
              addressFamilies = [ "AF_UNIX" ];
            };
          };

          "stub-web-${name}" = {
            description = "Stub web for ${name}";
            startLimitIntervalSec = 0;
            wantedBy = [ "multi-user.target" ];
            after = [ dbUnit ] ++ lib.optional cfg.migrate.enable migrateUnit;
            requires = lib.optional (odooShape && cfg.migrate.enable) migrateUnit;
            serviceConfig = {
              User = cfg.user;
              Group = cfg.group;
              ExecStart = "${pkgs.python3.interpreter} ${webServer} ${t.web.socketPath} ${targetDir name}/maintenance";
              Restart = "on-failure";
            }
            // fields {
              role = "web";
              site = name;
            }
            // nixos.hardening {
              readWritePaths = [ (dirOf t.web.socketPath) ];
              inherit (cfg) profiles;
            };
          };
        }
      ) cfg.targets
      ++ [
        (lib.mkIf (mariadbTargets != { }) {
          mysql.serviceConfig = fields { role = "db"; };
        })
        (lib.mkIf (lib.any (t: t.nginx.enable) (lib.attrValues cfg.targets)) {
          nginx.serviceConfig = fields { role = "nginx"; };
        })
        (lib.mkIf (postgresqlTargets != { }) {
          postgresql.serviceConfig = fields { role = "db"; };
          postgresql-setup.script = lib.mkAfter (
            lib.concatMapStrings (name: ''
              psql -tAc 'ALTER DATABASE "${name}" OWNER TO "${cfg.user}"'
            '') (lib.attrNames postgresqlTargets)
          );
        })
      ]
    );

    services.mysql = lib.mkIf (mariadbTargets != { }) {
      enable = true;
      package = lib.mkDefault pkgs.mariadb;
      ensureDatabases = lib.attrNames mariadbTargets;
      ensureUsers = [
        {
          name = cfg.user;
          ensurePermissions = lib.mapAttrs' (
            name: _: lib.nameValuePair "${name}.*" "ALL PRIVILEGES"
          ) mariadbTargets;
        }
      ];
    };

    services.postgresql = lib.mkIf (postgresqlTargets != { }) {
      enable = true;
      ensureDatabases = lib.attrNames postgresqlTargets;
      ensureUsers = [ { name = cfg.user; } ];
    };

    users.users.nginx.extraGroups =
      lib.mkIf (lib.any (t: t.nginx.enable) (lib.attrValues cfg.targets))
        [
          cfg.group
        ];
    services.nginx = lib.mkIf (lib.any (t: t.nginx.enable) (lib.attrValues cfg.targets)) (
      nixos.nginx.serviceDefaults { inherit (cfg.logging) accessLog; }
      // {
        upstreams = lib.mapAttrs (_: t: nixos.nginx.upstream { inherit (t.web) socketPath; }) (
          lib.filterAttrs (_: t: t.nginx.enable) cfg.targets
        );
        virtualHosts = lib.mapAttrs (
          name: t:
          nixos.nginx.vhost { inherit (t.nginx) socketPath; }
          // {
            locations."/" = nixos.nginx.proxy {
              pass = "http://${name}";
              viaSocket = t.nginx.socketPath != "";
            };
          }
        ) (lib.filterAttrs (_: t: t.nginx.enable) cfg.targets);
      }
    );
  };
}
