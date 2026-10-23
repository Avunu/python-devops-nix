# Fixes for the Python packages both products build that need a system library
# (docs/library.md, Python environment), as pyproject-nix package-set overlays
# (`final: prev: { ... }`). Each takes the better of the two products' fixes:
#
#   psycopg2     pg_config to build, libpq to link                 (odoo-nix)
#   python-ldap  OpenLDAP and Cyrus SASL, and setup.cfg's `ldap_r`
#                linked as `ldap` (OpenLDAP 2.5 merged them)       (odoo-nix)
#   lxml         libxml2 and libxslt headers                       (odoo-nix)
#   pycups       CUPS headers and library                          (both, the same)
#   python-magic libmagic's store path tried first                 (odoo-nix)
#   pyvips       libvips's store path, not a bare soname           (frappe-nix)
#   cairocffi    libcairo's store path, not a bare soname          (frappe-nix)
#
# Two kinds of fix, applied differently:
#
#   - Build fixes (headers, pkg-config, setuptools, the ldap_r link) matter
#     only when the package builds from its sdist, so a package uv2nix
#     installs from a wheel (`sourcePreference = "wheel"`) is left exactly as
#     it was. A wheel gains nothing from headers, and leaving it alone keeps
#     its derivation unchanged.
#   - Runtime fixes (a library's path where the package dlopens it) apply to
#     both, and patch the installed files, so they reach a wheel too. Nix has
#     no ldconfig cache and nothing on LD_LIBRARY_PATH, so ctypes.util.
#     find_library and a dlopen of a bare soname find nothing outside a dev
#     shell. Putting a library's /lib on LD_LIBRARY_PATH instead is a trap:
#     cairo's carries its own libexpat, which shadows the interpreter's and
#     breaks every XML parse in the process.
#
# Every overlay touches only a package the set has (`prev ? <name>`), so the
# set applies to any lock. A product composes it after uv2nix's overlay and
# layers its own on top:
#
#   pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; })
#     .overrideScope (lib.composeManyExtensions [
#       pyproject-build-systems.overlays.default
#       (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
#       (python-devops-nix.lib.python.overrides { inherit pkgs; })
#       productOverrides    # mysqlclient, the framework's own build, ...
#     ]);
#
# pyproject-nix and uv2nix stay the product's inputs (docs/library.md, design
# rule 3): an overlay is a plain function, and nothing here needs their library.
{ lib }:
let
  # uv2nix records how it builds each package in passthru.format: "wheel" or
  # "pyproject" (an sdist, a git or a path source). A set without it (a stub)
  # builds from source.
  isWheel = drv: (drv.passthru.format or null) == "wheel";

  # `fix` applied to a source build, and nothing to a wheel.
  fromSource = drv: fix: if isWheel drv then drv else drv.overrideAttrs fix;

  # The overlay for package `name`: `fix final prev` is the new derivation,
  # and the overlay is empty where the set has no such package.
  only =
    name: fix: final: prev:
    lib.optionalAttrs (prev ? ${name}) { ${name} = fix final prev; };

  # Shell for postInstall: in each installed file at `path` (a find -path
  # pattern), replace `from` with `to`, failing if the file no longer says
  # `from`. The installed file, not the source, so a wheel is patched too.
  replaceInstalled =
    {
      path,
      from,
      to,
    }:
    ''
      for f in $(find "$out" -path ${lib.escapeShellArg path}); do
        substituteInPlace "$f" --replace-fail ${lib.escapeShellArg from} ${lib.escapeShellArg to}
      done
    '';

  # A shared library's file name on the host: <base>.so.<abi> or
  # <base>.<abi>.dylib.
  soname =
    pkgs: base: abi:
    if pkgs.stdenv.hostPlatform.isDarwin then "${base}.${abi}.dylib" else "${base}.so.${abi}";
