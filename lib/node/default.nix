# JavaScript targets (docs/conventions.md, JavaScript): a directory with a
# package.json, the package manager its lockfile (or package.json) names, the
# node_modules that lockfile installs without the network, and the tree its
# `build` script produces. One rule set for both products — an Odoo addon, a
# Frappe app or its nested frontend — and the industry's: what Corepack,
# frappe/pilot, Vercel and Netlify do.
#
#   1. No package.json, no target.
#   2. The manager is package.json's `packageManager` (Corepack's
#      "pnpm@10.4.1+sha512…"), else its `devEngines.packageManager` (the
#      { name, version } form; pnpm-12 apps pin through it because yarn 1
#      refuses `packageManager`), else the lockfile's: pnpm-lock.yaml is
#      pnpm, a v1 yarn.lock is yarn, package-lock.json or npm-shrinkwrap.json
#      is npm. Two lockfiles, a declared manager the lockfile disagrees with,
#      a yarn berry lock and a bun lock are evaluation errors that name the fix.
#   3. A package.json directory with no lockfile of its own is a workspace
#      member of the nearest target above it whose lock lists it (pnpm: an
#      `importers:` key; yarn and npm: matched by `workspaces`), installed
#      once with it. One that is not is lockless, and the product decides.
#   4. Managers come from nixpkgs, never from a download: the declared major
#      picks pnpm_<major>, yarn 1 is `yarn`, and npm is the one node ships.
#   5. Installs are frozen, offline and --ignore-scripts.
#   6. Only a target with a `build` script is built: `<manager> run build` in
#      the target, and the result is the target's tree without node_modules.
#      Where the outputs go is the target's own configuration.
#
# Every function takes the product's `label` for its messages.
{ lib }:

let
  pythonLib = import ../python { inherit lib; };
