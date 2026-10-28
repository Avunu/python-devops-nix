# lib.checks.sharedConfig against the stub product: it passes on a product that
# adds settings and documents its one exception, and fails, naming the setting,
# on each kind of drift.
{ pkgs, self }:
let
  stub = import ../tests/stub-product.nix { inherit pkgs self; };
  shared =
    src: extra:
    self.lib.checks.sharedConfig (
      {
        inherit pkgs src;
        inherit (stub) product exceptions;
      }
      // extra
    );
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
checks.script "lib-shared-config" { } ''
  echo "a conforming product passes: ${shared stub.tree { }}"
  echo "statix.toml can be listed too: ${shared stub.tree { files = [ "statix.toml" ]; }}"

  # The undocumented difference: no exception for tabs.
  ${stub.expectFailure (shared stub.tree { exceptions = { }; }) [
    ".editorconfig: *.py > indent_style: \"tab\" (base: \"space\")"
    "1 difference(s) from the base's copies"
  ]}

  ${stub.expectFailure (shared (stub.variant "ruff" "sed -i 's/py311/py312/' ruff.toml") { }) [
    "ruff.toml: target-version: \"py312\" (base: \"py311\")"
  ]}

  ${stub.expectFailure
    (shared (stub.variant "editorconfig" "sed -i '/^root = true$/d; /^charset/d' .editorconfig") { })
    [
      ".editorconfig: root: missing (base: \"true\")"
      ".editorconfig: * > charset: missing (base: \"utf-8\")"
    ]
  }

  # A shared hook dropped, and a shared hook's arguments changed.
  ${stub.expectFailure
    (shared (stub.variant "prek" ''
      sed -i -e '/id: ruff-format/,+1d' -e 's/--select=I, --fix/--select=I/' .pre-commit-config.yaml
    '') { })
    [
      "https://github.com/astral-sh/ruff-pre-commit > ruff-format (ruff formatter): missing"
      "https://github.com/astral-sh/ruff-pre-commit > ruff-check (ruff import sorter) > args: [\"--select=I\"] (base: [\"--select=I\", \"--fix\"])"
    ]
  }

  # A shared scope dropped, a commit rule changed, and a commit type added:
  # only the scope list is a set the product may extend.
  ${stub.expectFailure
    (shared (stub.variant "committed" ''
      sed -i -e '/^\t"tui",$/d' -e 's/^subject_length = 100$/subject_length = 72/' \
        -e 's/^allowed_types = \[$/&\n\t"wip",/' committed.toml
    '') { })
    [
      "committed.toml: allowed_scopes: missing \"tui\""
      "committed.toml: subject_length: 72 (base: 100)"
      "committed.toml: allowed_types: [\"wip\", \"build\""
      "3 difference(s) from the base's copies"
    ]
  }

  ${stub.expectFailure (shared (stub.variant "no-ruff" "rm ruff.toml") { }) [
    "ruff.toml: missing from the product"
  ]}

  # An exception for a setting the base does not make is stale.
  ${stub.expectFailure (shared stub.tree {
    exceptions = stub.exceptions // {
      "ruff.toml" = [ [ "line-length" ] ];
    };
  }) [ "ruff.toml: line-length: listed as an exception, but the base sets no such key" ]}
''
