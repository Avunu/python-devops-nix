---
title: Library reference
description: What python-devops-nix exports, from the secrets toolkit to the NixOS pieces and the reusable workflow, and the rules everything in it follows.
nav_title: Library
order: 2
tags: [reference, nix, lib]
updated: 2026-10-28
---

# Library reference

The flake has nixpkgs and flake-parts as its only inputs. Each output is read from one directory: `lib/<name>.nix` (or `lib/<name>/default.nix`) is `lib.<name>`, with a kebab-case file name as a camelCase attribute (`lib/db-nocow.nix` is `lib.dbNocow`), and `checks/<name>.nix` is `checks.<system>.<name>`. `flakeModules` is read from `modules/`, which is empty today. Python sits in `python/`, and the stub products and fixtures the checks run against sit in `tests/`.

Every function takes `pkgs` and the product's names (label, prefix, option path) as arguments, and none names a product. The `check` functions are the base's own tests, which a product can also build against its interpreters. Each takes `{ pkgs, product ? …, name ? …, interpreters ? … }` and runs on every interpreter a preset names.

## Secrets and backups

| Export | What it does |
| --- | --- |
| `lib.secrets.schema { varPrefix, projectRoles ? [ roles.backupAccess ], groups ? { } }` | The pure derivation of a product's secret set: `.age` paths, recipients, shell variables, the agenix-shell secret set and the rules JSON. `groups` are named sets of units with roles of their own (frappe-nix's sites). |
| `lib.secrets.mkModule { optionPath, label, varPrefix, projectRoles ? …, groups ? { }, agenix-shell, libName ? "secrets", libAliases ? [ ] }` | The flake-parts module: the option tree at the product's path, agenix-shell (the product's input) wired to it, and `flake.lib.secrets`. |
| `lib.secrets.tools { pkgs, cfg, label, optionPath ? …, restoreCommand, bucketExample, verifyListing }` | agecheck (the recipient-drift check), the rules file, and `edit-secret`, `rekey-secrets`, `check-secrets` and `setup-backup-access` as devenv scripts (`.scripts { fetch ? null }`). python/secret_tools.py. |
| `lib.secrets.roles`, `lib.secrets.rulesJSONOf` | The role definitions (`backupAccess`), and the recipients map agecheck compares against. |
| `lib.backups.fetch { pkgs, label, envPrefix, layout, legacyEnv ? { } }` | `<label>-backup-fetch {list,folders,fetch}`: list, download and cache backups over S3 (rclone, `<P>_NIX_RCLONE`) or from a directory. `layout` parses an object listing into backup sets. |
| `lib.backups.module`, `lib.backups.check` | python/backups.py, for a product that imports the fetch layer in-process, and its tests. |

