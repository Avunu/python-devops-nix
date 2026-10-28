# lib.checks.conventions against the stub product: it returns one check per
# convention, each passes on the stub, and each fails, naming the line, the
# entry or the option, on each kind of drift.
{ pkgs, self }:
let
  stub = import ../tests/stub-product.nix { inherit pkgs self; };
  inherit (pkgs) lib;
  inherit (self.lib) checks;

  # What a product registers: its checks, and the conventions' checks merged
  # into them, whose names are read back from the merged set.
  registered =
    let
      all = {
        nginx = null;
        devguard-18 = null;
      }
      // checks.conventions {
        inherit pkgs;
        inherit (stub)
          product
          dependabot
          envVars
          options
          tenant
          settings
          commands
          ;
        src = stub.tree;
        checks = all;
      };
    in
    all;

  pins =
    src:
    checks.ciPins {
      inherit pkgs src;
      inherit (stub) product;
    };
  dependabot =
    src: extra:
    checks.dependabot (
      {
        inherit pkgs src;
        inherit (stub) product;
        inherit (stub.dependabot) development;
      }
      // extra
    );
  names =
    list: extra:
    checks.checkNames (
      {
        inherit pkgs;
        inherit (stub) product;
        checks = lib.genAttrs list (_: null);
      }
      // extra
    );

  envs =
    src: extra:
    checks.envVars (
      {
        inherit pkgs src;
        inherit (stub) product;
      }
      // stub.envVars
      // extra
    );
  flags =
    commands: extra:
    checks.commandFlags (
      {
        inherit pkgs commands;
        inherit (stub) product;
      }
      // extra
    );
  # The stub's commands, with each [ from to ] of `pairs` replaced in `role`'s
  # help.
  helpWith =
    role: pairs:
    stub.commandsOf (
      stub.help
      // {
        ${role} = builtins.replaceStrings (map lib.head pairs) (map lib.last pairs) stub.help.${role};
      }
    );

  paths =
    options: extra:
    checks.optionPaths (
      {
        inherit pkgs options;
        inherit (stub) product tenant;
      }
      // extra
    );
  blocks =
    options: settings:
    checks.configBlocks {
      inherit pkgs options settings;
      inherit (stub) product;
    };

  # The stub's declarations with the option at `path` removed, with `value`
  # set at it, or with `value` in its place.
  without =
    path:
    lib.updateManyAttrsByPath [
      {
        path = lib.init path;
        update = old: removeAttrs old [ (lib.last path) ];
      }
    ] stub.declarations;
  declaring = path: value: lib.recursiveUpdate stub.declarations (lib.setAttrByPath path value);
  replacing =
    path: value:
    lib.updateManyAttrsByPath [
      {
        inherit path;
        update = _: value;
      }
    ] stub.declarations;
  withOptions = declared: stub.optionsOf declared stub.hidden;
  # A settings option of `type`, where the stub's shell has its own.
  settingsOf =
    type:
    withOptions (
      declaring
        [
          "shell"
          "stub"
          "settings"
        ]
        (
          lib.mkOption {
            inherit type;
            default = { };
          }
        )
    );
  shellSettings = lib.last stub.settings;
  # The stub's NixOS `sites`, its per-site submodule in `wrap`'s type, and its
  # settings block at `path` under it.
  sitesAs =
    wrap: path:
    blocks
      (withOptions (
        replacing
          [
            "nixos"
            "services"
            "stub"
            "sites"
          ]
          (
            lib.mkOption {
              type = wrap stub.declarations.nixos.services.stub.sites.type.nestedTypes.elemType;
              default = { };
            }
          )
      ))
      [
        ((lib.head stub.settings) // { path = [ "sites" ] ++ path ++ [ "settings" ]; })
      ];
  # The stub's shell with `watch.python` only as an alias of `legacy.dev`.
  aliased =
    alias:
    paths (stub.optionsOf
      (lib.recursiveUpdate
        (without [
          "shell"
          "stub"
          "watch"
          "python"
        ])
        {
          shell.stub.legacy.dev = lib.mkOption { type = lib.types.bool; };
        }
      )
      {
        shell = [
          (alias
            [
              "stub"
              "watch"
              "python"
            ]
            [
              "stub"
              "legacy"
              "dev"
            ]
          )
        ];
      }
    ) { };

  meta = stub.checks.eval "conventions-names" {
    expected = {
      ci-pins = "stub-ci-pins-check";
      dependabot = "stub-dependabot-check";
      check-names = "stub-check-names-check";
      env-vars = "stub-env-vars-check";
      option-paths = "stub-option-paths-check";
      config-blocks = "stub-config-blocks-check";
      command-flags = "stub-command-flags-check";
    };
    actual = lib.mapAttrs (_: drv: drv.name) (
      removeAttrs registered [
        "nginx"
        "devguard-18"
      ]
    );
  };

  # Without `options` and `commands`, the checks that read only the tree.
  alone = stub.checks.eval "conventions-names-alone" {
    expected = [
      "check-names"
      "ci-pins"
      "dependabot"
      "env-vars"
    ];
    actual = lib.attrNames (
      checks.conventions {
        inherit pkgs;
        inherit (stub) product;
        src = stub.tree;
        checks = { };
      }
    );
  };

  # commandFlags takes both commands, and no other.
  roles = stub.checks.eval "command-flags-roles" {
    assertion =
      !(builtins.tryEval (flags { inherit (stub.commands) restore; } { }).drvPath).success
      && !(builtins.tryEval (flags (stub.commands // { relock = [ "relock" ]; }) { }).drvPath).success;
    message = "commandFlags took commands other than restore and scaffolder";
  };

  # The stub, with its flake listing `exceptions` for env-vars, one name to a
  # line, as odoo-nix and frappe-nix do: the list holds every name, so only
  # the scan's skipping it lets a stale one fail.
  listing =
    name: exceptions:
    stub.variant name ''
      cat > flake.nix <<'EOF'
      {
        outputs = _: {
          envVars.exceptions = [
            # The framework's "own" names, and one that may be gone.
            ${lib.concatMapStringsSep "\n      " (name: ''"${name}" # why'') exceptions}
          ];
        };
      }
      EOF
    '';
  stale = stub.envVars.exceptions ++ [ "STUB_GONE" ];

  mk = checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
mk.script "lib-conventions" { } ''
  echo "the entry point names its checks: ${meta}"
  echo "and only the tree's without options and commands: ${alone}"
  echo "a conforming product passes: ${registered.ci-pins} ${registered.dependabot} ${registered.check-names}"
  echo "and its modules: ${registered.option-paths} ${registered.config-blocks}"
  echo "and its interface: ${registered.env-vars} ${registered.command-flags} ${roles}"

  # CI: a tag, a short SHA in a composite action, a docker image by its tag,
  # and pins with no comment: after a plain scalar, after a flow mapping and
  # after a folded scalar's indicator.
  ${stub.expectFailure
    (pins (stub.variant "tag" "sed -i 's|checkout@[0-9a-f]*|checkout@v7|' .github/workflows/check.yml"))
    [
      ".github/workflows/check.yml:7: actions/checkout@v7 is not pinned by a full commit SHA"
      "1 action(s) not pinned"
    ]
  }
  ${stub.expectFailure
    (pins (
      stub.variant "short-sha" "sed -i 's|@44924fe9ef4a6f88253c4b6b1bf11c689d3ce53d|@44924fe|' .github/actions/setup/action.yml"
    ))
    [
      ".github/actions/setup/action.yml:5: j178/prek-action@44924fe is not pinned by a full commit SHA"
    ]
  }
  ${stub.expectFailure
    (pins (
      stub.variant "no-comment" "sed -i 's| # main (no release yet)$||' .github/workflows/check.yml"
    ))
    [
      ".github/workflows/check.yml:10: Avunu/python-devops-nix/.github/workflows/dependabot-auto-merge.yml@51c31525e5a1f2ed0d3e4b8b8c0b9c9a2f0f3e11 has no trailing comment"
    ]
  }
  ${stub.expectFailure
    (pins (
      stub.variant "docker-tag" "sed -i 's|alpine@sha256:[0-9a-f]*|alpine:3.20|' .github/workflows/check.yml"
    ))
    [
      ".github/workflows/check.yml:14: docker://alpine:3.20 is not pinned by a sha256 digest"
    ]
  }
  ${stub.expectFailure
    (pins (
      stub.variant "no-comments" "sed -i 's/ # v[0-9.]*$//; s/>- # v3.1.0/>-/' .github/workflows/check.yml"
    ))
    [
      ".github/workflows/check.yml:7: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 has no trailing comment"
      ".github/workflows/check.yml:15: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 has no trailing comment"
      ".github/workflows/check.yml:16: j178/prek-action@44924fe9ef4a6f88253c4b6b1bf11c689d3ce53d has no trailing comment"
      "3 action(s) not pinned"
    ]
  }
  ${stub.expectFailure (pins (stub.variant "no-workflows" "rm -r .github")) [
    ".github/workflows: no workflow found"
  ]}

  # Dependabot: a wrong prefix, a missing cooldown, no catch-all group, a
  # development ecosystem not listed, one listed but with the release prefix, a
  # stale listing, and no file at all. `directories:` matches a listing too.
  echo "directories passes: ${
    dependabot (stub.variant "directories" ''
      sed -i 's|directory: /dev|directories: ["/dev"]|' .github/dependabot.yml
    '') { }
  }"
  ${stub.expectFailure
    (dependabot (stub.variant "chore" "sed -i '0,/fix(deps)/s//chore(deps)/' .github/dependabot.yml")
      { }
    )
    [
      ".github/dependabot.yml: nix in /: commit-message.prefix is \"chore(deps)\", not \"fix(deps)\""
      "1 difference(s) from the Dependabot shape"
    ]
  }
  ${stub.expectFailure (dependabot (stub.variant "cooldown" ''
    sed -i '/package-ecosystem: github-actions/,$ { /cooldown:/d; /default-days:/d }' .github/dependabot.yml
  '') { }) [ ".github/dependabot.yml: github-actions in /: cooldown.default-days is null, not 7" ]}
  ${stub.expectFailure (dependabot (stub.variant "groups" ''
    sed -i 's/"\*"]/"x*"]/' .github/dependabot.yml
  '') { }) [ ".github/dependabot.yml: nix in /: no group has patterns: [\"*\"]" ]}
  ${stub.expectFailure (dependabot stub.tree { development = [ ]; }) [
    ".github/dependabot.yml: uv in /dev: commit-message.prefix is \"chore(deps)\", not \"fix(deps)\""
  ]}
  ${stub.expectFailure
    (dependabot
      (stub.variant "development-fix" "sed -i 's/chore(deps)/fix(deps)/' .github/dependabot.yml")
      { }
    )
    [
      ".github/dependabot.yml: uv in /dev: commit-message.prefix is \"fix(deps)\", not \"chore(deps)\""
    ]
  }
  ${stub.expectFailure
    (dependabot stub.tree {
      development = stub.dependabot.development ++ [ { ecosystem = "pip"; } ];
    })
    [ ".github/dependabot.yml: pip in /: listed as development-only, but the file has no such entry" ]
  }
  # The base's own shape, `chore`, is a parameter; the stub has `fix(deps)`.
  ${stub.expectFailure (dependabot stub.tree { prefix = "chore"; }) [
    ".github/dependabot.yml: nix in /: commit-message.prefix is \"fix(deps)\", not \"chore\""
  ]}
  ${stub.expectFailure (dependabot (stub.variant "no-dependabot" "rm .github/dependabot.yml") { }) [
    ".github/dependabot.yml: missing from the product"
  ]}

  # Check names: kind prefixes and names that are not kebab-case, and an
  # exception that is no longer needed.
  echo "an exception passes: ${names [ "vm-legacy" ] { exceptions = [ "vm-legacy" ]; }}"
  ${stub.expectFailure
    (names [
      "nginx"
      "vm-nginx"
      "eval-ports"
      "module-restore-18"
      "Restore"
      "restore_18"
      "devguard-"
    ] { })
    [
      "vm-nginx: starts with the kind prefix vm-"
      "eval-ports: starts with the kind prefix eval-"
      "module-restore-18: starts with the kind prefix module-"
      "Restore: not kebab-case"
      "restore_18: not kebab-case"
      "devguard-: not kebab-case"
    ]
  }
  ${stub.expectFailure
    (names [ "nginx" ] {
      exceptions = [
        "nginx"
        "vm-gone"
      ];
    })
    [
      "nginx: listed as an exception, but it is not a check that breaks the rule"
      "vm-gone: listed as an exception, but it is not a check that breaks the rule"
    ]
  }

  # Option paths: a missing option and a missing tree, which two sections
  # name, the new name kept only as an alias of the old, hidden or not, an
  # internal option, a read-only port, a visible option in the dev tree, the
  # NixOS module's own and tenants under other words.
  ${stub.expectFailure
    (paths (withOptions (without [
      "shell"
      "stub"
      "restore"
      "migrate"
    ])) { })
    [
      "stub.restore.migrate: missing (Migrate after restore)"
    ]
  }
  ${stub.expectFailure
    (paths (withOptions (without [
      "shell"
      "stub"
      "devguard"
      "jobs"
    ])) { })
    [
      "stub.devguard.jobs.enable: missing (Job denylist, Shared guard names)"
      "stub.devguard.jobs.extraBlocked: missing (Job denylist)"
    ]
  }
  ${stub.expectFailure (aliased lib.mkRenamedOptionModule) [
    "stub.watch.python: only an alias of stub.legacy.dev (The development shell option tree)"
  ]}
  ${stub.expectFailure (aliased lib.mkAliasOptionModule) [
    "stub.watch.python: only an alias of stub.legacy.dev (The development shell option tree)"
  ]}
  ${stub.expectFailure
    (paths (withOptions (declaring [ "shell" "stub" "mailpit" "host" ] { internal = true; })) { })
    [
      "stub.mailpit.host: hidden (a removed option, or visible = false or internal) (The development shell option tree)"
    ]
  }
  ${stub.expectFailure
    (paths (withOptions (declaring [ "shell" "stub" "ports" "http" ] { readOnly = true; })) { })
    [
      "stub.ports.http: read-only, so lib.mkForce cannot set it (The development shell option tree)"
    ]
  }
  ${stub.expectFailure
    (paths (stub.optionsOf (declaring [ "shell" "stub" "dev" "autoReload" ] (
      lib.mkOption { type = lib.types.bool; }
    )) { }) { })
    [
      "stub.dev.autoReload: declared in the dev tree, which the conventions removed (The development shell option tree)"
    ]
  }
  ${stub.expectFailure
    (paths
      (withOptions (without [
        "nixos"
        "services"
        "stub"
        "package"
      ]))
      {
        tenant = {
          shell = "dbName";
          nixos = "domain";
        };
      }
    )
    [
      "services.stub.package: missing (NixOS module path)"
      "stub.dbName: missing (Identity options)"
      "services.stub.domain: missing (Identity options)"
    ]
  }
  # An exception passes the path it names, and fails once nothing is wrong
  # with it.
  echo "an exception passes: ${
    paths
      (withOptions (without [
        "shell"
        "stub"
        "mailpit"
        "pop3"
      ]))
      {
        exceptions = map (key: "stub.mailpit.pop3.${key}") [
          "enable"
          "port"
          "user"
          "password"
        ];
      }
  }"
  ${stub.expectFailure
    (paths stub.options {
      exceptions = [
        "stub.mailpit.port"
        "stub.gone"
      ];
    })
    [
      "stub.mailpit.port: listed as an exception, but nothing is wrong with it"
      "stub.gone: listed as an exception, but nothing is wrong with it"
    ]
  }

  # Config blocks: another format, the format's other arguments, a type that is
  # not a submodule or has no freeformType, an option not named settings, a
  # path that skips the attribute set's names, and no block at all.
  ${stub.expectFailure
    (blocks stub.options [
      ((lib.head stub.settings) // { format = "ini"; })
    ])
    [
      "services.stub.sites.<name>.settings: its freeformType is JSON value, not pkgs.formats.ini's attribute set of section of an INI file (attrs of INI atom (null, bool, int, float or string))"
    ]
  }
  ${stub.expectFailure
    (blocks stub.options [
      (removeAttrs shellSettings [ "args" ])
    ])
    [
      "stub.settings: its freeformType is attribute set of section of an INI file (attrs of INI atom (null, bool, int, float or string) or a non-empty list of them), not pkgs.formats.ini's"
    ]
  }
  ${stub.expectFailure (blocks (settingsOf (lib.types.attrsOf lib.types.str)) [ shellSettings ]) [
    "stub.settings: its type is attribute set of string, not a submodule"
  ]}
  ${stub.expectFailure (blocks (settingsOf (lib.types.submodule { })) [ shellSettings ]) [
    "stub.settings: a submodule with no freeformType"
  ]}
  ${stub.expectFailure
    (blocks stub.options [
      (shellSettings // { path = [ "extraConfig" ]; })
    ])
    [
      "stub.extraConfig: named extraConfig, not settings"
    ]
  }
  ${stub.expectFailure
    (blocks stub.options [
      (
        (lib.head stub.settings)
        // {
          path = [
            "sites"
            "settings"
          ];
        }
      )
    ])
    [
      "services.stub.sites.settings: missing"
    ]
  }
  # A path names a list's items `*` and an attribute set's names as its type
  # does, through nullOr too, and no other way.
  echo "the placeholders pass: ${sitesAs lib.types.listOf [ "*" ]} ${
    sitesAs lib.types.lazyAttrsOf [ "<name>" ]
  } ${sitesAs (t: lib.types.nullOr (lib.types.attrsOf t)) [ "<name>" ]} ${
    sitesAs (
      elemType:
      lib.types.attrsWith {
        inherit elemType;
        placeholder = "fqdn";
      }
    ) [ "<fqdn>" ]
  }"
  ${stub.expectFailure (sitesAs lib.types.listOf [ "<name>" ]) [
    "services.stub.sites.<name>.settings: missing"
  ]}
  ${stub.expectFailure (sitesAs (t: lib.types.nullOr (lib.types.attrsOf t)) [ ]) [
    "services.stub.sites.settings: missing"
  ]}
  ${stub.expectFailure
    (sitesAs (
      elemType:
      lib.types.attrsWith {
        inherit elemType;
        placeholder = "fqdn";
      }
    ) [ "<name>" ])
    [
      "services.stub.sites.<name>.settings: missing"
    ]
  }
  ${stub.expectFailure (blocks stub.options [ ]) [
    "no settings option is listed (docs/conventions.md, Framework config blocks)"
  ]}

  # Environment variables: each way a source reads or sets a name outside
  # STUB_NIX_*, in Python, shell and Nix; the exceptions and the ignored
  # fixtures, without which their names fail; the required names; and stale
  # exceptions, also when the tree declares them. A product named stub-nix has
  # the same prefix.
  echo "the prefix drops -nix: ${envs stub.tree { product = "stub-nix"; }}"
  ${stub.expectFailure
    (envs (stub.variant "env-forms" ''
      echo 'PORT = os.environ.get("STUB_PORT")' > lib/port.py
      cat >> bin/stub-ci <<'EOF'
      echo "$STUB_HOST $STUB_HOST"
      echo "$STUB_HOST"
      : "''${STUB_USER:-x}"
      STUB_MODE=dev stub
      unset STUB_TOKEN
      STUB_SHA="$(git rev-parse HEAD)"
      EOF
      echo '{ wrap = "--set STUB_FLAG 1"; env.STUB_LEVEL = "debug"; }' > modules/wrap.nix
    '') { })
    [
      "STUB_PORT: not STUB_NIX_* or STUB_DEVGUARD_* (lib/port.py:1) (Tool-owned variables)"
      "STUB_HOST: not STUB_NIX_* or STUB_DEVGUARD_* (bin/stub-ci:7, and 2 more) (Tool-owned variables)"
      "STUB_USER: not STUB_NIX_* or STUB_DEVGUARD_* (bin/stub-ci:9)"
      "STUB_MODE: not STUB_NIX_* or STUB_DEVGUARD_* (bin/stub-ci:10)"
      "STUB_TOKEN: not STUB_NIX_* or STUB_DEVGUARD_* (bin/stub-ci:11)"
      "STUB_SHA: not STUB_NIX_* or STUB_DEVGUARD_* (bin/stub-ci:12)"
      "STUB_FLAG: not STUB_NIX_* or STUB_DEVGUARD_* (modules/wrap.nix:1)"
      "STUB_LEVEL: not STUB_NIX_* or STUB_DEVGUARD_* (modules/wrap.nix:1)"
      "8 variable(s) outside the conventions (docs/conventions.md, Environment variables)."
    ]
  }
  ${stub.expectFailure
    (envs stub.tree {
      exceptions = [ ];
      ignore = [ ];
    })
    [
      "STUB_DB: not STUB_NIX_* or STUB_DEVGUARD_* (lib/restore.py:11)"
      "STUB_RC: not STUB_NIX_* or STUB_DEVGUARD_* (lib/restore.py:15)"
      "STUB_VERSION: not STUB_NIX_* or STUB_DEVGUARD_* (lib/template.py:1)"
      "STUB_FIXTURE: not STUB_NIX_* or STUB_DEVGUARD_* (tests/fixtures/env.sh:1)"
      "4 variable(s) outside the conventions"
    ]
  }
  ${stub.expectFailure
    (envs (stub.variant "env-required" ''
      sed -i '/STUB_NIX_DEVGUARD/d' modules/devenv.nix
      sed -i '/^ALLOW = /d' lib/restore.py
    '') { })
    [
      "STUB_NIX_DEVGUARD: never read or set (Tool-owned variables)"
      "STUB_NIX_RESTORE_ALLOW_UNGUARDED: never read or set (Unguarded restore)"
    ]
  }
  ${stub.expectFailure
    (envs stub.tree {
      exceptions = stub.envVars.exceptions ++ [
        "STUB_GONE"
        "STUB_NIX_CI"
      ];
    })
    [
      "STUB_GONE: listed as an exception, but nothing is wrong with it"
      "STUB_NIX_CI: listed as an exception, but nothing is wrong with it"
    ]
  }
  echo "exceptions declared in the tree: ${envs (listing "env-declared" stub.envVars.exceptions) { }}"
  ${stub.expectFailure (envs (listing "env-declared-stale" stale) { exceptions = stale; }) [
    "STUB_GONE: listed as an exception, but nothing is wrong with it"
    "1 variable(s) outside the conventions"
  ]}

  # Command flags: restore and the scaffolder each without the flags of a
  # section, a flag left only in prose or renamed, exceptions, needed and
  # stale, a command that fails or does not run, whose exceptions are not
  # judged, and the other shapes of an option line that pass.
  ${stub.expectFailure
    (flags (helpWith "restore" [
      [
        "--no-force                      Refuse"
        "--no-overwrite                  Refuse"
      ]
      [
        "--migrate / --no-migrate"
        "--migrate               "
      ]
      [
        "--neutralize / --no-neutralize"
        "--neutralize                  "
      ]
      [
        "--files / --no-files"
        "--attachments       "
      ]
      [
        "--at TEXT"
        "--to TEXT"
      ]
    ]) { })
    [
      "restore --no-force: not in its --help (Overwrite)"
      "restore --no-migrate: not in its --help (Migrate after restore)"
      "restore --no-neutralize: not in its --help (Neutralise)"
      "restore --files: not in its --help (Files)"
      "restore --at: not in its --help (Point in time)"
      "5 difference(s) from the shared flags (docs/conventions.md, Restore and Scaffolder)."
    ]
  }
  ${stub.expectFailure
    (flags (helpWith "scaffolder" [
      [
        "-y, --yes"
        "--yes    "
      ]
      [
        "--version <v>"
        "--series <v> "
      ]
      [
        "--commit[=<msg>]"
        "--commit-msg <m>"
      ]
    ]) { })
    [
      "scaffolder -y: not in its --help (Flags)"
      "scaffolder --version: not in its --help (Version flag)"
      "scaffolder --commit: not in its --help (Flags)"
    ]
  }
  echo "an exception passes: ${
    flags (helpWith "scaffolder" [
      [
        "-y, --yes"
        "--yes    "
      ]
    ]) { exceptions = [ "scaffolder -y" ]; }
  }"
  ${stub.expectFailure
    (flags stub.commands {
      exceptions = [
        "restore --no-force"
        "scaffolder --gone"
      ];
    })
    [
      "restore --no-force: listed as an exception, but nothing is wrong with it"
      "scaffolder --gone: listed as an exception, but nothing is wrong with it"
    ]
  }
  ${stub.expectFailure
    (flags {
      restore = [
        (lib.head stub.commands.restore)
        "database"
        "restore"
      ];
      scaffolder = [ "/nonexistent/stub-init" ];
    } { exceptions = [ "restore --no-force" ]; })
    [
      "database restore --help` exited 2: stub: unexpected arguments: database restore --help"
      "scaffolder: `/nonexistent/stub-init --help` did not run:"
      "2 difference(s) from the shared flags"
    ]
  }
  echo "and other shapes of an option line: ${
    flags (helpWith "restore" [
      [
        "--neutralize / --no-neutralize"
        "--[no-]neutralize             "
      ]
      [
        "--files / --no-files            "
        "--files/--no-files\t"
      ]
      [
        "--at TEXT"
        "--at AT [AT ...]"
      ]
    ]) { }
  }"
''