in
rec {
  # The fixes one by one, as { <package> = overlay; }, for a product that
  # exports them to its consumers under their own names.
  packageOverrides =
    { pkgs }:
    {
      # psycopg2 (the source package; Odoo requires it, not psycopg2-binary)
      # needs pg_config to build and libpq to link. In current nixpkgs
      # pg_config is split out as postgresql.pg_config, and the client library
      # is libpq.
      psycopg2 = only "psycopg2" (
        final: prev:
        fromSource prev.psycopg2 (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.setuptools
            pkgs.postgresql.pg_config
          ];
          buildInputs = (old.buildInputs or [ ]) ++ [
            pkgs.libpq
            pkgs.openssl
            pkgs.zlib
          ];
        })
      );

      # python-ldap needs OpenLDAP and Cyrus SASL headers. Its setup.cfg also
      # links the reentrant libldap_r, which OpenLDAP 2.5 merged into libldap,
      # so the link target becomes plain `ldap`.
      python-ldap = only "python-ldap" (
        final: prev:
        fromSource prev.python-ldap (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.setuptools
            pkgs.pkg-config
          ];
          buildInputs = (old.buildInputs or [ ]) ++ [
            pkgs.openldap
            pkgs.cyrus_sasl
            pkgs.openssl
          ];
          postPatch = (old.postPatch or "") + ''
            if [ -f setup.cfg ]; then
              substituteInPlace setup.cfg --replace-quiet "ldap_r" "ldap"
            fi
          '';
        })
      );

      # lxml needs libxml2 and libxslt headers.
      lxml = only "lxml" (
        final: prev:
        fromSource prev.lxml (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.setuptools
            pkgs.pkg-config
            pkgs.libxml2.dev
            pkgs.libxslt.dev
          ];
          buildInputs = (old.buildInputs or [ ]) ++ [
            pkgs.libxml2
            pkgs.libxslt
            pkgs.zlib
          ];
        })
      );

      # pycups needs CUPS headers and libraries.
      pycups = only "pycups" (
        final: prev:
        fromSource prev.pycups (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.setuptools
            pkgs.pkg-config
            pkgs.cups.dev
          ];
          buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.cups ];
        })
      );

      # python-magic finds libmagic with ctypes.util.find_library, which finds
      # nothing on NixOS: it worked only in a dev shell whose LD_LIBRARY_PATH
      # carried `file`'s lib. nixpkgs' libmagic is tried first, as nixpkgs' own
      # python-magic does, and the stock lookup stays behind it.
      python-magic = only "python-magic" (
        _final: prev:
        prev.python-magic.overrideAttrs (old: {
          postInstall = (old.postInstall or "") + ''
            for f in $(find "$out" -path '*/magic/loader.py'); do
              substituteInPlace "$f" --replace-fail \
                'for lib in _lib_candidates():' \
                'for lib in ["${pkgs.file}/lib/libmagic${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}", *_lib_candidates()]:'
            done
          '';
        })
      );

      # pyvips's ABI mode dlopens libvips by soname at import time. Its store
      # path instead, which resolves libvips's own dependencies through the
      # RUNPATH Nix baked into it.
      pyvips = only "pyvips" (
        _final: prev:
        prev.pyvips.overrideAttrs (old: {
          buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.vips ];
          postInstall =
            (old.postInstall or "")
            + replaceInstalled {
              path = "*/pyvips/__init__.py";
              from = "library_name('vips', 42)";
              to = "'${lib.getLib pkgs.vips}/lib/${soname pkgs "libvips" "42"}'";
            };
        })
      );

      # cairocffi is a cffi wrapper, not a compiled extension: it dlopens
      # libcairo by file name at import time. Its store path instead, which
      # resolves cairo's own dependencies (libpng, freetype, fontconfig,
      # pixman, X11) through cairo's RUNPATH with nothing on LD_LIBRARY_PATH.
      cairocffi = only "cairocffi" (
        final: prev:
        (fromSource prev.cairocffi (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.setuptools
            final.cffi
            final.pycparser
          ];
        })).overrideAttrs
          (old: {
            buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.cairo ];
            postInstall =
              (old.postInstall or "")
              + replaceInstalled {
                path = "*/cairocffi/__init__.py";
                from = "'${soname pkgs "libcairo" "2"}'";
                to = "'${lib.getLib pkgs.cairo}/lib/${soname pkgs "libcairo" "2"}'";
              };
          })
      );
    };

  # Every fix in packageOverrides, as one overlay.
  overrides =
    { pkgs }:
    lib.composeManyExtensions (
      lib.attrValues (packageOverrides {
        inherit pkgs;
      })
    );
}
