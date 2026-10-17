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

  # The post-import hook every `.pth` graft carries (python/devguard/_hook.py):
  # `on_import(name, callback)`. Each graft gets its own copy, under its own
  # package, so a graft that ships to production imports nothing from one that
  # must not (PLAN.md, design rule 7). `mkGraft` puts it in place.
  hook = ../python/devguard/_hook.py;

  # A package to graft (`withGrafts`): `src`, the package's own directory
  # (holding its __init__.py), copied to $out/<name>/ with `hook` beside it as
  # _hook.py, so the package imports it as `from ._hook import on_import`.
  # Fails if `src` has a _hook.py of its own. passthru.module is `name`.
  mkGraft =
    {
      pkgs,
      name,
      src,
    }:
    mergeTrees {
      inherit pkgs name;
      what = "lib.python.mkGraft";
      trees = [
        {
          label = "${name}'s tree";
          path = src;
        }
      ];
      files."_hook.py" = hook;
    };

  # Packages grafted into a virtualenv's site-packages with a `.pth` bootstrap,
  # so every interpreter started from the environment runs them at startup:
  # below the framework, without installing anything into it and without
  # editing its configuration.
  #
  # Grafted rather than put on PYTHONPATH, because the application's own code
  # reaches sys.path through uv2nix's editable `.pth` files inside the same
  # virtualenv: an interpreter started outside the development shell (an
  # editor terminal, `nix run`, CI, a stray `sudo -u`) still imports the
  # framework, and would otherwise run without the graft.
  #
  #   python  the virtualenv's interpreter (for its site-packages path)
  #   env     the virtualenv: pyproject-nix's mkVirtualEnv, or any derivation
  #           whose installPhase creates $out/<site-packages>
  #   grafts  packages from mkGraft or lib.devguard.mkPackage, or
  #           { src, module } with the package at <src>/<module>
  #
  # Each graft is loaded by `zzz-<module, _ as ->.pth`, which runs
  # `import <module>; <module>.install()`. `zzz-` orders them after the
  # editable `.pth` files, and install() is called from the `.pth` line rather
  # than the package body, so importing one submodule cannot re-enter a
  # partially initialised package.
  withGrafts =
    {
      python,
      env,
      grafts,
    }:
    let
      normal =
        graft:
        if lib.isDerivation graft then
          {
            src = graft;
            inherit (graft) module;
          }
        else
          graft;
      all = map normal grafts;
      modules = map (graft: graft.module) all;
      sitePackages = "$out/${python.sitePackages}";
    in
    assert lib.assertMsg (
      lib.unique modules == modules
    ) "lib.python.withGrafts: a module is grafted twice: ${lib.concatStringsSep ", " modules}";
    if all == [ ] then
      env
    else
      # postInstall, not postBuild: mkVirtualEnv sets dontBuild and creates the
      # tree from pyprojectMakeVenvHook's installPhase.
      env.overrideAttrs (old: {
        postInstall =
          (old.postInstall or "")
          + lib.concatMapStrings (graft: ''
            cp -r ${graft.src}/${graft.module} "${sitePackages}/"
            printf 'import %s; %s.install()\n' ${graft.module} ${graft.module} \
              > "${sitePackages}/${pthName graft.module}"
          '') all;
      });

  # The `.pth` file that loads `module`: zzz-<module, with _ as ->.pth.
  pthName = module: "zzz-${lib.replaceStrings [ "_" ] [ "-" ] module}.pth";

  # One package directory, $out/<name>/, merged from `trees` (each
  # { label, path }, a package-relative tree) and `files` ({ <relative path> =
  # <file>; }, written last). Fails, naming the files and the trees, when two
  # trees hold the same file or a tree holds one of `files`: one would silently
  # overwrite the other. __pycache__ is left out. `what` names the caller in
  # the failure. passthru.module is `name`.
  mergeTrees =
    {
      pkgs,
      name,
      what,
      trees,
      files ? { },
      passthru ? { },
    }:
    let
      clean =
        tree:
        if builtins.isPath tree.path then
          tree
          // {
            path = builtins.path {
              inherit (tree) path;
              name = "${lib.replaceStrings [ "_" ] [ "-" ] name}-src";
              filter = path: _type: baseNameOf path != "__pycache__";
            };
          }
        else
          tree;
      sources = map clean trees;
    in
    assert lib.assertMsg (
      builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null
    ) "${what}: name ${builtins.toJSON name} is not a Python package name";
    pkgs.runCommandLocal (lib.replaceStrings [ "_" ] [ "-" ] name)
      {
        passthru = passthru // {
          module = name;
        };
      }
      ''
        set -euo pipefail
        : > files
        ${lib.concatMapStrings (tree: ''
          (cd ${tree.path} && find . -name __pycache__ -prune -o \( -type f -o -type l \) -print) \
            | sed -e 's|^\./||' -e "s|$|\t"${lib.escapeShellArg tree.label}"|" >> files
        '') sources}
        cut -f1 files | sort | uniq -d > twice
        if [ -s twice ]; then
          {
            echo "${what} ${name}: these files are in more than one tree, and one would overwrite the other:"
            while read -r file; do
              printf '  %s, in %s\n' "$file" "$(awk -F'\t' -v f="$file" '$1 == f { print $2 }' files | paste -sd'|' | sed 's/|/ and in /g')"
            done < twice
          } >&2
          exit 1
        fi
        ${lib.concatMapStrings (file: ''
          if cut -f1 files | grep -qxF ${lib.escapeShellArg file}; then
            printf '%s\n' ${lib.escapeShellArg "${what} ${name}: ${file} is written by ${what}; remove it from"} \
              "  $(awk -F'\t' -v f=${lib.escapeShellArg file} '$1 == f { print $2 }' files)" >&2
            exit 1
          fi
        '') (lib.attrNames files)}
        mkdir -p "$out/${name}"
        ${lib.concatMapStrings (tree: ''
          cp -r --no-preserve=mode ${tree.path}/. "$out/${name}/"
        '') sources}
        find "$out" -name __pycache__ -prune -exec rm -rf {} +
        ${lib.concatStrings (
          lib.mapAttrsToList (file: source: ''
            mkdir -p "$(dirname "$out/${name}/${file}")"
            cp --no-preserve=mode ${source} "$out/${name}/${file}"
          '') files
        )}
      '';
}
