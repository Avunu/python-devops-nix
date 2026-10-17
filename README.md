# python-devops-nix

Shared building blocks and conventions for Avunu's Nix packaging of Python application frameworks: [frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix).

It holds the pieces that are independent of either product and expensive to get wrong: the secrets toolkit and backup access, the core of the development guard rails (devguard), the Python environment fixes, small development-shell and NixOS helpers, and repository hygiene. The products' development shells, NixOS modules and containers stay in the products.

-   [PLAN.md](PLAN.md): what moves here, in what order, and how products consume it.
-   [CONVENTIONS.md](CONVENTIONS.md): how both products name and do the jobs they share. Product maintainers read this before adding an option, flag, environment variable or command that the other product also has.

## Layout

The flake is built on flake-parts, and its only inputs are nixpkgs and flake-parts. Each output is read from one directory, so adding a piece means adding a file (a kebab-case `lib/` file is a camelCase attribute: `lib/db-nocow.nix` is `lib.dbNocow`):

| Path | Output |
| --- | --- |
| `lib/<name>.nix`, `lib/<name>/default.nix` | `lib.<name>`: functions that take `pkgs` and the product's names as arguments |
| `modules/<name>.nix` | `flakeModules.<name>`: flake-parts modules a product imports |
| `checks/<name>.nix` | `checks.<system>.<name>`, built in CI |

Code that is not Nix sits beside it: `python/` (standard library only, written to 3.11), `sh/` (bash glue), and `tests/`, with the stub products and fixtures the checks run against.

What it provides so far, the secrets toolkit (PLAN.md, Phase 2), the devguard core (Phase 3), the Python environment pieces (Phase 4), the development shell pieces (Phase 5) and repository hygiene (Phase 7):

