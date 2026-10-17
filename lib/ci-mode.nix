# CI mode for a development shell (PLAN.md, Phase 5): what enterShell does
# differently when <P>_NIX_CI is set. frappe-nix's lib/ci-mode.nix, with the
# variable and the label passed in.
#
# Read when enterShell runs, not at evaluation, so one shell derivation serves
# both. A CI job enters the shell once (`nix develop --no-pure-eval -c <the
# test command>`) and needs neither the interactive steps (frappe-nix's
# node_modules install costs about two minutes of yarn per entry on a cold
# runner) nor the banner.
#
#   ciMode { label = "odoo-nix"; envPrefix = "ODOO"; }
#
# returns shell snippets for the product's enterShell:
#
#   isCi        a condition: true when <P>_NIX_CI is set to anything but "",
#               "0" or "false"
#   exports     the first thing enterShell runs: in CI mode,
#               DEVENV_IN_DIRENV_SHELL=true and PC_TUI_ENABLED=0, which `devenv
#               up` from a script needs (the flake-compat wrapper otherwise
#               re-enters the shell, and process-compose's TUI has no terminal
#               to draw on)
#   unlessCi    unlessCi <shell>: <shell> only outside CI mode
#   banner      banner { name, details ? "", interactive }: `interactive`
#               outside CI mode, and in it one line,
#               "<label>: CI mode (<name>[, <details>])". `name` is escaped;
#               `details` is shell, expanded when the shell starts, so a value
#               set in .env is the one named
{ lib }:

{
  label,
  envPrefix,
}:
let
  variable = "${envPrefix}_NIX_CI";
  # Safe inside double quotes.
  quote = lib.escape [
    "\""
    "$"
    "`"
    "\\"
  ];
in
rec {
  inherit variable;

  isCi = ''case "''${${variable}:-}" in "" | 0 | false) false ;; *) true ;; esac'';

  exports = ''
    if ${isCi}; then
      export DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0
    fi
  '';

  unlessCi = body: ''
    if ! ${isCi}; then
      :
      ${body}
    fi
  '';

  banner =
    {
      name,
      details ? "",
      interactive,
    }:
    ''
      if ${isCi}; then
        echo "${quote label}: CI mode (${quote name}${lib.optionalString (details != "") ", ${details}"})"
      else
        ${interactive}
      fi
    '';
}
