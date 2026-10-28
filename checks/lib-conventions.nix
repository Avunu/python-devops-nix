# lib.checks.conventions against the stub product: it returns one check per
# convention, each passes on the stub, and each fails, naming the line or the
# entry, on each kind of drift.
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
        inherit (stub) product dependabot;
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

  meta = stub.checks.eval "conventions-names" {
    expected = {
      ci-pins = "stub-ci-pins-check";
      dependabot = "stub-dependabot-check";
      check-names = "stub-check-names-check";
    };
    actual = lib.mapAttrs (_: drv: drv.name) (
      removeAttrs registered [
        "nginx"
        "devguard-18"
      ]
    );
  };

  mk = checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
mk.script "lib-conventions" { } ''
  echo "the entry point names its checks: ${meta}"
  echo "a conforming product passes: ${registered.ci-pins} ${registered.dependabot} ${registered.check-names}"

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
''
