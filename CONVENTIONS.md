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

Adopting a convention follows the renaming rule in [PLAN.md, Design rules](PLAN.md#design-rules): both products move to the name here, even when that is a breaking change. Every rename ships with a migration note in the product's changelog. Where an alias costs a few lines (`mkRenamedOptionModule`, or reading the old environment variable with a warning), the product keeps it until its next minor release.

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

-   **Convention:** `--at` accepts ISO 8601 in both, and each product's own backup naming as well.
-   **Today:** frappe-nix takes `YYYYMMDD_HHMMSS`. odoo-nix takes a `YYYY_MM_DD_HH_MM_SS` prefix.
-   **Status:** proposed.
-   **Check:** yes, a unit test of the parser.

## devguard

### SMTP port

-   **Convention:** the option is named `port`, matching both Python settings schemas. Where it lives is in [the development shell option tree](#the-development-shell-option-tree): `mailpit.port`.
-   **Today:** frappe-nix has `devguard.mail.smtpPort`. odoo-nix has `devguard.mail.port`.
-   **Status:** decided.
-   **Check:** yes, the option path exists.

### Job denylist

-   **Convention:** `devguard.jobs.{enable,extraBlocked}`, and `blocked_jobs` in the Python settings. Log lines keep the framework's words (scheduler, crons). frappe-nix's `blockServerScripts` moves to `devguard.jobs.blockServerScripts`.
-   **Today:** frappe-nix has `scheduler.extraBlockedJobs` and `blocked_jobs`. odoo-nix has `crons.extraBlocked` and `blocked_crons`.
-   **Status:** decided.
-   **Check:** yes, the option paths exist and the baked settings carry `blocked_jobs`.

### Shared guard names

-   **Convention:** the guards both products have keep their names, `egress`, `mail`, `mail_stdlib`, `objectstore`, `backups` and `webhooks`, with the same option shape under each (`enable` and the same settings).
-   **Today:** the names already match. The shapes under them differ.
-   **Status:** proposed.
-   **Check:** yes, compare the option trees under each guard.

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

-   **Convention:** shared: the `migrate.{enable,snapshot,rollbackOnFailure,snapshotRetention}` options, the snapshot flow, and verifying the snapshot before migrating (`pg_restore --list` in odoo-nix, a MariaDB equivalent in frappe-nix). Product-specific: what is left running after a failed migrate. frappe-nix keeps serving in Frappe's native maintenance mode. Odoo has no maintenance mode, and serving a half-migrated schema is not safe, so odoo-nix stops the service.
-   **Today:** frappe-nix runs migrate with `RemainAfterExit` and a marker file; services require only init, so a failed migrate leaves the site serving in maintenance mode. odoo-nix migrates on every start with `--if-needed`, keeps its marker in the database, and `odoo.service` requires the migrate unit, so a failed migrate stops serving. frappe-nix does not verify its snapshot yet.
-   **Status:** decided.
-   **Check:** yes for the options (evaluation); the flow is a VM test in each product.

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

-   **Convention:** journald formatting is always on when stderr is the journal, loaded through a `.pth` graft.
-   **Today:** frappe-nix does this. odoo-nix needs `ODOO_NIX_JOURNALD=1` and an option, through a sitecustomize shim.
-   **Status:** proposed.
-   **Check:** partly. A test points `JOURNAL_STREAM` at its own stderr and asserts the `<N>` prefix.

### Journald vocabulary

-   **Convention:** `SyslogIdentifier=<p>-<role>`, and one written list of roles.
-   **Today:** frappe-nix uses `frappe-<role>`, with `APP_SERVICE` one of runtime, web, worker, scheduler, socketio, init, migrate, db, redis and nginx. odoo-nix uses `odoo`, `odoo-init` and `odoo-migrate`, with web as the main unit.
-   **Status:** proposed.
-   **Check:** yes. frappe-nix's `tests/logging-fields.nix` and odoo-nix's `tests/module-eval.nix` assert the same field vocabulary.

### Journald line format

-   **Convention:** `<N>{logger} [{site or db}] msg`.
-   **Today:** frappe-nix writes `<N>{module} [{site}] msg`. odoo-nix writes `<N>{dbname} {logger}: msg`.
-   **Status:** proposed.
-   **Check:** yes, a formatter unit test.

## Containers

### Images

-   **Convention:** images are `<registry>/<name>/<role>`, flake outputs are `container-<role>`, and every image has OCI labels, a non-root `User` and a healthcheck.
-   **Today:** frappe-nix names images `${benchName}/<role>`, with outputs named by role. odoo-nix names its image `${projectName}-odoo`, output `container-odoo`.
-   **Status:** proposed.
-   **Check:** yes, evaluate the image configs.

## Repository

### Formatter

-   **Convention:** nixfmt-rs, through `nix fmt` and the nix-lint check (nixfmt, statix, deadnix), pinned by this repository's `lib.checks.nixLint`.
-   **Today:** frappe-nix uses nixfmt-rs. odoo-nix uses nixfmt. odoo-nix's switch is one formatting commit, listed in `.git-blame-ignore-revs`.
-   **Status:** decided.
-   **Check:** yes, the nix-lint check.

### CI

-   **Convention:** every action is pinned by full commit SHA, with the version in a trailing comment. A `lint` job runs prek over the whole tree.
-   **Today:** frappe-nix pins most actions by SHA and has the lint job. odoo-nix pins by tag and has no lint job.
-   **Status:** proposed.
-   **Check:** yes, a pattern over `uses:` lines.

### Repository files

-   **Convention:** the same set in both: `.editorconfig`, `.envrc`, `.pre-commit-config.yaml`, `ruff.toml`, `ty.toml` and `statix.toml`. `version.txt` only once releases are wired up. The shared parts match this repository's copies.
-   **Today:** frappe-nix has all of them. odoo-nix has `statix.toml` only.
-   **Status:** proposed.
-   **Check:** yes, the files exist, and the lint job compares them with this repository's copies.

### Dependabot

-   **Convention:** one shape: commit prefix `chore`, a seven-day cooldown, and one group per ecosystem (`patterns: ["*"]`), plus named groups only where inputs must move apart (odoo-nix's `ocb` and `odoo-ls`). Each product lists its own ecosystems. Pull requests auto-merge, except github-actions bumps, which a person reviews.
-   **Today:** frappe-nix covers nix, uv, pre-commit and github-actions, with the `chore` prefix and no cooldown. odoo-nix covers nix and github-actions, with the `chore` prefix, the `ocb` and `odoo-ls` groups, and no cooldown since it dropped docs-site.
-   **Status:** proposed.
-   **Check:** partly, a check over `.github/dependabot.yml` for prefix and cooldown.

### Docs pipeline

-   **Convention:** `Avunu/site-docs`'s `check-docs.yml`, and frappe-nix's page structure: getting started, scaffolding, development, production, reference. odoo-nix drops `docs-site/` and `docs-publish.yml` and splits its README into those pages.
-   **Today:** frappe-nix uses `check-docs.yml` and a 50-page `docs/` tree. odoo-nix uses `Avunu/docusystem`'s `docs-build.yml` and `docs-publish.yml`, with a 700-line README duplicated into `docs/README.md`.
-   **Status:** decided.
-   **Check:** yes, the workflow is the one called.

### Tests

-   **Convention:** one naming scheme for checks and VM tests, and one helper, `lib.checks.mk`, for registering them.
-   **Today:** frappe-nix drives `tests/<x>.sh` from `lib/<x>.nix`, with derivations named `frappe-nix-<x>-check`. odoo-nix has `test_*.py`, `module-*.nix` VM tests and `-<major>` suffixes.
-   **Status:** proposed. The naming scheme is written down with `lib.checks.mk` (PLAN.md, Phase 7).
-   **Check:** yes, a pattern over check attribute names.

### Commits

-   **Convention:** conventional commits, enforced by a commit-message check, with one scope list for both.
-   **Today:** both mostly use conventional commits: about 16% of frappe-nix's commits do not, and about 21% of odoo-nix's.
-   **Status:** proposed.
-   **Check:** yes, a commit-message hook and CI job.

## The development shell option tree

**Status: proposed, awaiting review.**

Both devenv modules have a `devguard` tree. odoo-nix also has a `dev` tree, which holds one option, `dev.autoReload`. Since the whole module configures the development shell, `dev` names nothing, and the options related to it are spread elsewhere in both products (odoo-nix: `odooConf.devMode`, `odooConf.workers`, `testBrowser`, `ide`; frappe-nix: `runtime.dev`, `watch.*`, `assets.reassert.*`). `devguard` names a real subsystem and stays, but holds the Mailpit catcher's settings, which are not a guard. Ports are spread over four trees in odoo-nix.

The proposal, for both products:

| Tree | Holds | Moves in |
| --- | --- | --- |
| `watch` | What the shell does when a file changes. `watch.python` (bool, default true): restart the server on a Python change. frappe-nix keeps `watch.{apps,excludePublishers,rtl,nativeSass}` and gains `watch.reassert`. | odoo-nix `dev.autoReload` and frappe-nix `runtime.dev` become `watch.python`; frappe-nix `assets.reassert` becomes `watch.reassert`. odoo-nix keeps its assertions that `watch.python` needs `workers = 0` and a reloading `devMode`. The `dev` tree is removed. |
| `devguard` | Guard policy only: what is refused or redirected. Shared guards `egress`, `mail`, `jobs`, `backups`, `objectstore` and `webhooks`, each with `enable` and the same shape. Product guards beside them (frappe-nix `integrations`, `google`, `plaid`; odoo-nix `iap`). | `scheduler` and `crons` become `jobs`. `devguard.mail` keeps `enable`, `sender` and `unmute`. |
| `mailpit` | The catcher as a service: `mailpit.{enable,host,port,httpPort}` and `mailpit.pop3.{enable,port,user,password}`, with ports defaulting from `ports`. On by default in both products, whether or not the mail guard is on. | `devguard.mail.{host,smtpPort/port,httpPort}` from both, and frappe-nix's `devguard.mail.pop3.*`. |
| `ports` | `ports.{base,offset,worktreeSalt}` as frappe-nix has them (from `lib.ports`), and one read-only attribute per service port (`ports.http`, `ports.smtp`, `ports.mailUi`, `ports.db`, …) that the rest of the module reads. Each can still be forced with `lib.mkForce`. | odoo-nix `odooConf.httpPort`, `odooConf.geventPort` and `odooConf.dbPort`, and the Mailpit ports. |

This keeps the decided name `port`: the SMTP port is `mailpit.port`, which defaults to `ports.smtp`, and the mail guard redirects to it.

Today:

-   frappe-nix runs Mailpit only when the mail guard is on, and derives every port from `ports.{base,offset,worktreeSalt}`.
-   odoo-nix always runs Mailpit (the guard only orders startup after it), and sets fixed ports in `odooConf` and `devguard.mail`.

To check during the move:

-   frappe-runtime's `--dev` turns on reload and asset serving together. If `watch.python = false` should still serve `/assets` and `/files`, frappe-runtime needs a separate flag; otherwise `watch.python` documents that it turns off both.
-   Mailpit-before-server ordering moves from the guard to the `mailpit` tree, so a server with the mail guard on never starts before its catcher.

Left alone for now, since neither product is clearly better: frappe-nix's `appsReconcile`, `renamedApps` and `replacedApps`, and odoo-nix's `layout.*`.

**Check:** yes, once adopted: the option paths exist in both devenv modules, and the `dev` tree does not.
