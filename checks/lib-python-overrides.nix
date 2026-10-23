# lib.python.overrides against a stub pyproject-nix package set
# (docs/library.md, design rule 8): a scope of stand-in derivations, each
# installing the file its fix patches, with the line the real package carries. A
# source build gains its headers and a wheel does not; the runtime fixes reach
# both; a package the set lacks stays absent; and a product's overlay layers on
# top.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  # A stand-in for what uv2nix builds: `files` ({ <site-packages path> =
  # text; }) installed into $out/<site-packages>, and the source's setup.cfg
  # (after postPatch) kept at $out/setup.cfg. `format` is uv2nix's
  # passthru.format.
  site = "lib/python3/site-packages";
  stub =
    name:
    {
      format ? "pyproject",
      files ? { },
      setupCfg ? null,
    }:
    pkgs.stdenvNoCC.mkDerivation {
      inherit name;
      src = pkgs.writeTextDir "setup.cfg" (if setupCfg == null then "" else setupCfg);
      dontBuild = true;
      installPhase = ''
        runHook preInstall
        mkdir -p "$out/${site}"
        cp setup.cfg "$out/setup.cfg"
        ${lib.concatStrings (
          lib.mapAttrsToList (path: text: ''
            mkdir -p "$(dirname "$out/${site}/${path}")"
            printf '%s\n' ${lib.escapeShellArg text} > "$out/${site}/${path}"
          '') files
        )}
        runHook postInstall
      '';
      passthru = { inherit format; };
    };

  base = lib.makeScope pkgs.newScope (_: {
    setuptools = stub "setuptools" { };
    cffi = stub "cffi" { };
    pycparser = stub "pycparser" { };
    psycopg2 = stub "psycopg2" { };
    python-ldap = stub "python-ldap" {
      setupCfg = ''
        [_ldap]
        libs = ldap_r lber
      '';
    };
    lxml = stub "lxml" { format = "wheel"; };
    python-magic = stub "python-magic" {
      format = "wheel";
      files."magic/loader.py" = "  for lib in _lib_candidates():";
    };
    pyvips = stub "pyvips" {
      files."pyvips/__init__.py" = "    vips_lib = ffi.dlopen(library_name('vips', 42))";
    };
    # A wheel: the runtime fix applies, the build inputs do not.
    cairocffi = stub "cairocffi" {
      format = "wheel";
      files."cairocffi/__init__.py" = "    ('libcairo.so.2', 'libcairo.2.dylib', 'libcairo-2.dll'))";
    };
    # No pycups: the set must stay without one.
  });

  fixed = base.overrideScope (self.lib.python.overrides { inherit pkgs; });

  # A product's own overlay, layered on top, sees the fixed package.
  layered = base.overrideScope (
    lib.composeManyExtensions [
      (self.lib.python.overrides { inherit pkgs; })
      (_final: prev: {
        psycopg2 = prev.psycopg2.overrideAttrs (old: {
          postInstall = (old.postInstall or "") + "touch $out/product";
        });
      })
    ]
  );

  # The same set with a source-built lxml, whose fix does apply.
  sourceLxml = (base.overrideScope (_: _: { lxml = stub "lxml" { }; })).overrideScope (
    self.lib.python.overrides { inherit pkgs; }
  );

  has =
    drv: dep: lib.elem (lib.getName dep) (map lib.getName (drv.nativeBuildInputs ++ drv.buildInputs));
  ext = pkgs.stdenv.hostPlatform.extensions.sharedLibrary;
  cairoSo = if pkgs.stdenv.hostPlatform.isDarwin then "libcairo.2.dylib" else "libcairo.so.2";
  vipsSo = if pkgs.stdenv.hostPlatform.isDarwin then "libvips.42.dylib" else "libvips.so.42";

  evaluated = checks.eval "lib-python-overrides-eval" {
    expected = {
      pycupsAbsent = true;
      wheelUntouched = true;
      psycopg2 = true;
      pythonLdap = true;
      sourceLxml = true;
      cairocffiWheelNoBuildInputs = true;
      cairocffiCairo = true;
      packageNames = [
        "cairocffi"
        "lxml"
        "psycopg2"
        "pycups"
        "python-ldap"
        "python-magic"
        "pyvips"
      ];
    };
    actual = {
      pycupsAbsent = !(fixed ? pycups);
      # A wheel's derivation is the one uv2nix made: not merely equivalent.
      wheelUntouched = fixed.lxml.drvPath == base.lxml.drvPath;
      psycopg2 = has fixed.psycopg2 pkgs.libpq && has fixed.psycopg2 pkgs.postgresql.pg_config;
      pythonLdap = has fixed.python-ldap pkgs.openldap && has fixed.python-ldap pkgs.cyrus_sasl;
      sourceLxml = has sourceLxml.lxml pkgs.libxml2 && has sourceLxml.lxml pkgs.libxslt;
      cairocffiWheelNoBuildInputs = !(has fixed.cairocffi fixed.cffi);
      cairocffiCairo = has fixed.cairocffi pkgs.cairo;
      packageNames = lib.attrNames (self.lib.python.packageOverrides { inherit pkgs; });
    };
  };
in
checks.script "lib-python-overrides" { } ''
  : ${evaluated}
  site=${lib.escapeShellArg site}
  expect() { # <file> <text>
    grep -qF -- "$2" "$1" || { echo "$1 does not contain: $2"; cat "$1"; exit 1; }
  }
  expect ${fixed.python-ldap}/setup.cfg "libs = ldap lber"
  expect ${fixed.python-magic}/$site/magic/loader.py \
    'for lib in ["${pkgs.file}/lib/libmagic${ext}", *_lib_candidates()]:'
  expect ${fixed.pyvips}/$site/pyvips/__init__.py \
    "ffi.dlopen('${lib.getLib pkgs.vips}/lib/${vipsSo}')"
  expect ${fixed.cairocffi}/$site/cairocffi/__init__.py \
    "'${lib.getLib pkgs.cairo}/lib/${cairoSo}'"
  test -e ${layered.psycopg2}/product
  echo "python-devops-nix: the override set applies to the stub set" | tee "$out"
''