Conventions: [Secrets](conventions.md#secrets), [Restore](conventions.md#restore).

## devguard

| Export | What it does |
| --- | --- |
| `lib.devguard.mkPackage { pkgs, name, src, settings ? { } }` | The product's devguard package (`<p>_devguard`): the core (the post-import hook, settings, the patch helpers, the egress and mail_stdlib guards) merged with the product's tree, failing on a file both have, with `settings` baked in as `_baked.json`. |
| `lib.devguard.core`, `lib.devguard.stub`, `lib.devguard.check` | The core's tree (python/devguard), the stub product the tests run against, and the tests, including the stub grafted beside a second graft. |

Conventions: [devguard](conventions.md#devguard).

## Python environment

| Export | What it does |
| --- | --- |
| `lib.python.overrides { pkgs }`, `lib.python.packageOverrides { pkgs }` | One pyproject-nix overlay with the fixes for packages that need a system library (psycopg2, python-ldap, lxml, pycups, python-magic, pyvips, cairocffi), composed after uv2nix's; and the same fixes one by one. |
| `lib.python.rich { python, extraPackages ? ps: [ ] }` | A nixpkgs interpreter with rich, for a product's tooling. Its `.rich` is that rich, for a tool package to depend on. |
| `lib.python.textual { python, extraPackages ? ps: [ ] }` | The same with textual, for the TUI: rich.nix's fixes made once in the package set, so textual, rich, markdown-it-py and mdit-py-plugins are one copy each. |
| `lib.python.lockAudit { workspaceRoot, hasPackage, label, … }` | `{ missing, message, assertCurrent }`: a stale `uv.lock` reported as a sentence that names the member and the requirement. |
| `lib.python.buildDeps { pkgs, label, always ? { }, python ? … }`, `lib.python.buildDepsModule` | `<label>-build-deps {update,check}`: `[tool.uv.extra-build-dependencies]` derived from `uv.lock` (python/uv_build_deps.py). |
| `lib.python.threadCaps` | The BLAS and OpenMP thread caps (one thread each) for the application's environment. |
| `lib.python.withGrafts { python, env, grafts }` | Grafts packages into a virtualenv, each loaded at start-up by `zzz-<module>.pth` (`lib.python.pthName`). |
| `lib.python.mkGraft { pkgs, name, src }`, `lib.python.hook` | A graft's package with the shared post-import hook (`_hook.py`) beside it. |
| `lib.python.mergeTrees` | One package directory merged from several trees, failing when two hold the same file. |
| `lib.python.command { pkgs, name, modules, imports, main, python ? … }` | A command that runs one call into stdlib-only modules, isolated (`-I`) and byte-compiled at build time. |
| `lib.python.interpreters { presets ? [ ] }`, `lib.python.defaultInterpreters` | The interpreters the products' presets files name, for a test matrix. |

Conventions: [Python environment](conventions.md#python-environment).

## Development shell

| Export | What it does |
| --- | --- |
| `lib.ports.options { cfg, name, label, variable, bases, … }` | The `ports` option tree: `worktreeSalt`, `offset` (a hash of the name, 0 to 899), `base` and one derived port per entry of `bases`. `lib.ports.{offsetFor,seed,gitKindAt,parseEnvOffset,effectiveOffset,portsFor}` are the pure pieces. |
| `lib.ciMode { label, envPrefix }` | enterShell snippets for `<P>_NIX_CI`: `isCi`, `exports`, `unlessCi <shell>` and a one-line `banner` in CI. |
| `lib.sources.command { pkgs, label, … }`, `lib.sources.module`, `lib.sources.check` | `<label>-sources enter [ROOT]`, shell entry's submodule checkout, and `<label>-sources clone`, a scaffolder's first clone (python/sources.py). |
| `lib.dbNocow { pkgs, label, strategy, server, pidFile ? … }` | `<label>-db-nocow {prepare,migrate}`: btrfs NOCOW for the database datadir (`strategy` `"datadir"` or `"parent"`, `server` `"cmdline"` or `"pidfile"`). |
| `lib.banner.command { pkgs, name, python ? … }`, `lib.banner.script { pkgs, banner, spec, vars ? { } }`, `lib.banner.module`, `lib.banner.check` | The shell banner, rendered with rich from a JSON spec with `@name@` placeholders filled at run time (python/banner.py). |
| `lib.tui.command { pkgs, name, python ? …, plugins ? [ ], extraPackages ? … }`, `lib.tui.script { pkgs, tui, spec, vars ? { } }`, `lib.tui.commands { pkgs, name, python, target, cli, … }`, `lib.tui.probe { interpreter, target, cli, … }`, `lib.tui.module`, `lib.tui.check` | The shell's TUI (python/devtui), from a JSON spec as the banner is: Home (the banner, live), Stack (devenv's processes through process-compose's socket: start, stop, restart, rebuild), Commands (the product's click tree, built with the shell or probed in the application's environment, as forms) and Store (the product's plugin: its catalog and the command lines that add to the workspace). Every change it makes is a command line of the product's own, shown before it runs. `vars` are re-read whenever the stack starts. |

Conventions: [Development shell](conventions.md#development-shell), [The development shell option tree](conventions.md#the-development-shell-option-tree).

## Production

| Export | What it does |
| --- | --- |
| `lib.nixos.hardening { readWritePaths ? [ ], profiles ? [ ], addressFamilies ? …, capabilities ? [ ], overrides ? { } }` | A unit's systemd sandbox: `.baseline` plus named `.profiles` (`jit`, `chromium`, `wkhtmltopdf`), each value a `mkDefault`, and no `UMask`. `.defaultAddressFamilies` is the default list. |
| `lib.nixos.migrate.options { what, dumpTool, where }` | The `migrate.{enable,snapshot,rollbackOnFailure,snapshotRetention}` options. |
| `lib.nixos.migrate.script { pkgs, label, target, cfg, migrate, snapshotDir, extension, dump, verify, restore, … }` | A migrate unit's command: check, a private snapshot and its verification, the migration, rollback and the product's failure step, then retention (python/migrate.py). `.verifiers.{mysqldump,pgRestoreList}` verify the dumps; `.module` and `.modules` are the Python for in-process use; `.check` is the tests. |
| `lib.nixos.unixSocket { user, group, sockets }` | `{ assertions, tmpfiles, directories }` for sockets that each live in a directory of their own (`.assertion` for one path). |
| `lib.nixos.nginx.{accessLog, journalJsonFormat, journalJsonFields, errorLog, errorLogAt, cloudflareRealIp, socketProxyHeaders}` | nginx's access log as JSON and its error log over syslog, both to the journal, and a unix-socket listener behind a Cloudflare tunnel. |
| `lib.nixos.nginx.{serviceDefaults, upstream, vhost, proxy, internal}` | The configuration itself: `services.nginx`'s defaults with both logs, an upstream on TCP or a unix socket, a virtual host and its proxying locations (websockets included) in either shape, and the `internal` location an `X-Accel-Redirect` lands on. |
| `lib.journald.fields { prefix, role, site ? null, identifier ? … }`, `lib.journald.roles`, `lib.journald.upstreamRoles` | A unit's `APP_SERVICE` and `APP_SITE` journal fields and its `<p>-<role>` identifier, from one list of roles. |
| `lib.journald.mkGraft { pkgs, name, src }`, `lib.journald.module`, `lib.journald.check` | The product's journald graft with the formatter core (python/journald.py) and the post-import hook beside it. |

Conventions: [Production](conventions.md#production), [Logging](conventions.md#logging), [nginx configuration](conventions.md#nginx-configuration).

## Containers

| Export | What it does |
| --- | --- |
| `lib.containers.mk { pkgs, label, name, user, home, nonRoot, source, version, licenses, registry ? "", revision ? null, labelOverrides ? { }, uid ? 1000, gid ? 1000 }` | The parts of an image both products write: `imageName <role>` (`[<registry>/]<name>/<role>`), `labels <role>` (the `org.opencontainers.image.*` labels), `user` (`<uid>:<gid>` with `nonRoot`, else `0:0`) and `homeEnv`, `users` (`/etc/passwd` and `/etc/group` for root, the user and nobody, named `<label>-container-users`), `writable <dirs>` and `fakeRootCommands { writable ? [ ], dirs ? [ ] }` (a world-writable `/tmp`, `dirs` created, and `writable` created and, with `nonRoot`, owned by the user), `healthcheck` and `buildImage` (`dockerTools.buildLayeredImage` with the config added to `passthru` as `imageConfig`). `label` is the product (`odoo-nix`), `name` the project the images are named after. |
| `lib.containers.healthcheck <command>`, `lib.containers.second` | An image's `Healthcheck`: the command every 30 seconds, 10 seconds to answer, 3 retries, and failures in the first 120 seconds not counted. Durations are nanoseconds, and `second` is one. |

Conventions: [Images](conventions.md#images).

## Repository

| Export | What it does |
| --- | --- |
| `lib.checks.mk { pkgs, product }` | The helper a product registers its checks with: `script`, `eval`, `nixosTest` and `checkName`, each named `<product>-<x>-check`. |
| `lib.checks.nixLint { pkgs, src, product, ignore ? [ ], compareStatix ? true }` | nixfmt-rs, statix and deadnix over the tree, and `statix.toml` compared with this repository's copy. |
| `lib.checks.sharedConfig { pkgs, src, product, files ? [ … ], exceptions ? { } }` | Checks that the product's `.editorconfig`, `.pre-commit-config.yaml` and `ruff.toml` carry this repository's settings. |
| `lib.checks.conventions { pkgs, src, product, checks, options ? null, tenant ? null, settings ? null, commands ? null, dependabot ? { }, checkNames ? { }, envVars ? { }, optionPaths ? { }, commandFlags ? { } }` | The conformance checks, by check name, for a product to merge into its checks: `ci-pins`, `dependabot`, `check-names` and `env-vars`; with `options`, `option-paths` and `config-blocks`; and with `commands`, `command-flags`. `checks` is the set they are merged into; only its names are read. `options` is `{ shell, nixos }`, the option sets of the product's modules, and needs `tenant` and `settings`. `commands` is `{ restore, scaffolder }`, each a command's words. |
| `lib.checks.ciPins { pkgs, src, product }` | Every `uses:` in `.github/workflows` and `.github/actions` pinned by a full commit SHA (a `docker://` image by its sha256 digest) with a trailing comment, except a local `./` path. |
| `lib.checks.dependabot { pkgs, src, product, prefix ? "fix(deps)", developmentPrefix ? "chore(deps)", development ? [ ] }` | Every ecosystem in `.github/dependabot.yml` with the prefix, `cooldown.default-days: 7` and a `patterns: ["*"]` group. `development` lists `{ ecosystem, directory ? "/" }` entries that take `developmentPrefix`. |
| `lib.checks.checkNames { pkgs, product, checks, exceptions ? [ ] }` | An eval check that each name in `checks` is kebab-case with no `eval-`, `module-` or `vm-` prefix. |
| `lib.checks.envVars { pkgs, src, product, prefix ? <P>, ignore ? [ ], exceptions ? [ ] }` | Every `<P>_*` name the tree's Python, shell and Nix use as a variable is `<P>_NIX_*` or `<P>_DEVGUARD_*`, and `<P>_NIX_DEVGUARD` and `<P>_NIX_RESTORE_ALLOW_UNGUARDED` are among them. `prefix` is `product` without `-nix`, in upper case. A quoted template key and a shell script's own upper-case variable count too. The Nix list that declares `exceptions`, if `src` holds it, does not. |
| `lib.checks.commandFlags { pkgs, product, commands, exceptions ? [ ] }` | Each of `commands`, `{ restore, scaffolder }`, run with `--help`, lists the flags the conventions name for it, at the head of an option line. Two spaces or a tab end the flags, and a metavar is in upper case or in brackets. |
| `lib.checks.optionPaths { pkgs, product, options, tenant, exceptions ? [ ] }` | An eval check that the options the conventions name are declared and visible under `<product>.*` in `options.shell` and `services.<product>.*` in `options.nixos`, that `ports.*` are not read-only, and that nothing visible is left in `dev`. `tenant` is `{ shell, nixos }`, the default tenant's option in each. |
| `lib.checks.configBlocks { pkgs, product, options, settings }` | An eval check that each `{ module, path, format, args ? { } }` in `settings` is a `settings` submodule whose `freeformType` is `pkgs.formats.<format> args`'s type. |
| `.github/workflows/dependabot-auto-merge.yml` | A reusable workflow that auto-merges Dependabot pull requests by a policy the caller passes. A caller pins it by SHA. |
| `.github/actions/pre-commit-autofix` | Runs the caller's fixers (`nix fmt`) and prek hooks over a pull request's branch, pushes what they rewrote, dispatches the workflows the push cannot start, and comments what it did. |
| `.github/actions/nix-failures` | Markdown from the log of a failed `nix build` or `nix flake check`: each failed derivation with the end of its log, or the evaluation error. |
| `.github/actions/check-report` | A workflow's last job: comments its jobs' results and the `failures-*` artifacts on the pull request when a job failed, and brings that comment up to date once they pass. |
| `.github/actions/sticky-comment` | One comment per marker on a pull request, created or edited, never failing the job. The other actions comment through it. |
| `.editorconfig`, `.pre-commit-config.yaml`, `ruff.toml`, `statix.toml` | The shared copies those checks compare with. |

Conventions: [Tests](conventions.md#tests), [Repository files](conventions.md#repository-files), [CI](conventions.md#ci).

## Design rules

Everything in this repository follows these rules.

1.  **No product names in code.** The base never says Frappe or Odoo. Each product passes in its option paths, environment prefix, package names, log prefixes and command names.
2.  **Uniform names over unchanged names.** Where the products name the same thing differently, both move to the name in the [conventions](conventions.md), with an alias that warns until the product's next major release ([Renames and the 1.x line](conventions.md#renames-and-the-1x-line)).
3.  **A small lock.** The base's own inputs are nixpkgs and flake-parts, and consumers `follows` both. Heavier dependencies (agenix-shell, pyproject-nix, uv2nix) are taken as arguments from the product, never as inputs here.
4.  **Python versions follow from where the code runs.** See [Python versions](#python-versions).
5.  **Logic is Python, glue is bash.** Anything that parses data, handles dates, JSON or more than a few flags, keeps an exit-code table or prompts is Python. Bash is kept for thin glue, built with `writeShellApplication` so shellcheck runs on it.
6.  **Python is vendored at build time, not installed as a dependency.** Shared Python is copied into the product's package by Nix (the devguard core into `<p>_devguard/`), so a virtualenv gains no new distribution.
7.  **No new reach into production.** Nothing here reaches a production virtualenv, service or container unless it is meant to (the journald core, the overrides, the hardening baseline). devguard is development-only, and each product checks that it is absent from production.
8.  **Tests live with the code, and CI runs all of them.** The base tests what it ships against a stub product, and every check is built in CI on every pull request.

### Python versions

- **In-process code** (the devguard core, the journald core, `_hook.py`) loads into the application's own interpreter. It is stdlib-only, written to 3.11 (ruff's `target-version = "py311"` enforces the floor), and tested on every interpreter a preset names (`lib.python.interpreters`). The floor rises when the oldest preset is retired.
- **Out-of-process tools** (agecheck, the secret scripts, the backup fetch layer, the banner, the TUI, the command lines) run on one interpreter the product picks, usually its nixpkgs' `python3`, and may use rich, textual and click from nixpkgs. In a production closure a tool runs on the application's interpreter unless there is a reason not to, to avoid a second Python.
