# lib.nixos and lib.journald's fields, evaluated in the stub product's NixOS
# module (tests/nixos-stub.nix): the hardening each unit gets, its profiles and
# overrides; the migrate options and the configuration the migrate command is
# given; the socket directories' tmpfiles rules and assertions; the nginx
# configuration text; the journal fields. Pure evaluation, no VM: the flow
# itself runs in lib-nixos-migrate, and the sandbox in lib-nixos-hardening.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  inherit (self.lib) nixos journald;

  eval =
    stub: extra:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        (import ../tests/nixos-stub.nix self)
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          # No root file system or boot loader to assert about.
          boot.isContainer = true;
          services.stub-nix = {
            enable = true;
          }
          // stub;
        }
        extra
      ];
    }).config;

  targets = {
    maria = {
      kind = "mariadb";
      web.socketPath = "/run/stub-nix/maria/web.sock";
      nginx.enable = true;
      nginx.socketPath = "/run/stub-nix/maria/nginx.sock";
    };
    pg = {
      kind = "postgresql";
      web.socketPath = "/run/stub-nix/pg/web.sock";
    };
  };

  config = eval { inherit targets; } { };
  units = config.systemd.services;

  failing = cfg: map (a: a.message) (lib.filter (a: !a.assertion) cfg.assertions);

  # The baseline as a unit gets it, without the keys a test varies.
  baselineOf = unit: lib.getAttrs (lib.attrNames nixos.hardening.baseline) unit.serviceConfig;

  migrateConfig = cfg: target: cfg.services.stub-nix.migrateScripts.${target}.passthru.config;

  pairs = {
    hardening-baseline = {
      expected = nixos.hardening.baseline;
      actual = baselineOf units.stub-web-maria;
    };
    hardening-no-umask = {
      assertion = lib.all (unit: !(units.${unit}.serviceConfig ? UMask)) [
        "stub-web-maria"
        "stub-web-pg"
        "stub-migrate-maria"
        "stub-migrate-pg"
      ];
      message = "no unit-wide UMask: files nginx serves through the group must stay readable (frappe-nix#83, odoo-nix#51)";
    };
    hardening-paths-and-families = {
      expected = {
        web = {
          ReadWritePaths = [ "/run/stub-nix/maria" ];
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
        };
        migrate = {
          ReadWritePaths = [ "/var/lib/stub-nix/maria" ];
          RestrictAddressFamilies = [ "AF_UNIX" ];
        };
      };
      actual = {
        web = lib.getAttrs [
          "ReadWritePaths"
          "RestrictAddressFamilies"
        ] units.stub-web-maria.serviceConfig;
        migrate = lib.getAttrs [
          "ReadWritePaths"
          "RestrictAddressFamilies"
        ] units.stub-migrate-maria.serviceConfig;
      };
    };
    hardening-chromium = {
      expected = {
        MemoryDenyWriteExecute = false;
        RestrictNamespaces = false;
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
          "AF_NETLINK"
        ];
        NoNewPrivileges = true;
        ProtectSystem = "strict";
      };
      actual =
        lib.getAttrs
          [
            "MemoryDenyWriteExecute"
            "RestrictNamespaces"
            "RestrictAddressFamilies"
            "NoNewPrivileges"
            "ProtectSystem"
          ]
          (eval {
            inherit targets;
            profiles = [
              "chromium"
              "wkhtmltopdf"
            ];
          } { }).systemd.services.stub-web-maria.serviceConfig;
    };
    hardening-jit = {
      expected = nixos.hardening.baseline // {
        MemoryDenyWriteExecute = false;
      };
      actual =
        baselineOf
          (eval {
            inherit targets;
            profiles = [ "jit" ];
          } { }).systemd.services.stub-web-maria;
    };
    # A host relaxes one key on one unit without a conflict, and a list it
    # sets adds to the product's.
    hardening-host-override = {
      expected = {
        ProtectHome = "read-only";
        ReadWritePaths = [
          "/run/stub-nix/maria"
          "/srv/uploads"
        ];
      };
      actual =
        lib.getAttrs [ "ProtectHome" "ReadWritePaths" ]
          (eval { inherit targets; } {
            systemd.services.stub-web-maria.serviceConfig = {
              ProtectHome = "read-only";
              ReadWritePaths = [ "/srv/uploads" ];
            };
          }).systemd.services.stub-web-maria.serviceConfig;
    };
    hardening-capabilities-and-overrides = {
      expected = {
        CapabilityBoundingSet = [ "CAP_CHOWN" ];
        PrivateTmp = false;
        NoNewPrivileges = true;
      };
      actual =
        let
          applied = nixos.hardening {
            capabilities = [ "CAP_CHOWN" ];
            overrides.PrivateTmp = false;
          };
        in
        {
          CapabilityBoundingSet = applied.CapabilityBoundingSet.content;
          inherit (applied) PrivateTmp;
          NoNewPrivileges = applied.NoNewPrivileges.content;
        };
    };
    hardening-unknown-profile = {
      assertion =
        !(builtins.tryEval (builtins.deepSeq (nixos.hardening { profiles = [ "chrome" ]; }) true)).success;
      message = "an unknown hardening profile fails evaluation";
    };

    migrate-options = {
      expected = {
        enable = true;
        snapshot = true;
        rollbackOnFailure = true;
        snapshotRetention = 3;
      };
      actual = config.services.stub-nix.migrate;
    };
    migrate-config = {
      expected = {
        label = "stub-migrate";
        target = "maria";
        rollbackOnFailure = true;
        snapshot = {
          dir = "/var/lib/stub-nix/maria/snapshots";
          prefix = "premigrate-maria";
          extension = ".sql.gz";
          retention = 3;
          verifier = true;
        };
        pgVerifier = true;
      };
      actual =
        let
          c = migrateConfig config "maria";
        in
        {
          inherit (c) label target rollbackOnFailure;
          snapshot = {
            inherit (c.snapshot)
              dir
              prefix
              extension
              retention
              ;
            verifier = lib.hasSuffix "verify-mysqldump" c.snapshot.verify;
          };
          pgVerifier = lib.hasSuffix "verify-pg-restore-list" (migrateConfig config "pg").snapshot.verify;
        };
    };
    migrate-without-snapshot = {
      expected = {
        snapshot = null;
        rollbackOnFailure = false;
        retention = 5;
      };
      actual =
        let
          off = eval {
            inherit targets;
            migrate = {
              snapshot = false;
              rollbackOnFailure = false;
            };
          } { };
          kept = eval {
            inherit targets;
            migrate.snapshotRetention = 5;
          } { };
        in
        {
          inherit (migrateConfig off "maria") snapshot rollbackOnFailure;
          retention = (migrateConfig kept "maria").snapshot.retention;
        };
    };
    migrate-needs-snapshot-commands = {
      assertion =
        !(builtins.tryEval
          (nixos.migrate.script {
            inherit pkgs;
            label = "stub-migrate";
            target = "x";
            cfg = { };
            migrate = "/bin/true";
          }).drvPath
        ).success;
      message = "a snapshot without dump, verify and restore fails evaluation";
    };
    migrate-failure-shapes = {
      expected = {
        mariadb = {
          remain = true;
          webRequires = [ ];
        };
        postgresql = {
          remain = false;
          webRequires = [ "stub-migrate-pg.service" ];
        };
      };
      actual = {
        mariadb = {
          remain = units.stub-migrate-maria.serviceConfig.RemainAfterExit;
          webRequires = units.stub-web-maria.requires;
        };
        postgresql = {
          remain = units.stub-migrate-pg.serviceConfig.RemainAfterExit;
          webRequires = units.stub-web-pg.requires;
        };
      };
    };

    unix-socket-tmpfiles = {
      expected = [
        "d /run/stub-nix/maria 0770 stub stub -"
        "d /run/stub-nix/pg 0750 stub stub -"
      ];
      actual = lib.filter (lib.hasPrefix "d /run/stub-nix") config.systemd.tmpfiles.rules;
    };
    unix-socket-assertions = {
      expected = [
        "services.stub-nix.targets.\"bad\".web.socketPath must be an absolute path inside its own directory (e.g. /run/<name>/<socket>.sock), not directly in /run: the directory's mode is what keeps the socket private."
        "services.stub-nix.targets.\"bad\".nginx.socketPath must be an absolute path inside its own directory (e.g. /run/stub-nix/nginx.sock), not directly in /run: the directory's mode is what keeps the socket private."
        "services.stub-nix.targets.\"relative\".web.socketPath must be an absolute path inside its own directory (e.g. /run/<name>/<socket>.sock), not directly in /run: the directory's mode is what keeps the socket private."
      ];
      actual = failing (
        eval {
          targets = targets // {
            bad = {
              kind = "mariadb";
              web.socketPath = "/run/web.sock";
              nginx.socketPath = "/nginx.sock";
            };
            relative = {
              kind = "mariadb";
              web.socketPath = "run/web.sock";
            };
          };
        } { }
      );
    };
    unix-socket-ok = {
      expected = [ ];
      actual = failing config;
    };
    unix-socket-directories = {
      expected = [
        "/run/a"
        "/run/b"
      ];
      actual =
        (nixos.unixSocket {
          user = "u";
          group = "g";
          sockets = [
            {
              option = "x";
              path = "/run/b/one.sock";
            }
            {
              option = "y";
              path = "/run/a/two.sock";
            }
            {
              option = "z";
              path = "/run/b/three.sock";
            }
            {
              option = "unset";
              path = "";
            }
            {
              option = "null";
              path = null;
            }
          ];
        }).directories;
    };

    nginx-text = {
      expected = {
        commonHttpConfig = ''
          log_format journal_json escape=json '{"time":"$time_iso8601","site":"$host","method":"$request_method","uri":"$request_uri","status":$status,"bytes":$body_bytes_sent,"request_time":$request_time,"upstream_time":"$upstream_response_time","remote_addr":"$remote_addr","user_agent":"$http_user_agent","referer":"$http_referer"}';
          access_log syslog:server=unix:/dev/log,tag=nginx_access,nohostname journal_json;
        '';
        logError = "syslog:server=unix:/dev/log,tag=nginx,nohostname error";
        realIp = true;
        socketHeaders = true;
        listen = [ "unix:/run/stub-nix/maria/nginx.sock" ];
      };
      actual = {
        inherit (config.services.nginx) commonHttpConfig logError;
        realIp = lib.hasInfix ''
          set_real_ip_from unix:;
          real_ip_header CF-Connecting-IP;
        '' config.services.nginx.virtualHosts.maria.extraConfig;
        socketHeaders =
          lib.hasInfix "proxy_set_header X-Forwarded-Proto https;"
            config.services.nginx.virtualHosts.maria.locations."/".extraConfig;
        listen = map (l: l.addr) config.services.nginx.virtualHosts.maria.listen;
      };
    };
    nginx-access-log-off = {
      expected = ''
        access_log off;
      '';
      actual =
        (eval {
          inherit targets;
          logging.accessLog = false;
        } { }).services.nginx.commonHttpConfig;
    };
    nginx-fields = {
      assertion = lib.all (
        field: lib.hasInfix "\"${field}\":" nixos.nginx.journalJsonFormat
      ) nixos.nginx.journalJsonFields;
      message = "journalJsonFields names every field of journalJsonFormat";
    };
    nginx-service-defaults = {
      expected = {
        enable = true;
        recommendedProxySettings = true;
        recommendedGzipSettings = true;
        upstream = [ "unix:/run/stub-nix/maria/web.sock" ];
        proxyPass = "http://maria";
        recommendedProxySettingsAtSocket = false;
      };
      actual = {
        inherit (config.services.nginx) enable recommendedProxySettings recommendedGzipSettings;
        upstream = lib.attrNames config.services.nginx.upstreams.maria.servers;
        inherit (config.services.nginx.virtualHosts.maria.locations."/") proxyPass;
        recommendedProxySettingsAtSocket =
          config.services.nginx.virtualHosts.maria.locations."/".recommendedProxySettings;
      };
    };
    nginx-proxy-tcp = {
      # On TCP the recommended settings stand and nothing is added in front
      # of the location's own lines.
      expected = {
        proxyPass = "http://app";
        proxyWebsockets = true;
        recommendedProxySettings = true;
        extraConfig = "proxy_read_timeout 1h;\n";
      };
      actual = nixos.nginx.proxy {
        pass = "http://app";
        viaSocket = false;
        websockets = true;
        extraConfig = "proxy_read_timeout 1h;\n";
      };
    };
    nginx-proxy-socket = {
      # The socket headers first, then the location's own lines.
      expected = nixos.nginx.socketProxyHeaders + "proxy_redirect off;\n";
      actual =
        (nixos.nginx.proxy {
          pass = "http://app";
          viaSocket = true;
          extraConfig = "proxy_redirect off;\n";
        }).extraConfig;
    };
    nginx-vhost-tcp = {
      expected = {
        listen = [ ];
        extraConfig = "client_max_body_size 64m;\n";
      };
      actual = nixos.nginx.vhost { extraConfig = "client_max_body_size 64m;\n"; };
    };
    nginx-internal = {
      expected = {
        alias = "/var/lib/app/files/";
        extraConfig = "internal;\ntry_files $uri =404;\n";
      };
      actual = nixos.nginx.internal {
        alias = "/var/lib/app/files/";
        extraConfig = "try_files $uri =404;\n";
      };
    };
    nginx-upstream-tcp = {
      expected.servers."127.0.0.1:8069" = { };
      actual = nixos.nginx.upstream {
        socketPath = "";
        port = 8069;
      };
    };
    nginx-error-log-is-a-default = {
      expected = "stderr";
      actual =
        (eval { inherit targets; } { services.nginx.logError = "stderr"; }).services.nginx.logError;
    };

    journald-fields = {
      expected = {
        web = {
          LogExtraFields = [
            "APP_SERVICE=web"
            "APP_SITE=maria"
          ];
          SyslogIdentifier = "stub-web";
        };
        migrate = {
          LogExtraFields = [
            "APP_SERVICE=migrate"
            "APP_SITE=pg"
          ];
          SyslogIdentifier = "stub-migrate";
        };
        db = {
          LogExtraFields = [ "APP_SERVICE=db" ];
          hasIdentifier = false;
        };
        nginx = [ "APP_SERVICE=nginx" ];
        worker = journald.fields {
          prefix = "stub";
          role = "worker";
          site = "s";
          identifier = "stub-worker-short";
        };
      };
      actual = {
        web = lib.getAttrs [ "LogExtraFields" "SyslogIdentifier" ] units.stub-web-maria.serviceConfig;
        migrate = lib.getAttrs [ "LogExtraFields" "SyslogIdentifier" ] units.stub-migrate-pg.serviceConfig;
        db = {
          inherit (units.mysql.serviceConfig) LogExtraFields;
          hasIdentifier = (units.mysql.serviceConfig.SyslogIdentifier or null) == "stub-db";
        };
        nginx = units.nginx.serviceConfig.LogExtraFields;
        worker = {
          LogExtraFields = [
            "APP_SERVICE=worker"
            "APP_SITE=s"
          ];
          SyslogIdentifier = "stub-worker-short";
        };
      };
    };
    journald-unknown-role = {
      assertion =
        !(builtins.tryEval (
          builtins.deepSeq (journald.fields {
            prefix = "stub";
            role = "cron";
          }) true
        )).success;
      message = "a role outside the written list fails evaluation";
    };
  };

  built = lib.mapAttrsToList (name: checks.eval "lib-nixos-${name}") pairs;
in
if pkgs.stdenv.hostPlatform.isLinux then
  checks.script "lib-nixos" { } ''
    ${lib.concatMapStrings (drv: ''
      test -e ${drv} && echo "  ok   ${drv.name}" | tee -a "$out"
    '') built}
  ''
else
  checks.script "lib-nixos" { } ''echo "NixOS evaluation is Linux only" > "$out"''
