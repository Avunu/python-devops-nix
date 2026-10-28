# A stub product for testing lib.checks (docs/library.md, design rule 8): a tree
# with the shared repository files and a .github as a product would carry them,
# the options its modules declare, and helpers to derive broken variants of
# them and to assert that a check fails on one.
#
# The tree is built here rather than committed, because a nested
# .pre-commit-config.yaml would be picked up by prek's workspace discovery.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "stub";
  };
in
rec {
  product = "stub";
  inherit checks;

  # What a conforming product carries: the base's settings, plus its own. It
  # adds a key to .editorconfig and sets one differently (tabs for Python, as
  # frappe-nix does, listed in `exceptions`); extends another ruff config;
  # passes an extra argument to a shared hook, adds a hook, excludes its own
  # paths and pins its own revisions.
  tree = pkgs.runCommand "stub-product" { } ''
    mkdir "$out"
    cd "$out"
    cp ${self}/statix.toml statix.toml

    sed -e '/^\[\*\.py\]$/,/^$/s/^indent_style = space$/indent_style = tab/' \
      ${self}/.editorconfig > .editorconfig
    printf '\n[*.js]\nmax_line_length = 99\n' >> .editorconfig

    cat > ruff.toml <<'EOF'
    extend = "dev/upstream-ruff.toml"
    target-version = "py311"
    extend-exclude = ["tests/fixtures"]
    EOF

    sed -e 's/^exclude: .*/exclude: ^tests\/fixtures\//' \
      -e 's/^\(    rev: \).*/\1v0.0.1/' \
      -e 's/^      - id: check-yaml$/&\n        args: [--allow-multiple-documents]/' \
      ${self}/.pre-commit-config.yaml > .pre-commit-config.yaml
    cat >> .pre-commit-config.yaml <<'EOF'

      - repo: https://github.com/bwhmather/ssort
        rev: 0.17.0
        hooks:
          - id: ssort
    EOF

    mkdir templates
    echo '{ name = @NAME@; }' > templates/flake.nix
    printf '_: { }\n' > default.nix

    # Its .github: pinned actions in a workflow and in a composite action (one
    # quoted, one a reusable workflow, one local, one a docker image, one in a
    # flow mapping and one a folded scalar), and Dependabot's shape with one
    # development-only ecosystem, listed in `dependabot.development`.
    mkdir -p .github/workflows .github/actions/setup
    cat > .github/workflows/check.yml <<'EOF'
    on: pull_request
    permissions: {}
    jobs:
      lint:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
          - uses: ./.github/actions/setup
      auto-merge:
        uses: Avunu/python-devops-nix/.github/workflows/dependabot-auto-merge.yml@51c31525e5a1f2ed0d3e4b8b8c0b9c9a2f0f3e11 # main (no release yet)
      docker:
        runs-on: ubuntu-latest
        steps:
          - uses: docker://alpine@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef # 3.20
          - { uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333, with: { name: report } } # v8.0.2
          - uses: >- # v3.1.0
              j178/prek-action@44924fe9ef4a6f88253c4b6b1bf11c689d3ce53d
    EOF
    cat > .github/actions/setup/action.yml <<'EOF'
    name: setup
    runs:
      using: composite
      steps:
        - uses: "j178/prek-action@44924fe9ef4a6f88253c4b6b1bf11c689d3ce53d" # v3.1.0
    EOF
    cat > .github/dependabot.yml <<'EOF'
    version: 2
    updates:
      - package-ecosystem: nix
        directory: /
        schedule:
          interval: weekly
        cooldown:
          default-days: 7
        commit-message:
          prefix: "fix(deps)"
        groups:
          ocb:
            patterns: ["ocb-*"]
          nix:
            patterns: ["*"]
      - package-ecosystem: uv
        directory: /dev
        schedule:
          interval: weekly
        cooldown:
          default-days: 7
        commit-message:
          prefix: "chore(deps)"
        groups:
          dev-tools:
            patterns: ["*"]
      - package-ecosystem: github-actions
        directory: /
        schedule:
          interval: daily
        cooldown:
          default-days: 7
        commit-message:
          prefix: "fix(deps)"
        groups:
          github-actions:
            patterns: ["*"]
    EOF
  '';

  # The development-only ecosystem the stub's .github/dependabot.yml has.
  dependabot.development = [
    {
      ecosystem = "uv";
      directory = "/dev";
    }
  ];

  # Its modules' option declarations: `stub.*` in the development shell and
  # `services.stub.*` in NixOS, each option the conventions name, with the
  # framework's settings as RFC 42 blocks. Only the declarations are read, so
  # every leaf is the same untyped option.
  declarations =
    let
      leaf = lib.mkOption {
        type = lib.types.anything;
        default = null;
      };
      leaves = names: lib.genAttrs names (_: leaf);
      guard = leaves [ "enable" ];
    in
    {
      shell.stub = {
        name = leaf;
        defaultSite = leaf;
        restore = leaves [
          "enable"
          "migrate"
          "neutralize"
          "files"
          "requireDevguard"
        ];
        watch = leaves [ "python" ];
        devguard = {
          enable = leaf;
          egress = leaves [
            "enable"
            "allowHosts"
          ];
          mail = leaves [
            "enable"
            "sender"
            "unmute"
          ];
          jobs = leaves [
            "enable"
            "extraBlocked"
          ];
          backups = guard;
          objectstore = guard;
          webhooks = guard;
          # A product guard beside the shared ones.
          iap = guard;
        };
        mailpit =
          leaves [
            "enable"
            "host"
            "port"
            "httpPort"
          ]
          // {
            pop3 = leaves [
              "enable"
              "port"
              "user"
              "password"
            ];
          };
        ports = leaves [
          "base"
          "offset"
          "worktreeSalt"
          "http"
          "db"
          "smtp"
          "mailUi"
        ];
        # odoo.conf's shape: INI by section, lists as one value.
        settings = lib.mkOption {
          type = lib.types.submodule {
            freeformType = (pkgs.formats.ini { listToValue = lib.concatStringsSep ","; }).type;
          };
          default = { };
        };
      };
      nixos.services.stub = {
        enable = leaf;
        package = leaf;
        # site_config.json's shape, per site.
        sites = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.submodule {
              options.settings = lib.mkOption {
                type = lib.types.submodule {
                  freeformType = (pkgs.formats.json { }).type;
                  options.host_name = leaf;
                };
                default = { };
              };
            }
          );
          default = { };
        };
      };
    };

  # Old names, which a product keeps hidden: `runtime.dev`, an alias of
  # `watch.python`, and the removed `dev` tree.
  hidden.shell = [
    (lib.doRename {
      from = [
        "stub"
        "runtime"
        "dev"
      ];
      to = [
        "stub"
        "watch"
        "python"
      ];
      visible = false;
      warn = false;
      use = lib.id;
    })
    {
      options.stub.dev = lib.mkOption {
        type = lib.types.anything;
        visible = false;
      };
    }
  ];

  # The option sets `declared` and its hidden `aliases` evaluate to, as
  # lib.checks.conventions takes them.
  optionsOf =
    declared: aliases:
    lib.mapAttrs (
      module: options:
      (lib.evalModules { modules = [ { inherit options; } ] ++ aliases.${module} or [ ]; }).options
    ) declared;
  options = optionsOf declarations hidden;

  # The default tenant's options, and the settings blocks.
  tenant = {
    shell = "defaultSite";
    nixos = "sites";
  };
  settings = [
    {
      module = "nixos";
      path = [
        "sites"
        "<name>"
        "settings"
      ];
      format = "json";
    }
    {
      module = "shell";
      path = [ "settings" ];
      format = "ini";
      args.listToValue = lib.concatStringsSep ",";
    }
  ];

  # The exception the stub's .editorconfig needs.
  exceptions.".editorconfig" = [
    [
      "*.py"
      "indent_style"
    ]
  ];

  # `tree`, with `script` run in a writable copy of it.
  variant =
    name: script:
    pkgs.runCommand "stub-product-${name}" { } ''
      cp -r ${tree} "$out"
      chmod -R u+w "$out"
      cd "$out"
      ${script}
    '';

  # Builds `drv`, which must fail, and asserts its log has each of `lines`.
  expectFailure =
    drv: lines:
    let
      failed = pkgs.testers.testBuildFailure drv;
    in
    ''
      echo "expect ${drv.name} to fail"
      [ "$(cat ${failed}/testBuildFailure.exit)" != 0 ]
      ${lib.concatMapStrings (line: ''
        grep -qF -- ${lib.escapeShellArg line} ${failed}/testBuildFailure.log || {
          echo "${drv.name}: log lacks: "${lib.escapeShellArg line}
          cat ${failed}/testBuildFailure.log
          exit 1
        }
      '') lines}
    '';
}
