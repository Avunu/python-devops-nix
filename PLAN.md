# python-dev-nix: plan

Status: proposed, 2026-10-16. Nothing is extracted yet. This repository was planned as nix-dev-base; it is renamed python-dev-nix because its scope is now Python application products only.

## Why this repository exists

[frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix) package two Python ERP frameworks the same way: a flake-parts flake with a devenv development shell, a uv2nix Python environment, a NixOS module, container images and a scaffolder. They have the same users, the same production model and the same failure modes. Pieces of one have been copied into the other, and the copies are now maintained twice.

**The same change keeps landing twice.** On 14 and 15 October 2026 alone, odoo-nix merged five explicit ports from frappe-nix: partial-clone fixes for submodules (#39), the root wrapper (#40), the btrfs copy-on-write fix for the database datadir (#41), the nix-lint check (#43) and paired-branch clones (#45, mirrored back as frappe-nix#81).

**Copies drift, and the drift is where bugs live.** odoo-nix's egress guard never matched a hostname on its allowlist, because clients resolve a name and connect to the address. frappe-nix's port of that guard, written a week later, fixed it ([frappe-nix#82](https://github.com/Avunu/frappe-nix/pull/82)), and the fix then had to be carried back ([odoo-nix#46](https://github.com/Avunu/odoo-nix/pull/46)). The same pattern shows up without a bug report: odoo-nix's `python-ldap` override has the `ldap_r` fix and frappe-nix's does not; frappe-nix's migrate snapshots are private and odoo-nix's are not.

**The products also differ where they should not.** The same job has different names, flags and defaults in each: `bench restore` overwrites the existing database by default and `odoo db restore` refuses; the devguard SMTP port is `mail.smtpPort` in one and `mail.port` in the other; the unguarded-restore override is `FRAPPE_RESTORE_ALLOW_UNGUARDED` in one and `ODOO_NIX_RESTORE_ALLOW_UNGUARDED` in the other. Someone who works on both products has to learn both.

**The duplicated pieces are the ones it is costliest to get wrong.** They check that secrets are encrypted to the right people, hold production backup credentials, write database dumps, and stop a development copy of production from reaching production. When those drift, the result is not a failing build but a leak, or a guard that quietly stopped guarding.

### Goal

frappe-nix and odoo-nix should behave as one product family. Wherever the two do the same job, they do it the same way, under the same names, and from one tested copy of the code when the code is product-independent.

That is three kinds of work, run as tracks:

-   **Track A, shared code.** One copy of each product-independent piece, in this repository.
-   **Track B, shared conventions.** One name, flag, default and layout for each job both products do, written down in this repository's `CONVENTIONS.md` and checked where a check is cheap. This needs no shared code.
-   **Track C, parity.** A feature one product has and the other lacks is ported, unless it is genuinely product-specific.

Before any of them, [Phase 0](#phase-0-fix-now) fixes problems the comparison found that should not wait for the base.

### Non-goals

-   **A framework the products are built on.** The development shells, NixOS modules, containers and Python environments stay in the products. Their services differ (MariaDB and Redis against PostgreSQL), and so do their deployment models (frappe-nix serves many sites per bench, odoo-nix one instance). Sharing the modules themselves would mean an option for every difference. What is shared is the pieces inside them and the conventions they follow.
-   **wordpress-nix.** It is PHP on FrankenPHP and shares almost none of this. It has no committed secrets and no agenix-shell input. devguard is a Python `.pth` graft and cannot load into PHP. It has no lint check, and its scaffolder and modules score 0.04 to 0.28 against the other two. None of its 208 commits is a file-level port to or from either. It may take leaf pieces later (see [wordpress-nix](#wordpress-nix)), but it does not shape anything here.

## What is shared today

The first comparison paired files by name. That missed pieces that do the same job under different names, and it mispaired files that share a name but do different jobs (frappe-nix's `dev/env.nix` builds the development Python environment; odoo-nix's `lib/env.nix` caps BLAS threads). The comparison was redone by job. Method in the [appendix](#appendix-how-the-overlap-was-measured).

| Kind | Pieces |
| --- | --- |
| Copies (0.95 and up) | lib/agecheck.py, lib/secrets-tools.nix, tests/secrets-cli.sh, lib/sh/backup-access.sh, devguard _hook.py, statix.toml, the nginx journal_json access-log format (byte-identical), the alice/bob fixture keys |
| Same design, drifted | lib/secrets-schema.nix, modules/secrets.nix, devguard _settings.py, guards/mail_stdlib.py, guards/egress.py, lib/db-nocow.nix, tests/secrets-cli.nix, dependabot-auto-merge.yml, submodule clone and update (frappe-nix lib/apps-report.nix, odoo-nix lib/submodules-init.nix), the backup fetch and list layer, the journald formatter core, lib/overrides.nix, lib/rich-python.nix, the migrate.* option set and snapshot flow, the unix-socket directory assertion, the Cloudflare real-IP block |
| On one side only, but product-neutral | frappe-nix lib/ports.nix, lib/ci-mode.nix, lib/lock-audit.nix; odoo-nix lib/uv_build_deps.py, the systemd-run admin wrapper |
| Separate products | the devenv, NixOS and container modules as wholes, the Python workspace shapes, the guards that patch application APIs, the command-line tools, the documentation |

The post-import hook exists six times: devguard, unixsock, nodebuild, benchcli and journald in frappe-nix, and the journald shim's `_NetsvcImportHook` in odoo-nix, plus odoo-nix's devguard copy.

## Design rules

These hold for everything in this repository.

1.  **No product names in code.** The base never says Frappe or Odoo. Each product passes in its option paths, environment prefix, package names, log prefixes and command names.
2.  **Uniform names over unchanged names.** Where the products name the same thing differently, both move to the name in `CONVENTIONS.md`, even when that is a breaking change. Only a handful of deployments exist today, so renames are cheap now and expensive later. Every rename ships with a migration note in the product's changelog. An alias (`mkRenamedOptionModule`, or reading the old environment variable with a warning) is added when it costs a few lines, and removed at the product's next minor release.
3.  **A small lock.** The base's own inputs are nixpkgs and flake-parts, and consumers `follows` both, so adopting it adds one node to a product's lock and none to a second nixpkgs. Heavier dependencies are taken as arguments from the product rather than as inputs. For example, the secrets module takes the product's `agenix-shell` input, and ragenix comes from nixpkgs. The uv2nix inputs (pyproject-nix, uv2nix, pyproject-build-systems) are the exception if Track A's Python helpers need them; they follow the product's.
4.  **Runtime Python runs on 3.11, and is stdlib-only unless the product's environment already carries the dependency.** The oldest interpreter either product supports is 3.11 (Odoo 18). Code that loads inside the application (devguard, journald) is stdlib-only. Development-shell tools may use rich, which both products already ship in their tool environment.
5.  **Python is vendored at build time, not installed as a dependency.** Shared Python is copied into the product's package by Nix (for example, the devguard core into `frappe_devguard/`). Each virtualenv still contains exactly one guard package, under the product's name, with no new distribution to resolve and no version skew inside a process.
6.  **No new reach into production.** Nothing here may reach a production virtualenv, NixOS service or container that does not already reach it today, except where a phase says so explicitly (the journald core, the overrides, the hardening baseline). devguard stays development-only by construction: frappe-nix grafts it into the development environment only, and odoo-nix's `odoo-init-*` checks assert that it is absent from the production one. frappe-nix gains an equivalent check in Phase 3.
7.  **Tests live with the code, and CI runs all of them.** The base tests what it ships, against a stub product. Product suites keep their product-specific tests and drop the duplicated ones. Every check a product defines is built in CI on every pull request; a check that is not built does not count as coverage.

## Phase 0: fix now

These came out of the comparison. None depends on the base, and each is a pull request to one product.

| Fix | Product | Detail |
| --- | --- | --- |
| Make migrate and backup dumps private | odoo-nix | Only the init script sets umask 077 (modules/nixos.nix:203). odoo db writes pre-migrate snapshots and backups with the service's default umask (lib/odoo_nix_cli/commands/db.py, around 563–586). Under nginx.xSendfile or socketPath, nginx joins the odoo group and can likely read full database dumps. Port frappe-nix#83: UMask=0077 on the unit, a 0700 snapshot directory, 0600 files, and a VM test like frappe-nix's tests/migrate-rollback.nix. |
| Build every check in CI | frappe-nix | check.yml builds a hand-picked list, so journald, agecheck, secrets-cli, logging-fields, unixsock and db-nocow are never built, and VM tests run only on manual dispatch. Build checks.x86_64-linux as a whole, as odoo-nix does, and run at least socket-runtime (the default topology) on every pull request. This has to land before Phase 1, whose safety net is these checks. |
| Run the root admin CLI as the service user | frappe-nix | The root bench wrapper runs as the caller, so files it writes are owned by root. Port odoo-nix's adminCli, which uses systemd-run with the service's user and environment. |
| Clone apps as partial clones | frappe-nix | frappe-init clones with --depth 1 (lib/sh/init.sh:27), contradicting the partial-clone contract shell entry follows. Use --filter=blob:none --single-branch, as odoo-init does. |
| Make containers.registry do something | both | odoo-nix reads cfg.registry while the option is cfg.containers.registry (modules/containers.nix:102), so it is always empty. frappe-nix declares the option and never reads it. |
| Drop unused inputs | frappe-nix | nix-oci is unused. Its lock has 51 nodes against odoo-nix's 32. |
| Remove stale documentation | odoo-nix | addons/dev_mailcatch is a no-op stub that the README still describes as live. The template's comment names src/odoo and src/external/*, which are odoo/ and modules/. |

## Phase 1: the repository

-   Rename the GitHub repository from Avunu/nix-dev-base to Avunu/python-dev-nix before anything consumes it.
-   `flake.nix` on flake-parts, exposing `flakeModules.*`, `lib.*` and `checks.*`.
-   `nix fmt` and the nix-lint check from Phase 7, run on this repository itself.
-   CI: a `check` workflow, plus a **downstream** job that builds all of frappe-nix's and odoo-nix's checks against the pull request's head with `--override-input python-dev-nix`. This is the main defence against one bad release breaking both products at once.
-   Dependabot for the nix and github-actions ecosystems, and the auto-merge workflow from Phase 7.
-   The MIT license, as in both products.
-   `CONVENTIONS.md`, started with the decisions in [Track B](#track-b-conventions).

## Track A: shared code

Phases 2 to 7, roughly in order of how expensive each is to get wrong. Phase 7 has no dependencies and can land at any point.

### Phase 2: the secrets toolkit

The secrets toolkit consists of:

-   age ciphertext committed to the repository;
-   agenix-shell decryption into the development shell;
-   generated agenix rules;
-   the recipient-drift check;
-   `edit-secret`, `rekey-secrets` and `check-secrets`;
-   `setup-backup-access`;
-   the backup fetch, list and cache layer behind `bench restore` and `odoo db restore`.

It goes first because it is nearly identical in both products, so the extraction changes little and can be verified by comparing outputs.

| Piece | frappe-nix | odoo-nix | Difference |
| --- | --- | --- | --- |
| Recipient-drift check | lib/agecheck.py | lib/agecheck.py | The product name in three messages. |
| Rules, on-demand decryption, setup tool | lib/secrets-tools.nix | lib/secrets-tools.nix | Comments only. |
| Backup-access prompt | lib/sh/backup-access.sh | lib/sh/backup-access.sh | The restore command it names, the example bucket, and how it confirms the credentials work: frappe-nix counts backups and names the newest, odoo-nix counts database folders. |
| Backup fetch and list | lib/backup-fetch.nix, lib/sh/backup-fetch.sh | lib/odoo_nix_cli/remote.py | The same contract: decrypt backup-access per command, mc over S3, the same BACKUPS_URL, ACCESS_KEY, SECRET_KEY, BUCKET and PREFIX variables, a local cache. Only the parser for each product's backup layout differs. |
| Secret scripts | inside lib/scripts.nix | lib/secret-scripts.nix | Packaging. |
| Secret set schema | lib/secrets-schema.nix | lib/secrets-schema.nix | frappe-nix has bench-level roles and per-site roles (encryption key, database password, site config). odoo-nix has project-level roles only. The variable prefix differs (frappe_, odoo_). |
| flake-parts module | modules/secrets.nix | modules/secrets.nix | The same option tree (enable, dir, relDir, recipients, hostRecipients, identityPaths, backupAccess, extra, declared) under frappe-nix.secrets and odoo-nix.secrets. frappe-nix adds sites. Outputs flake.lib.frappeSecrets and flake.lib.odooSecrets. |
| Tests | tests/secrets-cli.{sh,nix}, tests/agecheck.sh, tests/backup-fetch.sh | tests/secrets-cli.{sh,nix}, eval-secrets-schema-* checks, tests/test_remote.py | Fixture keys and product names. The alice/bob keys are the same in both. |

#### Shape in the base

-   `lib.secrets.schema { lib, varPrefix, projectRoles, groups ? { } }` is the pure schema. `groups` generalises frappe-nix's sites: a named set of units, each with its own roles.
-   `lib.secrets.mkModule { optionPath, label, varPrefix, groups, agenix-shell }` returns the flake-parts module that declares the options at the product's path and wires agenix-shell. The product's own `modules/secrets.nix` shrinks to this call plus frappe-nix's `sites` option feeding `groups`.
-   `lib.secrets.tools { pkgs, label, restoreCommand, bucketExample, verifyListing }` provides agecheck, the rules file, the scripts and `setup-backup-access`. `verifyListing` is the product's snippet for confirming the credentials reach its backups.
-   `lib.backups.fetch { pkgs, label, envPrefix, layout }` provides fetch, list and cache. `layout` is the product's parser from an object listing to backup sets. The source overrides are `<P>_NIX_BACKUP_SOURCE` and `<P>_NIX_MC` (see [Track B](#track-b-conventions)).
-   One output name in both products: `flake.lib.secrets`. The old `frappeSecrets` and `odooSecrets` names stay as aliases for one release.

#### Done when

-   The generated rules file and the agenix-shell secret set evaluate identically before and after, in both products and in one real consumer of each (a frappe-nix bench and an odoo-nix project). Compare the evaluated values as JSON.
-   Both products' secrets and backup checks pass in CI, and `check-secrets` and a restore pass in the real consumers.
-   The product copies are deleted.

### Phase 3: the devguard core

devguard keeps a development copy of production from reaching production. It is a Python package grafted into the development virtualenv with a `.pth` file, so every interpreter started from that environment loads it below the application.

The core is the part that knows nothing about the application:

-   the post-import hook;
-   settings resolution (environment, then an optional runtime file, then baked values, then defaults);
-   the patch helpers;
-   the two transport-level guards: `egress`, which refuses non-loopback `connect()` with an allowlist, and `mail_stdlib`, which points `smtplib` at the mail catcher and refuses IMAP and POP3.

The guards that patch application APIs stay in the products, but their settings follow one schema (see [Track B](#track-b-conventions)).

| Piece | Similarity | Difference |
| --- | --- | --- |
| _hook.py | 0.99 | Tabs against spaces. Five more copies in other grafts (four in frappe-nix, one in odoo-nix's journald shim). |
| _settings.py | 0.65 | frappe-nix adds the runtime-file layer ($DEVENV_RUNTIME/devguard-runtime.json) and bakes a Python literal (_baked.py). odoo-nix bakes _baked.json. Each has its own environment prefix and defaults. |
| _patch.py | 0.36 | Shared subset: warn, announce, block, mark, is_guarded, no_op, blocking, require, require_any. frappe-nix adds Frappe helpers (throw, throwing, rewhitelist, disarm_subclasses, redirect_kwargs, assert_not_overridden, with_in_install), and its warn also writes to Frappe's logger. odoo-nix adds user_error. The ACTIVE line uses -- in one and — in the other. |
| guards/mail_stdlib.py | 0.84 | Comments and type-checker annotations. |
| guards/egress.py | 0.54 | After frappe-nix#82 and odoo-nix#46 both match a hostname against the name a connection was resolved from. frappe-nix's also has passage() (used by its object-store guard), shares its allowlist with the integrations guard, unwraps IPv4-mapped loopback and treats *.localhost as local. |

#### Shape in the base

-   `python/devguard/` contains `_hook.py`, `_settings.py`, `_patch.py`, `guards/egress.py` and `guards/mail_stdlib.py`, all with relative imports so they work under any package name. Style is Ruff's defaults (four spaces); the product does not lint vendored files.
-   The product supplies `_product.py`, which holds:
    -   its package name and environment prefix;
    -   its default settings;
    -   two hooks: an extra log sink (Frappe's logger) and a desk-facing error (`frappe.throw`, Odoo's `UserError`);
    -   optionally, extra allowlist entries for egress (frappe-nix's `integrations.allowHosts`).
-   Both products use the runtime-file layer, so a running shell can change a setting without rebuilding.
-   The product keeps its own guards and its own helpers, which move out of `_patch.py` into a product module such as `_frappe.py`.
-   `lib.devguard.mkPackage { pkgs, name, src, settings }` merges the base tree and the product's tree, failing on any file present in both, and writes `_baked.json`. frappe-nix moves from `_baked.py` to JSON, which removes its `toPy` helper.
-   `lib.python.withGrafts { python, env, grafts }` is the `.pth` graft, generalised from frappe-nix's `graftFor` and `withGrafts`. odoo-nix's `withDevguard` becomes one call. Every graft in both products, including odoo-nix's journald shim, takes `_hook.py` from here.
-   The base's tests cover the hook, settings precedence, the egress matrix and passage, and the mail transport against stub SMTP and POP3 servers, all with a stub product. They move here from both products' suites.
-   The base takes frappe-nix's egress as the starting point, since it is a superset. odoo-nix thereby gains `passage()`, IPv4-mapped loopback and `*.localhost`. All three allow traffic to the local machine only, and none loosens a refusal to a remote host.

#### Done when

-   frappe-nix's `checks.devguard` and odoo-nix's `checks.devguard`, `checks.devguard-baked` and fixture tests (`odoo-test-*`) pass.
-   Log lines (`<package>[egress] …`) and `.pth` file names are unchanged. Option paths and environment variables follow `CONVENTIONS.md`.
-   devguard is still absent from every production environment: by odoo-nix's `odoo-init-*` checks, and by a new frappe-nix check that `prodPythonEnv` carries no `frappe_devguard`.
-   The product copies of the core are deleted, including every other copy of `_hook.py`.

### Phase 4: the Python environment

Both products build their environment the same way: pyproject-nix, uv2nix and pyproject-build-systems, `sourcePreference = "wheel"`, `mkEditablePyprojectOverlay`, and a `.pth` graft. What has drifted is the package fixes.

-   **`lib.python.overrides`**: one override set for the packages both stacks build. The base takes the better fix of each:
    
    -   `python-ldap` with odoo-nix's `ldap_r` fix;
    -   lxml, psycopg2, pycups, pyvips and cairocffi;
    -   odoo-nix's python-magic `dlopen` patch.
    
    Each product layers its own overrides on top.
    
-   **`lib.python.rich`**: one `rich-python.nix`, taking odoo-nix's, which is the superset (the markdown-it-py fix).
    
-   **`lib.python.lockAudit`**: frappe-nix's `lib/lock-audit.nix`, which turns uv2nix's "attribute missing" into a sentence naming the stale lock. odoo-nix adopts it, since an OCA submodule bump hits the same failure.
    
-   **`lib.python.buildDeps`**: odoo-nix's `uv_build_deps.py`, which derives `extra-build-dependencies` from `uv.lock`. frappe-nix adopts it in place of the fixed list its template copies.
    
-   **Thread caps.** odoo-nix's BLAS thread caps (`lib/env.nix`) move into the base and frappe-nix sets them too.
    

Done when both products build their development and production environments from these, the derivation hashes of the production environments are unchanged except where a fix is new to that product, and each product's VM tests pass.

### Phase 5: the development shell pieces

These are leaf functions inside the devenv modules. The modules themselves stay in the products.

-   **`lib.ports`**: frappe-nix's `lib/ports.nix`, which gives each project its own block of ports from a hash of its name. It is product-neutral except for the variable name and the base ports. odoo-nix adopts it, which ends its fixed 8069, 8072, 5432, 1025 and 8025, so two projects on one machine no longer collide.
-   **`lib.ciMode`**: frappe-nix's `lib/ci-mode.nix` (the `<P>_NIX_CI` flag, `PC_TUI_ENABLED=0`, a one-line banner). odoo-nix gains a CI mode.
-   **`lib.sources`**: submodule clone and update: paired branch, partial clone, no pack pile-up. It has been ported in both directions twice, and the messages are already near-verbatim (frappe-nix `lib/apps-report.nix`, odoo-nix `lib/submodules-init.nix`). It covers both the scaffolder's first clone and shell entry.
-   **`lib.dbNocow`**: the btrfs NOCOW marking (`lib/db-nocow.nix`, 0.66). The product says which directories to mark.
-   **`lib.banner`**: one rich renderer taking JSON (title, commands, URLs, guard state) from Nix. It replaces odoo-nix's nine positional arguments and frappe-nix's flags.

Done when odoo-nix's shell starts on per-project ports, both shells start in CI mode in CI, and the product copies are deleted.

### Phase 6: production pieces

These live in the NixOS modules. They are small, and they are exactly the kind of security posture that should not differ between products.

-   **`lib.nixos.hardening`**: a systemd sandboxing baseline that neither module sets today: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict` with explicit `ReadWritePaths`, `ProtectHome`, `ProtectKernel*`, `RestrictAddressFamilies`, `CapabilityBoundingSet=` and `UMask=0077` on units that write state. Each product applies it to every unit it defines and documents any exception in the unit.
-   **`lib.nixos.migrate`**: the `migrate.{enable,snapshot,rollbackOnFailure,snapshotRetention}` options, which already have the same names and defaults, plus the snapshot steps: private directory, dump, verify, keep N. The product supplies the dump, verify and restore commands (`mysqldump` or `pg_dump`).
-   **`lib.nixos.unixSocket`**: the assertion that a socket lives in its own 0750 or 0770 directory, and its tmpfiles rule.
-   **`lib.nixos.nginx`**: the `journal_json` access-log format as one constant, the Cloudflare real-IP block, and sending the error log to syslog so it keeps its severity (frappe-nix's `logError`).
-   **`lib.journald`**: the stdlib formatter core: the priority mapping (2, 3, 4, 6, 7 by threshold), the `JOURNAL_STREAM` check against stderr's device and inode, the `<N>` prefix on every line of a record, and ANSI stripping (from odoo-nix). Each product keeps its own line content and its own patching into the framework's logging.

Done when both modules use these, each product's VM tests pass, and frappe-nix's `tests/logging-fields.nix` and odoo-nix's `tests/module-eval.nix` assert the same field vocabulary.

### Phase 7: repository hygiene

-   **nix-lint.** Both products run the same check, nixfmt, statix and deadnix over the tree excluding fixtures and templates: frappe-nix in `dev/checks.nix`, odoo-nix in `flake.nix`. Ship it as `lib.checks.nixLint { pkgs, src, ignore }`, using `nixfmt-rs`. `statix.toml` stays in each repository, because tools read it from the root, and the check confirms it matches the base's copy.
-   **Check registration.** `lib.checks.mk`, the one helper Track B's test convention calls for, replacing frappe-nix's `runCommand` wrappers and odoo-nix's `mkCheck` and `mkEvalCheck`.
-   **Dependabot auto-merge.** The two workflows differ only in a comment. Ship a reusable workflow (`workflow_call`). Products call it pinned to a commit, with the version in a comment, and Dependabot's github-actions entry moves the pin.
-   **Shared repository config.** The base's copies of `.editorconfig`, `ruff.toml` and the prek configuration, which the products' lint job confirms they match, like `statix.toml`.

## Track B: conventions

`CONVENTIONS.md` in this repository records one decision for each item below. Each product then has a pull request that adopts it, under the renaming rule in [Design rules](#design-rules). Where a decision can be checked mechanically (option paths exist, environment variables are read, a CLI accepts a flag), the base ships a check that each product runs.

The last column is the decision. Rows marked _decided_ were settled on 2026-10-16; the rest are proposals that stand unless someone objects in review. The development shell's option tree has its own section, [below the table](#the-development-shell-option-tree).

| Area | frappe-nix today | odoo-nix today | Proposed |
| --- | --- | --- | --- |
| Tool-owned environment variables | FRAPPE_RESTORE_ALLOW_UNGUARDED, FRAPPE_BACKUP_SOURCE, FRAPPE_BACKUP_MC, devguard state baked into scripts | ODOO_NIX_RESTORE_ALLOW_UNGUARDED, ODOO_NIX_BACKUP_SOURCE, ODOO_NIX_MC, ODOO_NIX_DEVGUARD | <P>_NIX_* everywhere: FRAPPE_NIX_* and ODOO_NIX_*. Both export <P>_NIX_DEVGUARD. |
| Restore: overwrite | on by default, --no-force refuses | off by default, --force overwrites | Decided: on by default in both, --no-force refuses. frappe-nix keeps its behaviour; odoo-nix changes, and keeps --force as an accepted no-op for one release. |
| Restore: migrate after | --no-migrate, restore.migrate | none | Both, on by default. |
| Restore: neutralise | none | --neutralize, restore.neutralize | Both. frappe-nix pauses the scheduler and mutes mail in the restored database, as defence in depth below devguard. |
| Restore: files | --files, --private-files, restore.withFiles | --attachments, restore.attachments | --files, restore.files. frappe-nix keeps --private-files as its own extra. |
| Restore: unguarded | refuses only when it would carry production's encryption key; restore.requireDevguard | always refuses; no option | Always refuses without devguard; restore.requireDevguard in both. |
| Restore: --at | YYYYMMDD_HHMMSS | YYYY_MM_DD_HH_MM_SS prefix | Accept ISO 8601 in both, and each product's backup naming as well. |
| devguard: SMTP port | mail.smtpPort | mail.port | Decided: port, matching both Python schemas. Where the option lives is in the option tree. |
| devguard: job denylist | scheduler.extraBlockedJobs, blocked_jobs | crons.extraBlocked, blocked_crons | Decided: devguard.jobs.{enable,extraBlocked}, blocked_jobs in Python. Log lines keep the framework's words (scheduler, crons). frappe-nix's blockServerScripts moves to devguard.jobs.blockServerScripts. |
| devguard: shared guard names | egress, mail, mail_stdlib, objectstore, backups, webhooks | the same | Keep, with the same option shape under each. |
| Scaffolder: version flag | --frappe-version | --series | Decided: --version, with the old names as aliases. |
| Scaffolder: flags | --dry-run, --skip-lock, -y, --commit, migrate an existing tree | none of these | All of them in both. |
| Scaffolder: exit codes | documented 1 to 7 | always 1 | frappe-nix's table, in both. |
| Scaffolder: presets file | {label, branch, python, nodejs, requiresPython, overrideDependencies} | {label, series, python, requiresPython} | One schema: branch, with nodejs and overrideDependencies optional. |
| Scaffolder: seeds | secrets/README.md, warnings summary | none | Both. |
| Scaffolder: next steps | provision-site or bench restore | odoo db provision | Same wording, under each product's command. |
| systems | includes x86_64-darwin | dropped it (nixpkgs 26.11 did) | Drop it in both. |
| Command layout | bench umbrella, but provision-site is standalone | odoo umbrella | Everything under the umbrella: bench provision (keep provision-site as an alias). |
| Source management | bench get-app, bench remove-app | odoo module add, no remove | Add and remove in both; odoo-nix gains odoo module remove. |
| Relock | update-deps and nix run .#relock for consumers | inside project update; relock covers fixtures only | One consumer-facing relock in both. |
| Migrate on deploy | RemainAfterExit plus a marker file; services require init only, so a failed migrate leaves the site serving in maintenance mode | runs on every start with --if-needed, marker in the database, odoo.service requires it, so a failed migrate stops serving | Decided: serve in maintenance mode. Odoo has no maintenance mode of its own, and serving it against a half-migrated schema is not safe, so odoo-nix's version is nginx answering 503 with a maintenance page while odoo.service stays stopped; see Track C. Both verify the snapshot before migrating (odoo-nix's pg_restore --list, a MariaDB equivalent for frappe-nix). |
| Production secrets | LoadCredential, init as the service user | init runs as root and reads the files | LoadCredential in both, and environmentFiles (odoo-nix's) in both. |
| Database logging | none | slowQueryMs, log_line_prefix | Both: database.slowQueryMs. |
| Journald enablement | always on when stderr is the journal, via a .pth graft | needs ODOO_NIX_JOURNALD=1 and an option, via a sitecustomize shim | Always on when stderr is the journal, via a .pth graft. |
| Journald vocabulary | SyslogIdentifier=frappe-<role>; APP_SERVICE in runtime, web, worker, scheduler, socketio, init, migrate, db, redis, nginx | odoo, odoo-init, odoo-migrate; main unit web | <p>-<role> and one written list of roles. |
| Journald line format | <N>{module} [{site}] msg | <N>{dbname} {logger}: msg | <N>{logger} [{site or db}] msg. |
| Container images | ${benchName}/<role>, outputs named by role | ${projectName}-odoo, output container-odoo | <registry>/<siteName>/<role>, outputs container-<role>, with OCI labels, a non-root User and a healthcheck. |
| Identity options | benchName (project identity), siteName (default FRAPPE_SITE) | projectName, odooConf.dbName | Decided: siteName is the project identity in both: frappe-nix's benchName and odoo-nix's projectName become siteName. frappe-nix's current siteName is renamed defaultSite, and the scaffolder's --site flag becomes --default-site. benchName and projectName ship with aliases. The old siteName cannot, because the name is reused, so a frappe-nix consumer whose siteName holds a hostname fails evaluation with a message naming defaultSite. Frappe's own vocabulary still says site for a tenant of the bench (sites/, --site, the NixOS sites option), and defaultSite keeps that meaning intact. |
| Formatter | nixfmt-rs | nixfmt | Decided: nixfmt-rs in both, pinned in the base's lint check. odoo-nix's switch is one formatting commit, added to .git-blame-ignore-revs. |
| CI | actions pinned by SHA; a prek lint job | actions pinned by tag; no lint job | SHA pins with the version in a comment, and the lint job, in both. |
| Repository files | .editorconfig, .envrc, .pre-commit-config.yaml, ruff.toml, ty.toml | none of these | The same set in both. version.txt only once releases are wired up. |
| Dependabot | uv and pre-commit ecosystems, chore prefix, no cooldown | npm for docs-site, cooldown, ocb and odoo-ls groups | One shape: the same prefix, cooldown and grouping style; each product lists its own ecosystems. |
| Docs pipeline | Avunu/site-docs check-docs.yml; a 50-page docs/ tree | Avunu/docusystem docs-build.yml and docs-publish.yml; a 700-line README duplicated into docs/README.md | Decided: Avunu/site-docs. odoo-nix drops docs-site/ and docs-publish.yml, moves to check-docs.yml, and splits its README into frappe-nix's page structure (getting started, scaffolding, development, production, reference). |
| Tests | tests/<x>.sh driven by lib/<x>.nix, derivations frappe-nix-<x>-check | test_*.py, module-*.nix VM tests, -<major> suffixes | One naming scheme for checks and VM tests, and one helper (lib.checks.mk) for registering them. |
| Commits | conventional, about 16% not | conventional, about 21% not | Conventional commits enforced by a commit-message check, with the same scope list. |

### The development shell option tree

Both devenv modules have a `devguard` tree. odoo-nix also has a `dev` tree, which holds one option, `dev.autoReload`. They do different jobs, and the question is whether `dev` should exist at all.

**`dev` names nothing.** The whole module configures the development shell, so every option in it is a development option. In odoo-nix, `dev` holds one option, and the related options are spread elsewhere:

-   `odooConf.devMode`, the value of Odoo's `--dev` flag;
-   `odooConf.workers`, which has to be 0 for reload to work;
-   `testBrowser`;
-   `ide`.

frappe-nix has no `dev` tree. Its equivalents are spread too: `runtime.dev` (reload on a Python change, and serve `/assets` and `/files`), `watch.*` (asset rebuilds) and `assets.reassert.*`.

**`devguard` names a real subsystem.** It is the `<p>_devguard` package, the `<P>_DEVGUARD_*` environment variables and the baked settings, and its options map one to one onto them. It stays. It does, however, hold settings for something that is not a guard: the Mailpit catcher. `host`, `smtpPort`/`port` and `httpPort` sit under `devguard.mail`, and frappe-nix's POP3 server under `devguard.mail.pop3`. The products have also drifted on what that means:

-   frappe-nix runs Mailpit only when the mail guard is on (`services.mailpit = lib.mkIf mailEnabled`);
-   odoo-nix runs it always, and the guard only orders startup after it.

**Ports are spread across four trees.** frappe-nix derives every port from `ports.{base,offset,worktreeSalt}`. odoo-nix sets its ports in three places: `odooConf.httpPort`, `odooConf.geventPort` and `odooConf.dbPort` (5432 in `odooConf`), and `devguard.mail.{port,httpPort}`.

The proposal for both products:

| Tree | Holds | Moves in |
| --- | --- | --- |
| watch | What the shell does when a file changes. watch.python (bool, default true): restart the server on a Python change. frappe-nix keeps watch.{apps,excludePublishers,rtl,nativeSass} and gains watch.reassert (from assets.reassert). | odoo-nix dev.autoReload → watch.python; frappe-nix runtime.dev → watch.python. odoo-nix's assertions that watch.python needs workers = 0 and a devMode with reload stay. The dev tree is removed. |
| devguard | Guard policy only: what is refused or redirected. Shared guards egress, mail, jobs, backups, objectstore, webhooks, each with enable and the same shape. Product guards beside them (integrations, google, plaid; iap). | scheduler and crons → jobs. devguard.mail keeps enable, sender and unmute (frappe-nix). |
| mailpit | The catcher as a service: mailpit.{enable,host,port,httpPort}, mailpit.pop3.{enable,port,user,password}. The ports default from ports. Default: on whenever devguard.mail.enable is, and on in both products otherwise too, since a catcher that is up costs nothing and a developer testing mail with the guard off still wants one. | frappe-nix and odoo-nix devguard.mail.{host,smtpPort/port,httpPort}, frappe-nix devguard.mail.pop3.*. |
| ports | ports.{base,offset,worktreeSalt} as frappe-nix has them, from lib.ports, and one read-only attribute per service port (ports.http, ports.smtp, ports.mailUi, ports.db, …) that the rest of the module reads. Each stays overridable with lib.mkForce for the rare fixed-port case. | odoo-nix odooConf.httpPort, odooConf.geventPort, odooConf.dbPort, and the Mailpit ports. odooConf keeps only options that end up in odoo.conf. |

This keeps the decided name `port`. The SMTP port is `mailpit.port`, which defaults to `ports.smtp`, and the mail guard redirects to it.

Two things to check during the move:

-   **frappe-runtime's `--dev`** turns on reload and asset serving together. If `watch.python = false` should still serve `/assets` and `/files`, frappe-runtime needs a separate flag. Otherwise `watch.python` documents that it turns off both.
-   **Process ordering.** Mailpit-before-server ordering moves from the guard to the `mailpit` tree, so a server with the mail guard on never starts before its catcher.

Other trees could be aligned in the same pass, but they are left alone here, since neither product is clearly better: frappe-nix's `appsReconcile`, `renamedApps` and `replacedApps`, against odoo-nix's `layout.*`.

## Track C: parity

Features one product has and the other lacks. Each is a pull request to the product that lacks it. Where Track A already moves the code into the base, the port is that adoption and is not repeated here.

| Feature | From | To |
| --- | --- | --- |
| Private migrate and backup dumps | frappe-nix | odoo-nix (Phase 0) |
| Root admin CLI as the service user | odoo-nix | frappe-nix (Phase 0) |
| Partial clones in the scaffolder | odoo-nix | frappe-nix (Phase 0) |
| Per-project ports, CI mode | frappe-nix | odoo-nix (Phase 5) |
| Lock audit for consumers | frappe-nix | odoo-nix (Phase 4) |
| Build dependencies from uv.lock, thread caps | odoo-nix | frappe-nix (Phase 4) |
| Snapshot verification before migrate | odoo-nix | frappe-nix (Phase 6) |
| Error log to syslog | frappe-nix | odoo-nix (Phase 6) |
| ANSI stripping in journald | odoo-nix | frappe-nix (Phase 6) |
| LoadCredential for production secrets | frappe-nix | odoo-nix |
| environmentFiles | odoo-nix | frappe-nix |
| Slow-query logging | odoo-nix | frappe-nix |
| Neutralise after restore | odoo-nix | frappe-nix |
| Migrate after restore | frappe-nix | odoo-nix |
| Remove a source repository | frappe-nix | odoo-nix |
| Scaffolder --dry-run, --skip-lock, --commit, migrate an existing tree | frappe-nix | odoo-nix |
| ide option and language-server docs | odoo-nix | frappe-nix |
| Docs: troubleshooting, upgrading, memory and disk, options reference, scaffolder reference, secrets page, layout reference | frappe-nix | odoo-nix |
| Prek lint job and repository config files | frappe-nix | odoo-nix |
| Build every check in CI | odoo-nix | frappe-nix (Phase 0) |
| Maintenance mode while a migrate on deploy has failed (nginx 503 and a maintenance page, the service stopped) | frappe-nix | odoo-nix |
| Mailpit runs whether or not the mail guard is on | odoo-nix | frappe-nix (with the mailpit tree) |

## What stays in the products

| Piece | Why it stays |
| --- | --- |
| modules/devenv.nix as a whole | Different services (MariaDB and Redis on Unix sockets, PostgreSQL on TCP), processes and tooling. frappe-nix has app mode, the runtime, Node builds, watch and assets; odoo-nix has odooConf, auto-reload and the test browser. |
| modules/nixos.nix as a whole | frappe-nix is multi-site (sites = attrsOf siteModule) with Redis, chromium and wkhtmltopdf; odoo-nix is one instance with xSendfile, ensureExtensions and dbFilter. |
| modules/containers.nix as a whole | Different roles. Only the naming and the labels follow Track B. |
| The Python workspace shapes | frappe-nix's bench workspace against odoo-nix's OCB plus OCA submodules. Only the pieces in Phase 4 move. |
| Application guards (mail, backups, objectstore, webhooks, scheduler and crons, integrations, google, plaid, iap) | They patch Frappe or Odoo APIs. Their settings follow Track B. |
| Restore internals | frappe-nix's offline migrate (pt-osc) and odoo-nix's migrate plan (checksums) do different jobs. |
| frappe-nix's app standards and app mode; odoo-nix's OCA catalog and bundles | Product features with no counterpart. |
| The command-line tools themselves | bench (bash) and odoo (click). Their commands and flags follow Track B. |

## How products consume it

```nix
inputs.python-dev-nix = {
  url = "github:Avunu/python-dev-nix";
  inputs.nixpkgs.follows = "nixpkgs";
  inputs.flake-parts.follows = "flake-parts";
};
```

Products depend on the base. Their consumers (benches, Odoo projects) do not: they reach it through the product's lock, and the product's Dependabot nix group moves the pin, as it already does for the product's other inputs. For example, frappe-nix's `flakeModules.default` imports the base's secrets module at `frappe-nix.secrets`.

## Releases and compatibility

-   Tags `v0.x.y` until both products have adopted Phases 2 and 3, then `v1`.
-   **A breaking change** is anything a product's users could see: an option path, an environment variable, a log prefix, a generated file, the layout under `secrets/`. Before 1.0 one ships in a minor release, with a migration note and the product pull requests opened alongside. After 1.0 one ships in a major release.
-   The products follow the same rule for their own Track B renames. Neither product has tagged a release yet; both start tagging, with release-please, when they adopt the base.
-   The downstream CI job has to pass before a release.
-   Rollback is the product's: it pins a base revision, and reverting the product's adoption commit restores its own copy.

## Migration order

1.  Phase 0, in both products, now. The frappe-nix CI fix comes first.
2.  Phase 1, and the first draft of `CONVENTIONS.md`. Phase 7 can follow straight away.
3.  Phase 2, with frappe-nix's copy as the starting point (it is the superset: sites). One pull request here, then one per product. Each product pull request shows the before-and-after evaluation comparison.
4.  Phase 3, starting from the egress code as it stands after frappe-nix#82 and odoo-nix#46. Same pattern.
5.  Phases 4 to 6, in any order, each when the next change to that piece comes due.
6.  Track B renames ride along with the Track A phase that touches the same surface (restore flags with Phase 2, devguard options with Phase 3, ports with Phase 5). The rest go in one rename pull request per product.
7.  Track C ports at any point, each owned by the product that lacks the feature.

## Open decisions

None. The following were settled on 2026-10-16 and are recorded in Track B:

-   Migrate on deploy serves in maintenance mode when the migrate fails.
-   The docs pipeline is `Avunu/site-docs`.
-   The formatter is `nixfmt-rs`.
-   Restore overwrites an existing database by default (`--no-force` refuses).
-   `siteName` is the project identity in both products.
-   The scaffolder's version flag is `--version`.
-   odoo-nix moves to per-project ports.
-   The devguard option names are `mail.port` and `jobs.extraBlocked`.

The development shell option tree (`watch`, `devguard`, `mailpit`, `ports`) is a proposal awaiting review.

## wordpress-nix

wordpress-nix is not a consumer and none of the rules above bend for it. It may take three leaf pieces when they exist, with no further commitment:

-   the nix-lint check from Phase 7, after a `nixfmt` sweep of its modules;
-   the reusable Dependabot auto-merge workflow from Phase 7. Its policy differs (it merges major bumps and is gated on the OCI build), so the workflow takes an input for that;
-   the journald field vocabulary (`APP_SERVICE`, `APP_SITE`), which it already sets in its NixOS module. That is a document, not code.

Its own binary cache (`cache.yml`) and OCI publishing (`oci.yml`) are worth copying into frappe-nix and odoo-nix, as Track C items outside this repository.

## Success looks like

-   One copy of each Track A piece, and the product copies deleted.
-   A fix to egress, agecheck, backups, overrides or the hardening baseline lands once and reaches both products through a Dependabot bump.
-   Someone who knows one product can use the other without relearning restore, secrets, devguard settings, the scaffolder or the logs.
-   `CONVENTIONS.md` has an answer for every row in Track B, and the base's checks confirm both products follow it.

## Appendix: how the overlap was measured

The first pass paired files in frappe-nix and odoo-nix by file name. Where a name occurs more than once, each frappe-nix file was paired with its closest odoo-nix match. Lock files, fixtures, `__init__.py`, images and licenses were skipped. Before comparing, the following were normalised away: tabs to four spaces, the words frappe, odoo and bench (case-insensitive), surrounding whitespace, and blank lines. Similarity is Python's `difflib.SequenceMatcher` ratio over the remaining lines. That pass was made against frappe-nix at `f470690` (the head of #82) and odoo-nix at `ca7e648` (the head of #46).

The second pass, on 2026-10-16 against frappe-nix at `68caed8` and odoo-nix at `0b42a97`, compared by job instead of by name. It read both products area by area: scaffolding, shell entry, source checkouts, restore, the CLI, devguard settings, the NixOS modules, logging, containers, Python packaging, flake structure, CI and tests. It classified each difference as shared code, a convention, a parity gap, or product-specific. The same method was used for wordpress-nix against both, with wordpress and wp added to the normalised words.
