# Stub products for lib.secrets (PLAN.md, design rule 8), shared by the
# lib-agecheck, lib-secrets-cli, lib-secrets-schema and lib-secrets-module
# checks. Two shapes, the two in use:
#
#   grouped  units with roles of their own, like frappe-nix's sites
#   flat     project-level roles only, like odoo-nix, whose backup-access
#            secret the deployment host reads too (hosts = true)
{ pkgs, self }:
let
  inherit (pkgs) lib;
  inherit (self.lib) secrets;

  # The roles of a unit in the grouped stub: frappe-nix's per-site secrets.
  unitRoles = [
    {
      attr = "encryptionKey";
      file = "encryption-key";
      format = "raw";
      var = "encryption_key";
    }
    {
      attr = "databasePassword";
      file = "db-password";
      format = "raw";
      var = "db_password";
    }
    {
      attr = "extraConfig";
      file = "site-config";
      format = "json";
      var = "site_config";
    }
  ];
in
rec {
  # Fixture keypairs, committed (fixtures/keys/README.md), the same in both
  # products. They have to be known at eval time: the recipient list ends up
  # in a store file the rendered scripts read by path.
  pub = name: lib.head (lib.splitString "\n" (builtins.readFile ./fixtures/keys/${name}.pub));

  grouped = {
    label = "stub-nix";
    optionPath = "stub-nix.secrets";
    varPrefix = "stub";
    groups.sites = {
      roles = unitRoles;
      description = "Per-site secrets.";
    };
  };

  flat = {
    label = "flat-nix";
    optionPath = [
      "flat-nix"
      "secrets"
    ];
    varPrefix = "flat";
    projectRoles = [ (secrets.roles.backupAccess // { hosts = true; }) ];
  };

  groupedSchema = secrets.schema { inherit (grouped) varPrefix groups; };
  flatSchema = secrets.schema { inherit (flat) varPrefix projectRoles; };

  # An evaluated option set, as the module would produce it, for the schema
  # and tools tests that do not go through the module.
  mkCfg =
    schema: overrides:
    let
      cfg = {
        enable = true;
        dir = ./fixtures/secrets;
        relDir = "secrets";
        recipients = { };
        hostRecipients = { };
        backupAccess = {
          enable = true;
          hosts = false;
        };
        extra = { };
        identityPaths = [ "$HOME/.ssh/id_ed25519" ];
      }
      // overrides;
    in
    cfg // { declared = schema.secretList cfg; };

  # The tools for the grouped stub, against a recipient list.
  toolsFor =
    recipients:
    secrets.tools {
      inherit pkgs;
      inherit (grouped) label;
      cfg = mkCfg groupedSchema { inherit recipients; };
      restoreCommand = "stub restore";
      bucketExample = "my-stub-backups";
      verifyListing = verifyListing.sets;
    };

  # The two products' credential checks for setup-backup-access, as each
  # would pass it: count the backups and name the newest, or count the
  # database folders.
  verifyListing = {
    sets = ''
      local listing
      listing="$("$FETCH" list)" || return
      printf '%s backup(s) found; newest is %s\n' \
        "$(printf '%s\n' "$listing" | grep -c .)" "$(printf '%s\n' "$listing" | tail -n1)"
    '';
    folders = ''
      local listing
      listing="$("$FETCH" folders)" || return
      printf 'bucket reachable; %s database folder(s) under the prefix\n' \
        "$(printf '%s\n' "$listing" | grep -c . || true)"
    '';
  };

  # agenix-shell's flake module, reduced to the two options the secrets module
  # sets. The real one is the product's input; the base takes it as an
  # argument and has none of its own (PLAN.md, design rule 3).
  agenix-shell.flakeModules.default = {
    options.agenix-shell = {
      secrets = lib.mkOption {
        type = lib.types.attrsOf (lib.types.attrsOf lib.types.anything);
        default = { };
      };
      identityPaths = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
      };
    };
  };

  # A consumer's flake, evaluated: `config` is the whole flake-parts config,
  # `lib` the flake.lib it outputs.
  evalFlake =
    spec: settings:
    let
      flake = self.inputs.flake-parts.lib.mkFlake { inputs.self = { }; } {
        imports = [
          (secrets.mkModule (spec // { inherit agenix-shell; }))
          {
            debug = true;
            systems = [ ];
          }
          (lib.setAttrByPath (
            if builtins.isList spec.optionPath then spec.optionPath else lib.splitString "." spec.optionPath
          ) settings)
        ];
      };
    in
    {
      inherit (flake.debug) config options;
      lib = flake.lib or { };
    };
}