in
rec {
  # The lockfiles, by file name, and the manager each belongs to.
  lockfiles = {
    "pnpm-lock.yaml" = "pnpm";
    "yarn.lock" = "yarn";
    "package-lock.json" = "npm";
    "npm-shrinkwrap.json" = "npm";
    "bun.lock" = "bun";
    "bun.lockb" = "bun";
  };

  # The managers a target can name.
  managers = [
    "npm"
    "pnpm"
    "yarn"
  ];

  # The readers, with the product's label in their messages: yarnLockWith {
  # label } is lib/node/yarn-lock.nix (parse, urlToName, plan,
  # mkOfflineMirror) and pnpmLockWith { label } lib/node/pnpm-lock.nix
  # (importers, members, plan). yarnLock and pnpmLock are the same with
  # "lib.node" as the label.
  yarnLockWith = { label }: import ./yarn-lock.nix { inherit lib label; };
  pnpmLockWith = { label }: import ./pnpm-lock.nix { inherit lib label; };
  yarnLock = yarnLockWith { label = "lib.node"; };
  pnpmLock = pnpmLockWith { label = "lib.node"; };

  # The first lines of a yarn.lock say which yarn wrote it.
  isYarnV1 =
    path: lib.elem "# yarn lockfile v1" (lib.take 8 (lib.splitString "\n" (builtins.readFile path)));

  # What package.json declares, as { name; version; major; via; } or null.
  # `packageManager` is "<name>@<version>[+<hash>]"; devEngines.packageManager
  # is { name, version?, onFail? } or a list of them, of which the first is
  # the one to run.
  declaredManager =
    pj:
    let
      majorOf =
        v:
        let
          m = if v == null then null else builtins.match "[^0-9]*([0-9]+).*" v;
        in
        if m == null then null else builtins.head m;
      fromField =
        let
          m = builtins.match "([^@]+)@([^+]+).*" pj.packageManager;
        in
        if m == null then
          {
            name = pj.packageManager;
            version = null;
          }
        else
          {
            name = builtins.elemAt m 0;
            version = builtins.elemAt m 1;
          };
      devEngine =
        let
          v = pj.devEngines.packageManager;
        in
        if builtins.isList v then builtins.head v else v;
      d =
        if pj ? packageManager then
          fromField // { via = "packageManager"; }
        else if pj ? devEngines.packageManager then
          {
            inherit (devEngine) name;
            version = devEngine.version or null;
            via = "devEngines.packageManager";
          }
        else
          null;
    in
    if d == null then null else d // { major = majorOf d.version; };

  # Workspace patterns (package.json `workspaces`, a list or { packages }) as
  # the member directories under `src` they match that have a package.json.
  # A `*` matches one path segment; `**` is refused rather than guessed at.
  expandWorkspaces =
    {
      src,
      label,
      key,
      patterns,
    }:
    let
      dirsOf =
        rel:
        let
          p = if rel == "" then src else src + "/${rel}";
        in
        if builtins.pathExists p then
          lib.attrNames (lib.filterAttrs (n: t: t == "directory" && n != "node_modules") (builtins.readDir p))
        else
          [ ];
      join = a: b: if a == "" then b else "${a}/${b}";
      step =
        rels: seg:
        if seg == "**" then
          throw "${label}: ${key}'s package.json workspaces use `**`; list the member folders, or one `*` per level"
        else if lib.hasInfix "*" seg then
          let
            re = lib.replaceStrings [ "." "*" ] [ "\\." "[^/]*" ] seg;
          in
          lib.concatMap (r: map (join r) (lib.filter (n: builtins.match re n != null) (dirsOf r))) rels
        else
          map (r: join r seg) rels;
      expand =
        pattern:
        builtins.foldl' step [ "" ] (
          lib.filter (s: s != "" && s != ".") (lib.splitString "/" (lib.removePrefix "./" pattern))
        );
      positive = lib.filter (p: !lib.hasPrefix "!" p) patterns;
    in
    lib.unique (
      lib.filter (r: r != "" && builtins.pathExists (src + "/${r}/package.json")) (
        lib.concatMap expand positive
      )
    );

  # A directory as a target, or null without a package.json:
  #   { key; src; manager; declared; version; major; lockFile; lockName;
  #     packageJson; scripts; hasBuild; hasWatch; hasDeps; members;
  #     memberBuilds; }
  # `manager` is "npm" for a target with neither lockfile nor declaration (it
  # can still have a build script that needs nothing installed). `members`
  # are the workspace members the lockfile covers, relative to `src`.
  detect =
    {
      src,
      label,
      key ? toString src,
    }:
    let
      has = f: builtins.pathExists (src + "/${f}");
    in
    if !has "package.json" then
      null
    else
      let
        pj = lib.importJSON (src + "/package.json");
        present = lib.filter has (lib.attrNames lockfiles);
        lockName =
          if present == [ ] then
            null
          else if builtins.length present == 1 then
            builtins.head present
          else
            throw "${label}: ${key} has ${lib.concatStringsSep " and " present}; one folder installs with one package manager — delete the lockfile it does not use";
        lockFile = if lockName == null then null else src + "/${lockName}";
        fromLock =
          if lockName == null then
            null
          else if lockfiles.${lockName} == "bun" then
            throw "${label}: ${key}/${lockName} is a bun lock, which cannot be installed offline here; use pnpm (`pnpm import` reads the package.json), npm or yarn 1"
          else if lockName == "yarn.lock" && !isYarnV1 lockFile then
            throw "${label}: ${key}/yarn.lock is a yarn berry (2+) lock; yarn 1, npm and pnpm install offline here — `pnpm import` converts it"
          else
            lockfiles.${lockName};
        declared = declaredManager pj;
        manager =
          if declared == null then
            if fromLock == null then "npm" else fromLock
          else if !lib.elem declared.name managers then
            throw "${label}: ${key}'s package.json names ${declared.name} (${declared.via}); one of ${lib.concatStringsSep ", " managers} installs here"
          else if declared.name == "yarn" && declared.major != null && declared.major != "1" then
            throw "${label}: ${key}'s package.json names yarn ${declared.version} (${declared.via}), which is yarn berry; yarn 1, npm and pnpm install offline here"
          else if fromLock != null && fromLock != declared.name then
            throw "${label}: ${key}'s package.json names ${declared.name} (${declared.via}), but its lockfile is ${lockName}; regenerate the lock with ${declared.name}, or change ${declared.via}"
          else
            declared.name;
        scripts = pj.scripts or { };
        nonEmpty = f: (pj.${f} or { }) != { };
        members =
          if lockName == null then
            [ ]
          else if manager == "pnpm" then
            lib.filter (m: has "${m}/package.json") ((pnpmLockWith { inherit label; }).members lockFile)
          else
            let
              ws = pj.workspaces or [ ];
            in
            expandWorkspaces {
              inherit src label key;
              patterns = if builtins.isList ws then ws else ws.packages or [ ];
            };
      in
      {
        inherit
          key
          src
          manager
          declared
          lockFile
          lockName
          scripts
          members
          ;
        version = if declared == null then null else declared.version;
        major = if declared == null then null else declared.major;
        packageJson = pj;
        hasBuild = scripts ? build;
        # The members with a build script of their own: what is built when
        # the root has none (pilot's rule turned around: a root build script
        # builds the workspace, so the members' are not run beside it).
        memberBuilds = lib.filter (
          m: (lib.importJSON (src + "/${m}/package.json")).scripts.build or null != null
        ) members;
        hasWatch = scripts ? watch;
        hasDeps = nonEmpty "dependencies" || nonEmpty "devDependencies" || nonEmpty "optionalDependencies";
      };

  # Candidate directories, [ { key; src; } ] with keys as paths relative to
  # one root ("custom/acme_ui", "apps/hrms/frontend", or "." for the root
  # itself), sorted into
  #   targets   the directories with a lockfile (detect's result, `key` kept)
  #   covered   [ { key; root; } ] the workspace members a target's lock
  #             installs, with that target's key
  #   lockless  the rest with a package.json (detect's result)
  # A directory without a package.json is in none of them.
  discover =
    { label, candidates }:
    let
      found = lib.filter (t: t != null) (
        map (
          c:
          detect {
            inherit (c) src key;
            inherit label;
          }
        ) candidates
      );
      roots = lib.sort (a: b: lib.stringLength a.key > lib.stringLength b.key) (
        lib.filter (t: t.lockFile != null) found
      );
      under = root: key: root == "." || lib.hasPrefix "${root}/" key;
      relTo = root: key: if root == "." then key else lib.removePrefix "${root}/" key;
      rootOf =
        t:
        lib.findFirst (
          r: r.key != t.key && under r.key t.key && lib.elem (relTo r.key t.key) r.members
        ) null roots;
      unlocked = lib.filter (t: t.lockFile == null) found;
      withRoot = map (t: t // { root = rootOf t; }) unlocked;
    in
    {
      targets = lib.filter (t: t.lockFile != null) found;
      covered = map (t: {
        inherit (t) key;
        root = t.root.key;
      }) (lib.filter (t: t.root != null) withRoot);
      lockless = map (t: removeAttrs t [ "root" ]) (lib.filter (t: t.root == null) withRoot);
    };

  # The package that runs a target's manager.
  managerPackage =
    {
      pkgs,
      target,
      nodejs,
      label,
    }:
    if target.manager == "npm" then
      nodejs
    else if target.manager == "yarn" then
      pkgs.yarn
    else if target.major == null then
      pkgs.pnpm
    else
      pkgs."pnpm_${target.major}"
        or (throw "${label}: ${target.key}'s package.json names pnpm ${target.version} (${target.declared.via}), and nixpkgs has no pnpm_${target.major}; it has ${
          lib.concatStringsSep ", " (
            lib.filter (n: builtins.match "pnpm_[0-9]+" n != null && (builtins.tryEval pkgs.${n}).success) (
              lib.attrNames pkgs
            )
          )
        }");

  # Environment for any run of a manager in the sandbox: no version switch
  # (a pnpm 11+ that finds devEngines naming another version downloads it,
  # and without a network it retries until the build times out), no install
  # before `pnpm run` (pnpm 12's verifyDepsBeforeRun, whose supply-chain
  # check fetches every tarball again), no update check.
  sandboxEnv = {
    pnpm_config_pm_on_fail = "ignore";
    pnpm_config_verify_deps_before_run = "false";
    pnpm_config_update_notifier = "false";
    npm_config_update_notifier = "false";
    npm_config_fund = "false";
    npm_config_audit = "false";
  };

  # A product's overrides over a derivation's attributes: each replaces the
  # attribute it names, but `env` is merged into the one there.
  withOverrides =
    overrides: attrs:
    attrs
    // overrides
    // lib.optionalAttrs (overrides ? env) { env = attrs.env or { } // overrides.env; };

  # Only what an install reads, so a node_modules does not rebuild for an
  # edit to the target's code: package.json, the lock (under its own name),
  # the manager's settings and each workspace member's package.json.
  manifests =
    {
      pkgs,
      target,
      lockFile ? target.lockFile,
      name,
    }:
    let
      lockName = if lockFile == target.lockFile then target.lockName else baseNameOf (toString lockFile);
      optional =
        dir: f:
        lib.optionalString (builtins.pathExists (target.src + "/${dir}${f}")) ''
          mkdir -p "$out/${dir}"
          cp ${target.src + "/${dir}${f}"} "$out/${dir}${f}"
        '';
      settings = [
        ".npmrc"
        ".yarnrc"
        ".pnpmfile.cjs"
        "pnpm-workspace.yaml"
      ];
    in
    pkgs.runCommand "${name}-manifests" { } ''
      mkdir -p $out
      cp ${target.src + "/package.json"} $out/package.json
      cp ${lockFile} $out/${lockName}
      ${lib.concatMapStrings (optional "") settings}
      ${lib.concatMapStrings (
        m: optional "${m}/" "package.json" + optional "${m}/" ".npmrc"
      ) target.members}
    '';

  # A target's node_modules: $out/node_modules, and $out/<member>/node_modules
  # for each workspace member that has one. null for a target with nothing to
  # install; an evaluation error for one with dependencies and no lockfile.
  #
  #   lockFile   the lock to install from: the target's own by default, or one
  #              the product keeps for it (a fallback for a lockless app)
  #   pnpm2nix   the pnpm2nix-nzbr source, for pnpm targets
  #   overrides  merged into the derivation: an install script a package
  #              cannot do without (`postInstall = "npm rebuild <x>"`), a
  #              message, an environment variable
  modules =
    {
      pkgs,
      label,
      target,
      nodejs,
      lockFile ? target.lockFile,
      pnpm2nix ? null,
      overrides ? { },
      name ? lib.strings.sanitizeDerivationName (lib.replaceStrings [ "/" ] [ "-" ] target.key),
    }:
    if lockFile == null then
      if target.hasDeps then
        throw "${label}: ${target.key} has dependencies and no lockfile, so nothing says which versions to install; run `${target.manager} install` there and commit the lockfile"
      else
        null
    else
      let
        kind = lockfiles.${baseNameOf (toString lockFile)} or target.manager;
        manager = managerPackage {
          inherit
            pkgs
            target
            nodejs
            label
            ;
        };
        src = manifests {
          inherit
            pkgs
            target
            lockFile
            name
            ;
        };
        byManager = {
          npm = {
            nativeBuildInputs = [
              nodejs
              pkgs.importNpmLock.npmConfigHook
            ];
            npmDeps = pkgs.importNpmLock {
              npmRoot = target.src;
              package = target.packageJson;
              packageLock = lib.importJSON lockFile;
              pname = target.packageJson.name or name;
              version = target.packageJson.version or "0.0.0";
            };
            npmFlags = [ "--ignore-scripts" ];
          };

          # yarnConfigHook's own flags: --offline --frozen-lockfile
          # --ignore-scripts --ignore-engines --ignore-platform.
          yarn = {
            nativeBuildInputs = [
              pkgs.yarnConfigHook
              manager
              nodejs
            ];
            yarnOfflineCache = (yarnLockWith { inherit label; }).mkOfflineMirror {
              inherit pkgs lockFile name;
            };
          };

          # pnpm2nix-nzbr's derivation.nix, without its build: every tarball
          # into the store, then a frozen install from the rewritten lock.
          pnpm =
            let
              pnpmLock' = pnpmLockWith { inherit label; };
              plan = pnpmLock'.plan {
                inherit pkgs pnpm2nix lockFile;
                registry = pnpmLock'.registryOf target.src;
              };
              patchedLock = pkgs.writeText "pnpm-lock.yaml" (builtins.toJSON plan.patchedLockfile);
              tarballs = pkgs.writeText "${name}-pnpm-tarballs" (
                lib.concatMapStringsSep "\n" toString (lib.unique plan.dependencyTarballs)
              );
            in
            {
              nativeBuildInputs = [
                nodejs
                manager
              ];
              # The lock's shape is pnpm2nix's rewrite of it, not what pnpm
              # would write; trusting it skips the resolution-shape check.
              env.pnpm_config_trust_lockfile = "true";
              env.PNPM_CONFIG_TRUST_LOCKFILE = "true";
              configurePhase = ''
                runHook preConfigure
                export HOME="$NIX_BUILD_TOP/home"
                mkdir -p "$HOME"
                export npm_config_nodedir=${nodejs}
                store=$(pnpm store path)
                mkdir -p "$(dirname "$store")"
                cp -f ${patchedLock} pnpm-lock.yaml
                pnpm store add --silent $(cat ${tarballs})
                pnpm install --ignore-scripts --force --frozen-lockfile
                runHook postConfigure
              '';
            };
        };
        attrs = byManager.${kind};
      in
      pkgs.stdenv.mkDerivation (
        withOverrides overrides (
          attrs
          // {
            name = "${name}-node-modules";
            inherit src;
            env = sandboxEnv // attrs.env or { };
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cp -R node_modules $out/node_modules
              ${lib.concatMapStrings (m: ''
                if [ -d ${lib.escapeShellArg m}/node_modules ]; then
                  mkdir -p $out/${lib.escapeShellArg m}
                  cp -R ${lib.escapeShellArg m}/node_modules $out/${lib.escapeShellArg m}/node_modules
                fi
              '') target.members}
              runHook postInstall
            '';
            passthru = {
              inherit target lockFile;
              manager = kind;
            };
          }
        )
      );

  # Shell that links a node_modules from the store into the tree under the
  # current directory, entry by entry into real directories (a bundler writes
  # beside its packages: Vite's node_modules/.vite-temp), for the target root
  # and each member. A link the store's copy resolves to a workspace member —
  # the store holds only the member's node_modules, not its code — is pointed
  # at the member in the tree instead. Expects $modules set.
  linkModules = members: ''
    link_entry() {
      local real
      real=$(readlink -f "$2")
      case "$real" in
        "$modules"/node_modules/* | "$modules"/*/node_modules/*) ln -s "$2" "$1" ;;
        "$modules"/*) ln -s "$PWD/''${real#"$modules"/}" "$1" ;;
        *) ln -s "$2" "$1" ;;
      esac
    }
    link_modules() {
      local dir=$1 from=$2 e s name
      [ -d "$from" ] || return 0
      rm -rf "$dir/node_modules"
      mkdir -p "$dir/node_modules"
      for e in "$from"/* "$from"/.[!.]*; do
        [ -e "$e" ] || [ -L "$e" ] || continue
        name=''${e##*/}
        if [ "''${name#@}" != "$name" ] && [ -d "$e" ] && [ ! -L "$e" ]; then
          mkdir -p "$dir/node_modules/$name"
          for s in "$e"/*; do
            [ -e "$s" ] || [ -L "$s" ] || continue
            link_entry "$dir/node_modules/$name/''${s##*/}" "$s"
          done
        else
          link_entry "$dir/node_modules/$name" "$e"
        fi
      done
    }
    link_modules . "$modules/node_modules"
    ${lib.concatMapStrings (m: ''
      link_modules ${lib.escapeShellArg m} "$modules/"${lib.escapeShellArg m}"/node_modules"
    '') members}
  '';

  # Whether a target builds: a build script of its own, or members with one.
  builds = target: target.hasBuild || target.memberBuilds != [ ];

  # The target's tree after `<manager> run build`, without node_modules: in
  # the target, or, when it has no build script, in each member that has one.
  #
  #   nodeModules  modules' result (null when there is nothing to install)
  #   buildDirs    where to run it, relative to the target
  #   src          the tree to build: the target's, without node_modules
  #   env          more environment for the build
  #   overrides    merged into the derivation
  build =
    {
      pkgs,
      label,
      target,
      nodejs,
      nodeModules,
      src ? lib.cleanSourceWith {
        inherit (target) src;
        filter = path: type: baseNameOf path != "node_modules" && lib.cleanSourceFilter path type;
        name = "${name}-source";
      },
      env ? { },
      overrides ? { },
      name ? lib.strings.sanitizeDerivationName (lib.replaceStrings [ "/" ] [ "-" ] target.key),
      buildDirs ? if target.hasBuild then [ "." ] else target.memberBuilds,
    }:
    assert lib.assertMsg (buildDirs != [ ])
      "${label}: ${target.key} has no build script, nor a workspace member with one, so there is nothing to build";
    pkgs.stdenv.mkDerivation (
      withOverrides overrides {
        name = "${name}-built";
        inherit src;
        nativeBuildInputs = [
          nodejs
          (managerPackage {
            inherit
              pkgs
              target
              nodejs
              label
              ;
          })
        ];
        env = sandboxEnv // env;
        dontConfigure = true;
        buildPhase = ''
          runHook preBuild
          export HOME="$NIX_BUILD_TOP/home"
          mkdir -p "$HOME"
          ${lib.optionalString (nodeModules != null) ''
            modules=${nodeModules}
            ${linkModules target.members}
          ''}
          ${lib.concatMapStrings (d: ''
            (cd ${lib.escapeShellArg d} && ${target.manager} run build)
          '') buildDirs}
          runHook postBuild
        '';
        installPhase = ''
          runHook preInstall
          find . -name node_modules -prune -exec rm -rf {} +
          cp -r . $out
          runHook postInstall
        '';
        dontFixup = true;
        passthru = { inherit target nodeModules; };
      }
    );

  # python/node.py, for a product that imports it in-process.
  module = ../../python/node.py;

  # The shell's command: `<name> {list,install,build,watch} [KEY...]` over the
  # targets in a JSON file (`targets`: [ { key; path; } ], paths relative to
  # the directory the command runs in, the repository root). install is the
  # online, frozen install the dev shell runs on entry (skipped when the
  # manifests have not changed since the last one); build and watch run the
  # targets' scripts.
  #
  #   label   the message prefix
  #   ciVar   the environment variable that marks CI mode (lib.ciMode), in
  #           which install does nothing
  command =
    {
      pkgs,
      label,
      targets,
      ciVar ? null,
      python ? pkgs.python3,
      name ? "${label}-node",
    }:
    pythonLib.command {
      inherit pkgs name python;
      modules = builtins.path {
        name = "node";
        path = ../../python;
        filter = path: _type: baseNameOf path == "node.py";
      };
      imports = ''
        import node
      '';
      main = ''
        node.main(
            sys.argv[1:],
            label=${builtins.toJSON label},
            targets=${builtins.toJSON (toString targets)},
            ci_var=${if ciVar == null then "None" else builtins.toJSON ciVar},
            prog=${builtins.toJSON name},
        )'';
      passthru = { inherit targets; };
    };

  # The base's tests (tests/node.nix): detection over every fixture, the
  # yarn.lock reader, a built node_modules and build per manager, and the
  # command's unit tests on every interpreter a preset names.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "node",
      interpreters ? pythonLib.defaultInterpreters,
      pnpm2nix,
    }:
    import ../../tests/node.nix {
      inherit
        lib
        pkgs
        product
        name
        interpreters
        pnpm2nix
        ;
      node = import ./. { inherit lib; };
    };
}
