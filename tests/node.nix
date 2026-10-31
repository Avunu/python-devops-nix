# lib.node's tests (lib.node.check): detection over every fixture in
# tests/fixtures/node, discovery and the readers at evaluation time; a real
# offline node_modules and build per manager, a pnpm 12 workspace among them;
# the yarn.lock reader (tests/yarn-lock.nix); the command; and python/node.py's
# unit tests on every interpreter a preset names, with lib.node.detect's own
# answers for the fixtures so the two cannot drift apart.
#
# The builds fetch the fixtures' few registry tarballs by the hashes their
# lockfiles carry, as a product's builds do.
{
  lib,
  pkgs,
  product,
  name,
  interpreters,
  pnpm2nix,
  node,
}:

let
  checks = (import ../lib/checks { inherit lib; }).mk { inherit pkgs product; };
  fixtures = ./fixtures/node;
  label = "stub-nix";
  inherit (pkgs) nodejs;
  sh = lib.escapeShellArg;

  # The value, or "error" where it throws.
  attempt =
    x:
    let
      r = builtins.tryEval (builtins.deepSeq x x);
    in
    if r.success then r.value else "error";

  targetAt =
    fixture:
    node.detect {
      src = fixtures + "/${fixture}";
      inherit label;
      key = fixture;
    };
  summary =
    t:
    if t == null then
      null
    else
      {
        inherit (t)
          manager
          lockName
          hasBuild
          hasDeps
          members
          memberBuilds
          major
          ;
      };

  names = lib.attrNames (builtins.readDir fixtures);
  detected = lib.genAttrs names (n: attempt (summary (targetAt n)));
  nixDetect = pkgs.writeText "nix-detect.json" (builtins.toJSON detected);

  row = manager: lockName: hasBuild: hasDeps: members: major: {
    inherit
      manager
      lockName
      hasBuild
      hasDeps
      members
      major
      ;
    memberBuilds = [ ];
  };
  expected = {
    ambiguous = "error";
    berry = "error";
    bun = "error";
    mismatch = "error";
    corepack = row "yarn" "yarn.lock" false true [ ] "1";
    lockless = row "npm" null true true [ ] null;
    no-build = row "npm" "package-lock.json" false true [ ] null;
    no-deps = row "npm" null true false [ ] null;
    npm = row "npm" "package-lock.json" true true [ ] null;
    old-major = row "pnpm" "pnpm-lock.yaml" false true [ ] "7";
    pnpm = row "pnpm" "pnpm-lock.yaml" true true [ ] null;
    pnpm-workspace = row "pnpm" "pnpm-lock.yaml" true true [ "types" ] "12";
    yarn = row "yarn" "yarn.lock" true true [ ] null;
    yarn-workspace =
      row "yarn" "yarn.lock" false false [
        "frontend"
        "packages/a"
      ] null
      // {
        memberBuilds = [ "frontend" ];
      };
  };

  # A yarn workspace: the root's lock covers frontend/ and packages/a, which
  # have none; e2e/ has its own; tools/ has neither.
  discovered = node.discover {
    inherit label;
    candidates =
      map
        (key: {
          inherit key;
          src = fixtures + "/yarn-workspace${lib.optionalString (key != ".") "/${key}"}";
        })
        [
          "."
          "frontend"
          "packages/a"
          "e2e"
          "tools"
          "missing"
        ];
  };

  wsLock = fixtures + "/pnpm-workspace/pnpm-lock.yaml";
  pnpmLock = node.pnpmLockWith { inherit label; };
  managerOf =
    fixture:
    (node.managerPackage {
      inherit pkgs nodejs label;
      target = targetAt fixture;
    }).name;

  # Each as [ what expected actual ].
  evalCases = [
    [
      "detect: every fixture, as tests/node.nix expects"
      expected
      detected
    ]
    [
      "discover: lockfiles make targets"
      [
        "."
        "e2e"
      ]
      (map (t: t.key) discovered.targets)
    ]
    [
      "discover: members are covered by the root that lists them"
      [
        {
          key = "frontend";
          root = ".";
        }
        {
          key = "packages/a";
          root = ".";
        }
      ]
      discovered.covered
    ]
    [
      "discover: the rest is lockless"
      [ "tools" ]
      (map (t: t.key) discovered.lockless)
    ]
    [
      "the pnpm lock's project document, past the env document pnpm 12 writes"
      {
        version = "9.0";
        importers = [
          "."
          "types"
        ];
        envDocumentDropped = true;
      }
      {
        version = pnpmLock.version wsLock;
        importers = pnpmLock.importers wsLock;
        envDocumentDropped =
          !lib.hasInfix "packageManagerDependencies" (builtins.readFile (pnpmLock.projectDocument wsLock));
      }
    ]
    [
      "the registry is npm's without an .npmrc"
      "https://registry.npmjs.org"
      (pnpmLock.registryOf (fixtures + "/pnpm"))
    ]
    [
      "managers come from nixpkgs: the declared pnpm major, yarn 1, node's npm"
      {
        ws = pkgs.pnpm_12.name;
        pnpm = pkgs.pnpm.name;
        corepack = pkgs.yarn.name;
        npm = nodejs.name;
      }
      {
        ws = managerOf "pnpm-workspace";
        pnpm = managerOf "pnpm";
        corepack = managerOf "corepack";
        npm = managerOf "npm";
      }
    ]
    [
      "a pnpm major nixpkgs lacks is refused"
      "error"
      (attempt (managerOf "old-major"))
    ]
    [
      "nothing to install is no node_modules; dependencies without a lock are refused"
      {
        no-deps = null;
        lockless = "error";
      }
      {
        no-deps = node.modules {
          inherit pkgs label nodejs;
          target = targetAt "no-deps";
        };
        lockless =
          attempt
            (node.modules {
              inherit pkgs label nodejs;
              target = targetAt "lockless";
            }).drvPath;
      }
    ]
    [
      "a pnpm target without the pnpm2nix input is refused"
      "error"
      (attempt
        (node.modules {
          inherit pkgs label nodejs;
          target = targetAt "pnpm";
        }).drvPath
      )
    ]
    [
      "a workspace root without a build script builds its members that have one"
      [ "frontend" ]
      (targetAt "yarn-workspace").memberBuilds
    ]
    [
      "a target without a build script is not built"
      "error"
      (attempt
        (node.build {
          inherit pkgs label nodejs;
          target = targetAt "no-build";
          nodeModules = null;
        }).drvPath
      )
    ]
  ];
  evalReport = lib.concatMapStrings (
    c:
    let
      what = builtins.elemAt c 0;
      want = builtins.toJSON (builtins.elemAt c 1);
      got = builtins.toJSON (attempt (builtins.elemAt c 2));
    in
    "eq ${sh what} ${sh want} ${sh got}\n"
  ) evalCases;

  # The builds, offline, one per manager.
  built = lib.genAttrs [ "npm" "yarn" "pnpm" "pnpm-workspace" "no-deps" ] (
    fixture:
    let
      target = targetAt fixture;
    in
    node.build {
      inherit
        pkgs
        label
        nodejs
        target
        ;
      nodeModules = node.modules {
        inherit
          pkgs
          label
          nodejs
          target
          pnpm2nix
          ;
      };
    }
  );
  buildReport = lib.concatMapStrings (fixture: ''
    b=${built.${fixture}}
    eq ${sh "${fixture}: the build wrote its output"} "export const built = true;" "$(cat "$b/out/x.js" 2>/dev/null)"
    eq ${sh "${fixture}: …and the tree carries no node_modules"} "" "$(find "$b" -name node_modules)"
  '') (lib.attrNames built);

  wsModules = built.pnpm-workspace.nodeModules;

  yarnLockCheck = import ./yarn-lock.nix {
    inherit pkgs checks;
    yl = node.yarnLock;
  };

  command = node.command {
    inherit pkgs label;
    targets = pkgs.writeText "targets.json" (
      builtins.toJSON [
        {
          key = "ws";
          path = "ws";
        }
        {
          key = "npm";
          path = "npm";
        }
      ]
    );
  };

  unit =
    attr:
    let
      python = pkgs.${attr};
    in
    ''
      echo "== ${attr} (${python.version})"
      ${python.interpreter} -I ${./test_node.py} ${../python} ${nixDetect} ${fixtures} 2>&1
    '';
in
checks.script name { tools = [ pkgs.findutils ]; } ''
  {
    set -e
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    echo "── evaluation ─────────────────────────────────────────────────"
    ${evalReport}

    echo "── builds, offline ───────────────────────────────────────────"
    ${buildReport}
    eq "pnpm-workspace: the member's own node_modules is installed" "yes" \
      "$([ -e ${wsModules}/types/node_modules/is-number/package.json ] && echo yes)"
    eq "…and the member's code is in the built tree, not the store's node_modules" "yes" \
      "$([ -f ${built.pnpm-workspace}/types/index.js ] && echo yes)"

    echo "── yarn.lock ─────────────────────────────────────────────────"
    cat ${yarnLockCheck}

    echo "── the command ───────────────────────────────────────────────"
    cp -r ${fixtures}/pnpm-workspace ws
    cp -r ${fixtures}/npm npm
    chmod -R u+w ws npm
    eq "list reads the targets file" '["types"]' \
      "$(${lib.getExe command} list --json | ${lib.getExe pkgs.jq} -c .ws.members)"

    if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi

    echo "── python/node.py ────────────────────────────────────────────"
    ${lib.concatMapStrings unit interpreters}
  } | tee "$out"
''
