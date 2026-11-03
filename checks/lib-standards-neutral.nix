# The core names no product and no organisation (standards/DESIGN.md §10.2): a
# grep over standards/, its composite actions, lib/standards.nix and these
# checks. DESIGN.md, the design record, is the one file left out.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  # Built from fragments, so this file does not match itself.
  pattern = lib.concatStringsSep "|" [
    ("fra" + "ppe")
    ("od" + "oo")
    ("av" + "unu")
  ];

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../standards
      ../lib/standards.nix
      (lib.fileset.fileFilter (f: lib.hasPrefix "lib-standards" f.name) ../checks)
      (lib.fileset.maybeMissing ../.github/actions)
    ];
  };
in
checks.script "lib-standards-neutral"
  {
    inherit src;
    tools = [ pkgs.gnugrep ];
  }
  ''
    paths=(standards lib/standards.nix checks/lib-standards*.nix)
    for dir in .github/actions/standards-*/; do
      if [ -d "$dir" ]; then paths+=("$dir"); fi
    done
    status=0
    grep -rniE ${lib.escapeShellArg pattern} "''${paths[@]}" \
      --exclude=DESIGN.md --exclude-dir=__pycache__ > hits || status=$?
    if [ "$status" = 0 ]; then
      echo "product or organisation names in the core:" >&2
      cat hits >&2
      exit 1
    fi
    if [ "$status" != 1 ]; then
      echo "grep failed with status $status" >&2
      exit 1
    fi
    echo "neutral: ''${paths[*]}" > "$out"
  ''
