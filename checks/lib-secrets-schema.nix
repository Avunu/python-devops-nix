# lib.secrets.schema, evaluated for both stub shapes: a grouped product (units
# with roles of their own, like frappe-nix's sites) and a flat one (project
# roles only, like odoo-nix). Each assertion is its own eval check, so a
# failure names itself; this check builds them all.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  stub = import ../tests/secrets.nix { inherit pkgs self; };
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  # Keys need not be real for the schema: it only labels and routes them.
  recipients.tim = "ssh-ed25519 AAAA";
  hostRecipients.prod = "ssh-ed25519 BBBB";

  flatCfg = stub.mkCfg stub.flatSchema {
    inherit recipients hostRecipients;
    backupAccess = {
      enable = true;
      hosts = true;
    };
    extra.s3-writer = {
      format = "env";
      var = "flat_s3_writer";
      hosts = false;
    };
  };

  groupedCfg = stub.mkCfg stub.groupedSchema {
    inherit recipients hostRecipients;
    sites = {
      "erp.example.com" = {
        encryptionKey = true;
        databasePassword = true;
        extraConfig = true;
        developers = true;
      };
      "ops.example.com" = {
        encryptionKey = true;
        databasePassword = false;
        extraConfig = false;
        developers = false;
      };
    };
    extra.api-token = {
      format = "raw";
      var = "stub_api_token";
      hosts = false;
    };
  };

  rules = cfg: builtins.fromJSON (stub.groupedSchema.rulesJSON cfg);
  find = cfg: role: lib.findFirst (x: x.role == role) null cfg.declared;

  pairs = {
    # ── flat: odoo-nix's eval-secrets-schema-* checks, ported ─────────────
    # A secret whose .age file does not exist yet is declared (check-secrets
    # reports it) but left out of agenix-shell, so declaring one and then
    # running `edit-secret` to create it works on a fresh clone.
    flat-declared-vs-shell = {
      expected = {
        declared = [
          "secrets/backup-access.age"
          "secrets/s3-writer.age"
        ];
        shell = [ "flat_backup_access" ];
      };
      actual = {
        declared = map (x: x.relPath) flatCfg.declared;
        shell = lib.attrNames (stub.flatSchema.agenixShellSecrets flatCfg);
      };
    };
    flat-recipients = {
      # developers on the project secret; developers + hosts where hosts = true
      expected = {
        backup = [
          "ssh-ed25519 AAAA tim"
          "ssh-ed25519 BBBB prod"
        ];
        writer = [ "ssh-ed25519 AAAA tim" ];
      };
      actual =
        let
          r = builtins.fromJSON (stub.flatSchema.rulesJSON flatCfg);
        in
        {
          backup = r."secrets/backup-access.age";
          writer = r."secrets/s3-writer.age";
        };
    };
    flat-extra-var = {
      expected = "flat_s3_writer";
      actual = (find flatCfg "s3-writer").var;
    };
    flat-no-groups = {
      expected = {
        group = null;
        unit = null;
      };
      actual = {
        inherit (find flatCfg "backupAccess") group unit;
      };
    };

    # ── grouped: frappe-nix's sites, as a group ──────────────────────────
    grouped-declared = {
      expected = [
        {
          relPath = "secrets/backup-access.age";
          var = "stub_backup_access";
          format = "env";
          role = "backupAccess";
          group = null;
          unit = null;
        }
        {
          relPath = "secrets/erp.example.com/encryption-key.age";
          var = "stub_encryption_key_erp_example_com";
          format = "raw";
          role = "encryptionKey";
          group = "sites";
          unit = "erp.example.com";
        }
        {
          relPath = "secrets/erp.example.com/db-password.age";
          var = "stub_db_password_erp_example_com";
          format = "raw";
          role = "databasePassword";
          group = "sites";
          unit = "erp.example.com";
        }
        {
          relPath = "secrets/erp.example.com/site-config.age";
          var = "stub_site_config_erp_example_com";
          format = "json";
          role = "extraConfig";
          group = "sites";
          unit = "erp.example.com";
        }
        {
          relPath = "secrets/ops.example.com/encryption-key.age";
          var = "stub_encryption_key_ops_example_com";
          format = "raw";
          role = "encryptionKey";
          group = "sites";
          unit = "ops.example.com";
        }
        {
          relPath = "secrets/api-token.age";
          var = "stub_api_token";
          format = "raw";
          role = "api-token";
          group = null;
          unit = null;
        }
      ];
      actual = map (
        x:
        removeAttrs x [
          "file"
          "publicKeys"
        ]
      ) groupedCfg.declared;
    };
    # A unit's secrets are what the deployment host reads, so host keys
    # always apply; `developers = false` keeps the humans out. Project
    # secrets follow their own `hosts`.
    grouped-recipients = {
      expected = {
        backup = [ "ssh-ed25519 AAAA tim" ];
        erp = [
          "ssh-ed25519 AAAA tim"
          "ssh-ed25519 BBBB prod"
        ];
        ops = [ "ssh-ed25519 BBBB prod" ];
        token = [ "ssh-ed25519 AAAA tim" ];
      };
      actual =
        let
          r = rules groupedCfg;
        in
        {
          backup = r."secrets/backup-access.age";
          erp = r."secrets/erp.example.com/db-password.age";
          ops = r."secrets/ops.example.com/encryption-key.age";
          token = r."secrets/api-token.age";
        };
    };
    # Only the files that exist reach agenix-shell, under the exact variable
    # name rather than agenix-shell's punctuation-mangled default.
    grouped-shell = {
      expected = {
        stub_backup_access = {
          name = "stub_backup_access";
          mode = "0400";
        };
        stub_db_password_erp_example_com = {
          name = "stub_db_password_erp_example_com";
          mode = "0400";
        };
      };
      actual = lib.mapAttrs (_: v: removeAttrs v [ "file" ]) (
        stub.groupedSchema.agenixShellSecrets groupedCfg
      );
    };
    grouped-files-are-paths = {
      assertion = lib.all (x: builtins.isPath x.file) groupedCfg.declared;
      message = "every declared secret's file is a path";
    };
    labelled-keeps-a-comment = {
      # In attribute-name order.
      expected = [
        "ssh-ed25519 CCCC carol@laptop"
        "ssh-ed25519 AAAA tim"
      ];
      actual = stub.groupedSchema.labelled {
        tim = "ssh-ed25519 AAAA";
        carol = "ssh-ed25519 CCCC carol@laptop";
      };
    };
    slug = {
      expected = "erp_example_com_x_y";
      actual = stub.groupedSchema.slug "erp.example.com-x y";
    };
    disabled-roles-are-absent = {
      expected = [ ];
      actual = (stub.mkCfg stub.flatSchema { backupAccess.enable = false; }).declared;
    };
  };

  built = lib.mapAttrsToList (name: checks.eval "lib-secrets-schema-${name}") pairs;
in
checks.script "lib-secrets-schema" { } ''
  ${lib.concatMapStrings (drv: ''
    test -e ${drv} && echo "  ok   ${drv.name}" | tee -a "$out"
  '') built}
''
