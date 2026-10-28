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
          options
          tenant
          settings
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
      option-paths = "stub-option-paths-check";
      config-blocks = "stub-config-blocks-check";
    };
    actual = lib.mapAttrs (_: drv: drv.name) (
      removeAttrs registered [
        "nginx"
        "devguard-18"
      ]
    );
  };

  # Without `options`, the checks of the repository alone.
  alone = stub.checks.eval "conventions-names-alone" {
    expected = [
      "check-names"
      "ci-pins"
      "dependabot"
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

  mk = checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
mk.script "lib-conventions" { } ''
  echo "the entry point names its checks: ${meta}"
  echo "and only the repository's without options: ${alone}"
  echo "a conforming product passes: ${registered.ci-pins} ${registered.dependabot} ${registered.check-names}"
  echo "and its modules: ${registered.option-paths} ${registered.config-blocks}"

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
''
