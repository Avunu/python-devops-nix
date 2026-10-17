# lib.ciMode: the enterShell snippets rendered for a stub product and run
# under bash with <P>_NIX_CI set, unset, 0 and false. Ported from frappe-nix's
# standards-ci-mode check; frappe-nix's banner line is reproduced exactly.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  ciMode = self.lib.ciMode {
    label = "stub-nix";
    envPrefix = "STUB";
  };
  frappe = self.lib.ciMode {
    label = "frappe-nix";
    envPrefix = "FRAPPE";
  };
  bash = lib.getExe pkgs.bash;

  shell = pkgs.writeText "ci-mode-enter-shell" ''
    ${ciMode.exports}
    ${ciMode.unlessCi ''
      stub-interactive-step
    ''}
    ${ciMode.banner {
      name = "a \"quoted\" $name";
      details = "site \${STUB_SITE:-unset}";
      interactive = "echo interactive-banner";
    }}
    echo "DEVENV_IN_DIRENV_SHELL=''${DEVENV_IN_DIRENV_SHELL:-unset} PC_TUI_ENABLED=''${PC_TUI_ENABLED:-unset}"
  '';
  # What frappe-nix's lib/ci-mode.nix printed for a bench, as frappe-nix
  # would call this.
  frappeShell = pkgs.writeText "frappe-ci-mode" (
    frappe.banner {
      name = "standards-fixture";
      details = "site \${FRAPPE_SITE:-unset}";
      interactive = "echo interactive";
    }
  );
  noDetails = pkgs.writeText "no-details" (
    ciMode.banner {
      name = "plain";
      interactive = "echo interactive";
    }
  );
in
assert ciMode.variable == "STUB_NIX_CI";
checks.script "lib-ci-mode" { } ''
  fail() { echo "FAIL $*" >&2; exit 1; }
  mkdir bin
  printf '#!%s\necho "interactive step ran"\n' ${bash} > bin/stub-interactive-step
  chmod +x bin/stub-interactive-step
  export PATH="$PWD/bin:$PATH" STUB_SITE=fixture.localhost

  ci="$(env STUB_NIX_CI=1 ${bash} ${shell})"
  echo "$ci"
  ! grep -q 'step ran$' <<< "$ci" || fail "CI mode ran an interactive step"
  grep -qxF 'stub-nix: CI mode (a "quoted" $name, site fixture.localhost)' <<< "$ci" || fail "CI mode banner"
  ! grep -q interactive-banner <<< "$ci" || fail "CI mode printed the interactive banner"
  grep -q 'DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0' <<< "$ci" || fail "CI mode exports"
  echo "ok   STUB_NIX_CI=1: no interactive steps, one-line banner, exports"

  for off in "" 0 false; do
    got="$(env STUB_NIX_CI="$off" ${bash} ${shell})"
    grep -qx 'interactive step ran' <<< "$got" || fail "STUB_NIX_CI='$off' skipped the interactive step"
    grep -qx interactive-banner <<< "$got" || fail "STUB_NIX_CI='$off' lost the banner"
    grep -q 'DEVENV_IN_DIRENV_SHELL=unset' <<< "$got" || fail "STUB_NIX_CI='$off' exported CI variables"
  done
  got="$(env -u STUB_NIX_CI ${bash} ${shell})"
  grep -qx interactive-banner <<< "$got" || fail "unset STUB_NIX_CI lost the banner"
  echo "ok   unset, empty, 0 and false: the interactive shell"

  got="$(env FRAPPE_NIX_CI=true FRAPPE_SITE=fixture.localhost ${bash} ${frappeShell})"
  [ "$got" = 'frappe-nix: CI mode (standards-fixture, site fixture.localhost)' ] || fail "frappe-nix's line: $got"
  got="$(env FRAPPE_NIX_CI=1 ${bash} ${frappeShell})"
  [ "$got" = 'frappe-nix: CI mode (standards-fixture, site unset)' ] || fail "frappe-nix's line, no site: $got"
  echo "ok   frappe-nix's banner line, unchanged"

  got="$(env STUB_NIX_CI=1 ${bash} ${noDetails})"
  [ "$got" = 'stub-nix: CI mode (plain)' ] || fail "no details: $got"

  # An empty body is still valid shell.
  ${bash} -n ${pkgs.writeText "empty" (ciMode.unlessCi "")}
  cp ${shell} "$out"
''
