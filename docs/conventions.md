---
title: Conventions
description: How frappe-nix and odoo-nix name and do the jobs they both do, and where each product stands on each decision.
order: 1
tags: [conventions, frappe-nix, odoo-nix]
updated: 2026-10-23
---

# Conventions

How frappe-nix and odoo-nix name and do the jobs they both do. Where the two products do the same job, they do it the same way and under the same names. This page records one decision per job and where each product stands on it.

It is the record of what the products do on their `main` branches as of 2026-10-22. odoo-nix v1.1.0 shipped all of it; frappe-nix v1.2.0 and v1.2.1 shipped most of it, and the rest lands in frappe-nix's next release. It was first drafted on 2026-10-16, and what is left is tracked in each repository's issues. A pull request that changes a convention, or a product's adoption of one, updates this page.

## How to read this

Each section gives:

-   **Convention**: what both products do.
-   **Adopted**: where each product stands, with the pull request that adopted it where that helps. `frappe-nix#N` and `odoo-nix#N` are the products' pull requests; `#N` is this repository's. A linked number is an open issue. A pull request still open on 2026-10-22 is marked _pending_.
-   **Status**: _decided_ (both products follow it, or are bound to), _partial_ (one product, or part of the convention, is not there yet; the Adopted line says what), or _deferred to 2.0.0_ (shipped opt-in through 1.x and the default from the product's 2.0.0).
-   **Check**: what checks the convention mechanically. A check this repository ships is named. Where the base does not ship one yet, the line says so and points to [#19](https://github.com/Avunu/python-devops-nix/issues/19), which tracks the base's conformance checks, and names the product's own tests where they cover it.

`<P>` stands for the product's environment prefix (`FRAPPE`, `ODOO`), and `<p>` for its lower-case name (`frappe`, `odoo`).

### Renames and the 1.x line

Both products moved to the names here, even where that was a breaking change. Every rename ships with a migration note in the product's changelog and upgrading page. Every renamed option, flag and environment variable keeps an alias that warns until the product's next major release, a deliberate `Release-As: 2.0.0`, so a consumer that follows `release-1` never loses a name it set. In flake-parts `perSystem` and in odoo-nix's devenv module, the alias goes on the product's internal `deprecations` list, folded into `lib.warn`, because `mkRenamedOptionModule` cannot warn there (odoo-nix's devenv module renames with `lib.doRename` and `warn = false` beside that list); the NixOS modules use `mkRenamedOptionModule`.

A change that alters a running deployment without anyone asking for it ships opt-in through 1.x and becomes the default in 2.0.0: unit hardening, non-root containers and odoo-nix's role image name. [frappe-nix#182](https://github.com/Avunu/frappe-nix/issues/182) tracks frappe-nix's flips; odoo-nix's ship in its own 2.0.0.

## Naming rule

Option names follow one rule.

1.  **Structure follows nixpkgs.** Use `enable`, `package`, `user` and `group`, `dataDir` or `stateDir`, `environmentFiles` and `openFirewall`. Use `settings` for a freeform attribute set rendered into the framework's config file (RFC 42). Key multi-tenant modules by hostname, as in `services.wordpress.sites.<host>` and `services.nginx.virtualHosts.<host>`.
2.  **Names for things frappe-nix or odoo-nix invented are shared.** That covers the project identity, ports, guards, the mail catcher and the restore flags. Both products use the same word.
3.  **Names that mirror a framework config key use that framework's word**, in camelCase. Frappe's `default_site` is `defaultSite`; Odoo's `db_name` is `dbName`. Two frameworks with different concepts keep different names.

nixpkgs is not consistent about the option for an app's public hostname (nextcloud `hostName`, discourse `hostname`, odoo `domain`, mastodon `localDomain`, mediawiki `url`). Part 3 settles it: each product keeps the shape its nixpkgs neighbour uses, if there is one.

## Environment variables

### Tool-owned variables

-   **Convention:** every environment variable a product's own tools read or export is `<P>_NIX_*`: `FRAPPE_NIX_*` and `ODOO_NIX_*`. Both export `<P>_NIX_DEVGUARD`. devguard's settings are `<P>_DEVGUARD_*` (see [The core and the product's package](#the-core-and-the-products-package)).
-   **Adopted:**
    -   frappe-nix (frappe-nix#123) exports `FRAPPE_NIX_DEVGUARD` and reads `FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED`, `FRAPPE_NIX_BACKUP_SOURCE`, `FRAPPE_NIX_RCLONE` (`FRAPPE_NIX_MC` until minio-client went) and `FRAPPE_NIX_BACKUP_CACHE`. The old names (`FRAPPE_RESTORE_ALLOW_UNGUARDED`, `FRAPPE_BACKUP_SOURCE`, `FRAPPE_BACKUP_MC`, `FRAPPE_BACKUP_CACHE`, `FRAPPE_OFFLINE_MIGRATE*`, `FRAPPE_BENCH_UPDATE_FORCE` and others) are read with a warning until 2.0.0. `FRAPPE_UNIXSOCK_ENABLED` is still outside the pattern, and frappe-runtime reads its own `FRAPPE_SERVE_ASSETS` and `FRAPPE_WEB_THREADS`.
    -   odoo-nix (odoo-nix#57) reads `ODOO_NIX_*` and exports `ODOO_NIX_DEVGUARD`. `ODOO_DB`, `ODOO_TEST_ALLOW_SKIP`, `ODOO_HTTP_PORT` and `ODOO_GEVENT_PORT` stay, with a warning, until 2.0.0. `ODOO_NIX_JOURNALD` is no longer read, and setting it warns.
-   **Status:** partial: frappe-nix's `FRAPPE_UNIXSOCK_ENABLED`.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)). Such a check needs an allow-list for a framework's own variables and the aliases.

The backup fetch layer (`lib.backups`) reads `<P>_NIX_BACKUP_SOURCE`, `<P>_NIX_RCLONE` and `<P>_NIX_BACKUP_CACHE`. Its `legacyEnv` argument reads an old name, with a warning, for the deprecation window. The connection itself keeps the names the `backup-access` secret already uses in both products, which belong to the secret's format rather than to a product: `BACKUPS_URL`, `BACKUPS_ACCESS_KEY`, `BACKUPS_SECRET_KEY`, `BACKUPS_BUCKET`, `BACKUPS_PREFIX`, and the optional `BACKUPS_REGION`, which the layer otherwise reads from `BACKUPS_URL`'s host name.

The client was minio-client until nixpkgs marked it insecure, as abandoned upstream; it is rclone now. `<P>_NIX_MC`, its `legacyEnv` name, and an mc target (`<alias>/<bucket>/…`) as `<P>_NIX_BACKUP_SOURCE` are refused with what replaced them: `<P>_NIX_RCLONE`, and an rclone target (`<remote>:<bucket>/…`).

## Secrets

-   **Convention:** both products declare their secrets with `lib.secrets.mkModule` at `<p>-nix.secrets`, with one option tree: `enable`, `dir`, `relDir`, `recipients`, `hostRecipients`, `identityPaths`, `backupAccess.{enable,hosts}`, `extra.<name>.{format,var,hosts}`, plus frappe-nix's `sites` as a group. Shell variables are `<p>_<role>` (`odoo_backup_access`), and `<p>_<role>_<unit>` for a group's units (`frappe_db_password_erp_example_com`). The flake output is `flake.lib.secrets` in both. The dev-shell commands are `edit-secret`, `rekey-secrets`, `check-secrets` and `setup-backup-access`, from `lib.secrets.tools`. `edit-secret`, `rekey-secrets` and `setup-backup-access` are Python with rich prompts (#15), and `check-secrets` is an `exec` shim over agecheck.
-   **Adopted:** frappe-nix#97 and odoo-nix#52. `flake.lib.frappeSecrets` and `flake.lib.odooSecrets` stay as aliases (`libAliases`) until 2.0.0. `backupAccess.hosts` defaults to false in frappe-nix and true in odoo-nix, whose server reads the same secret; that stays a product's choice of its role's default.
-   **Status:** decided.
-   **Check:** the base's `lib-secrets-module`, `lib-secrets-schema`, `lib-secrets-cli`, `lib-secret-tools` and `lib-agecheck`. Adoption was shown by comparing each product's rules file and agenix-shell secret set before and after, as JSON.

## Restore

`bench restore` (frappe-nix) and `odoo db restore` (odoo-nix) take the same flags and options.

### Overwrite

-   **Convention:** restore overwrites an existing database by default. `--no-force` refuses instead.
-   **Adopted:** frappe-nix always did (frappe-nix#98 kept it). odoo-nix since odoo-nix#53, where `--force` is accepted as a no-op with a warning until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)). Each product's restore tests assert it (frappe-nix's `tests/bench-restore.sh`).

### Migrate after restore

-   **Convention:** restore runs the framework's migration afterwards, on by default. `--no-migrate` and `restore.migrate` turn it off.
-   **Adopted:** frappe-nix always had both. odoo-nix since odoo-nix#53 (`--migrate/--no-migrate`, `restore.migrate`).
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Neutralise

-   **Convention:** `--neutralize`, `--no-neutralize` and `restore.neutralize` in both. In frappe-nix, neutralising pauses the scheduler and mutes mail in the restored database, as defence in depth below devguard.
-   **Adopted:** odoo-nix had both; its `restore.neutralize` is on in the development shell and off on a server. frappe-nix since frappe-nix#98 and #123, with `restore.neutralize` on by default; a restore from an explicit file is neutralised only with `--neutralize`.
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Files

-   **Convention:** `--files` and `restore.files`. frappe-nix keeps `--private-files` as its own extra.
-   **Adopted:** frappe-nix#123: `restore.files` is a bool or `"none" | "private" | "all"` (default `"none"`), since Frappe has private and public files; `restore.withFiles` is its alias. odoo-nix#57: `--files/--no-files` and `restore.files`, with `--attachments` and `restore.attachments` as aliases until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Unguarded restore

-   **Convention:** restore refuses to run without devguard, unless `<P>_NIX_RESTORE_ALLOW_UNGUARDED` is set. `restore.requireDevguard` (default true) in both.
-   **Adopted:** frappe-nix#98 and #123; odoo-nix#53, whose command line also reads `ODOO_NIX_RESTORE_REQUIRE_DEVGUARD`.
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)). The refusal is in each product's restore tests.

### Point in time (`--at`)

-   **Convention:** `--at` accepts ISO 8601 in both, and each product's own backup naming as well. A backup's exact name wins; otherwise the value's digits are matched as a prefix of the backup's `YYYYMMDDHHMMSS`, so any leading part of a date or time picks the newest backup within it (`2026-10` is the newest of October 2026). A trailing `Z` is accepted; other offsets, fractions and words are refused. Times are the backup's own clock. Implemented once, in `lib.backups` (`parse_at`, `pick`).
-   **Adopted:** both, through the base's fetch layer (frappe-nix#98, odoo-nix#53).
-   **Status:** decided.
-   **Check:** the base's `lib-backups` tests the parser on every preset interpreter, and drives the command with each form.

## devguard

### SMTP port

-   **Convention:** the option is named `port`, matching both Python settings schemas. Where it lives is in [the development shell option tree](#the-development-shell-option-tree): `mailpit.port`.
-   **Adopted:** frappe-nix#124 and odoo-nix#58. frappe-nix's `devguard.mail.smtpPort` and `devguard.mail.port`, and odoo-nix's `devguard.mail.port`, are aliases until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Job denylist

-   **Convention:** `devguard.jobs.{enable,extraBlocked}`, and `blocked_jobs` in the Python settings. Log lines keep the framework's words (scheduler, crons). frappe-nix's `blockServerScripts` is `devguard.jobs.blockServerScripts`. In the Python settings, `guards.jobs.blocked_jobs` is the product's built-in list (its default), `guards.jobs.extra_blocked` the consumer's additions (`extraBlocked`, baked), and the guard reads their union, in order, as `settings().blocked_jobs`, which the core provides.
-   **Adopted:** frappe-nix#112 and #124; odoo-nix#56 and #58. `scheduler` (frappe-nix) and `crons` (odoo-nix) are aliases until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base for the option paths ([#19](https://github.com/Avunu/python-devops-nix/issues/19)). `lib-devguard` tests the union.

### Shared guard names

-   **Convention:** the guards both products have keep their names, `egress`, `mail`, `mail_stdlib`, `objectstore`, `backups`, `jobs` and `webhooks`, with the same option shape under each (`enable` and the same settings). Product guards sit beside them (frappe-nix's `integrations`, `google` and `plaid`; odoo-nix's `iap`).
-   **Adopted:** both (frappe-nix#124, odoo-nix#58).
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

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
-   **Adopted:** frappe-nix#112 and odoo-nix#56. The product copies of the core are deleted.
-   **Status:** decided.
-   **Check:** `lib.devguard.check { pkgs, product, interpreters }` runs the core's tests against a stub product on every interpreter the product's presets name, and the merged build refuses a shadowed core file (`lib-devguard`, `lib-devguard-package`). Each product keeps a check that its production environment has no `<p>_devguard`.

### Grafts

-   **Convention:** every package loaded at interpreter start, devguard or not, is grafted with `lib.python.withGrafts { python, env, grafts }`, which writes `zzz-<module, _ as ->.pth` running `import <module>; <module>.install()`. A graft that needs a post-import hook is built with `lib.python.mkGraft { pkgs, name, src }`, which adds the shared `_hook.py`; no graft carries its own copy.
-   **Adopted:** frappe-nix#112 and #151 (devguard, unixsock, nodebuild, benchcli and journald); odoo-nix#56 and #73 (devguard and journald).
-   **Status:** decided.
-   **Check:** the base's `lib-devguard` grafts two packages into one virtualenv and asserts the `.pth` names and that both hooks fire.

## Python environment

Both products build their environment the same way: pyproject-nix, uv2nix and pyproject-build-systems (the product's inputs, not the base's), `sourcePreference = "wheel"`, `mkEditablePyprojectOverlay` for development, and `.pth` grafts. These sections fix the pieces around that ([library](library.md#python-environment)).

### Package fixes

-   **Convention:** a fix for a package that needs a system library lives in `lib.python.overrides { pkgs }`, one pyproject-nix overlay. The product composes it right after uv2nix's overlay and layers its own on top (`composeManyExtensions [ pyproject-build-systems.overlays.default workspaceOverlay (lib.python.overrides { inherit pkgs; }) productOverrides ]`). It covers psycopg2, python-ldap (with the `ldap_r` link fix), lxml, pycups, python-magic, pyvips and cairocffi, and touches only a package the set has. A build fix (headers, pkg-config, setuptools) applies only to a source build, so a wheel's derivation is left as uv2nix made it. A runtime fix (a library's store path where the package dlopens it) patches the installed files, so it reaches a wheel too. The product keeps the fixes only it needs (frappe-nix's mysqlclient, odoo-nix's OCB build and `lxml_html_clean`). `lib.python.packageOverrides { pkgs }` has the same fixes one by one, for a product that exports them to its consumers.
-   **Adopted:** frappe-nix#127 (its `lib/overrides.nix` stays as an alias until 2.0.0) and odoo-nix#59, which adds a consumer's `extraOverrides` last.
-   **Status:** decided.
-   **Check:** the base's `lib-python-overrides` applies the set to a stub package set. Adoption was shown by each product's production environment derivation being unchanged, except for the fixes new to it.

### Tool interpreter with rich

-   **Convention:** a product's own tooling (a banner, a command line) runs on `lib.python.rich { python, extraPackages ? ps: [ ] }`: nixpkgs' rich, with markdown-it-py's checks off and rich's racy `test_brokenpipeerror` skipped. It is never added to the application's environment.
-   **Adopted:** odoo-nix#59 (its command line, scaffolder and banner). frappe-nix's banner runs on it through `lib.banner`, but frappe-nix-tools, its command-line package, is still built on nixpkgs' plain rich (`lib/standards/package.nix`).
-   **Status:** partial: frappe-nix's tooling ([frappe-nix#179](https://github.com/Avunu/frappe-nix/issues/179)).
-   **Check:** the base's `lib-python-rich` builds it on every preset interpreter, with rich's suite.

### Stale lock

-   **Convention:** every virtualenv is wrapped in `(lib.python.lockAudit { workspaceRoot, hasPackage = name: pythonSet ? ${name}; label = "<p>-nix"; lock ? "uv.lock"; relock ? …; }).assertCurrent`, so a declared dependency missing from the lock fails evaluation with `<p>-nix: uv.lock does not cover every declared dependency`, a line per gap naming the member and the requirement, and how to re-lock, instead of uv2nix's `attribute '<name>' missing`.
-   **Adopted:** frappe-nix#127 and odoo-nix#59. In odoo-nix the audit catches a root dependency missing from `uv.lock`, which otherwise builds silently without it; an OCA addon's own `dynamic` requirements are out of its reach.
-   **Status:** decided.
-   **Check:** the base's `lib-python-lock-audit` (an eval check).

### Build dependencies

-   **Convention:** `[tool.uv.extra-build-dependencies]` is derived from `uv.lock`, not kept by hand. Every package the lock has no wheel for gets `setuptools`; the product's `always` adds entries the lock cannot show (a package that is sdist-only elsewhere, a backend other than setuptools). The entries sit between `# >>> <p>-nix: sdist build deps (managed) >>>` and `# <<< <p>-nix: sdist build deps (managed) <<<`, and the relock command runs `lib.python.buildDeps { pkgs, label = "<p>-nix", always }` (`<p>-nix-build-deps update`) after `uv lock`, then locks again. `<p>-nix-build-deps check` fails when the block is out of date.
-   **Adopted:** frappe-nix#127 and odoo-nix#59.
-   **Status:** decided.
-   **Check:** the base's `lib-python-build-deps` runs the tests against both products' lock shapes on every preset interpreter.

### Thread caps

-   **Convention:** the application's environment sets `lib.python.threadCaps` (`OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and `NUMEXPR_NUM_THREADS`, each `1`) everywhere it runs: the development shell, the NixOS services and the containers.
-   **Adopted:** frappe-nix#127 and odoo-nix#59, in all three places.
-   **Status:** decided.
-   **Check:** none.

## Scaffolder

frappe-nix's `frappe-init` and odoo-nix's `odoo-init`, both Python since frappe-nix#108 and odoo-nix#71.

### Version flag

-   **Convention:** `--version`. The old names stay as aliases until 2.0.0.
-   **Adopted:** frappe-nix#108 (`--frappe-version` aliased) and odoo-nix#71 (`--series` read with a warning).
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Flags

-   **Convention:** both scaffolders have `--dry-run`, `--skip-lock`, `-y/--yes`, `--commit`, `--force`, `--name`, and migrating an existing tree. frappe-nix's `--site` is `--default-site`.
-   **Adopted:** frappe-nix#108 (`--site` aliased until 2.0.0) and odoo-nix#71 (with `--migrate`, `--init` and `--db`).
-   **Status:** decided.
-   **Check:** not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Exit codes

-   **Convention:** one table in both: 0 done (or a dry run finished); 1 an error with no more specific code; 2 an initialisation into a directory that is not empty, without `--force`; 4 a `flake.nix` (or, in odoo-nix, a `pyproject.toml`) that is not the product's is in the way; 5 the framework version is missing, unknown or unsupported; 6 the directory is not the shape the mode needs, or the confirmation was declined; 7 there is nothing to migrate; 8 the tree is inside another git repository. There is no 3. odoo-nix adds 130 for an interrupt.
-   **Adopted:** frappe-nix#108 and odoo-nix#71. Each product's scaffolder reference documents its table.
-   **Status:** decided.
-   **Check:** partly: each product's scaffolder tests assert the code for each failure they reach.

### Presets file

-   **Convention:** one schema: `{label, branch, python, requiresPython}`, with `nodejs` and `overrideDependencies` optional.
-   **Adopted:** frappe-nix has `{label, branch, python, nodejs, requiresPython, overrideDependencies}`. odoo-nix#71 has `{label, branch, python, requiresPython}`, for 18.0, 19.0 and 20.0 (odoo-nix#75).
-   **Status:** decided.
-   **Check:** not yet in the base.

### Seeds

-   **Convention:** both scaffolders seed `secrets/README.md` and print a warnings summary.
-   **Adopted:** frappe-nix#108 and odoo-nix#71.
-   **Status:** decided.
-   **Check:** each product's dry-run tests list the seeded files.

### Next steps

-   **Convention:** the same closing wording in both, naming each product's own command: restore when secrets are declared, otherwise provision.
-   **Adopted:** odoo-nix#71 suggests `odoo db restore <db>` or `odoo db provision`. frappe-nix suggests `bench restore` or `bench provision` (frappe-nix#166).
-   **Status:** decided.
-   **Check:** no; wording is reviewed.

## Flake

### Systems

-   **Convention:** `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`. No `x86_64-darwin`, which nixpkgs 26.11 dropped.
-   **Adopted:** both (frappe-nix#123).
-   **Status:** decided.
-   **Check:** not yet in the base.

## Development shell

The leaf pieces inside both devenv modules ([library](library.md#development-shell)), adopted in frappe-nix#130 and odoo-nix#62. The modules themselves stay in the products.

### Ports

-   **Convention:** every TCP port the shell binds is a base the product picks plus one offset, 0 to 899, a hash of the project's `name` (`sha256(name)[0:4] mod 900`), taken from `lib.ports`. A linked worktree (`.git` a file) salts the hash with its path when `ports.worktreeSalt` is on. `<P>_NIX_PORT_OFFSET` picks another offset for one shell and wins over `ports.offset`; a value outside 0 to 899 is ignored with a warning. The devenv module declares its `ports` tree with `lib.ports.options` (see [The development shell option tree](#the-development-shell-option-tree)); the bases stay the product's (frappe-nix: web 8000, MariaDB 3306, socket.io 9000, Mailpit 19000 to 21000; odoo-nix: HTTP 10000, gevent 11000, PostgreSQL 12000, Mailpit 13000 and 14000), clear of each other so a bench and a project never meet.
-   **Runtime leases:** what `lib.ports` gives is the *preferred* port. devenv's port allocator is the identity under the flake integration, so two copies of one project (CI checkouts, which are never linked worktrees, or two clones) prefer the same ports, and nothing evaluated can move a taken one. A product may lease its ports when `devenv up` starts: a first process claims each preferred port host-wide for the project's lifetime and moves past a taken one, or refuses it where `ports.strict` says so (`<P>_NIX_STRICT_PORTS` at run time), and writes the ports in force to `$DEVENV_RUNTIME/<p>-nix/ports.json`. Consumers then read that file, not the evaluated values. A product that commits a port (frappe-nix's bench mode, whose `webserver_port` is in `common_site_config.json`) is strict by default.
-   **Adopted:** both take `lib.ports` (frappe-nix#130; odoo-nix#58 and #62, which ended its fixed ports). frappe-nix leases its nginx and Mailpit ports at `devenv up` (frappe-nix#144): app mode moves, bench mode refuses. odoo-nix uses the evaluated ports as they are.
-   **Status:** decided. Leasing is a product's choice.
-   **Check:** the base's `lib-ports`, which holds both products' ports as golden values.

### CI mode

-   **Convention:** `<P>_NIX_CI` set to anything but empty, `0` or `false` puts the shell in CI mode, read when enterShell runs: `PC_TUI_ENABLED=0` and `DEVENV_IN_DIRENV_SHELL=true` are exported first, the interactive steps (node installs, the sources report) are skipped, and the banner is one line, `<p>-nix: CI mode (<name>[, <details>])`. Snippets from `lib.ciMode { label, envPrefix }`.
-   **Adopted:** frappe-nix#130 (`FRAPPE_NIX_CI`) and odoo-nix#62 (`ODOO_NIX_CI`).
-   **Status:** decided.
-   **Check:** the base's `lib-ci-mode` runs the snippets; a product's own check asserts its enterShell uses them.

### Sources

-   **Convention:** source repositories are git submodules, and one command handles them, `lib.sources.command` (`<p>-nix-sources`). Shell entry runs `enter`: a submodule this clone has never set up is checked out once, as a partial clone (`--filter=blob:none`, or `tree:0` for a path the product names treeless) of the branch `.gitmodules` pairs it with and no other (`--single-branch`), falling back to the remote's default branch; one set up before and now missing is reported, never brought back. Every entry also fetches a pin a partial clone lacks before git asks for it lazily, merges a partial clone's packs geometrically, sets `fetch.recurseSubmodules=false`, and gives a shallow clone its paired branch's history in place. The scaffolder's first clone is `clone` (or `clone()`, imported). Messages are `<p>-nix: …` on stderr, and entry always exits 0. The product passes its scope (frappe-nix `[ "apps" ]`, odoo-nix every submodule), whether checkouts recurse (odoo-nix), its treeless paths (odoo-nix's core), and the update and remove commands its hints name.
-   **Adopted:** frappe-nix#130 and #131, and odoo-nix#62. Both products' bash copies are deleted.
-   **Status:** decided.
-   **Check:** the base's `lib-sources` runs one suite in both products' shapes on every preset interpreter.

### Database copy-on-write

-   **Convention:** on btrfs, the development database's datadir has copy-on-write off (`chattr +C`), from `lib.dbNocow { pkgs, label, strategy, server }` (`<p>-nix-db-nocow`). `prepare` runs on shell entry before the server starts: with `strategy = "datadir"` (MariaDB) it creates and marks the directory; with `"parent"` (PostgreSQL, whose initdb runs only when the datadir is missing) it marks the parent until initdb has made the datadir, then clears it. A datadir already holding data is reported with the command that fixes it, `migrate`, which refuses while the server runs (`server = "cmdline"`, a process with `--datadir=<dir>`; `"pidfile"`, a live `postmaster.pid`). Off btrfs both are silent no-ops, and `migrate` says so before looking for a server. Linux only.
-   **Adopted:** frappe-nix#130 (`datadir`) and odoo-nix#62 (`parent`).
-   **Status:** decided.
-   **Check:** the base's `lib-db-nocow` in both strategies, on whatever filesystem the builder has, and by hand on the other (`TMPDIR=<dir> bash tests/db-nocow.sh …`).

### Banner

-   **Convention:** the shell's banner is `lib.banner.command`, rendered with rich (on `lib.python.rich`, never the application's environment) from one JSON spec the product writes at evaluation: `title`, `commands`, `urls`, `facts`, `guard` (`enable`, `egress.{enable,allow}`, `mail.{enable,url,incoming}`) and `notes`. A fact known only when the shell starts is an `@name@` placeholder filled by `--var name=value` (`lib.banner.script`'s `vars`). Nothing in the spec is rich markup. The devguard lines read the same in both: `devguard OFF — nothing stops a restored database from reaching production`, `Egress: outbound connections blocked (allowed: …)`, `Mail: ALL outgoing email → Mailpit (…)`.
-   **Adopted:** frappe-nix#130 and odoo-nix#62.
-   **Status:** decided.
-   **Check:** the base's `lib-banner`.

## Command line

### Command layout

-   **Convention:** every command lives under the product's umbrella command, `bench` or `odoo`. frappe-nix's `provision-site` is `bench provision`, and `provision-site` stays as an alias until 2.0.0.
-   **Adopted:** odoo-nix has everything under `odoo` (`db`, `module`, `project`). frappe-nix has `bench provision` under `bench` (frappe-nix#166), with `provision-site` kept as a deprecated alias until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base.

### Source management

-   **Convention:** add and remove a source repository in both.
-   **Adopted:** frappe-nix has `bench get-app`, `bench remove-app`, `bench new-app` and `bench rename-app` (frappe-nix#131). odoo-nix has `odoo module add`, `odoo module add-bundle` and `odoo module remove` (odoo-nix#72).
-   **Status:** decided.
-   **Check:** not yet in the base.

### Relock

-   **Convention:** one consumer-facing relock command in both, run from the shell. A product also offers it as a flake app (`nix run .#relock`) for a project whose shell cannot evaluate because its lock is stale.
-   **Adopted:** frappe-nix#150: `relock` in the shell and `nix run .#relock` are one program (`--uv-only`, `--node-locks`); `update-deps` and `bench update` call it. odoo-nix#82: `odoo project relock`, with the workspace lock in Python. odoo-nix's `.#relock` re-locks its own test fixtures only, so a consumer has no flake app yet ([odoo-nix#95](https://github.com/Avunu/odoo-nix/issues/95)).
-   **Status:** partial: odoo-nix's flake app.
-   **Check:** not yet in the base.

## Production

The pieces in the NixOS modules ([library](library.md#production)), adopted in frappe-nix#151 and odoo-nix#73.

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
-   **Adopted:** frappe-nix#151 runs the flow (`lib.nixos.migrate.script`, `verifiers.mysqldump`). odoo-nix#73 vendors the modules and subclasses `Runner` inside `odoo db migrate`.
-   **Status:** decided.
-   **Check:** the base's `lib-nixos` evaluates the options and the flow's configuration in a stub product; `lib-nixos-migrate-flow` runs the flow's unit tests on every preset interpreter; `lib-nixos-migrate` runs it in a VM against MariaDB and PostgreSQL, in both products' failure shapes. Each product's VM test covers its own steps.

### Production secrets

-   **Convention:** secrets reach units through `LoadCredential`, and init runs as the service user. `environmentFiles` in both.
-   **Adopted:** frappe-nix always used `LoadCredential` and gained `services.frappe-nix.environmentFiles` (frappe-nix#151). odoo-nix#73 moved init to the service user with `LoadCredential`; it already had `environmentFiles`.
-   **Status:** decided.
-   **Check:** not yet in the base. Each product's module-evaluation tests inspect the units.

### Database logging

-   **Convention:** `database.slowQueryMs` in both.
-   **Adopted:** frappe-nix#151; odoo-nix#73 (its `logging.slowQueryMs` is an alias until 2.0.0).
-   **Status:** decided.
-   **Check:** not yet in the base.

### Unit hardening

-   **Convention:** every unit a product defines gets `lib.nixos.hardening { readWritePaths, profiles ? [ ], addressFamilies ? [ AF_UNIX AF_INET AF_INET6 ], capabilities ? [ ], overrides ? { } }` merged into its `serviceConfig`, behind `services.<p>-nix.hardening.enable`. The baseline (`lib.nixos.hardening.baseline`) is `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict` with the unit's `ReadWritePaths`, `ProtectHome`, `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `ProtectProc=invisible`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, `MemoryDenyWriteExecute`, `RemoveIPC`, an empty `CapabilityBoundingSet` and `AmbientCapabilities`, `SystemCallArchitectures=native`, and `SystemCallFilter=@system-service ~@privileged` failing with `EPERM`. Each value is a `mkDefault`, so a host can relax one key on one unit; `ReadWritePaths` is a plain list, so a host's paths add to the product's.

    `hardening.enable` is off through 1.x, because the sandbox can refuse something a running deployment does today, and on from 2.0.0. Off, a unit gets none of these keys.

    There is **no `UMask=0077`**, here or in any unit: units that write state also write files nginx serves through the service group (Frappe's Website Theme CSS, frappe-nix#83; Odoo's filestore under `xSendfile`, odoo-nix#51). Private files get their modes where they are written, as the migrate snapshots do.

    A unit names what it legitimately needs, and documents it next to the call:

    | Need | How | Who |
    | --- | --- | --- |
    | A JIT (W+X memory) | `profiles = [ "jit" ]` | Node (frappe-nix's socketio); odoo-nix's Odoo units |
    | Headless Chromium | `profiles = [ "chromium" ]`: the JIT, `capset` (each child drops its capabilities, even under `--no-sandbox`), user namespaces, `AF_NETLINK` | frappe-nix's units that render PDFs, with `chromium.enable` |
    | wkhtmltopdf | `profiles = [ "wkhtmltopdf" ]`: QtWebKit's JIT | frappe-nix's units that render PDFs, with `wkhtmltopdf.enable`; odoo-nix's Odoo units |
    | Only unix sockets | `addressFamilies = [ "AF_UNIX" ]` | a unit that reaches its database over its socket alone |
    | Still runs as root | `capabilities = [ ... ]`, which also lets `@privileged` calls through the filter | none today: odoo-nix's `odoo-init` runs as the service user since odoo-nix#73 ([Production secrets](#production-secrets)) |
    | Anything else | `overrides = { <key> = <value>; }`, with a comment saying why | |

    `ProtectSystem=strict` leaves `/run` writable, so a socket directory needs no `ReadWritePaths` (its mode is the gate: [Unix sockets](#unix-sockets)); a state directory does. A path in `ReadWritePaths` must exist when the unit starts; create it with tmpfiles.
-   **Adopted:** frappe-nix#151 and odoo-nix#73, opt-in, as above.
-   **Status:** deferred to 2.0.0 (as the default; shipped opt-in in both).
-   **Check:** the base's `lib-nixos` evaluates the baseline, the profiles and the overrides in a stub product, and asserts no unit has a `UMask`. `lib-nixos-hardening` runs Python with a ctypes callback, Node, headless Chromium and wkhtmltopdf under it in a VM, and shows the baseline refuses a JIT and a root unit keeps only the capabilities it names. A product's VM tests run its units with hardening on.

### Unix sockets

-   **Convention:** a socket lives in a directory of its own (never straight in `/run` or `/`), created by tmpfiles and owned by the service user and group: `0750` when the service creates the socket and a peer in the group (nginx) only connects, `0770` when a peer creates its own socket there too (nginx's listen socket). The socket file's own mode gates nothing, since nginx chmods its listen sockets to `0666`. `lib.nixos.unixSocket { user, group, sockets = [ { option, path, peerCreates ? false, example ? … } ] }` returns `{ assertions, tmpfiles, directories }`: one assertion per socket option (`<option> must be an absolute path inside its own directory (e.g. …), not directly in /run: the directory's mode is what keeps the socket private.`), one tmpfiles rule per directory, and the directories. An unset path (`""` or `null`) is skipped.
-   **Adopted:** frappe-nix#151 (gunicorn's and socketio's directories `0750`, nginx's `0770`) and odoo-nix#73 (nginx's socket).
-   **Status:** decided.
-   **Check:** the base's `lib-nixos` (the rules and the messages) and `lib-nixos-migrate` (the modes on a running system, with nginx connecting through them).

### nginx

-   **Convention:** nginx logs to the journal:

    -   the access log as `journal_json`, one JSON object per request with `time`, `site`, `method`, `uri`, `status`, `bytes`, `request_time`, `upstream_time`, `remote_addr`, `user_agent` and `referer`, under `SYSLOG_IDENTIFIER=nginx_access`: `services.nginx.commonHttpConfig = lib.nixos.nginx.accessLog cfg.logging.accessLog;` (`access_log off;` when false). `lib.nixos.nginx.journalJsonFormat` is the `log_format` line, and `journalJsonFields` its fields;
    -   the error log over syslog, so it keeps its severity: `services.nginx.logError = lib.mkDefault lib.nixos.nginx.errorLog;` (at `error`; `errorLogAt <level>` for another).

    A virtual host that listens on a unix socket behind a Cloudflare tunnel adds `lib.nixos.nginx.cloudflareRealIp` (`set_real_ip_from unix:; real_ip_header CF-Connecting-IP;`) to its `extraConfig`, never on a TCP listener, and each of its locations turns `recommendedProxySettings` off and adds `lib.nixos.nginx.socketProxyHeaders` (`X-Forwarded-Proto https`, since TLS ended at the connector).
-   **Adopted:** frappe-nix#151 and odoo-nix#73. The product copies are deleted, and odoo-nix's error log moved from stderr to syslog.
-   **Status:** decided.
-   **Check:** the base's `lib-nixos` (the configuration text) and `lib-nixos-migrate` (the client address from the header in the access log, and an upstream error at priority err).

### nginx configuration

-   **Convention:** a product builds its nginx configuration from `lib.nixos.nginx`, so the TCP and unix-socket shapes are the same in both:

    -   `services.nginx = lib.nixos.nginx.serviceDefaults { accessLog = cfg.logging.accessLog; } // { upstreams = …; virtualHosts = …; };` turns nginx on with the recommended proxy and gzip settings and the two logs above;
    -   an upstream is `lib.nixos.nginx.upstream { socketPath, port, address ? "127.0.0.1" }`: the unix socket when the application listens on one, `address:port` otherwise;
    -   a virtual host starts from `lib.nixos.nginx.vhost { socketPath, extraConfig ? "" }`, which listens on the socket and adds `cloudflareRealIp` when `socketPath` is set and keeps nginx's default listeners otherwise;
    -   a location that proxies to the application is `lib.nixos.nginx.proxy { pass, viaSocket, websockets ? false, extraConfig ? "" }`. Behind a socket listener it sets `socketProxyHeaders` in place of the recommended settings, ahead of its own lines; `websockets` is NixOS's `proxyWebsockets`;
    -   the location an `X-Accel-Redirect` lands on is `lib.nixos.nginx.internal { alias | extraConfig }`, so a client's own request for that path is a 404 (Frappe's `/protected/`, Odoo's `/web/filestore/` under `x_sendfile`).

    The whole chain can be unix sockets: a tunnel connector to nginx's listen socket, nginx to the application's. Each socket follows [unix sockets](#unix-sockets).
-   **Adopted:** frappe-nix and odoo-nix (pending). odoo-nix's application side needs its `odoo_socket` graft, since Odoo binds TCP only.
-   **Status:** decided.
-   **Check:** the base's `lib-nixos` (each helper's output, and the stub product built from them) and `lib-nixos-migrate` (the stub served through them, on sockets end to end).

### NixOS module path

-   **Convention:** `services.frappe-nix` and `services.odoo-nix`. These match the devenv and flake-parts roots (`frappe-nix.*`, `odoo-nix.*`) and cannot collide with a future nixpkgs module.
-   **Adopted:** frappe-nix#123, with `services.frappe` as an alias until 2.0.0. odoo-nix always used `services.odoo-nix`.
-   **Status:** decided.
-   **Check:** not yet in the base.

### Framework config blocks

-   **Convention:** `settings` wherever a block becomes the framework's config file, following [NixOS RFC 42](https://github.com/NixOS/rfcs/blob/master/rfcs/0042-config-option.md):
    -   `settings` is a submodule whose `freeformType` is the file's `pkgs.formats` type: `pkgs.formats.json` for frappe-nix's `site_config.json`, and `pkgs.formats.ini` for odoo-nix's `odoo.conf`, by section (`settings.options.<key>`), as nixpkgs' `services.odoo` has it.
    -   Keys inside `settings` use the framework's own spelling (`host_name`, `db_name`), declared or not, so `settings` is a one-to-one picture of the file.
    -   The module sets the keys it computes in `config.…settings`, at `mkDefault` where a user may change them and at normal priority, with a comment, where the module depends on the value. Reading `settings` therefore shows the effective file (minus secrets), and a user's value wins without `mkForce`.
    -   Only a few valuable keys (RFC 42 Part 2: mandatory or commonly set) are declared as typed sub-options; the module's own typed options (ports, workers, database) set their keys rather than being declared twice.
    -   Secrets never go in `settings`: frappe-nix has `settingsFiles` and `encryptionKeyFile`, odoo-nix `adminPasswordFile` and `environmentFiles`, both read through `LoadCredential`.
    -   The development shell's odoo-nix `settings` is the exception: a typed camelCase tree (`settings.dbName`) that predates this rule and keeps its shape through 1.x.
-   **Adopted:** frappe-nix#123 (`extraConfig` and `extraConfigFiles` aliased), frappe-nix#177 (`sites.<fqdn>.settings` freeform JSON; `db_name` and `db_type` warn when they differ from `database.name` through 1.x, and become module-owned in 2.0.0); odoo-nix#57 and #58 (`odooConf.*` aliased to `settings`, and its port keys to `ports.*`), odoo-nix#91 (`settings` freeform INI by section; the flat `settings.<key>` form is an alias for `settings.options.<key>`, with a warning, until 2.0.0).
-   **Status:** decided.
-   **Check:** not yet in the base. A check that each product's `settings` option has a `pkgs.formats` freeform type would be cheap ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Identity options

-   **Convention:** the project identity is `name` in both (`frappe-nix.name`, `odoo-nix.name`). It is a slug: it seeds the port hash and names container images, systemd scopes and log messages. Each devenv module sets devenv's own top-level `name` with `mkDefault cfg.name`. The default tenant keeps the framework's word: frappe-nix's `defaultSite` (Frappe's `default_site`), and odoo-nix's `dbName` (Odoo's `db_name`). The scaffolder's `--name` sets the identity in both, and frappe-nix's `--default-site` the default site. Production keeps nixpkgs' shapes: frappe-nix's `sites.<fqdn>` (as `services.wordpress.sites`), odoo-nix's `domain` (as `services.odoo.domain`). In frappe-nix, the word site means only what Frappe means by it: a tenant of the bench.
-   **Adopted:** frappe-nix#123 (`benchName` and `siteName` aliased) and odoo-nix#57 (`projectName` aliased), until 2.0.0.
-   **Status:** decided.
-   **Check:** not yet in the base. Each product's rename tests assert the aliases (frappe-nix's `tests/renames.nix`).

## Logging

### Journald enablement

-   **Convention:** journald formatting is always on when stderr is the journal, loaded through a `.pth` graft built with `lib.journald.mkGraft { pkgs, name, src }` (`<p>_journald`), which puts the post-import hook (`_hook.py`) and the formatter core (`_journald.py`) beside the product's tree. The product's `install()` does nothing unless `_journald.journal_stream()`: `JOURNAL_STREAM` names stderr's own device and inode, which an inherited variable on a redirected stderr does not. It ships to production as well as development, inert wherever stderr is not the journal. A command line that logs to the journal itself vendors `lib.journald.module`.
-   **Adopted:** frappe-nix#151 and odoo-nix#73, which retired its sitecustomize shim; `services.odoo-nix.logging.journald` is deprecated and does nothing.
-   **Status:** decided.
-   **Check:** the base's `lib-journald` grafts a stub product and points `JOURNAL_STREAM` at a file, on every preset interpreter.

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
-   **Adopted:** frappe-nix#151; odoo-nix#73 (`init`, `migrate`, `web` and `db`).
-   **Status:** decided.
-   **Check:** the base's `lib-nixos` evaluates the fields in a stub product, and frappe-nix's `tests/logging-fields.nix` and odoo-nix's `tests/module-eval.nix` assert them against `lib.journald.roles`.

### Journald line format

-   **Convention:** `<N>{logger} [{site or db}] msg`, from `JournaldFormatter(logger=None, tenant=None)` in the core (python/journald.py). `<N>` is the record's syslog priority by threshold (critical 2, error 3, warning 4, info 6, below 7), on every line of the record, tracebacks included; ANSI escapes are stripped; there is no timestamp, pid or level name, which journald records itself. The product says who the tenant is (`tenant(record)`: Frappe's `frappe.local.site`, Odoo's `record.dbname`), may fix the logger name (Frappe prints the module, not `<module>-<site>`), and may append what its framework puts after a message by overriding `message()` (Odoo's `perf_info`). Its patching into the framework's logging stays its own.
-   **Adopted:** frappe-nix#151 and odoo-nix#73 (`OdooFormatter`, on the core's `JournaldFormatter`).
-   **Status:** decided.
-   **Check:** the base's `lib-journald`, on every preset interpreter.

## Containers

### Images

-   **Convention:**
    -   images are `[<registry>/]<name>/<role>`, and flake outputs are `container-<role>`;
    -   every image carries OCI labels: `org.opencontainers.image.title`, `source`, `version`, `licenses`, and `revision` where the product knows it;
    -   every image that serves HTTP has a healthcheck;
    -   with `containers.nonRoot`, an image runs as the product's user, uid and gid 1000, with its own `/etc/passwd` and `/etc/group`. `containers.nonRoot` is off through 1.x, because a volume a root image wrote needs a one-time `chown` first, and on from 2.0.0;
    -   an nginx image keeps port 80. Run as non-root under rootless Podman, it needs `--sysctl net.ipv4.ip_unprivileged_port_start=0`.
-   **Adopted:**
    -   frappe-nix#157: `[<registry>/]<name>/<role>` and `container-<role>`, with the old output names aliased until 2.0.0 (`frappe-nix.containers.aliases`). The runtime, web, socketio and nginx images have a healthcheck; scheduler, the workers and bench do not. With `containers.nonRoot` the images run as `frappe`.
    -   odoo-nix#81: the output is `container-odoo`. The image name stays `[<registry>/]<name>-odoo` by default through 1.x, with a warning, and becomes `<name>/odoo` with `containers.roleImageNames`, the default from 2.0.0. With `containers.nonRoot` it runs as `odoo`, home `/var/lib/odoo`. `containers.labels` overrides the labels.
-   **Status:** decided for the outputs, the labels and the healthchecks; deferred to 2.0.0 for `containers.nonRoot` and odoo-nix's role image name.
-   **Check:** not yet in the base. Each product evaluates its image configs (odoo-nix's `container-user`). A shared `lib.containers` would put these pieces in one place ([#20](https://github.com/Avunu/python-devops-nix/issues/20)).

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
-   **Adopted:** both. odoo-nix's switch from nixfmt was one formatting commit, listed in `.git-blame-ignore-revs` with odoo-nix#76's prek sweep.
-   **Status:** decided.
-   **Check:** the nix-lint check (`lib-nix-lint` in the base).

### CI

-   **Convention:** every action is pinned by full commit SHA, with the version in a trailing comment. A `lint` job runs prek over the whole tree. A `flake` job runs `nix flake check` and builds the checks; VM tests may run in a job of their own. The docs are checked by `Avunu/site-docs`'s `check-docs.yml` ([Docs pipeline](#docs-pipeline)). Dependabot pull requests auto-merge through this repository's reusable workflow, called pinned the same way. This repository has no release yet, so the pin is a commit of `main`, and Dependabot's github-actions entry moves it:

    ```yaml
    on: pull_request

    permissions: {}

    jobs:
      auto-merge:
        if: github.event.pull_request.user.login == 'dependabot[bot]'
        permissions:
          contents: write # gh pr merge --auto
          pull-requests: write # gh pr merge --auto
        uses: Avunu/python-devops-nix/.github/workflows/dependabot-auto-merge.yml@<sha> # main (no release yet)
    ```

    Its inputs carry the policy. `update-types` (default `major minor patch other`, where `other` is an update with no semver type, such as a flake input) lists what auto-merges; `exclude-ecosystems` (default `github_actions`) lists ecosystems a person reviews; `merge-method` (default `squash`) is passed to `gh pr merge`. frappe-nix and odoo-nix take the defaults. The workflow decides by ecosystem and update type, not by group, so a group a person reviews is the caller's `if`: odoo-nix skips `dependabot/nix/ocb-*`, because an OCB bump can need a relock. wordpress-nix takes the defaults too: it merges major bumps, and its gate is its required OCI build, not an input. A repository that wants majors reviewed passes `update-types: minor patch other`.

    **Auto-merge is only as safe as the checks marked required on the default branch**, so `flake` is a required check. Without it, auto-merge merges a bump before its checks fail.

    **Fixes are pushed, not only reported.** An `autofix` workflow runs `nix fmt` and the prek hooks over each pull request from a branch of the repository, through this repository's `pre-commit-autofix` action, and pushes what they rewrite as a `style:` commit, so a contributor without `prek install` gets a passing `lint` without a second round trip. `lint` stays the gate. A push made with `GITHUB_TOKEN` starts no workflow, so the action dispatches the workflows whose checks the default branch requires on the branch, as each product's release workflow does for the release pull request. That is also why it cannot loop. A fork's branch is not the token's to push to, so a fork's pull request gets the lint job's diff instead:

    ```yaml
    on: pull_request

    permissions: {}

    jobs:
      autofix:
        if: github.event.pull_request.head.repo.full_name == github.repository
        permissions:
          contents: write # the push
          actions: write # gh workflow run
          pull-requests: write # the comment
        steps:
          - uses: actions/checkout@<sha> # v7
            with:
              ref: ${{ github.event.pull_request.head.sha }}
              persist-credentials: true # zizmor: ignore[artipacked] the action pushes
          - uses: Avunu/python-devops-nix/.github/actions/pre-commit-autofix@<sha> # main (no release yet)
            with:
              fixers: nix fmt
              dispatch: |
                check.yml
                pr-policy.yml
    ```

    **Failures are reported on the pull request.** A job that builds Nix tees its log, and on failure the `nix-failures` action turns it into the failed derivations and the end of each one's log, uploaded as a `failures-<job>` artifact. A last `report` job runs the `check-report` action, which comments the jobs' results and those details on the pull request when a job failed, and edits that comment once they pass, so a green pull request gets no comment and a red one that went green does not keep a stale one. A test runner that measures coverage comments its coverage and failed tests the same way, through the `sticky-comment` action.
-   **Adopted:**
    -   frappe-nix: SHA pins and the `lint`, `flake` and `vm-tests` jobs; the reusable workflow since frappe-nix#159. Its `main` requires the docs check (`build / Check`) and `flake`; `flake` became required on 2026-10-22, after frappe-nix#163 merged with it failing.
    -   odoo-nix: SHA pins, the `lint` job (odoo-nix#76), and `flake`, which builds every check; the reusable workflow since odoo-nix#78. Its `main` requires `flake` and `pr-policy`, since odoo-nix#63 merged red.
    -   Both, and this repository: `autofix` and the `report` job. frappe-nix's `app-test` comments its `frappe-test` report (coverage, stage verdicts and failed tests) on the app's pull request. odoo-nix measures no coverage yet, so its report is the failures alone.
-   **Status:** partial: frappe-nix's required checks.
-   **Check:** not yet in the base for the `uses:` pins ([#19](https://github.com/Avunu/python-devops-nix/issues/19)). Required checks are repository settings, which no check in the tree can see.

### Repository files

-   **Convention:** the same set in both: `.editorconfig`, `.envrc`, `.pre-commit-config.yaml`, `ruff.toml`, `ty.toml` and `statix.toml`, and `version.txt` once releases are wired up. Each product keeps its own copy at its root, because the tools read them from there, and the shared settings in each match this repository's copy.

    The rule is the same for every shared file: **the product's copy makes every setting this repository's copy makes, with the same value, and may add its own.** Not byte-identical copies, because the products legitimately add to them: frappe-nix's `ruff.toml` extends Frappe's own ruff config, and both products' prek configurations add hooks and exclude their own paths. A list is one value and is compared whole. Per file, a setting is:

    | File | A setting is | Not compared |
    | --- | --- | --- |
    | `statix.toml`, `ruff.toml` | each key, by its path through the TOML tables | — |
    | `.editorconfig` | each key under a section (`[*.py] indent_style`), and the preamble's `root` | — |
    | `.pre-commit-config.yaml` | each hook, by repository URL and hook `id` (with its `name` where the base gives one), and each key the base sets on it (`args`) | `rev`, which each repository's Dependabot moves on its own schedule; the top-level `exclude` and other keys, which name the repository's own paths |

    Where a product has to set a shared setting differently, it lists the setting as an exception in its check and says why in a comment next to it: frappe-nix's Python is written in Frappe's style, so its `[*.py] indent_style` is `tab`. An exception for a setting this repository no longer makes fails the check, so exceptions do not outlive their reason.

    ruff's `extend` was the obvious alternative for `ruff.toml`, and it was rejected: it takes a path, and this repository's copy is in the Nix store, which an editor or a prek run outside the development shell does not have. The base's ruff settings are few (the `target-version` floor), so copying them in is cheaper than any mechanism that resolves a store path.
-   **Adopted:** frappe-nix has every file, with the `[*.py] indent_style` exception. odoo-nix has every file since odoo-nix#76 (`.envrc`, `ty.toml`), with no exceptions. Both have `version.txt`.
-   **Status:** decided.
-   **Check:** `lib.checks.nixLint` compares `statix.toml`, and `lib.checks.sharedConfig` the rest:

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

-   **Convention:** one shape: commit prefix `fix(deps)`, so a merged bump cuts a patch release through release-please; a seven-day cooldown (`cooldown.default-days: 7`); and one group per ecosystem (`patterns: ["*"]`), plus named groups only where inputs must move apart (odoo-nix's `ocb` and `odoo-ls`). A bump to development-only tooling that ships nothing keeps `chore(deps)`. Each product lists its own ecosystems. Pull requests auto-merge, except github-actions bumps, which a person reviews, through the reusable workflow in [CI](#ci).
-   **Adopted:**
    -   frappe-nix#159: nix, pre-commit and github-actions with `fix(deps)`, and uv in `/dev` (its own lint and type-check tools, the `dev-tools` group) with `chore(deps)`. Every ecosystem has the cooldown.
    -   odoo-nix#78: nix (with the `ocb` and `odoo-ls` groups), pre-commit and github-actions, all `fix(deps)`, all with the cooldown. It has no uv entry, because its template's lock cannot be resolved without a project.
    -   This repository, which has no releases, uses `chore`.
-   **Status:** decided.
-   **Check:** not yet in the base: a check over `.github/dependabot.yml` for the prefix and the cooldown ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Docs pipeline

-   **Convention:** `Avunu/site-docs`'s `check-docs.yml`, and frappe-nix's page structure: getting started, scaffolding, development, production, reference.
-   **Adopted:** frappe-nix always. odoo-nix#79 dropped `docs-site/` and `docs-publish.yml` and split its README into those pages.
-   **Status:** decided.
-   **Check:** not yet in the base; the workflow each product calls is visible in its `.github/workflows`.

### Tests

-   **Convention:** every check and VM test a product defines is a `checks.<system>.<x>` attribute, registered through one helper, `lib.checks.mk`. The naming scheme:

    -   `<x>` is kebab-case and names what is checked (`agecheck`, `db-nocow`, `migrate-rollback`), never the kind of check: no `eval-`, `module-` or `vm-` prefix, because the helper already records the kind.
    -   A check that runs once per framework series takes the series' major version as a suffix: `<x>-<major>` (`devguard-18`, `devguard-19`).
    -   The derivation is named `<product>-<x>-check` (`frappe-nix-agecheck-check`), so a build log names the product and the check. VM tests keep nixpkgs' prefix: `vm-test-run-<product>-<x>`.
    -   The test's own files sit beside the code (`tests/<x>.sh`, `tests/<x>.nix`, `tests/test_<x>.py`), named by the same `<x>`.

    `lib.checks.mk { pkgs, product }` returns:

    | Function | Use |
    | --- | --- |
    | `script <x> { src ? null, tools ? [ ], env ? { }, drvArgs ? { } } <text>` | A shell script, run with `tools` on PATH, in a writable copy of `src` if given, with a writable `HOME` and pipefail on. It passes when it exits 0. It may write a report to `$out`, a file or a directory. |
    | `eval <x> { assertion, message }` or `eval <x> { expected, actual, message ? "" }` | A Nix expression, judged at evaluation and reported at build, so a failure fails one named check, not the whole evaluation. The failure prints the message and, for `expected`/`actual`, a diff of their JSON. |
    | `nixosTest <x> <module>` | A NixOS VM test (`pkgs.testers.runNixOSTest`), named by the helper; the module sets no `name`. |
    | `checkName <x>` | The derivation name, `<product>-<x>-check`, for a check built some other way. |

    `lib.checks.nixLint` and `lib.checks.sharedConfig` are built with it and take the same `product`.
-   **Adopted:** odoo-nix#77 and frappe-nix#160 register every check through it (odoo-nix's `module-nginx` became `nginx`). Every frappe-nix derivation is `frappe-nix-<x>-check` or `vm-test-run-frappe-nix-<x>`, except `runtime`, which is the `frappe-runtime` package itself.
-   **Status:** decided.
-   **Check:** the base's `lib-mk` tests the helper. A pattern over check attribute names is not yet in the base ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).

### Commits

-   **Convention:** conventional commits, enforced by `committed` in a required `pr-policy` job, with one scope list for both products. A breaking change is a deliberate `Release-As: N.0.0` footer, never `!` or `BREAKING CHANGE`, which the policy refuses; `deprecate` is a commit type of its own.
-   **Adopted:** both enforce it with `committed.toml` and `pr-policy`. The scope lists differ: frappe-nix's has 61 scopes, the union of both repositories' history (frappe-nix#159), and odoo-nix's has 30.
-   **Status:** partial: one scope list, owned by this repository ([#21](https://github.com/Avunu/python-devops-nix/issues/21)).
-   **Check:** each product's `pr-policy` job. Not yet in the base, which has no `committed.toml` of its own.

## The development shell option tree

**Status:** _decided_ (adopted as proposed on 2026-10-17). **Adopted:** frappe-nix#124 and odoo-nix#58.

Both devenv modules had a `devguard` tree. odoo-nix also had a `dev` tree, which held one option, `dev.autoReload`. Since the whole module configures the development shell, `dev` named nothing, and the options related to it were spread elsewhere in both products (odoo-nix: `odooConf.devMode`, `odooConf.workers`, `testBrowser`, `ide`; frappe-nix: `runtime.dev`, `watch.*`, `assets.reassert.*`). `devguard` names a real subsystem and stays, but held the Mailpit catcher's settings, which are not a guard. Ports were spread over four trees in odoo-nix.

For both products:

| Tree | Holds | Moved in |
| --- | --- | --- |
| `watch` | What the shell does when a file changes. `watch.python` (bool, default true): restart the server on a Python change. frappe-nix keeps `watch.{apps,excludePublishers,rtl,nativeSass}` and has `watch.reassert`. | odoo-nix `dev.autoReload` and frappe-nix `runtime.dev` became `watch.python`; frappe-nix `assets.reassert` became `watch.reassert`. odoo-nix keeps its assertions that `watch.python` needs `workers = 0` and a reloading `devMode`. The `dev` tree is removed. |
| `devguard` | Guard policy only: what is refused or redirected. Shared guards `egress`, `mail`, `jobs`, `backups`, `objectstore` and `webhooks`, each with `enable` and the same shape. Product guards beside them (frappe-nix `integrations`, `google`, `plaid`; odoo-nix `iap`). | `scheduler` and `crons` became `jobs`. `devguard.mail` keeps `enable`, `sender` and `unmute`. |
| `mailpit` | The catcher as a service: `mailpit.{enable,host,port,httpPort}` and `mailpit.pop3.{enable,port,user,password}`, with ports defaulting from `ports`. On by default in both products, whether or not the mail guard is on. | `devguard.mail.{host,smtpPort/port,httpPort}` from both, and frappe-nix's `devguard.mail.pop3.*`. |
| `ports` | `ports.{base,offset,worktreeSalt}` and one derived attribute per service port (`ports.http`, `ports.smtp`, `ports.mailUi`, `ports.db`, …) that the rest of the module reads, all from `lib.ports.options`. Each can still be forced with `lib.mkForce`, which is why they are not `readOnly`: a read-only option refuses that override too. A product that leases its ports at run time (see [Ports](#ports)) adds `ports.strict` (refuse a taken preferred port rather than move past it; null for the product's default) and `ports.span` (how many ports past the preferred one to try) beside them; frappe-nix declares both itself, outside `lib.ports.options`, and odoo-nix takes the same names if it leases. | odoo-nix `odooConf.httpPort`, `odooConf.geventPort` and `odooConf.dbPort`, and the Mailpit ports. |

This keeps the decided name `port`: the SMTP port is `mailpit.port`, which defaults to `ports.smtp`, and the mail guard redirects to it. Mailpit starts before the server from the `mailpit` tree, so a server with the mail guard on never starts before its catcher. Every old option path is an alias until 2.0.0.

Left alone, since neither product is clearly better: frappe-nix's `appsReconcile`, `renamedApps` and `replacedApps`, and odoo-nix's `layout.*`.

Both products also have `ide.enable`, which links `./.venv` to the development environment for editors: frappe-nix's (frappe-nix#156, default true) also generates a `pyrightconfig.json`; odoo-nix's, the original, seeds `.vscode/settings.json` and runs odoo-ls under `ide.languageServer`.

**Check:** not yet in the base: the option paths exist in both devenv modules, and the `dev` tree does not ([#19](https://github.com/Avunu/python-devops-nix/issues/19)).