| Piece | What it does |
| --- | --- |
| `lib.secrets.schema { varPrefix, projectRoles ? [ roles.backupAccess ], groups ? { } }` | The pure derivation of a product's secret set: `.age` paths, recipients, shell variables, the agenix-shell secret set and the rules JSON. `groups` are named sets of units with roles of their own (frappe-nix's sites) |
| `lib.secrets.mkModule { optionPath, label, varPrefix, projectRoles ? …, groups ? { }, agenix-shell, libName ? "secrets", libAliases ? [ ] }` | The flake-parts module: the option tree (`enable`, `dir`, `relDir`, `recipients`, `hostRecipients`, `identityPaths`, each project role, each group, `extra`, `declared`) at the product's path, agenix-shell (the product's input) wired to it, and `flake.lib.secrets` |
| `lib.secrets.tools { pkgs, cfg, label, optionPath ? "<label>.secrets", restoreCommand, bucketExample, verifyListing }` | agecheck (the recipient-drift check), the rules file, `edit-secret`, `rekey-secrets`, `check-secrets` and `setup-backup-access` as devenv scripts (`.scripts { fetch ? null }`), and the on-demand decryption snippets |
| `lib.backups.fetch { pkgs, label, envPrefix, layout, legacyEnv ? { } }` | `<label>-backup-fetch {list,folders,fetch}`: list, download and cache backups over S3 (minio-client) or from a directory. `layout` is the product's parser from an object listing to backup sets (python/backups.py, `Layout`) |
| `lib.backups.module` | python/backups.py, for a product that imports the fetch layer in-process |
| `lib.backups.check { pkgs, product ? …, interpreters ? … }` | The fetch layer's tests, on every interpreter a preset names |
| `lib.python.interpreters { presets ? [ ] }` | The interpreters the products' presets files name, for a test matrix |
| `lib.devguard.mkPackage { pkgs, name, src, settings ? { } }` | The product's devguard package (`<p>_devguard`): the core in python/devguard (the post-import hook, settings, the patch helpers, the egress and mail_stdlib guards) merged with the product's tree (`_product.py` and its own guards), failing on a file both have, with `settings` baked in as `_baked.json` (CONVENTIONS.md, devguard) |
| `lib.devguard.check { pkgs, product ? …, name ? …, interpreters ? … }` | The core's tests against a stub product, and the stub grafted beside a second graft, on every interpreter a preset names |
| `lib.python.withGrafts { python, env, grafts }` | Grafts packages into a virtualenv's site-packages, each loaded at start-up by `zzz-<module>.pth` |
| `lib.python.mkGraft { pkgs, name, src }`, `lib.python.hook` | A graft's package with the shared post-import hook (`_hook.py`) beside it |
| `lib.python.overrides { pkgs }` | One pyproject-nix overlay with the fixes for packages that need a system library (psycopg2, python-ldap, lxml, pycups, python-magic, pyvips, cairocffi), composed after uv2nix's overlay and before the product's own. `lib.python.packageOverrides { pkgs }` has them one by one (CONVENTIONS.md, Package fixes) |
| `lib.python.rich { python, extraPackages ? ps: [ ] }` | A nixpkgs interpreter with rich for a product's tooling, with markdown-it-py's checks off and rich's racy `test_brokenpipeerror` skipped |
| `lib.python.lockAudit { workspaceRoot, hasPackage, label, lock ? "uv.lock", relock ? …, rootPyproject ? … }` | `{ missing, message, assertCurrent }`: a stale `uv.lock` as a sentence naming the member and the requirement, instead of uv2nix's `attribute missing` |
| `lib.python.buildDeps { pkgs, label, always ? { }, python ? … }` | `<label>-build-deps {update,check}`: `[tool.uv.extra-build-dependencies]` derived from `uv.lock` (python/uv_build_deps.py, also `lib.python.buildDepsModule`) |
| `lib.python.threadCaps` | The BLAS and OpenMP thread caps for the application's environment |
| `lib.ports.options { cfg, name, label, variable, bases, descriptions ? { }, baseService ? null, saltDefault ? true, saltApplies ? true, envApplies ? true, … }` | The `ports` option tree: `worktreeSalt`, `offset` (a hash of the name, 0 to 899), `base` (pins `baseService`) and one derived port per entry of `bases`, overridable with `lib.mkForce`. `lib.ports.{offsetFor,seed,gitKindAt,parseEnvOffset,effectiveOffset,portsFor}` are the pure pieces (CONVENTIONS.md, Ports) |
| `lib.ciMode { label, envPrefix }` | enterShell snippets for `<P>_NIX_CI`: `isCi`, `exports` (`PC_TUI_ENABLED=0`, `DEVENV_IN_DIRENV_SHELL=true`), `unlessCi <shell>`, and `banner { name, details ? "", interactive }`, a one-line banner in CI (CONVENTIONS.md, CI mode) |
| `lib.sources.command { pkgs, label, noun ? "project", scope ? [ ], recursive ? false, treeless ? [ ], updateCommand ? "", removeCommand ? "", nestedHint ? null, python ? … }` | `<label>-sources enter [ROOT]`, shell entry's submodule checkout (once per clone, paired branch, partial clone, prefetched pins, merged packs, shallow clones given their history), and `<label>-sources clone [--branch B] [--treeless] [--require-branch] URL PATH`, a scaffolder's first clone. python/sources.py, also `lib.sources.module` (CONVENTIONS.md, Sources) |
| `lib.dbNocow { pkgs, label, strategy, server, pidFile ? "postmaster.pid" }` | `<label>-db-nocow {prepare,migrate}`: btrfs NOCOW for the database datadir, marking the datadir (`strategy = "datadir"`, MariaDB) or its parent before initdb (`"parent"`, PostgreSQL), and a server found by `--datadir=` (`server = "cmdline"`) or a live pid file (`"pidfile"`) (CONVENTIONS.md, Database copy-on-write) |
| `lib.banner.command { pkgs, name, python ? … }`, `lib.banner.script { pkgs, banner, spec, vars ? { } }` | The shell banner, rendered with rich from a JSON spec (title, commands, URLs, facts, guard state, notes) with `@name@` placeholders filled at run time. python/banner.py, also `lib.banner.module` (CONVENTIONS.md, Banner) |
| `lib.checks.mk { pkgs, product }` | The helper a product registers its checks with: `script`, `eval` and `nixosTest`, each named `<product>-<x>-check` (CONVENTIONS.md, Tests) |
| `lib.checks.nixLint { pkgs, src, product, ignore ? [ ], compareStatix ? true }` | nixfmt-rs, statix and deadnix over the tree, and `statix.toml` compared with this repository's copy |
| `lib.checks.sharedConfig { pkgs, src, product, files ? [ … ], exceptions ? { } }` | The product's `.editorconfig`, `.pre-commit-config.yaml` and `ruff.toml` carry this repository's settings (CONVENTIONS.md, Repository files) |
| `.github/workflows/dependabot-auto-merge.yml` | A reusable workflow that auto-merges Dependabot pull requests by a policy the caller passes (CONVENTIONS.md, CI) |
| `.editorconfig`, `.pre-commit-config.yaml`, `ruff.toml`, `statix.toml` | The shared copies those checks compare with |

## Development

```sh
direnv allow              # or: nix develop
nix fmt                   # nixfmt-rs over the tree
nix flake check           # every check, as CI builds it
prek run --all-files      # the hooks CI's lint job runs
```

CI also builds frappe-nix's and odoo-nix's checks against each pull request, once a product takes this flake as an input.

## License

MIT. See [LICENSE](LICENSE).
