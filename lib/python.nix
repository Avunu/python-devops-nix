# Helpers for the Python this repository ships (PLAN.md, Python versions).
{ lib }:
rec {
  # The interpreters the products' presets name, as nixpkgs attribute names.
  #
  # `presets` is a list of the products' presets files: JSON objects whose
  # every value carries a `python` attribute. A product passes its own, so a
  # preset that adds an interpreter adds it to the matrix. With none, the
  # union the presets name today (3.11, 3.12 and 3.14).
  interpreters =
    {
      presets ? [ ],
    }:
    if presets == [ ] then
      defaultInterpreters
    else
      lib.sort builtins.lessThan (
        lib.unique (
          lib.concatMap (file: map (preset: preset.python) (lib.attrValues (lib.importJSON file))) presets
        )
      );

  defaultInterpreters = [
    "python311"
    "python312"
    "python314"
  ];

  # A command that runs one call into stdlib-only modules.
  #
  # `modules` is a directory put first on sys.path; `main` is a Python
  # expression evaluated after `imports`, whose value is the exit status. The
  # command runs isolated (-I), so neither PYTHONPATH nor the caller's
  # directory can put another module in front of the ones named here.
  #
  # Not writers.writePython3Bin: that runs flake8 with its own opinions at
  # build time. A shebang'd text file is dependency-free.
  command =
    {
      pkgs,
      name,
      modules,
      imports,
      main,
      python ? pkgs.python3,
      passthru ? { },
    }:
    pkgs.writeTextFile {
      inherit name passthru;
      destination = "/bin/${name}";
      executable = true;
      text = ''
        #!${python.interpreter} -I
        import sys

        sys.path.insert(0, ${builtins.toJSON "${modules}"})
        ${imports}

        sys.exit(${main})
      '';
      meta.mainProgram = name;
    };
}
