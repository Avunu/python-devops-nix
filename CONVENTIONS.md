# Conventions

How frappe-nix and odoo-nix name and do the jobs they both do. Where the two products do the same job, they do it the same way and under the same names. This file records one decision per job. Each product then adopts it in its own pull request.

First draft, 2026-10-16, taken from [PLAN.md, Track B](PLAN.md#track-b-conventions).

## How to read this

Each section gives:

-   **Convention**: what both products do.
-   **Today**: where each product stands before adopting it.
-   **Status**: _decided_ (settled on 2026-10-16), or _proposed_ (stands unless someone objects in review).
-   **Check**: whether this repository can check the convention mechanically, and how. A convention that can be checked cheaply gets a check that each product runs.

`<P>` stands for the product's environment prefix (`FRAPPE`, `ODOO`), and `<p>` for its lower-case name (`frappe`, `odoo`).

Adopting a convention follows the renaming rule in [PLAN.md, Design rules](PLAN.md#design-rules): both products move to the name here, even when that is a breaking change. Every rename ships with a migration note in the product's changelog. Where an alias costs a few lines (`mkRenamedOptionModule`, or reading the old environment variable with a warning), the product keeps it until its next major release, a deliberate `Release-As: N.0.0`, so a consumer that follows a `release-N` branch never loses a name it set. In flake-parts `perSystem` the alias goes on the product's internal `deprecations` list, folded into `lib.warn`, because `mkRenamedOptionModule` cannot warn there.

## Naming rule

Option names follow one rule.

1.  **Structure follows nixpkgs.** Use `enable`, `package`, `user` and `group`, `dataDir` or `stateDir`, `environmentFiles` and `openFirewall`. Use `settings` for a freeform attribute set rendered into the framework's config file (RFC 42). Key multi-tenant modules by hostname, as in `services.wordpress.sites.<host>` and `services.nginx.virtualHosts.<host>`.
2.  **Names for things frappe-nix or odoo-nix invented are shared.** That covers the project identity, ports, guards, the mail catcher and the restore flags. Both products use the same word.
3.  **Names that mirror a framework config key use that framework's word**, in camelCase. Frappe's `default_site` is `defaultSite`; Odoo's `db_name` is `dbName`. Two frameworks with different concepts keep different names.

nixpkgs is not consistent about the option for an app's public hostname (nextcloud `hostName`, discourse `hostname`, odoo `domain`, mastodon `localDomain`, mediawiki `url`). Part 3 settles it: each product keeps the shape its nixpkgs neighbour uses, if there is one.

## Environment variables

### Tool-owned variables

-   **Convention:** every environment variable a product's own tools read or export is `<P>_NIX_*`: `FRAPPE_NIX_*` and `ODOO_NIX_*`. Both export `<P>_NIX_DEVGUARD`.
-   **Today:** frappe-nix has `FRAPPE_RESTORE_ALLOW_UNGUARDED`, `FRAPPE_BACKUP_SOURCE` and `FRAPPE_BACKUP_MC`, and bakes devguard state into its scripts. odoo-nix has `ODOO_NIX_RESTORE_ALLOW_UNGUARDED`, `ODOO_NIX_BACKUP_SOURCE`, `ODOO_NIX_MC` and `ODOO_NIX_DEVGUARD`.
-   **Status:** proposed.
-   **Check:** yes. A check can list the variables each product's tools read and fail on one outside `<P>_NIX_*` that is not a framework's own or a listed alias.

The backup fetch layer (`lib.backups`) reads `<P>_NIX_BACKUP_SOURCE`, `<P>_NIX_MC` and `<P>_NIX_BACKUP_CACHE`, where frappe-nix had `FRAPPE_BACKUP_SOURCE`, `FRAPPE_BACKUP_MC` and `FRAPPE_BACKUP_CACHE`. Its `legacyEnv` argument reads an old name, with a warning, for the deprecation window. The connection itself keeps the names the `backup-access` secret already uses in both products, which belong to the secret's format rather than to a product: `BACKUPS_URL`, `BACKUPS_ACCESS_KEY`, `BACKUPS_SECRET_KEY`, `BACKUPS_BUCKET` and `BACKUPS_PREFIX`.

## Secrets

-   **Convention:** both products declare their secrets with `lib.secrets.mkModule` at `<p>-nix.secrets`, with one option tree: `enable`, `dir`, `relDir`, `recipients`, `hostRecipients`, `identityPaths`, `backupAccess.{enable,hosts}`, `extra.<name>.{format,var,hosts}`, plus frappe-nix's `sites` as a group. Shell variables are `<p>_<role>` (`odoo_backup_access`), and `<p>_<role>_<unit>` for a group's units (`frappe_db_password_erp_example_com`). The flake output is `flake.lib.secrets` in both. The dev-shell commands are `edit-secret`, `rekey-secrets`, `check-secrets` and `setup-backup-access`.
-   **Today:** the option trees already match, except for frappe-nix's `sites`. The outputs are `flake.lib.frappeSecrets` and `flake.lib.odooSecrets`; they stay as aliases (`libAliases`) for one release. `backupAccess.hosts` defaults to false in frappe-nix and true in odoo-nix, whose server reads the same secret; that stays a product's choice of its role's default.
-   **Status:** proposed (PLAN.md, Phase 2).
-   **Check:** yes, the base's `lib-secrets-module` and `lib-secrets-schema` checks evaluate both shapes; a product's adoption shows its rules file and agenix-shell secret set unchanged, compared as JSON.

## Restore

`bench restore` (frappe-nix) and `odoo db restore` (odoo-nix) take the same flags and options.

### Overwrite

-   **Convention:** restore overwrites an existing database by default. `--no-force` refuses instead.
-   **Today:** frappe-nix already does this. odoo-nix refuses by default and overwrites with `--force`.
-   **Status:** decided. odoo-nix keeps `--force` as an accepted no-op for one release.
-   **Check:** yes, the command accepts `--no-force`.

### Migrate after restore

-   **Convention:** restore runs the framework's migration afterwards, on by default. `--no-migrate` and `restore.migrate` turn it off.
-   **Today:** frappe-nix has `--no-migrate` and `restore.migrate`. odoo-nix has neither.
-   **Status:** proposed.
-   **Check:** yes, the flag and the option exist.

### Neutralise

-   **Convention:** `--neutralize` and `restore.neutralize` in both. In frappe-nix, neutralising pauses the scheduler and mutes mail in the restored database, as defence in depth below devguard.
-   **Today:** odoo-nix has both. frappe-nix has neither.
-   **Status:** proposed.
-   **Check:** yes, the flag and the option exist.

### Files

-   **Convention:** `--files` and `restore.files`. frappe-nix keeps `--private-files` as its own extra.
-   **Today:** frappe-nix has `--files`, `--private-files` and `restore.withFiles`. odoo-nix has `--attachments` and `restore.attachments`.
-   **Status:** proposed.
-   **Check:** yes, the flag and the option exist.

### Unguarded restore

-   **Convention:** restore always refuses to run without devguard, unless `<P>_NIX_RESTORE_ALLOW_UNGUARDED` is set. `restore.requireDevguard` in both.
-   **Today:** frappe-nix refuses only when the restore would carry production's encryption key, and has `restore.requireDevguard`. odoo-nix always refuses and has no option.
-   **Status:** proposed.
-   **Check:** yes, the option exists; the refusal is a shell test.

### Point in time (`--at`)

-   **Convention:** `--at` accepts ISO 8601 in both, and each product's own backup naming as well. A backup's exact name wins; otherwise the value's digits are matched as a prefix of the backup's `YYYYMMDDHHMMSS`, so any leading part of a date or time picks the newest backup within it (`2026-10` is the newest of October 2026). A trailing `Z` is accepted; other offsets, fractions and words are refused. Times are the backup's own clock. Implemented once, in `lib.backups` (`parse_at`, `pick`).
-   **Today:** frappe-nix takes `YYYYMMDD_HHMMSS`, exactly. odoo-nix takes a `YYYY_MM_DD_HH_MM_SS` prefix, with `-`, `:`, `T` and space as separators.
-   **Status:** proposed.
-   **Check:** yes, the base's `lib-backups` check tests the parser on every preset interpreter, and drives the command with each form.

## devguard

### SMTP port

-   **Convention:** the option is named `port`, matching both Python settings schemas. Where it lives is in [the development shell option tree](#the-development-shell-option-tree): `mailpit.port`.
-   **Today:** frappe-nix has `devguard.mail.smtpPort`. odoo-nix has `devguard.mail.port`.
-   **Status:** decided.
-   **Check:** yes, the option path exists.

### Job denylist

-   **Convention:** `devguard.jobs.{enable,extraBlocked}`, and `blocked_jobs` in the Python settings. Log lines keep the framework's words (scheduler, crons). frappe-nix's `blockServerScripts` moves to `devguard.jobs.blockServerScripts`. In the Python settings, `guards.jobs.blocked_jobs` is the product's built-in list (its default), `guards.jobs.extra_blocked` the consumer's additions (`extraBlocked`, baked), and the guard reads their union, in order, as `settings().blocked_jobs`, which the core provides.
-   **Today:** frappe-nix has `scheduler.extraBlockedJobs`, and `blocked_jobs` with `extra_blocked_jobs` in Python. odoo-nix has `crons.extraBlocked`, and `blocked_crons` with `extra_blocked_crons`.
-   **Status:** decided.
-   **Check:** yes, the option paths exist and the baked settings carry `blocked_jobs`.

### Shared guard names

-   **Convention:** the guards both products have keep their names, `egress`, `mail`, `mail_stdlib`, `objectstore`, `backups` and `webhooks`, with the same option shape under each (`enable` and the same settings).
-   **Today:** the names already match. The shapes under them differ.
-   **Status:** proposed.
-   **Check:** yes, compare the option trees under each guard.

### The core and the product's package

-   **Convention:** each product's guard package is `<p>_devguard`, built by `lib.devguard.mkPackage { pkgs, name, src, settings }` from this repository's core (`python/devguard`: `__init__.py`, `_hook.py`, `_settings.py`, `_patch.py`, `guards/egress.py`, `guards/mail_stdlib.py`) and the product's own tree, and grafted into the development virtualenv only, with `lib.python.withGrafts`. The build fails on a file both trees have, so a product cannot shadow the core. The product's tree holds:

    -   `_product.py`, the contract:

        | Name | Required | What |
        | --- | --- | --- |
        | `ENV_PREFIX` | yes | `<P>`. The settings variables are `<P>_DEVGUARD_*`. |
        | `FRAMEWORK`, `PRODUCT` | yes | What a fail-closed `require` names: "`<FRAMEWORK>`'s internals have moved … Update `<PRODUCT>`". |
        | `DEFAULTS` | no | `{ "guards": { <guard>: { <key>: value } } }`, merged over the core's defaults key by key: the product's own guards' settings, and any core default it changes. |
        | `GUARDS` | no | The product's guard modules, by name under `guards/`, installed after `egress` and `mail_stdlib`, in order. |
        | `EGRESS_ALLOW` | no | `((<guard>, <key>), …)`: settings lists egress allows as well as `egress.allow_hosts` (frappe-nix's `("integrations", "allow_hosts")`). |
        | `log(guard, message)` | no | An extra log sink beside stderr (frappe-nix's `frappe.logger`). Its exceptions are swallowed. |
        | `user_error(guard, message)` | no | Raises the framework's user-facing error (`frappe.throw`, Odoo's `UserError`), for `_patch.user_error`. Without it, or if it returns, `DevGuardBlocked` is raised. |

    -   `guards/<guard>.py` for each guard of its own, each with `NAME` and `install()`, built on the core's `_hook.on_import` and `_patch` helpers;
    -   its own helpers, in a module of its own (`_frappe.py`, `_odoo.py`), not in `_patch.py`.

    The package name is not in the contract: the core reads it from the package itself, so the log prefix cannot disagree with the directory.

    Settings resolve, highest first: the environment (`<P>_DEVGUARD_<GUARD>_<KEY>`, plus `<P>_DEVGUARD_ENABLED` and `<P>_DEVGUARD_DISABLE=<guard>,…`); the runtime file (`$<P>_DEVGUARD_RUNTIME`, else `$DEVENV_RUNTIME/devguard-runtime.json`), which the shell writes with the values only known once its processes start; the baked `_baked.json` (`mkPackage`'s `settings`); the product's defaults; the core's. The schema is `{ "enabled", "guards": { <guard>: { "enable", … } } }`, in snake_case. The core reads `egress.allow_hosts`, `mail.{host,port,http_port,pop3_enabled,pop3_port,pop3_user,pop3_password}` and `jobs.{blocked_jobs,extra_blocked}`; every other key is the product's.

    What users see is fixed by the core: the `.pth` file is `zzz-<p>-devguard.pth`; every log line is `WARNING <p>_devguard[<guard>] <message>`, a guard's first is `… ACTIVE -- <what it does>`, and a refusal raises `DevGuardBlocked` (an `OSError`) reading `<p>_devguard[<guard>]: <message>`. `<P>_NIX_DEVGUARD` (see [Tool-owned variables](#tool-owned-variables)) is the shell's statement that devguard is on, for the product's own tools; the guard package does not read it.
-   **Today:** each product has its own copy of the core. frappe-nix bakes a Python literal (`_baked.py`, through `toPy`) and has the runtime file; odoo-nix bakes `_baked.json` and has no runtime file. frappe-nix's banner reads `ACTIVE —`, odoo-nix's `ACTIVE --`. odoo-nix's egress lacks `passage()`, IPv4-mapped loopback and `*.localhost`.
-   **Status:** proposed (PLAN.md, Phase 3).
-   **Check:** yes. `lib.devguard.check { pkgs, product, interpreters }` runs the core's tests against a stub product on every interpreter the product's presets name, and the merged build refuses a shadowed core file. Each product keeps a check that its production environment has no `<p>_devguard`.

### Grafts

-   **Convention:** every package loaded at interpreter start, devguard or not, is grafted with `lib.python.withGrafts { python, env, grafts }`, which writes `zzz-<module, _ as ->.pth` running `import <module>; <module>.install()`. A graft that needs a post-import hook is built with `lib.python.mkGraft { pkgs, name, src }`, which adds the shared `_hook.py`; no graft carries its own copy.
-   **Today:** frappe-nix has `graftFor` and `withGrafts` in `lib/python.nix`, and five copies of `_hook.py` (devguard, unixsock, nodebuild, benchcli, journald). odoo-nix has `withDevguard`, and its journald shim's `_NetsvcImportHook`.
-   **Status:** proposed (PLAN.md, Phase 3).
-   **Check:** yes, the base's `lib-devguard` check grafts two packages into one virtualenv and asserts the `.pth` names and that both hooks fire.

## Python environment

Both products build their environment the same way: pyproject-nix, uv2nix and pyproject-build-systems (the product's inputs, not the base's), `sourcePreference = "wheel"`, `mkEditablePyprojectOverlay` for development, and `.pth` grafts. These sections fix the pieces around that (PLAN.md, Phase 4).

### Package fixes

-   **Convention:** a fix for a package that needs a system library lives in `lib.python.overrides { pkgs }`, one pyproject-nix overlay. The product composes it right after uv2nix's overlay and layers its own on top (`composeManyExtensions [ pyproject-build-systems.overlays.default workspaceOverlay (lib.python.overrides { inherit pkgs; }) productOverrides ]`). It covers psycopg2, python-ldap (with the `ldap_r` link fix), lxml, pycups, python-magic, pyvips and cairocffi, and touches only a package the set has. A build fix (headers, pkg-config, setuptools) applies only to a source build, so a wheel's derivation is left as uv2nix made it. A runtime fix (a library's store path where the package dlopens it) patches the installed files, so it reaches a wheel too. The product keeps the fixes only it needs (frappe-nix's mysqlclient, odoo-nix's OCB build and `lxml_html_clean`). `lib.python.packageOverrides { pkgs }` has the same fixes one by one, for a product that exports them to its consumers.
-   **Today:** frappe-nix's `lib/overrides.nix` has mysqlclient, pycups, python-ldap (without the `ldap_r` fix), pyvips and cairocffi, applies only mysqlclient itself, and patches pyvips and cairocffi in `postPatch`, which fails on a wheel (a wheel build does not unpack). odoo-nix's has psycopg2, python-ldap, lxml and libsass, applies psycopg2 and python-ldap, gets pycups from its `pythonLibraries` option, and patches python-magic in `lib/python.nix`.
-   **Status:** proposed (PLAN.md, Phase 4).
-   **Check:** yes. The base's `lib-python-overrides` check applies the set to a stub package set. A product checks that its production environment's derivation is unchanged by adoption, except for the fixes new to it.

### Tool interpreter with rich

-   **Convention:** a product's own tooling (a banner, a command line) runs on `lib.python.rich { python, extraPackages ? ps: [ ] }`: nixpkgs' rich, with markdown-it-py's checks off and rich's racy `test_brokenpipeerror` skipped. It is never added to the application's environment.
-   **Today:** each product has `lib/rich-python.nix`. odoo-nix's has both fixes (odoo-nix#55); frappe-nix's has neither.
-   **Status:** proposed.
-   **Check:** yes, `lib-python-rich` builds it on every preset interpreter, with rich's suite.

### Stale lock

-   **Convention:** every virtualenv is wrapped in `(lib.python.lockAudit { workspaceRoot, hasPackage = name: pythonSet ? ${name}; label = "<p>-nix"; lock ? "uv.lock"; relock ? …; }).assertCurrent`, so a declared dependency missing from the lock fails evaluation with `<p>-nix: uv.lock does not cover every declared dependency`, a line per gap naming the member and the requirement, and how to re-lock, instead of uv2nix's `attribute '<name>' missing`.
-   **Today:** frappe-nix has `lib/lock-audit.nix`; odoo-nix had nothing. In odoo-nix the audit catches a root dependency missing from `uv.lock`, which otherwise builds silently without it; an OCA addon's own `dynamic` requirements are out of its reach.
-   **Status:** proposed.
-   **Check:** yes, `lib-python-lock-audit` (an eval check).

### Build dependencies

-   **Convention:** `[tool.uv.extra-build-dependencies]` is derived from `uv.lock`, not kept by hand. Every package the lock has no wheel for gets `setuptools`; the product's `always` adds entries the lock cannot show (a package that is sdist-only elsewhere, a backend other than setuptools). The entries sit between `# >>> <p>-nix: sdist build deps (managed) >>>` and `# <<< <p>-nix: sdist build deps (managed) <<<`, and the relock command runs `lib.python.buildDeps { pkgs, label = "<p>-nix", always }` (`<p>-nix-build-deps update`) after `uv lock`, then locks again. `<p>-nix-build-deps check` fails when the block is out of date.
-   **Today:** odoo-nix derives it with `lib/uv_build_deps.py`. frappe-nix's template carries a fixed list of about 100 entries.
-   **Status:** proposed.
-   **Check:** yes, `lib-python-build-deps` runs the tests against both products' lock shapes on every preset interpreter.

### Thread caps

-   **Convention:** the application's environment sets `lib.python.threadCaps` (`OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and `NUMEXPR_NUM_THREADS`, each `1`) everywhere it runs: the development shell, the NixOS services and the containers.
-   **Today:** odoo-nix sets them from `lib/env.nix` in all three. frappe-nix sets none.
-   **Status:** proposed.
-   **Check:** no.

## Scaffolder

frappe-nix's `frappe-init` and odoo-nix's `odoo-init`.

### Version flag

-   **Convention:** `--version`. The old names stay as aliases.
-   **Today:** frappe-nix has `--frappe-version`. odoo-nix has `--series`.
-   **Status:** decided.
-   **Check:** yes, the scaffolder accepts the flag.

### Flags

-   **Convention:** both scaffolders have `--dry-run`, `--skip-lock`, `-y`, `--commit`, and migrating an existing tree.
-   **Today:** frappe-nix has all of them. odoo-nix has none.
-   **Status:** proposed.
-   **Check:** yes, the scaffolder accepts each flag.

### Exit codes

-   **Convention:** frappe-nix's documented table of exit codes 1 to 7, in both.
-   **Today:** frappe-nix documents 1 to 7. odoo-nix always exits 1.
-   **Status:** proposed.
-   **Check:** partly. Shell tests can assert the code for each failure they reach.

### Presets file

-   **Convention:** one schema: `{label, branch, python, requiresPython}`, with `nodejs` and `overrideDependencies` optional.
-   **Today:** frappe-nix has `{label, branch, python, nodejs, requiresPython, overrideDependencies}`. odoo-nix has `{label, series, python, requiresPython}`.
-   **Status:** proposed.
-   **Check:** yes, validate each product's presets file against the schema.

### Seeds

-   **Convention:** both scaffolders seed `secrets/README.md` and print a warnings summary.
-   **Today:** frappe-nix does both. odoo-nix does neither.
-   **Status:** proposed.
-   **Check:** yes, a dry run lists the seeded files.

### Next steps

-   **Convention:** the same closing wording in both, naming each product's own command.
-   **Today:** frappe-nix suggests `provision-site` or `bench restore`. odoo-nix suggests `odoo db provision`.
-   **Status:** proposed.
-   **Check:** no; wording is reviewed.

## Flake

### Systems

-   **Convention:** `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`. No `x86_64-darwin`, which nixpkgs 26.11 dropped.
-   **Today:** frappe-nix still includes `x86_64-darwin`. odoo-nix has dropped it.
-   **Status:** proposed.
-   **Check:** yes, the systems the flake's outputs are defined for.

## Development shell

The leaf pieces inside both devenv modules (PLAN.md, Phase 5). The modules themselves stay in the products.

### Ports

-   **Convention:** every TCP port the shell binds is a base the product picks plus one offset, 0 to 899, a hash of the project's `name` (`sha256(name)[0:4] mod 900`), taken from `lib.ports`. A linked worktree (`.git` a file) salts the hash with its path when `ports.worktreeSalt` is on. `<P>_NIX_PORT_OFFSET` picks another offset for one shell and wins over `ports.offset`; a value outside 0 to 899 is ignored with a warning. The devenv module declares its `ports` tree with `lib.ports.options` (see [The development shell option tree](#the-development-shell-option-tree)); the bases stay the product's (frappe-nix: web 8000, MariaDB 3306, socket.io 9000, Mailpit 19000 to 21000; odoo-nix: HTTP 10000, gevent 11000, PostgreSQL 12000, Mailpit 13000 and 14000), clear of each other so a bench and a project never meet.
-   **Runtime leases:** what `lib.ports` gives is the *preferred* port. devenv's port allocator is the identity under the flake integration, so two copies of one project (CI checkouts, which are never linked worktrees, or two clones) prefer the same ports, and nothing evaluated can move a taken one. A product may lease its ports when `devenv up` starts: a first process claims each preferred port host-wide for the project's lifetime and moves past a taken one, or refuses it where `ports.strict` says so (`<P>_NIX_STRICT_PORTS` at run time), and writes the ports in force to `$DEVENV_RUNTIME/<p>-nix/ports.json`. Consumers then read that file, not the evaluated values. A product that commits a port (frappe-nix's bench mode, whose `webserver_port` is in `common_site_config.json`) is strict by default.
-   **Today:** frappe-nix has `lib/ports.nix`; odoo-nix ported it in odoo-nix#58. The base's functions give both products' current ports for any name unchanged (`lib-ports` holds their values as golden ones), so adoption moves no port. frappe-nix leases its nginx and Mailpit ports at `devenv up` (frappe-nix#144, `lib/port-lease.py`): app mode moves, bench mode refuses. odoo-nix uses the evaluated ports as they are.
-   **Status:** proposed.
-   **Check:** yes, `lib-ports`.

### CI mode

-   **Convention:** `<P>_NIX_CI` set to anything but empty, `0` or `false` puts the shell in CI mode, read when enterShell runs: `PC_TUI_ENABLED=0` and `DEVENV_IN_DIRENV_SHELL=true` are exported first, the interactive steps (node installs, the sources report) are skipped, and the banner is one line, `<p>-nix: CI mode (<name>[, <details>])`. Snippets from `lib.ciMode { label, envPrefix }`.
-   **Today:** frappe-nix has `FRAPPE_NIX_CI` (`lib/ci-mode.nix`). odoo-nix has no CI mode.
-   **Status:** proposed.
-   **Check:** yes, `lib-ci-mode` runs the snippets; a product's own check asserts its enterShell uses them.

### Sources

-   **Convention:** source repositories are git submodules, and one command handles them, `lib.sources.command` (`<p>-nix-sources`). Shell entry runs `enter`: a submodule this clone has never set up is checked out once, as a partial clone (`--filter=blob:none`, or `tree:0` for a path the product names treeless) of the branch `.gitmodules` pairs it with and no other (`--single-branch`), falling back to the remote's default branch; one set up before and now missing is reported, never brought back. Every entry also fetches a pin a partial clone lacks before git asks for it lazily, merges a partial clone's packs geometrically, sets `fetch.recurseSubmodules=false`, and gives a shallow clone its paired branch's history in place. The scaffolder's first clone is `clone` (or `clone()`, imported). Messages are `<p>-nix: …` on stderr, and entry always exits 0. The product passes its scope (frappe-nix `[ "apps" ]`, odoo-nix every submodule), whether checkouts recurse (odoo-nix), its treeless paths (odoo-nix's core), and the update and remove commands its hints name.
-   **Today:** frappe-nix's `lib/apps-report.nix` (385 lines of bash) and odoo-nix's `lib/submodules-init.nix` (353), ported into each other twice; the scaffolders clone with their own `git clone --filter=blob:none --single-branch`.
-   **Status:** proposed.
-   **Check:** yes, `lib-sources` runs one suite in both products' shapes on every preset interpreter.

### Database copy-on-write

-   **Convention:** on btrfs, the development database's datadir has copy-on-write off (`chattr +C`), from `lib.dbNocow { pkgs, label, strategy, server }` (`<p>-nix-db-nocow`). `prepare` runs on shell entry before the server starts: with `strategy = "datadir"` (MariaDB) it creates and marks the directory; with `"parent"` (PostgreSQL, whose initdb runs only when the datadir is missing) it marks the parent until initdb has made the datadir, then clears it. A datadir already holding data is reported with the command that fixes it, `migrate`, which refuses while the server runs (`server = "cmdline"`, a process with `--datadir=<dir>`; `"pidfile"`, a live `postmaster.pid`). Off btrfs both are silent no-ops, and `migrate` says so before looking for a server. Linux only.
-   **Today:** frappe-nix's and odoo-nix's `lib/db-nocow.nix`, one per database.
-   **Status:** proposed.
-   **Check:** yes, `lib-db-nocow` in both strategies; on whatever filesystem the builder has, and by hand on the other (`TMPDIR=<dir> bash tests/db-nocow.sh …`).

### Banner

-   **Convention:** the shell's banner is `lib.banner.command`, rendered with rich (on `lib.python.rich`, never the application's environment) from one JSON spec the product writes at evaluation: `title`, `commands`, `urls`, `facts`, `guard` (`enable`, `egress.{enable,allow}`, `mail.{enable,url,incoming}`) and `notes`. A fact known only when the shell starts is an `@name@` placeholder filled by `--var name=value` (`lib.banner.script`'s `vars`). Nothing in the spec is rich markup. The devguard lines read the same in both: `devguard OFF — nothing stops a restored database from reaching production`, `Egress: outbound connections blocked (allowed: …)`, `Mail: ALL outgoing email → Mailpit (…)`.
-   **Today:** frappe-nix's `lib/banner.py` takes 17 flags; odoo-nix's takes nine positional arguments.
-   **Status:** proposed.
-   **Check:** yes, `lib-banner`.

## Command line

### Command layout

-   **Convention:** every command lives under the product's umbrella command. frappe-nix's `provision-site` becomes `bench provision`, and `provision-site` stays as an alias.
-   **Today:** frappe-nix has the `bench` umbrella, but `provision-site` is standalone. odoo-nix has everything under `odoo`.
-   **Status:** proposed.
-   **Check:** yes, the umbrella lists each command.

### Source management

-   **Convention:** add and remove a source repository in both. odoo-nix gains `odoo module remove`.
-   **Today:** frappe-nix has `bench get-app` and `bench remove-app`. odoo-nix has `odoo module add` and no remove.
-   **Status:** proposed.
-   **Check:** yes, the commands exist.

### Relock

-   **Convention:** one consumer-facing relock command in both.
-   **Today:** frappe-nix has `update-deps`, and `nix run .#relock` for consumers. odoo-nix relocks inside `project update`, and its `relock` covers its own fixtures only.
-   **Status:** proposed.
-   **Check:** partly, the command exists.

## Production

### Migrate on deploy

-   **Convention:** shared: the `migrate.{enable,snapshot,rollbackOnFailure,snapshotRetention}` options (defaults `true`, `true`, `true`, `3`), the snapshot flow, and verifying the snapshot before migrating. Product-specific: what is left running after a failed migrate. frappe-nix keeps serving in Frappe's native maintenance mode. Odoo has no maintenance mode, and serving a half-migrated schema is not safe, so odoo-nix stops the service.

    The options are `migrate = lib.nixos.migrate.options { what, dumpTool, where };` in the product's option tree; a product adds its own beside them (frappe-nix's `maintenanceMode` and `offline`, odoo-nix's `full` and `timeout`). The unit's `ExecStart` is `lib.getExe (lib.nixos.migrate.script { ... })`, which runs python/migrate.py's flow for one target with the product's executables:

    | Step | Argument | Contract |
    | --- | --- | --- |
    | check | `check` (optional) | Exit 0 to migrate, 100 when there is nothing to do (it prints why), anything else is an error before anything changed. frappe-nix: the build marker and the uninstalled-site guard. |
    | snapshot | `snapshotDir`, `prefix` (default `premigrate-<target>`), `extension`, `dump` | `dump` writes to stdout; the file is `<prefix>-YYYYmmdd-HHMMSS<extension>`, 0600 from its first byte, in a directory made and kept 0700. A failed or empty dump is removed and stops the deploy before it migrates. |
    | verify | `verify` | `verify SNAPSHOT` exits 0 when the dump is whole. One that does not is removed and stops the deploy. `lib.nixos.migrate.verifiers.mysqldump { pkgs, compressed ? true }` (gzip's check and mysqldump's closing `-- Dump completed` line) and `.pgRestoreList { pkgs, postgresql }` (`pg_restore --list`). |
    | migrate | `migrate` | The migration. frappe-nix's turns maintenance mode on first. |
    | success | `onSuccess` (optional) | After a migration that exited 0; a failure here is a warning. Then the newest `snapshotRetention` snapshots are kept. frappe-nix: maintenance mode off, write the marker. |
    | rollback | `restore` | With `rollbackOnFailure`: `restore SNAPSHOT` puts the database back as the dump has it (frappe-nix: drop every table, then load the dump; odoo-nix: drop and recreate the database, then `pg_restore`). |
    | failure | `onFailure` (optional) | After the rollback, with `MIGRATE_EXIT` and `MIGRATE_ROLLBACK` (`restored`, `failed`, `none`). The unit then exits with the migration's status. |

    Every step runs with `MIGRATE_TARGET` and, once there is one, `MIGRATE_SNAPSHOT`. Messages are `<label>(<target>): …` on stderr, with a `<N>` syslog prefix when stderr is the journal, so a failed migrate is found at `journalctl -p err`. Failed migrations keep their snapshots; only a success prunes.

    The failure behaviour is wiring, not an argument to the flow: frappe-nix's migrate unit is `RemainAfterExit` and the services only order after it, and its migrate and `onFailure` steps leave maintenance mode on; odoo-nix's `odoo.service` `Requires=` a migrate unit without `RemainAfterExit`, so every start of Odoo migrates first and a failure keeps it down. A product whose own migrate tool decides in-process when a snapshot is due (odoo-nix's `odoo db migrate`, which snapshots only when its plan is not empty) vendors python/migrate.py and python/journald.py (`lib.nixos.migrate.modules`) and calls `Runner.take()`, `restore()` and `prune()`, or gives the flow a `check` that runs its change detection.
-   **Today:** frappe-nix runs migrate with `RemainAfterExit` and a marker file, in bash, and does not verify its snapshot. odoo-nix migrates on every start with `--if-needed`, keeps its marker in the database, snapshots and verifies in Python (`odoo db migrate`), and `odoo.service` requires the migrate unit.
-   **Status:** decided.
-   **Check:** yes. `lib-nixos` evaluates the options and the flow's configuration in a stub product; `lib-nixos-migrate-flow` runs the flow's unit tests on every preset interpreter; `lib-nixos-migrate` runs it in a VM against MariaDB and PostgreSQL, in both products' failure shapes. Each product's VM test covers its own steps.

### Production secrets

-   **Convention:** secrets reach units through `LoadCredential`, and init runs as the service user. `environmentFiles` in both.
-   **Today:** frappe-nix uses `LoadCredential` and runs init as the service user, and has no `environmentFiles`. odoo-nix runs init as root, reads the files directly, and has `environmentFiles`.
-   **Status:** proposed.
-   **Check:** yes, evaluate the units and inspect `LoadCredential` and `User`.

### Database logging

-   **Convention:** `database.slowQueryMs` in both.
-   **Today:** odoo-nix has `slowQueryMs` and sets `log_line_prefix`. frappe-nix has none.
-   **Status:** proposed.
-   **Check:** yes, the option exists.

### Unit hardening

-   **Convention:** every unit a product defines gets `lib.nixos.hardening { readWritePaths, profiles ? [ ], addressFamilies ? [ AF_UNIX AF_INET AF_INET6 ], capabilities ? [ ], overrides ? { } }` merged into its `serviceConfig`. The baseline (`lib.nixos.hardening.baseline`) is `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict` with the unit's `ReadWritePaths`, `ProtectHome`, `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `ProtectProc=invisible`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, `MemoryDenyWriteExecute`, `RemoveIPC`, an empty `CapabilityBoundingSet` and `AmbientCapabilities`, `SystemCallArchitectures=native`, and `SystemCallFilter=@system-service ~@privileged` failing with `EPERM`. Each value is a `mkDefault`, so a host can relax one key on one unit; `ReadWritePaths` is a plain list, so a host's paths add to the product's.

    There is **no `UMask=0077`**, here or in any unit: units that write state also write files nginx serves through the service group (Frappe's Website Theme CSS, frappe-nix#83; Odoo's filestore under `xSendfile`, odoo-nix#51). Private files get their modes where they are written, as the migrate snapshots do.

    A unit names what it legitimately needs, and documents it next to the call:

    | Need | How | Who |
    | --- | --- | --- |
    | A JIT (W+X memory) | `profiles = [ "jit" ]` | Node (frappe-nix's socketio) |
    | Headless Chromium | `profiles = [ "chromium" ]`: the JIT, `capset` (each child drops its capabilities, even under `--no-sandbox`), user namespaces, `AF_NETLINK` | frappe-nix's units that render PDFs, with `chromium.enable` |
    | wkhtmltopdf | `profiles = [ "wkhtmltopdf" ]`: QtWebKit's JIT | frappe-nix's units that render PDFs, with `wkhtmltopdf.enable` |
    | Only unix sockets | `addressFamilies = [ "AF_UNIX" ]` | a unit that reaches its database over its socket alone |
    | Still runs as root | `capabilities = [ ... ]`, which also lets `@privileged` calls through the filter | odoo-nix's `odoo-init` until init runs as the service user ([Production secrets](#production-secrets)) |
    | Anything else | `overrides = { <key> = <value>; }`, with a comment saying why | |

    `ProtectSystem=strict` leaves `/run` writable, so a socket directory needs no `ReadWritePaths` (its mode is the gate: [Unix sockets](#unix-sockets)); a state directory does. A path in `ReadWritePaths` must exist when the unit starts; create it with tmpfiles.
-   **Today:** neither product sets any of it.
-   **Status:** proposed (PLAN.md, Phase 6).
-   **Check:** yes. `lib-nixos` evaluates the baseline, the profiles and the overrides in a stub product, and asserts no unit has a `UMask`. `lib-nixos-hardening` runs Python with a ctypes callback, Node, headless Chromium and wkhtmltopdf under it in a VM, and shows the baseline refuses a JIT and a root unit keeps only the capabilities it names. A product's VM tests run its units under it.

### Unix sockets

-   **Convention:** a socket lives in a directory of its own (never straight in `/run` or `/`), created by tmpfiles and owned by the service user and group: `0750` when the service creates the socket and a peer in the group (nginx) only connects, `0770` when a peer creates its own socket there too (nginx's listen socket). The socket file's own mode gates nothing, since nginx chmods its listen sockets to `0666`. `lib.nixos.unixSocket { user, group, sockets = [ { option, path, peerCreates ? false, example ? … } ] }` returns `{ assertions, tmpfiles, directories }`: one assertion per socket option (`<option> must be an absolute path inside its own directory (e.g. …), not directly in /run: the directory's mode is what keeps the socket private.`), one tmpfiles rule per directory, and the directories. An unset path (`""` or `null`) is skipped.
-   **Today:** both products assert it with their own wording and write the rules by hand. frappe-nix creates every socket directory `0770`; odoo-nix only has nginx's socket, in a `0770` directory.
-   **Status:** proposed (PLAN.md, Phase 6).
-   **Check:** yes, `lib-nixos` (the rules and the messages) and `lib-nixos-migrate` (the modes on a running system, with nginx connecting through them).

### nginx

-   **Convention:** nginx logs to the journal:

    -   the access log as `journal_json`, one JSON object per request with `time`, `site`, `method`, `uri`, `status`, `bytes`, `request_time`, `upstream_time`, `remote_addr`, `user_agent` and `referer`, under `SYSLOG_IDENTIFIER=nginx_access`: `services.nginx.commonHttpConfig = lib.nixos.nginx.accessLog cfg.logging.accessLog;` (`access_log off;` when false). `lib.nixos.nginx.journalJsonFormat` is the `log_format` line, and `journalJsonFields` its fields;
    -   the error log over syslog, so it keeps its severity: `services.nginx.logError = lib.mkDefault lib.nixos.nginx.errorLog;` (at `error`; `errorLogAt <level>` for another).

    A virtual host that listens on a unix socket behind a Cloudflare tunnel adds `lib.nixos.nginx.cloudflareRealIp` (`set_real_ip_from unix:; real_ip_header CF-Connecting-IP;`) to its `extraConfig`, never on a TCP listener, and each of its locations turns `recommendedProxySettings` off and adds `lib.nixos.nginx.socketProxyHeaders` (`X-Forwarded-Proto https`, since TLS ended at the connector).
-   **Today:** both products have byte-identical copies of the access log, the real-IP block and the socket headers. frappe-nix sends the error log to syslog; odoo-nix leaves it on stderr, where every line is info.
-   **Status:** proposed (PLAN.md, Phase 6).
-   **Check:** yes, `lib-nixos` (the configuration text) and `lib-nixos-migrate` (the client address from the header in the access log, and an upstream error at priority err).

### NixOS module path

-   **Convention:** `services.frappe-nix` and `services.odoo-nix`. These match the devenv and flake-parts roots (`frappe-nix.*`, `odoo-nix.*`) and cannot collide with a future nixpkgs module. `services.frappe` ships as an alias.
-   **Today:** frappe-nix uses `services.frappe`. odoo-nix uses `services.odoo-nix` (nixpkgs already has `services.odoo`).
-   **Status:** decided.
-   **Check:** yes, the option path exists.

### Framework config blocks

-   **Convention:** `settings` wherever a block becomes the framework's config file, as in nixpkgs. frappe-nix: `sites.<fqdn>.settings` and `settingsFiles` (`site_config.json`, with files for secrets). odoo-nix: the devenv module's `odooConf` becomes `settings`, matching its NixOS module, and holds only `odoo.conf` keys once the ports leave it. Declared keys inside a freeform `settings` are camelCase (`settings.dbName`); undeclared keys pass through in the framework's own spelling.
-   **Today:** frappe-nix has per-site `extraConfig` and `extraConfigFiles` in its NixOS module and nothing in devenv. odoo-nix has `settings` in its NixOS module and `odooConf` in devenv.
-   **Status:** decided.
-   **Check:** yes, the option paths exist.

### Identity options

-   **Convention:** the project identity is `name` in both (`frappe-nix.name`, `odoo-nix.name`). It is a slug: it seeds the port hash and names container images, systemd scopes and log messages. Each devenv module sets devenv's own top-level `name` with `mkDefault cfg.name`. The default tenant keeps the framework's word: frappe-nix's `siteName` becomes `defaultSite` (Frappe's `default_site`), and odoo-nix keeps `dbName` (Odoo's `db_name`). The scaffolder's `--site` becomes `--default-site`, and `--name` sets the identity in both. Production keeps nixpkgs' shapes: frappe-nix's `sites.<fqdn>` (as `services.wordpress.sites`), odoo-nix's `domain` (as `services.odoo.domain`). `benchName`, `projectName` and `siteName` ship as `mkRenamedOptionModule` aliases. In frappe-nix, the word site means only what Frappe means by it: a tenant of the bench.
-   **Today:** frappe-nix has `benchName` (identity) and `siteName` (the default `FRAPPE_SITE`). odoo-nix has `projectName` (identity) and `odooConf.dbName` (the default database).
-   **Status:** decided.
-   **Check:** yes, the option paths and the aliases exist.

## Logging

### Journald enablement

-   **Convention:** journald formatting is always on when stderr is the journal, loaded through a `.pth` graft built with `lib.journald.mkGraft { pkgs, name, src }` (`<p>_journald`), which puts the post-import hook (`_hook.py`) and the formatter core (`_journald.py`) beside the product's tree. The product's `install()` does nothing unless `_journald.journal_stream()`: `JOURNAL_STREAM` names stderr's own device and inode, which an inherited variable on a redirected stderr does not. It ships to production as well as development, inert wherever stderr is not the journal. A command line that logs to the journal itself vendors `lib.journald.module`.
-   **Today:** frappe-nix does this, with its own copy of the core. odoo-nix needs `ODOO_NIX_JOURNALD=1` and an option, through a sitecustomize shim.
-   **Status:** proposed.
-   **Check:** yes. `lib-journald` grafts a stub product and points `JOURNAL_STREAM` at a file, on every preset interpreter.

### Journald vocabulary

-   **Convention:** every unit carries `LogExtraFields` `APP_SERVICE=<role>`, plus `APP_SITE=<site or database>` when it serves exactly one, and a unit the product defines has `SyslogIdentifier=<p>-<role>`; an upstream unit the product only enables (the database, Redis, nginx) keeps its own identifier. `serviceConfig = { ... } // lib.journald.fields { prefix = "<p>"; role; site ? null; identifier ? … };` writes them, and refuses a role not in the list. The roles (`lib.journald.roles`):

    | Role | Unit |
    | --- | --- |
    | `runtime` | frappe-nix's unified runtime (web, realtime, jobs, scheduler) |
    | `web` | the HTTP server: gunicorn, `odoo.service` |
    | `worker` | a background-job worker |
    | `scheduler` | the scheduler |
    | `socketio` | the realtime server |
    | `init` | setup before anything else starts |
    | `migrate` | migrate on deploy |
    | `db` | the database server |
    | `redis` | Redis |
    | `nginx` | nginx |

    A unit that needs telling apart within its role passes `identifier` (frappe-nix's `frappe-worker-<queue>`, `frappe-db-password`).
-   **Today:** frappe-nix uses `frappe-<role>`, with these roles. odoo-nix uses `odoo`, `odoo-init` and `odoo-migrate`, with `web` as the main unit's role.
-   **Status:** proposed.
-   **Check:** yes. `lib-nixos` evaluates the fields in a stub product, and frappe-nix's `tests/logging-fields.nix` and odoo-nix's `tests/module-eval.nix` assert them against `lib.journald.roles`.

### Journald line format

-   **Convention:** `<N>{logger} [{site or db}] msg`, from `JournaldFormatter(logger=None, tenant=None)` in the core (python/journald.py). `<N>` is the record's syslog priority by threshold (critical 2, error 3, warning 4, info 6, below 7), on every line of the record, tracebacks included; ANSI escapes are stripped; there is no timestamp, pid or level name, which journald records itself. The product says who the tenant is (`tenant(record)`: Frappe's `frappe.local.site`, Odoo's `record.dbname`), may fix the logger name (Frappe prints the module, not `<module>-<site>`), and may append what its framework puts after a message by overriding `message()` (Odoo's `perf_info`). Its patching into the framework's logging stays its own.
-   **Today:** frappe-nix writes `<N>{module} [{site}] msg`, without ANSI stripping. odoo-nix writes `<N>{dbname} {logger}: msg`, with it.
-   **Status:** proposed.
-   **Check:** yes, `lib-journald`, on every preset interpreter.

## Containers

### Images

-   **Convention:** images are `<registry>/<name>/<role>`, flake outputs are `container-<role>`, and every image has OCI labels, a non-root `User` and a healthcheck.
-   **Today:** frappe-nix names images `${benchName}/<role>`, with outputs named by role. odoo-nix names its image `${projectName}-odoo`, output `container-odoo`.
-   **Status:** proposed.
-   **Check:** yes, evaluate the image configs.

## Repository

### Formatter

-   **Convention:** nixfmt-rs, through `nix fmt` and the nix-lint check (nixfmt, statix, deadnix), pinned by this repository's `lib.checks.nixLint`. The check also compares `statix.toml` with this repository's copy ([Repository files](#repository-files)). Each product registers it over its whole tree, leaving out what is not its own Nix:

    ```nix
    nix-lint = python-devops-nix.lib.checks.nixLint {
      inherit pkgs;
      src = ./.;
      product = "odoo-nix";
      ignore = [ "tests/fixtures" "templates" ];
    };
    ```
-   **Today:** frappe-nix uses nixfmt-rs. odoo-nix uses nixfmt. odoo-nix's switch is one formatting commit, listed in `.git-blame-ignore-revs`.
-   **Status:** decided.
-   **Check:** yes, the nix-lint check.

### CI

-   **Convention:** every action is pinned by full commit SHA, with the version in a trailing comment. A `lint` job runs prek over the whole tree. A `checks` job builds every flake check (`nix flake check`). Dependabot pull requests auto-merge through this repository's reusable workflow, called pinned the same way:

    ```yaml
    on: pull_request
    permissions:
      contents: write
      pull-requests: write
    jobs:
      auto-merge:
        uses: Avunu/python-devops-nix/.github/workflows/dependabot-auto-merge.yml@<sha> # v0.x.y
    ```

    Its inputs carry the policy. `update-types` (default `major minor patch other`, where `other` is an update with no semver type, such as a flake input) lists what auto-merges; `exclude-ecosystems` (default `github_actions`) lists ecosystems a person reviews; `merge-method` (default `squash`) is passed to `gh pr merge`. frappe-nix and odoo-nix take the defaults, which are what both do today. wordpress-nix does too: it merges major bumps, and its gate is its required OCI build, not an input. A repository that wants majors reviewed passes `update-types: minor patch other`. Auto-merge is only as safe as the checks marked required on the default branch.
-   **Today:** frappe-nix pins most actions by SHA and has the lint job. odoo-nix pins by tag and has no lint job. Each has its own copy of the auto-merge workflow, which differ only in a comment.
-   **Status:** proposed.
-   **Check:** yes, a pattern over `uses:` lines.

### Repository files

-   **Convention:** the same set in both: `.editorconfig`, `.envrc`, `.pre-commit-config.yaml`, `ruff.toml`, `ty.toml` and `statix.toml`. `version.txt` only once releases are wired up. Each product keeps its own copy at its root, because the tools read them from there, and the shared settings in each match this repository's copy.

    The rule is the same for every shared file: **the product's copy makes every setting this repository's copy makes, with the same value, and may add its own.** Not byte-identical copies, because the products legitimately add to them: frappe-nix's `ruff.toml` extends Frappe's own ruff config, and both products' prek configurations add hooks and exclude their own paths. A list is one value and is compared whole. Per file, a setting is:

    | File | A setting is | Not compared |
    | --- | --- | --- |
    | `statix.toml`, `ruff.toml` | each key, by its path through the TOML tables | — |
    | `.editorconfig` | each key under a section (`[*.py] indent_style`), and the preamble's `root` | — |
    | `.pre-commit-config.yaml` | each hook, by repository URL and hook `id` (with its `name` where the base gives one), and each key the base sets on it (`args`) | `rev`, which each repository's Dependabot moves on its own schedule; the top-level `exclude` and other keys, which name the repository's own paths |

    Where a product has to set a shared setting differently, it lists the setting as an exception in its check and says why in a comment next to it: frappe-nix's Python is written in Frappe's style, so its `[*.py] indent_style` is `tab`. An exception for a setting this repository no longer makes fails the check, so exceptions do not outlive their reason.

    ruff's `extend` was the obvious alternative for `ruff.toml`, and it was rejected: it takes a path, and this repository's copy is in the Nix store, which an editor or a prek run outside the development shell does not have. The base's ruff settings are few (the `target-version` floor), so copying them in is cheaper than any mechanism that resolves a store path.
-   **Today:** frappe-nix has all of them; its `.editorconfig` uses tabs for Python. odoo-nix has `statix.toml` only.
-   **Status:** proposed.
-   **Check:** yes. `lib.checks.nixLint` compares `statix.toml`, and `lib.checks.sharedConfig` the rest:

    ```nix
    shared-config = python-devops-nix.lib.checks.sharedConfig {
      inherit pkgs;
      src = ./.;
      product = "frappe-nix";
      # Python here is Frappe's style: tabs, as Frappe's own .editorconfig.
      exceptions.".editorconfig" = [ [ "*.py" "indent_style" ] ];
    };
    ```

    `files` defaults to `.editorconfig`, `.pre-commit-config.yaml` and `ruff.toml`; a product that has no Python yet can drop `ruff.toml` from it until it does.

### Dependabot

-   **Convention:** one shape: commit prefix `chore`, a seven-day cooldown, and one group per ecosystem (`patterns: ["*"]`), plus named groups only where inputs must move apart (odoo-nix's `ocb` and `odoo-ls`). Each product lists its own ecosystems. Pull requests auto-merge, except github-actions bumps, which a person reviews, through the reusable workflow in [CI](#ci).
-   **Today:** frappe-nix covers nix, uv, pre-commit and github-actions, with the `chore` prefix and no cooldown. odoo-nix covers nix and github-actions, with the `chore` prefix, the `ocb` and `odoo-ls` groups, and no cooldown since it dropped docs-site.
-   **Status:** proposed.
-   **Check:** partly, a check over `.github/dependabot.yml` for prefix and cooldown.

### Docs pipeline

-   **Convention:** `Avunu/site-docs`'s `check-docs.yml`, and frappe-nix's page structure: getting started, scaffolding, development, production, reference. odoo-nix drops `docs-site/` and `docs-publish.yml` and splits its README into those pages.
-   **Today:** frappe-nix uses `check-docs.yml` and a 50-page `docs/` tree. odoo-nix uses `Avunu/docusystem`'s `docs-build.yml` and `docs-publish.yml`, with a 700-line README duplicated into `docs/README.md`.
-   **Status:** decided.
-   **Check:** yes, the workflow is the one called.

### Tests

-   **Convention:** every check and VM test a product defines is a `checks.<system>.<x>` attribute, registered through one helper, `lib.checks.mk`. The naming scheme:

    -   `<x>` is kebab-case and names what is checked (`agecheck`, `db-nocow`, `migrate-rollback`), never the kind of check: no `eval-`, `module-` or `vm-` prefix, because the helper already records the kind. odoo-nix's `module-nginx` becomes `nginx`.
    -   A check that runs once per framework series takes the series' major version as a suffix: `<x>-<major>` (`devguard-18`, `devguard-19`).
    -   The derivation is named `<product>-<x>-check` (`frappe-nix-agecheck-check`), so a build log names the product and the check. VM tests keep nixpkgs' prefix: `vm-test-run-<product>-<x>`.
    -   The test's own files sit beside the code (`tests/<x>.sh`, `tests/<x>.nix`, `tests/test_<x>.py`), named by the same `<x>`.

    `lib.checks.mk { pkgs, product }` returns:

    | Function | Use | Replaces |
    | --- | --- | --- |
    | `script <x> { src ? null, tools ? [ ], env ? { }, drvArgs ? { } } <text>` | A shell script, run with `tools` on PATH, in a writable copy of `src` if given, with a writable `HOME` and pipefail on. It passes when it exits 0. It may write a report to `$out`, a file or a directory. | frappe-nix's `runCommand` wrappers, odoo-nix's `mkCheck` |
    | `eval <x> { assertion, message }` or `eval <x> { expected, actual, message ? "" }` | A Nix expression, judged at evaluation and reported at build, so a failure fails one named check, not the whole evaluation. The failure prints the message and, for `expected`/`actual`, a diff of their JSON. | odoo-nix's `mkEvalCheck` |
    | `nixosTest <x> <module>` | A NixOS VM test (`pkgs.testers.runNixOSTest`), named by the helper; the module sets no `name`. | both products' `runNixOSTest` calls |
    | `checkName <x>` | The derivation name, `<product>-<x>-check`, for a check built some other way. | |

    `lib.checks.nixLint` and `lib.checks.sharedConfig` are built with it and take the same `product`.
-   **Today:** frappe-nix drives `tests/<x>.sh` from `lib/<x>.nix`, with derivations named `frappe-nix-<x>-check` (and a few `frappe-<x>-check`). odoo-nix has `test_*.py`, `module-*.nix` VM tests, `eval-*` checks, derivations named `odoo-nix-check-<x>`, and `-<major>` suffixes.
-   **Status:** proposed.
-   **Check:** yes, a pattern over check attribute names.

### Commits

-   **Convention:** conventional commits, enforced by a commit-message check, with one scope list for both.
-   **Today:** both mostly use conventional commits: about 16% of frappe-nix's commits do not, and about 21% of odoo-nix's.
-   **Status:** proposed.
-   **Check:** yes, a commit-message hook and CI job.

## The development shell option tree

**Status:** _decided_ (adopted as proposed on 2026-10-17).

Both devenv modules have a `devguard` tree. odoo-nix also has a `dev` tree, which holds one option, `dev.autoReload`. Since the whole module configures the development shell, `dev` names nothing, and the options related to it are spread elsewhere in both products (odoo-nix: `odooConf.devMode`, `odooConf.workers`, `testBrowser`, `ide`; frappe-nix: `runtime.dev`, `watch.*`, `assets.reassert.*`). `devguard` names a real subsystem and stays, but holds the Mailpit catcher's settings, which are not a guard. Ports are spread over four trees in odoo-nix.

For both products:

| Tree | Holds | Moves in |
| --- | --- | --- |
| `watch` | What the shell does when a file changes. `watch.python` (bool, default true): restart the server on a Python change. frappe-nix keeps `watch.{apps,excludePublishers,rtl,nativeSass}` and gains `watch.reassert`. | odoo-nix `dev.autoReload` and frappe-nix `runtime.dev` become `watch.python`; frappe-nix `assets.reassert` becomes `watch.reassert`. odoo-nix keeps its assertions that `watch.python` needs `workers = 0` and a reloading `devMode`. The `dev` tree is removed. |
| `devguard` | Guard policy only: what is refused or redirected. Shared guards `egress`, `mail`, `jobs`, `backups`, `objectstore` and `webhooks`, each with `enable` and the same shape. Product guards beside them (frappe-nix `integrations`, `google`, `plaid`; odoo-nix `iap`). | `scheduler` and `crons` become `jobs`. `devguard.mail` keeps `enable`, `sender` and `unmute`. |
| `mailpit` | The catcher as a service: `mailpit.{enable,host,port,httpPort}` and `mailpit.pop3.{enable,port,user,password}`, with ports defaulting from `ports`. On by default in both products, whether or not the mail guard is on. | `devguard.mail.{host,smtpPort/port,httpPort}` from both, and frappe-nix's `devguard.mail.pop3.*`. |
| `ports` | `ports.{base,offset,worktreeSalt}` as frappe-nix has them, and one derived attribute per service port (`ports.http`, `ports.smtp`, `ports.mailUi`, `ports.db`, …) that the rest of the module reads, all from `lib.ports.options`. Each can still be forced with `lib.mkForce`, which is why they are not `readOnly`: a read-only option refuses that override too. A product that leases its ports at run time (see Ports) adds `ports.strict` (refuse a taken preferred port rather than move past it; null for the product's default) and `ports.span` (how many ports past the preferred one to try) beside them; frappe-nix declares both itself today, outside `lib.ports.options`, and odoo-nix takes the same names if it leases. | odoo-nix `odooConf.httpPort`, `odooConf.geventPort` and `odooConf.dbPort`, and the Mailpit ports. |

This keeps the decided name `port`: the SMTP port is `mailpit.port`, which defaults to `ports.smtp`, and the mail guard redirects to it.

Today:

-   frappe-nix runs Mailpit only when the mail guard is on, and derives every port from `ports.{base,offset,worktreeSalt}`.
-   odoo-nix always runs Mailpit (the guard only orders startup after it), and sets fixed ports in `odooConf` and `devguard.mail`.

To check during the move:

-   frappe-runtime's `--dev` turns on reload and asset serving together. If `watch.python = false` should still serve `/assets` and `/files`, frappe-runtime needs a separate flag; otherwise `watch.python` documents that it turns off both.
-   Mailpit-before-server ordering moves from the guard to the `mailpit` tree, so a server with the mail guard on never starts before its catcher.

Left alone for now, since neither product is clearly better: frappe-nix's `appsReconcile`, `renamedApps` and `replacedApps`, and odoo-nix's `layout.*`.

**Check:** yes, once adopted: the option paths exist in both devenv modules, and the `dev` tree does not.
