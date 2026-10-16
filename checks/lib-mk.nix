# lib.checks.mk: the names it gives, a script check's environment and its
# failure on a failing pipeline, and an eval check's pass and failure, each
# failure with its message.
{ pkgs, self }:
let
  stub = import ../tests/stub-product.nix { inherit pkgs self; };
  inherit (stub) checks;
  inherit (pkgs) lib;

  script =
    checks.script "script"
      {
        src = stub.tree;
        tools = [ pkgs.jq ];
        env.GREETING = "hello";
      }
      ''
        test -f statix.toml              # runs in a copy of src
        touch written                    # ...which is writable
        touch "$HOME/.rc"                # HOME is writable
        test "$GREETING" = hello         # env reaches the script
        jq -n '{ok: true}' > "$out"      # tools are on PATH; the report is kept
      '';

  meta = checks.eval "names" {
    expected = {
      checkName = "stub-x-check";
      script = "stub-script-check";
      eval = "stub-names-check";
    }
    // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux { vm = "vm-test-run-stub-vm"; };
    actual = {
      checkName = checks.checkName "x";
      script = script.name;
      eval =
        (checks.eval "names" {
          assertion = true;
          message = "";
        }).name;
    }
    // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
      vm =
        (checks.nixosTest "vm" {
          nodes.machine = { };
          testScript = "";
        }).name;
    };
  };

  mk = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
mk.script "lib-mk" { } ''
  grep -q true ${script}
  echo "eval checks pass: ${meta} ${
    checks.eval "assertion" {
      assertion = 1 + 1 == 2;
      message = "arithmetic";
    }
  }"
  ${stub.expectFailure (checks.script "pipefail" { } "false | true") [ ]}
  ${stub.expectFailure (checks.eval "false" {
    assertion = false;
    message = "the option is not set";
  }) [ "eval check stub-false-check failed: the option is not set" ]}
  ${stub.expectFailure
    (checks.eval "unequal" {
      expected = {
        port = 8000;
        host = "localhost";
      };
      actual = {
        port = 8080;
        host = "localhost";
      };
      message = "default ports";
    })
    [
      "eval check stub-unequal-check failed: default ports"
      "-  \"port\": 8000"
      "+  \"port\": 8080"
    ]
  }
''
