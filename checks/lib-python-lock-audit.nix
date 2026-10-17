# lib.python.lockAudit: the gaps it reports in a fixture workspace and the
# sentence it makes of them, with the default advice and a product's own.
# The audit is pure evaluation, so this is an eval check.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  workspaceRoot = ../tests/fixtures/lock-audit;

  # Stands in for the resolved package set. It holds the normalized names, so
  # a fixture requirement only matches if the audit normalizes it too.
  resolved = [
    "framework-cli"
    "setuptools"
    "ruff"
    "pytest"
    "requests"
    "zope-interface"
    "pillow-simd"
  ];
  hasPackage = name: lib.elem name resolved;

  audit = self.lib.python.lockAudit {
    inherit workspaceRoot hasPackage;
    label = "stub-nix";
  };

  # A product whose workspace is generated into the store supplies its own
  # advice: the gaps are the same, and only the last paragraph differs.
  generated = self.lib.python.lockAudit {
    inherit workspaceRoot hasPackage;
    label = "stub-nix";
    lock = "locks/uv.lock";
    relock = ''
      `nix flake update` moved a pin. Re-lock from this repository:

          nix run .#relock
    '';
  };

  # A workspace whose every declaration resolves.
  clean = self.lib.python.lockAudit {
    inherit workspaceRoot hasPackage;
    label = "stub-nix";
    rootPyproject = {
      project.dependencies = [ "requests" ];
      tool.uv.workspace.members = [ ];
    };
  };

  gaps = result: map (d: "${d.where}\t${d.name}") result.missing;
  fails = value: !(builtins.tryEval (builtins.deepSeq value value)).success;
in
checks.eval "lib-python-lock-audit" {
  expected = {
    # Every gap, and only the gaps: the marker-gated and direct-URL
    # requirements stay out (they can legitimately sit outside a resolution),
    # extras stay out (nothing requests them), apps/beta has no
    # pyproject.toml and apps/gone no directory, and the include-group entry
    # is not a requirement.
    gaps = [
      "apps/alpha/pyproject.toml\tjson-repair"
      "vendor/legacy/pyproject.toml\tvendor-gap"
      "pyproject.toml\troot-only-gap"
    ];
    sameGapsWithOtherAdvice = true;
    cleanReportsNothing = [ ];
    message = {
      label = true;
      namesTheMember = true;
      defaultAdvice = true;
    };
    generatedMessage = {
      namesTheLock = true;
      ownAdvice = true;
      noUvLock = true;
    };
    assertCurrent = {
      passesClean = "env";
      failsStale = true;
    };
  };
  actual = {
    gaps = gaps audit;
    sameGapsWithOtherAdvice = gaps generated == gaps audit;
    cleanReportsNothing = clean.missing;
    message = {
      label = lib.hasPrefix "stub-nix: uv.lock does not cover every declared dependency" audit.message;
      namesTheMember = lib.hasInfix "apps/alpha/pyproject.toml requires json-repair>=0.30, but 'json-repair' is not in uv.lock" audit.message;
      defaultAdvice = lib.hasInfix "uv lock" audit.message;
    };
    generatedMessage = {
      namesTheLock = lib.hasInfix "but 'json-repair' is not in locks/uv.lock" generated.message;
      ownAdvice = lib.hasInfix "nix run .#relock" generated.message;
      noUvLock = !(lib.hasInfix "uv lock" generated.message);
    };
    assertCurrent = {
      passesClean = clean.assertCurrent "env";
      failsStale = fails (audit.assertCurrent "env");
    };
  };
}
