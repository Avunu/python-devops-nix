# The secrets toolkit (PLAN.md, Phase 2): age ciphertext committed to the
# repository, agenix-shell decryption in the development shell, generated agenix
# rules, the recipient-drift check, and the commands around them.
#
#   schema    the pure derivation of a product's secret set
#   mkModule  the flake-parts module that declares the options and wires
#             agenix-shell, at the product's option path
#   tools     the build-time pieces: agecheck, the rules file, the dev-shell
#             scripts and `setup-backup-access`
#   roles     the standard roles every product declares
{ lib }:

let
  inherit (lib)
    concatMap
    filter
    literalExpression
    mkOption
    optionals
    types
    ;

  pythonLib = import ./python { inherit lib; };

  # "frappe-nix.secrets" or [ "frappe-nix" "secrets" ] -> [ "frappe-nix" "secrets" ]
  optionPathOf = p: if builtins.isList p then p else lib.splitString "." p;
  toDotted = p: lib.concatStringsSep "." (optionPathOf p);
in
rec {
  # A role is a *shape*, not just a name:
  #
  #   attr         the option it is declared under (`<path>.backupAccess`)
  #   file         the .age file's name, without the extension. Not
  #                configurable: agenix resolves a rule by the literal path
  #                string and writes output relative to $PWD, so the .age
  #                location and the rules key have to stay in lockstep.
  #   format       what the dev shell does with the plaintext: env | raw | json
  #   var          the shell variable, after the product's `varPrefix_`
  #   hosts        project roles: whether `hostRecipients` read it by default
  #   default      group roles: whether a unit declares it by default
  #   description  the option's description
  roles = {
    backupAccess = {
      attr = "backupAccess";
      file = "backup-access";
      format = "env";
      var = "backup_access";
      hosts = false;
      description = ''
        Declare `<dir>/backup-access.age`: object-store credentials for
        fetching production backups, as a shell env-file (also valid systemd
        `EnvironmentFile` syntax when values are unquoted or single-quoted).

            BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
            BACKUPS_ACCESS_KEY=…
            BACKUPS_SECRET_KEY=…
            BACKUPS_BUCKET=my-backups
            BACKUPS_PREFIX=Backups/      # optional

        Bucket and prefix live in the secret rather than in Nix on purpose:
        they are per-deployment facts that change together with the
        credentials, and keeping them together means restoring needs no Nix
        configuration at all. `setup-backup-access` writes it.
      '';
    };
  };

  # The one derivation of a product's secret set.
  #
  # Called by the module (to configure agenix-shell), by the product's devenv
  # module (through `declared`, to build the rules file and the scripts) and by
  # the checks, so the .age paths, the rules file and the shell variable names
  # cannot drift apart.
  #
  # Pure: no pkgs, no IO, and deliberately no `self`. flake-parts' `self` is the
  # flake's own output set, so reading it from something that *writes* a flake
  # output (flake.lib.secrets) is an infinite recursion. Repo-relative paths
  # therefore come from `relDir`, a plain string, rather than from stripping a
  # prefix off the flake root.
  #
  #   varPrefix     the product's shell-variable prefix ("odoo" -> $odoo_backup_access)
  #   projectRoles  roles declared once per project (default: backupAccess)
  #   groups        named sets of units, each unit with roles of its own:
  #                 { <option> = { roles = [ role… ]; hosts ? true; }; }. The
  #                 units are read from cfg.<option>, an attrset of
  #                 { <role attr> = bool; developers = bool; }.
  schema =
    {
      varPrefix,
      projectRoles ? [ roles.backupAccess ],
      groups ? { },
      ...
    }:
    rec {
      inherit projectRoles groups varPrefix;

      # A shell-variable-safe rendering of a unit or secret name.
      # ("erp.example.com" -> "erp_example_com")
      slug = lib.replaceStrings [ "." "-" " " ] [ "_" "_" "_" ];

      # `recipients` is an attrset so every key has a name. Carry that name into
      # the key line as an SSH comment: age parses authorized_keys format and
      # ignores the comment, and the recipient tag is derived from the key blob
      # alone, so the generated rules file and every agecheck message can say
      # "gideon" instead of "AAAAC3NzaC1l…". Cosmetic, and it cannot change what
      # a file is encrypted to.
      labelled = lib.mapAttrsToList (
        name: key: if lib.length (lib.splitString " " key) >= 3 then key else "${key} ${name}"
      );

      # Every declared secret, normalised:
      #
      #   relPath    repo-relative .age path: the agenix rules key, because
      #              agenix looks a rule up by the literal string you pass it
      #              and writes its output relative to $PWD
      #   file       store path agenix-shell reads
      #   var        agenix-shell secret name; $<var> and $<var>_PATH follow
      #   format     env | raw | json
      #   role       which standard role, or the `extra` attribute name
      #   group      owning group's option name, or null
      #   unit       owning unit's name within the group, or null
      #   publicKeys recipients for this particular secret
      secretList =
        cfg:
        let
          mk =
            {
              subpath,
              var,
              format,
              role,
              group ? null,
              unit ? null,
              developers,
              hosts,
            }:
            {
              inherit
                var
                format
                role
                group
                unit
                ;
              relPath = "${cfg.relDir}/${subpath}";
              file = cfg.dir + "/${subpath}";
              publicKeys =
                optionals developers (labelled cfg.recipients) ++ optionals hosts (labelled cfg.hostRecipients);
            };

          project = map (
            r:
            mk {
              subpath = "${r.file}.age";
              var = "${varPrefix}_${r.var}";
              inherit (r) format;
              inherit (cfg.${r.attr}) hosts;
              role = r.attr;
              developers = true;
            }
          ) (filter (r: cfg.${r.attr}.enable) projectRoles);

          grouped = concatMap (
            group:
            let
              spec = groups.${group};
              units = cfg.${group} or { };
            in
            concatMap (
              unit:
              map (
                r:
                mk {
                  subpath = "${unit}/${r.file}.age";
                  var = "${varPrefix}_${r.var}_${slug unit}";
                  inherit (r) format;
                  role = r.attr;
                  inherit group unit;
                  inherit (units.${unit}) developers;
                  # A unit's secrets are what the deployment host consumes, so
                  # host keys apply unless the group says otherwise;
                  # `developers` gates the humans.
                  hosts = spec.hosts or true;
                }
              ) (filter (r: units.${unit}.${r.attr}) spec.roles)
            ) (lib.attrNames units)
          ) (lib.attrNames groups);

          extra = lib.mapAttrsToList (
            name: e:
            mk {
              subpath = "${name}.age";
              inherit (e) format var hosts;
              role = name;
              developers = true;
            }
          ) cfg.extra;
        in
        project ++ grouped ++ extra;

      # { <var> = { name; file; mode; }; }: agenix-shell.secrets.
      #
      # `name` is set explicitly rather than left to agenix-shell's toShellVar,
      # which maps every hyphen to "__": the variable a script reads would
      # otherwise depend on the attribute key's punctuation.
      #
      # Secrets whose .age file does not exist yet are left out, deliberately.
      # agenix-shell types `file` as a path, so Nix copies it into the store
      # during evaluation and a missing one is an *evaluation* error, which
      # would make a freshly declared secret break the whole flake, and with it
      # `edit-secret`, the command that creates the file. Declaring a secret and
      # then creating it is the documented first run, so it has to work.
      #
      # They stay in `declared`, so `check-secrets` still reports them as
      # missing and says which command writes them. This is the one place the
      # two lists differ.
      agenixShellSecrets =
        cfg:
        lib.listToAttrs (
          map (
            s:
            lib.nameValuePair s.var {
              name = s.var;
              inherit (s) file;
              mode = "0400";
            }
          ) (filter (s: builtins.pathExists s.file) (secretList cfg))
        );

      # What agecheck compares against, and what the rules file is rendered
      # from: { "<relPath>" = [ <pubkey> … ]; }
      rulesJSON = cfg: rulesJSONOf (secretList cfg);
    };

  rulesJSONOf =
    declared:
    builtins.toJSON (lib.listToAttrs (map (s: lib.nameValuePair s.relPath s.publicKeys) declared));

  # The flake-parts module: the option tree at `optionPath`, agenix-shell wired
  # to it, and the secret set as a flake output.
  #
  #   optionPath    where the options live: "odoo-nix.secrets"
  #   label         the product's name, in descriptions
  #   varPrefix     as for `schema`
  #   projectRoles  as for `schema`
  #   groups        as for `schema`, plus, per group, the option's
  #                 `description` and `example`, and per role a `description`
  #                 and `default`
  #   agenix-shell  the product's agenix-shell input. Taken as an argument, not
  #                 an input of this flake (PLAN.md, design rule 3). Its module
  #                 is imported here, so a consumer declares only the
  #                 product's options; flake-parts deduplicates it by key if
  #                 the product imports it too.
  #   libName       the flake output: flake.lib.<libName>
  #   libAliases    further names for the same output, for a rename's
  #                 deprecation window (flake.lib.frappeSecrets, say)
  #
  # TOP-LEVEL, not perSystem: agenix-shell.secrets is a top-level option, and
  # recipients, .age paths and audiences are facts about the project, not about
  # one system. Top level says *who* and *what*; the product's perSystem devenv
  # module says *how the shell behaves*.
  mkModule =
    {
      optionPath,
      label,
      varPrefix,
      projectRoles ? [ roles.backupAccess ],
      groups ? { },
      agenix-shell,
      libName ? "secrets",
      libAliases ? [ ],
    }:
    { config, ... }:
    let
      path = optionPathOf optionPath;
      cfg = lib.getAttrFromPath path config;
      s = schema { inherit varPrefix projectRoles groups; };

      sshKey =
        types.strMatching "^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/]+=*( .*)?$"
        // {
          name = "sshPublicKey";
          description = "SSH public key (authorized_keys format)";
        };

      projectRoleOptions = r: {
        enable = mkOption {
          type = types.bool;
          default = cfg.enable;
          defaultText = literalExpression "secrets.enable";
          description = r.description or "Declare `<dir>/${r.file}.age`.";
        };

        hosts = mkOption {
          type = types.bool;
          default = r.hosts or false;
          description = "Also encrypt this secret to `hostRecipients`.";
        };
      };

      unitModule =
        spec:
        types.submodule {
          options =
            lib.listToAttrs (
              map (
                r:
                lib.nameValuePair r.attr (mkOption {
                  type = types.bool;
                  default = r.default or true;
                  description = r.description or "Declare `<dir>/<unit>/${r.file}.age`.";
                })
              ) spec.roles
            )
            // {
              developers = mkOption {
                type = types.bool;
                default = true;
                description =
                  spec.developersDescription or ''
                    Let `recipients` read this unit's secrets, not just `hostRecipients`.
                    Turning it off gives a capability tier: someone who can fetch and
                    restore a backup but cannot read the credentials stored inside it.
                  '';
              };
            };
        };

      groupOptions = lib.mapAttrs (
        name: spec:
        mkOption (
          {
            type = types.attrsOf (unitModule spec);
            default = { };
            description = spec.description or "Secrets per ${name} entry.";
          }
          // lib.optionalAttrs (spec ? example) { inherit (spec) example; }
        )
      ) groups;

      extraModule = types.submodule (
        { name, ... }:
        {
          options = {
            format = mkOption {
              type = types.enum [
                "env"
                "raw"
                "json"
              ];
              default = "env";
              description = ''
                How the dev shell consumes the plaintext:

                `env`  — `KEY=value` lines, sourced with `set -a` so each becomes a
                         variable. The file is executed by the shell, so it must be
                         shell syntax.
                `raw`  — a single value; `$<var>` is it.
                `json` — a JSON object; left on disk, `$<var>_PATH` points at it.
              '';
            };

            var = mkOption {
              type = types.str;
              default = "${varPrefix}_${s.slug name}";
              defaultText = literalExpression "\"${varPrefix}_\${name}\"";
              description = "The shell variable; `$<var>_PATH` follows.";
            };

            hosts = mkOption {
              type = types.bool;
              default = false;
              description = "Also encrypt this secret to `hostRecipients`.";
            };
          };
        }
      );

      output = {
        recipients = s.labelled cfg.recipients ++ s.labelled cfg.hostRecipients;

        # { "<shell var>" = <path to .age>; }: every declared secret, flat.
        files = lib.listToAttrs (map (x: lib.nameValuePair x.var x.file) cfg.declared);
      }
      # Per group: { "<unit>" = { <role attr> = <path to .age>; }; }
      // lib.mapAttrs (
        group: _:
        lib.mapAttrs (
          unit: _:
          lib.listToAttrs (
            map (x: lib.nameValuePair x.role x.file) (
              filter (x: x.group == group && x.unit == unit) cfg.declared
            )
          )
        ) cfg.${group}
      ) groups;
    in
    {
      imports = [ agenix-shell.flakeModules.default ];

      options = lib.setAttrByPath path (
        {
          enable = mkOption {
            type = types.bool;
            default = cfg.recipients != { };
            defaultText = literalExpression "recipients != { }";
            description = "Wire agenix and agenix-shell into this ${label} project.";
          };

          dir = mkOption {
            type = types.path;
            example = literalExpression "./secrets";
            description = ''
              Directory holding this project's `.age` files. Write it as a path
              literal relative to your `flake.nix`, e.g. `dir = ./secrets;`.

              The `.age` files are **meant to be committed**: they are age
              ciphertext, and a flake's source tree is exactly its git-tracked
              files, so an untracked one is invisible to the build and the shell
              reports it missing.
            '';
          };

          relDir = mkOption {
            type = types.str;
            default = baseNameOf (toString cfg.dir);
            defaultText = literalExpression "baseNameOf dir";
            description = ''
              The same directory, relative to the project root.

              agenix keys its rules by the literal path string and writes output
              relative to `$PWD`, so the rules file has to name
              `secrets/foo.age`, not a `/nix/store` path. That cannot be derived
              from `dir` by stripping the flake root: flake-parts' `self` is the
              flake's own output set, and this module writes one, so reading it
              here would be an infinite recursion.

              Override only if `dir` is nested (`dir = ./nix/secrets;` needs
              `relDir = "nix/secrets";`).
            '';
          };

          recipients = mkOption {
            type = types.attrsOf sshKey;
            default = { };
            example = literalExpression ''{ alice = "ssh-ed25519 AAAAC3Nza…"; }'';
            description = ''
              Public keys of the people who may read this project's secrets.

              The single source of truth. The agenix rules file is generated from
              it (there is no `secrets.nix` to edit), and `check-secrets` verifies
              that every `.age` file is actually encrypted to exactly these keys.
              A rotated key therefore cannot sit in the rules while the ciphertext
              still names the key it replaced.

              Attribute names are labels: they are carried into the key line as
              an SSH comment so error messages can name a person instead of a
              base64 blob.

              After changing this, run `rekey-secrets`.
            '';
          };

          hostRecipients = mkOption {
            type = types.attrsOf sshKey;
            default = { };
            example = literalExpression ''{ appserver = "ssh-ed25519 AAAAC3Nza…"; }'';
            description = ''
              Public keys of deployment hosts. Added to every secret the
              deployment consumes (the units of each group, and any secret with
              `hosts = true`), but not to developer-only secrets.

              A host key here is what lets the deployment read the same
              ciphertext the developers do, instead of a second copy in the
              server repository that has to be rotated in lockstep and silently
              drifts when it is not.
            '';
          };

          identityPaths = mkOption {
            type = types.listOf types.str;
            default = [
              "$HOME/.ssh/id_ed25519"
              "$HOME/.ssh/id_rsa"
            ];
            description = ''
              Private keys tried when decrypting, in order. Forwarded to
              `agenix-shell.identityPaths`. Shell strings, expanded at runtime,
              which is why the dev shell needs `--no-pure-eval`.
            '';
          };

          extra = mkOption {
            type = types.attrsOf extraModule;
            default = { };
            description = "Additional project-specific secrets.";
          };

          # Read-only, for the product's devenv module and the checks.
          declared = mkOption {
            type = types.listOf (types.attrsOf types.unspecified);
            internal = true;
            readOnly = true;
            default = if cfg.enable then s.secretList cfg else [ ];
            description = "Normalised secret list; see lib.secrets.schema.";
          };
        }
        // lib.listToAttrs (map (r: lib.nameValuePair r.attr (projectRoleOptions r)) projectRoles)
        // groupOptions
      );

      # `mkIf` goes on each attribute, NOT around the whole `config`.
      #
      # `cfg.enable` defaults to `recipients != {}`, and `recipients` is declared
      # in this very module. Wrapping the whole config in `mkIf cfg.enable` means
      # the module system has to force the condition just to learn which options
      # this module defines, which means gathering the definitions of
      # `recipients`, which means evaluating this `config`. Infinite recursion,
      # surfacing as a `_module.freeformType` loop several layers away.
      config = {
        agenix-shell = lib.mkIf cfg.enable {
          inherit (cfg) identityPaths;
          secrets = s.agenixShellSecrets cfg;
        };

        # Exposed so a deployment consumes the very same ciphertext the project
        # declares, instead of a second copy that has to be rotated in lockstep:
        #
        #   age.secrets = lib.mapAttrs (_: f: { file = f; mode = "0400"; })
        #     inputs.myproject.lib.secrets.files;
        #
        # Under `lib` because it is a recognised free-form flake output; an
        # unknown one draws a warning from `nix flake check` on every run.
        # `optionalAttrs`, not `mkIf`: free-form flake outputs are typed `raw`,
        # so the module system would hand an `mkIf` straight through and the
        # consumer would read `{ _type = "if"; … }`.
        flake.lib = lib.optionalAttrs cfg.enable (lib.genAttrs ([ libName ] ++ libAliases) (_: output));
      };
    };

  # The build-time pieces, for the product's perSystem devenv module.
  #
  #   cfg              the evaluated options (config.<optionPath>)
  #   label            the product's name: messages, store names, binaries
  #                    (<label>-agecheck, <label>-setup-backup-access)
  #   optionPath       where the options live, for messages (default
  #                    "<label>.secrets")
  #   restoreCommand   the command that restores from a backup ("odoo db restore")
  #   bucketExample    the placeholder bucket name setup-backup-access shows
  #   verifyListing    bash, the body of a function setup-backup-access runs to
  #                    confirm the credentials reach the product's backups. It
  #                    runs with BACKUPS_* exported and $FETCH set to the fetch
  #                    command, prints a one-line summary on success, and
  #                    returns non-zero (with the reason on stderr) otherwise.
  #   python           the interpreter agecheck and the secret commands run
  #                    on (setup-backup-access with lib.python.rich for it)
  tools =
    {
      pkgs,
      cfg,
      label,
      optionPath ? "${label}.secrets",
      restoreCommand,
      bucketExample,
      verifyListing,
      python ? pkgs.python3,
    }:
    let
      enabled = cfg.enable or false;
      option = toDotted optionPath;

      # ragenix, not ryantm/agenix: it is in nixpkgs (so no new flake input),
      # it is a drop-in (same RULES env var, same -e/-r/-d/-i), and it ships an
      # `agenix` symlink. The real agenix as an input would drag nix-darwin and
      # home-manager into every consumer's lock, because its outputs
      # destructure them positionally and they therefore cannot be followed
      # away.
      cli = pkgs.ragenix;
      agenixBin = lib.getExe' cli "agenix";

      # The declared recipients, as JSON: { "<repo-relative .age path>": [key…] }.
      # Everything else is derived from this: the checker reads it directly,
      # and the rules file agenix reads is rendered from it at runtime (below).
      rulesJSON = pkgs.writeText "${label}-secrets.json" (rulesJSONOf cfg.declared);

      # Shell snippet: write an agenix rules file to $RULES, with **absolute**
      # paths.
      #
      # It cannot be a store file with repo-relative keys, which is the obvious
      # design and was the first one tried. ragenix resolves a rule key relative
      # to the *rules file's own directory*, so a store-path RULES turns
      # "secrets/backup-access.age" into "/nix/store/secrets/backup-access.age",
      # which does not exist. `agenix -e` then reports no rule for the file, and
      # `agenix -r` skips it as "does not exist, ignored" and exits 0, so a
      # rekey silently does nothing at all. (ryantm/agenix resolves the same key
      # against $PWD instead. Absolute paths are correct for both.)
      #
      # Rendered at runtime because the worktree path is not knowable at eval
      # time: a flake sees its own source as a store path. The script has it as
      # $REPO_ROOT, the git worktree.
      #
      # The commands here render it themselves (secret_tools.render_rules);
      # this snippet is the same renderer, for a product script that runs
      # agenix on its own.
      writeRules = ''
        __secrets_rules_dir="$(mktemp -d)"
        trap 'rm -rf "$__secrets_rules_dir"' EXIT
        RULES="$__secrets_rules_dir/secrets.nix"
        export RULES
        ${secretsBin} rules "$REPO_ROOT" > "$RULES"
      '';

      agecheck = pythonLib.command {
        inherit pkgs python;
        name = "${label}-agecheck";
        modules = lib.fileset.toSource {
          root = ../python;
          fileset = ../python/agecheck.py;
        };
        imports = "import agecheck";
        main = "agecheck.main(sys.argv, label=${builtins.toJSON label}, recipients_option=${builtins.toJSON "${option}.recipients"})";
      };
      agecheckBin = lib.getExe agecheck;

      # The commands with logic in them (python/secret_tools.py; PLAN.md,
      # design rule 5): edit-secret, rekey-secrets, setup-backup-access and the
      # rules renderer. Their configuration is a JSON file the tool reads into
      # a typed Config, failing on an unknown or missing key, rather than
      # values spliced into code.
      toolConfig = {
        inherit label option;
        inherit (cfg) relDir;
        rules = "${rulesJSON}";
        # Shell strings ("$HOME/.ssh/id_ed25519"), expanded by the tool.
        identityPaths = cfg.identityPaths or [ ];
        secretNames = map (x: x.name) names;
        agenix = agenixBin;
        agecheck = agecheckBin;
        # For reading an existing secret back: ragenix implements only
        # --edit/--rekey/--schema, so there is no `agenix -d`.
        rage = lib.getExe pkgs.rage;
        git = lib.getExe pkgs.git;
      };

      toolCommand =
        {
          name,
          config,
          python,
          tool ? null,
        }:
        let
          configFile = pkgs.writeText "${name}.json" (builtins.toJSON config);
          argv = if tool == null then "sys.argv[1:]" else "[${builtins.toJSON tool}, *sys.argv[1:]]";
        in
        pythonLib.command {
          inherit pkgs name python;
          modules = lib.fileset.toSource {
            root = ../python;
            fileset = ../python/secret_tools.py;
          };
          imports = "import secret_tools";
          main = "secret_tools.main(${argv}, config=${builtins.toJSON "${configFile}"})";
          passthru = { inherit config configFile; };
        };

      # `<label>-secrets {rules ROOT | edit-secret ... | rekey-secrets ...}`:
      # the standard-library tools, on agecheck's interpreter.
      secretsCommand = toolCommand {
        name = "${label}-secrets";
        config = toolConfig;
        inherit python;
      };
      secretsBin = lib.getExe secretsCommand;

      # The product's check that the credentials reach its backups, as a
      # command setup-backup-access runs: verifyListing is the body of a bash
      # function, run with BACKUPS_* and FETCH in its environment. Without
      # errexit, as it always ran: it decides its own status (`|| return`).
      verifyListingCommand = pkgs.writeShellApplication {
        name = "${label}-verify-backup-listing";
        runtimeInputs = with pkgs; [
          coreutils
          gnugrep
          gnused
        ];
        text = ''
          set +o errexit
          verify_listing() {
            ${verifyListing}
          }
          verify_listing
        '';
      };

      # `setup-backup-access`: five prompts instead of five variable names and
      # the quoting rules for a file the shell will source. `fetch` is the
      # backup fetch command (lib.backups.fetch) verifyListing calls. The
      # prompts are rich's (lib.python.rich, on agecheck's interpreter).
      # Without a terminal it refuses and names the non-interactive way
      # (edit-secret with the env-file on stdin), so it never waits on a
      # prompt.
      setupBackupAccess =
        fetch:
        toolCommand {
          name = "${label}-setup-backup-access";
          tool = "setup-backup-access";
          python = pythonLib.rich { inherit python; };
          config = toolConfig // {
            backupAccess = {
              inherit restoreCommand bucketExample;
              fetch = "${fetch}";
              verifyListing = lib.getExe verifyListingCommand;
            };
          };
        };

      # Shell fragment: decrypt every declared secret and make it usable.
      #
      # Sourced on demand by the commands that need it, NOT from enterShell.
      # Three reasons, all load-bearing:
      #
      #   1. agenix-shell rm -rf's its runtime dir and re-runs rage on every
      #      source, and enterShell runs on every direnv reload, so a
      #      passphrase-protected key would prompt on every file save.
      #   2. agenix-shell exports the plaintext itself, not just the path. At
      #      shell entry that puts credentials in the environment of every
      #      process in the session, readable through /proc/<pid>/environ.
      #   3. A decryption failure becomes an error where the secret is used,
      #      with a message that can say what to do, instead of a warning
      #      scrolled past minutes earlier.
      loadSecrets =
        installationScript:
        let
          envSecrets = builtins.filter (x: x.format == "env") cfg.declared;
        in
        ''
          # agenix-shell hardcodes "$XDG_RUNTIME_DIR/agenix-shell/<hash>" with no
          # fallback. Unset (the normal state on darwin), that path becomes
          # "/agenix-shell/…", every decrypt fails, and because nothing here runs
          # under `set -e` it fails silently.
          if [ -z "''${XDG_RUNTIME_DIR:-}" ]; then
            XDG_RUNTIME_DIR="''${DEVENV_RUNTIME:-''${TMPDIR:-/tmp}/${label}-$UID}"
            export XDG_RUNTIME_DIR
            mkdir -p "$XDG_RUNTIME_DIR"
          fi

          # The installation script is built with bashOptions = [], so it does
          # not touch our shell options, but a failing `rage` still returns
          # non-zero and would trip our own `set -e`. Decryption failure is
          # diagnosed where the secret is used; it must not abort here.
          __secrets_errexit=0
          case "$-" in *e*) __secrets_errexit=1; set +e ;; esac
          # shellcheck source=/dev/null
          . "${lib.getExe installationScript}"
          [ "$__secrets_errexit" = 1 ] && set -e
          unset __secrets_errexit

          ${lib.concatMapStringsSep "\n" (x: ''
            if [ -r "''${${x.var}_PATH:-/nonexistent}" ]; then
              set -a
              # shellcheck source=/dev/null
              . "''$${x.var}_PATH"
              set +a
            fi
          '') envSecrets}
        '';

      # Shell fragment: fail with something actionable when a secret is empty.
      requireSecret = var: ''
        if [ -z "''${${var}:-}" ]; then
          echo "${label}: could not decrypt the '${var}' secret." >&2
          echo >&2
          ${agecheckBin} explain ${rulesJSON} "${var}" \
            --root "$REPO_ROOT" >&2 || true
          exit 3
        fi
      '';

      # Every declared secret, as `<name>` lines for `edit-secret`'s usage
      # text. The name is what the user passes: the .age path with the secrets
      # dir and the extension stripped.
      names = map (x: {
        name = lib.removeSuffix ".age" (lib.removePrefix "${cfg.relDir}/" x.relPath);
        inherit (x) relPath format var;
      }) cfg.declared;

      atRepo = ''
        cd "''${REPO_ROOT:?run this from the dev shell}"
      '';

      # The dev-shell commands, as devenv `scripts`: edit-secret,
      # rekey-secrets, check-secrets, and setup-backup-access when `fetch` (the
      # backup fetch command) is given. Empty when secrets are off, rather than
      # stubs that fail late. The product merges them ahead of its own
      # extraScripts, so a consumer can still override any of them.
      scripts =
        {
          fetch ? null,
        }:
        lib.optionalAttrs enabled {
          # Python (python/secret_tools.py): usage text, terminal detection,
          # rules rendering, staging, and the exit status of each step.
          edit-secret.exec = ''
            exec ${secretsBin} edit-secret "$@"
          '';

          rekey-secrets.exec = ''
            exec ${secretsBin} rekey-secrets "$@"
          '';

          # Glue (design rule 5): it only runs agecheck.

          check-secrets.exec = ''
            set -euo pipefail
            ${atRepo}
            if [ -n "''${1:-}" ]; then
              exec ${agecheckBin} explain ${rulesJSON} "$1" --root .
            fi
            exec ${agecheckBin} check ${rulesJSON} --root .
          '';
        }
        // lib.optionalAttrs (enabled && fetch != null) {
          setup-backup-access.exec = ''
            exec ${lib.getExe (setupBackupAccess fetch)} "$@"
          '';
        };
    in
    {
      inherit
        enabled
        cli
        writeRules
        rulesJSON
        setupBackupAccess
        secretsCommand
        agecheck
        loadSecrets
        requireSecret
        names
        scripts
        ;

      inherit (cfg) declared relDir;

      # Packages the dev shell needs once secrets are on.
      packages = lib.optionals enabled [
        cli
        agecheck
        pkgs.jq
      ];
    };
}
