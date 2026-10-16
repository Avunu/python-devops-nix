# lib.secrets.mkModule, evaluated in a stub consumer's flake for both shapes
# (grouped and flat), and the rest of lib.secrets.tools: which scripts it
# offers, and setup-backup-access built (so shellcheck runs on it) with each
# product's credential check, refusing to run without a terminal.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  stub = import ../tests/secrets.nix { inherit pkgs self; };
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  alice = stub.pub "alice";
  bob = stub.pub "bob";

  grouped = stub.evalFlake (stub.grouped // { libAliases = [ "stubSecrets" ]; }) {
    dir = ../tests/fixtures/secrets;
    recipients.alice = alice;
    hostRecipients.host = bob;
    sites."erp.example.com".extraConfig = false;
  };
  groupedCfg = grouped.config.stub-nix.secrets;

  flat = stub.evalFlake stub.flat {
    dir = ../tests/fixtures/secrets;
    recipients.alice = alice;
    hostRecipients.host = bob;
  };

  off = stub.evalFlake stub.flat { dir = ../tests/fixtures/secrets; };

  pairs = {
    grouped-options = {
      expected = [
        "backupAccess"
        "declared"
        "dir"
        "enable"
        "extra"
        "hostRecipients"
        "identityPaths"
        "recipients"
        "relDir"
        "sites"
      ];
      actual = lib.attrNames grouped.options.stub-nix.secrets;
    };
    grouped-unit-options = {
      expected = {
        databasePassword = true;
        developers = true;
        encryptionKey = true;
        extraConfig = false;
      };
      actual = groupedCfg.sites."erp.example.com";
    };
    grouped-output = {
      expected = {
        recipients = [
          "${alice}"
          "${bob}"
        ];
        files = [
          "stub_backup_access"
          "stub_db_password_erp_example_com"
          "stub_encryption_key_erp_example_com"
        ];
        sites."erp.example.com" = [
          "databasePassword"
          "encryptionKey"
        ];
      };
      actual = {
        inherit (grouped.lib.secrets) recipients;
        files = lib.attrNames grouped.lib.secrets.files;
        sites = lib.mapAttrs (_: lib.attrNames) grouped.lib.secrets.sites;
      };
    };
    grouped-alias = {
      assertion = grouped.lib.stubSecrets == grouped.lib.secrets;
      message = "libAliases name the same output";
    };
    grouped-agenix-shell = {
      expected = {
        identityPaths = [
          "$HOME/.ssh/id_ed25519"
          "$HOME/.ssh/id_rsa"
        ];
        secrets = [
          "stub_backup_access"
          "stub_db_password_erp_example_com"
        ];
      };
      actual = {
        inherit (grouped.config.agenix-shell) identityPaths;
        secrets = lib.attrNames grouped.config.agenix-shell.secrets;
      };
    };
    flat-output = {
      expected = {
        keys = [
          "files"
          "recipients"
        ];
        # The backup-access secret is the server's too: hosts = true by the
        # product's role.
        backupHosts = true;
        relDir = "secrets";
      };
      actual = {
        keys = lib.attrNames flat.lib.secrets;
        backupHosts = flat.config.flat-nix.secrets.backupAccess.hosts;
        inherit (flat.config.flat-nix.secrets) relDir;
      };
    };
    flat-extra-var-default = {
      expected = "flat_s3_writer";
      actual =
        (stub.evalFlake stub.flat {
          dir = ../tests/fixtures/secrets;
          recipients.alice = alice;
          extra.s3-writer = { };
        }).config.flat-nix.secrets.extra.s3-writer.var;
    };
    off-without-recipients = {
      expected = {
        enable = false;
        lib = { };
        declared = [ ];
        secrets = { };
      };
      actual = {
        inherit (off.config.flat-nix.secrets) enable declared;
        inherit (off) lib;
        inherit (off.config.agenix-shell) secrets;
      };
    };
    tools-scripts = {
      expected = {
        without = [
          "check-secrets"
          "edit-secret"
          "rekey-secrets"
        ];
        withFetch = [
          "check-secrets"
          "edit-secret"
          "rekey-secrets"
          "setup-backup-access"
        ];
        off = [ ];
        names = [
          "backup-access"
          "erp.example.com/encryption-key"
        ];
      };
      actual =
        let
          tools =
            cfg: verifyListing:
            self.lib.secrets.tools {
              inherit pkgs cfg verifyListing;
              label = "stub-nix";
              restoreCommand = "stub restore";
              bucketExample = "my-stub-backups";
            };
          t = tools (stub.mkCfg stub.groupedSchema {
            recipients.alice = alice;
            sites."erp.example.com" = {
              encryptionKey = true;
              databasePassword = false;
              extraConfig = false;
              developers = true;
            };
          }) stub.verifyListing.sets;
        in
        {
          without = lib.attrNames (t.scripts { });
          withFetch = lib.attrNames (t.scripts { fetch = "/bin/false"; });
          off = lib.attrNames (
            (tools { enable = false; } stub.verifyListing.sets).scripts { fetch = "/bin/false"; }
          );
          names = map (x: x.name) t.names;
        };
    };
  };

  built = lib.mapAttrsToList (name: checks.eval "lib-secrets-module-${name}") pairs;

  setupFor =
    verifyListing:
    lib.getExe (
      (self.lib.secrets.tools {
        inherit pkgs verifyListing;
        cfg = stub.mkCfg stub.groupedSchema { recipients.alice = alice; };
        label = "stub-nix";
        restoreCommand = "stub restore";
        bucketExample = "my-stub-backups";
      }).setupBackupAccess
        "/bin/false"
    );
in
checks.script "lib-secrets-module" { } ''
  ${lib.concatMapStrings (drv: ''
    test -e ${drv} && echo "  ok   ${drv.name}" | tee -a "$out"
  '') built}

  for setup in ${setupFor stub.verifyListing.sets} ${setupFor stub.verifyListing.folders}; do
    rc=0
    "$setup" </dev/null >log 2>&1 || rc=$?
    if [ "$rc" -ne 1 ] || ! grep -qx 'stub-nix: setup-backup-access needs an interactive terminal.' log; then
      echo "  FAIL $setup without a terminal: rc=$rc"; cat log; exit 1
    fi
    grep -q 'edit-secret backup-access' log
  done
  echo "  ok   setup-backup-access builds (shellcheck) with both credential checks, and needs a terminal" | tee -a "$out"
''
