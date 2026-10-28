# A stub product for testing lib.checks (docs/library.md, design rule 8): a tree
# with the shared repository files and a .github as a product would carry them,
# and helpers to derive broken variants of it and to assert that a check fails
# on one.
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
    # quoted, one a reusable workflow, one local), and Dependabot's shape with
    # one development-only ecosystem, listed in `dependabot.development`.
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
