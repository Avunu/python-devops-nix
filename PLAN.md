# python-devops-nix: plan

Status: proposed, 2026-10-16. Nothing is extracted yet. This repository was planned as nix-dev-base; it is renamed python-devops-nix because its scope is now Python application products only.

## Why this repository exists

[frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix) package two Python ERP frameworks the same way: a flake-parts flake with a devenv development shell, a uv2nix Python environment, a NixOS module, container images and a scaffolder. They have the same users, the same production model and the same failure modes. Pieces of one have been copied into the other, and the copies are now maintained twice.

**The same change keeps landing twice.** On 14 and 15 October 2026 alone, odoo-nix merged five explicit ports from frappe-nix: partial-clone fixes for submodules (#39), the root wrapper (#40), the btrfs copy-on-write fix for the database datadir (#41), the nix-lint check (#43) and paired-branch clones (#45, mirrored back as frappe-nix#81).

**Copies drift, and the drift is where bugs live.** odoo-nix's egress guard never matched a hostname on its allowlist, because clients resolve a name and connect to the address. frappe-nix's port of that guard, written a week later, fixed it ([frappe-nix#82](https://github.com/Avunu/frappe-nix/pull/82)), and the fix then had to be carried back ([odoo-nix#46](https://github.com/Avunu/odoo-nix/pull/46)). The same pattern shows up without a bug report: odoo-nix's `python-ldap` override has the `ldap_r` fix and frappe-nix's does not; frappe-nix's migrate snapshots are private and odoo-nix's are not.

**The products also differ where they should not.** The same job has different names, flags and defaults in each: `bench restore` overwrites the existing database by default and `odoo db restore` refuses; the devguard SMTP port is `mail.smtpPort` in one and `mail.port` in the other; the unguarded-restore override is `FRAPPE_RESTORE_ALLOW_UNGUARDED` in one and `ODOO_NIX_RESTORE_ALLOW_UNGUARDED` in the other. Someone who works on both products has to learn both.

**The duplicated pieces are the ones it is costliest to get wrong.** They check that secrets are encrypted to the right people, hold production backup credentials, write database dumps, and stop a development copy of production from reaching production. When those drift, the result is not a failing build but a leak, or a guard that quietly stopped guarding.

### Goal

frappe-nix and odoo-nix should behave as one product family. Wherever the two do the same job, they do it the same way, under the same names, and from one tested copy of the code when the code is product-independent.

That is four kinds of work, run as tracks:

-   **Track A, shared code.** One copy of each product-independent piece, in this repository.
-   **Track B, shared conventions.** One name, flag, default and layout for each job both products do, written down in this repository's `CONVENTIONS.md` and checked where a check is cheap. This needs no shared code.
-   **Track C, parity.** A feature one product has and the other lacks is ported, unless it is genuinely product-specific.
-   **Track D, frappe-nix's command line in Python.** Every command with real logic in frappe-nix moves from bash to Python, under the language rule in [Design rules](#design-rules). odoo-nix's command line is already Python.

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
4.  **Python versions follow from where the code runs.** See [Python versions](#python-versions). In short:
    -   code that loads inside the application runs on every interpreter the products' presets name (3.11 to 3.14 today), is stdlib-only, and is tested on each;
    -   tools that run as their own process run on one interpreter that the product picks;
    -   all Python here is written to 3.11 syntax.
5.  **Logic is Python, glue is bash.** Anything that parses data, handles dates, JSON or more than a few flags, keeps an exit-code table or prompts is written in Python. Bash is kept for thin glue that runs a few commands with no decisions in between: `ExecStart` wrappers, container entrypoints, `systemd-run` and `exec` shims, and the `bench` dispatcher. Glue is built with `writeShellApplication` so shellcheck runs on it. Shell-driven tests that exercise a command from outside (`tests/*.sh`) stay in shell. They define the command's behaviour, and they are what checks a port. Nothing moves from Python to bash.
6.  **Python is vendored at build time, not installed as a dependency.** Shared Python is copied into the product's package by Nix (for example, the devguard core into `frappe_devguard/`). Each virtualenv still contains exactly one guard package, under the product's name, with no new distribution to resolve and no version skew inside a process.
7.  **No new reach into production.** Nothing here may reach a production virtualenv, NixOS service or container that does not already reach it today, except where a phase says so explicitly (the journald core, the overrides, the hardening baseline). devguard stays development-only by construction: frappe-nix grafts it into the development environment only, and odoo-nix's `odoo-init-*` checks assert that it is absent from the production one. frappe-nix gains an equivalent check in Phase 3.
8.  **Tests live with the code, and CI runs all of them.** The base tests what it ships, against a stub product. Product suites keep their product-specific tests and drop the duplicated ones. Every check a product defines is built in CI on every pull request; a check that is not built does not count as coverage.

### Python versions

The presets name three interpreters today:

| Product | Series | Interpreter |
| --- | --- | --- |
| frappe-nix | develop, version-16 | python314 |
| frappe-nix | version-15 | python312 |
| odoo-nix | 18.0 | python311 |
| odoo-nix | 19.0 | python312 |

Shared Python falls into two classes, and the risk differs between them.

**In-process code** loads into the application's own interpreter: the devguard core, the journald core and `_hook.py`. It cannot pick a version, so it has to run on every interpreter above at once. That gives it two limits:

-   **The floor.** Nothing newer than 3.11 may be used, for example PEP 695 type syntax, `itertools.batched` or `typing.override`. Ruff's `target-version = "py311"` in the base's `ruff.toml` enforces this.
-   **The ceiling.** This is the larger risk. devguard patches stdlib internals (`socket`, `smtplib`, `imaplib`, `poplib`), which change between releases. The stdlib also drops modules: `smtpd` and `asyncore` are gone from 3.12, so test stubs must not use them. The `.pth` import line that every graft depends on has been discussed upstream for replacement by a structured startup configuration. Each new CPython release is checked for that before a preset adopts it.

The base's checks for in-process code run as a matrix over every interpreter in either product's presets. The matrix is read from `frappe-presets.json` and `odoo-presets.json`, which the products pass in, so a preset that adds an interpreter adds it to the matrix. Each product also builds its grafted environment for every preset's interpreter, not only the default one. Bytecode compilation checks only the interpreter that actually builds.

The floor is the oldest interpreter of any preset still shipped. When a preset is retired, the floor rises in the same pull request.

**Out-of-process tools** run as their own process: agecheck, the secret scripts, the backup fetch layer, the banner and the products' command lines. Each product packages them with one interpreter it picks. That is a real pin, but a cheap one, because Nix gives the tool its own interpreter and consumers never see it. Three costs come with it:

-   **Closure size.** A tool interpreter that differs from the application's puts a second Python of about 100 MB into the closure. In development shells this is accepted. In production closures, tools run on the application's interpreter unless there is a reason not to.
-   **The pin moves with nixpkgs.** The interpreter is the product's nixpkgs' (`pkgs.python3` or a named `python3xx`), so it changes when that nixpkgs does. Tools are written to 3.11 like everything else, so the move is a no-op.
-   **Importing the framework ties a tool to the application's interpreter.** odoo-nix's CLI imports Odoo in-process (`odooenv.py`) and runs on the application's interpreter, with click and rich from nixpkgs for that interpreter (`lib/cli.nix`). Any base module it imports, such as the backup fetch layer, therefore also runs on 3.11 to 3.12 and is tested there. frappe-nix's CLI imports no Frappe code and calls into the bench by subprocess (see [Track D](#track-d-frappe-nixs-command-line-in-python)), so it runs on the tool interpreter.

Development-shell tools may use rich and click, taken from nixpkgs for the interpreter they run on, not from the application's environment.

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

-   Rename the GitHub repository from Avunu/nix-dev-base to Avunu/python-devops-nix before anything consumes it.
-   `flake.nix` on flake-parts, exposing `flakeModules.*`, `lib.*` and `checks.*`.
-   `nix fmt` and the nix-lint check from Phase 7, run on this repository itself.
-   CI: a `check` workflow, plus a **downstream** job that builds all of frappe-nix's and odoo-nix's checks against the pull request's head with `--override-input python-devops-nix`. This is the main defence against one bad release breaking both products at once.
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
-   The fetch layer is Python. It starts from odoo-nix's `remote.py`, not frappe-nix's `backup-fetch.sh`: the two are in different languages, so this is a rewrite of frappe-nix's side, not an extraction. frappe-nix's bash copy is deleted, not shared. The layer is a module that odoo-nix's CLI imports in-process and a command that frappe-nix's CLI calls. Because of that import, it is tested on odoo-nix's interpreters as well (see [Python versions](#python-versions)). The `layout` parsers are Python functions. frappe-nix's parser is its first piece of [Track D](#track-d-frappe-nixs-command-line-in-python).
-   `backup-access.sh` stays bash. It is already a byte-identical copy in both products, and it is a prompt plus three commands.
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
-   **`lib.sources`**: submodule clone and update: paired branch, partial clone, no pack pile-up. It has been ported in both directions twice, and the messages are already near-verbatim (frappe-nix `lib/apps-report.nix`, odoo-nix `lib/submodules-init.nix`). It covers both the scaffolder's first clone and shell entry. Both copies are bash today (frappe-nix 385 lines, odoo-nix 353). Branch pairing, retry and reporting are logic, so the shared copy is Python. Shell entry calls it as a command.
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

### Naming rule

Option names follow one rule, recorded at the top of `CONVENTIONS.md`:

1.  **Structure follows nixpkgs.** That means `enable`, `package`, `user` and `group`, `dataDir` or `stateDir`, `environmentFiles` and `openFirewall`. It also means `settings` as a freeform attribute set rendered into the framework's config file (RFC 42), and multi-tenant modules keyed by hostname as in `services.wordpress.sites.<host>` and `services.nginx.virtualHosts.<host>`.
2.  **Names for something frappe-nix or odoo-nix invented are shared.** That covers the project identity, ports, guards, the mail catcher and the restore flags. Both products use the same word.
3.  **Names that mirror a framework config key use that framework's word**, in camelCase: Frappe's `default_site` is `defaultSite`, Odoo's `db_name` is `dbName`. Two frameworks with different concepts keep different names. Matching the framework is what its users will guess.

nixpkgs itself is not consistent about the name for an app's public hostname. nextcloud uses `hostName`, discourse `hostname`, odoo `domain`, mastodon `localDomain` and mediawiki `url`. Part 3 settles that case: each product keeps the shape its upstream nixpkgs neighbour uses, if there is one.

### Decisions

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
| Migrate on deploy | RemainAfterExit plus a marker file; services require init only, so a failed migrate leaves the site serving in maintenance mode | runs on every start with --if-needed, marker in the database, odoo.service requires it, so a failed migrate stops serving | Decided: what happens after a failed migrate is product-specific. frappe-nix keeps serving in Frappe's native maintenance mode. Odoo has no maintenance mode, and serving it against a half-migrated schema is not safe, so odoo-nix keeps stopping the service. The rest is shared: the migrate.* options, the snapshot flow, and verifying the snapshot before migrating (odoo-nix's pg_restore --list, a MariaDB equivalent for frappe-nix). |
| Production secrets | LoadCredential, init as the service user | init runs as root and reads the files | LoadCredential in both, and environmentFiles (odoo-nix's) in both. |
| Database logging | none | slowQueryMs, log_line_prefix | Both: database.slowQueryMs. |
| Journald enablement | always on when stderr is the journal, via a .pth graft | needs ODOO_NIX_JOURNALD=1 and an option, via a sitecustomize shim | Always on when stderr is the journal, via a .pth graft. |
| Journald vocabulary | SyslogIdentifier=frappe-<role>; APP_SERVICE in runtime, web, worker, scheduler, socketio, init, migrate, db, redis, nginx | odoo, odoo-init, odoo-migrate; main unit web | <p>-<role> and one written list of roles. |
| Journald line format | <N>{module} [{site}] msg | <N>{dbname} {logger}: msg | <N>{logger} [{site or db}] msg. |
| Container images | ${benchName}/<role>, outputs named by role | ${projectName}-odoo, output container-odoo | <registry>/<name>/<role>, outputs container-<role>, with OCI labels, a non-root User and a healthcheck. |
| Identity options | benchName (project identity), siteName (default FRAPPE_SITE) | projectName (project identity), odooConf.dbName (default database) | Decided: the project identity is `name` in both (`frappe-nix.name`, `odoo-nix.name`). It is a slug: it seeds the port hash and names container images, systemd scopes and log messages. Each module sets devenv's own top-level `name` ("Name of the project") with `mkDefault cfg.name`, so the shell and the product agree. The default tenant keeps the framework's word under part 3 of the naming rule: frappe-nix's siteName becomes `defaultSite` (Frappe's `default_site`), and odoo-nix keeps `dbName` (Odoo's `db_name`). The scaffolder's `--site` becomes `--default-site`, and `--name` sets the identity in both. Production keeps nixpkgs' shapes: frappe-nix's `sites.<fqdn>` (as `services.wordpress.sites`), odoo-nix's `domain` (as upstream `services.odoo.domain`). No name is reused, so benchName, projectName and siteName all ship as `mkRenamedOptionModule` aliases. The word site keeps exactly Frappe's meaning, a tenant of the bench, everywhere in frappe-nix. |
| NixOS module path | services.frappe | services.odoo-nix (nixpkgs already has services.odoo) | `services.frappe-nix` and `services.odoo-nix`. These match the devenv and flake-parts roots (`frappe-nix.*`, `odoo-nix.*`) and cannot collide with a future nixpkgs module. `services.frappe` ships as an alias. |
| Framework config blocks | per-site extraConfig and extraConfigFiles (site_config.json) in NixOS; none in devenv | settings (odoo.conf) in NixOS; odooConf in devenv | `settings` everywhere a block becomes the framework's config file, as in nixpkgs. frappe-nix: `sites.<fqdn>.settings` and `settingsFiles` (site_config.json, the files for secrets). odoo-nix: devenv's odooConf becomes `settings`, matching its NixOS module. Once the ports leave it (see the option tree), it holds only odoo.conf keys. Declared keys inside a freeform `settings` use camelCase (`settings.dbName`), and undeclared keys pass through under the framework's own spelling. |
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
| ports | ports.{base,offset,worktreeSalt} as frappe-nix has them, from lib.ports, and one read-only attribute per service port (ports.http, ports.smtp, ports.mailUi, ports.db, …) that the rest of the module reads. Each stays overridable with lib.mkForce for the rare fixed-port case. | odoo-nix odooConf.httpPort, odooConf.geventPort, odooConf.dbPort, and the Mailpit ports. odooConf, renamed `settings` (see the framework config row), keeps only options that end up in odoo.conf. |

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
| Mailpit runs whether or not the mail guard is on | odoo-nix | frappe-nix (with the mailpit tree) |

## Track D: frappe-nix's command line in Python

frappe-nix's command line is bash, about 6,000 lines of it:

-   the scaffolder and the app-standards tools in `lib/sh/` (3,451 lines);
-   the `bench` subcommands written inline in `lib/scripts.nix` (about 1,400 of its 1,795 lines);
-   the tools in `lib/apps-report.nix`, `lib/node-locks.nix`, `lib/node-modules.nix`, `lib/root-sync.nix`, `lib/bench-patches.nix` and `lib/rename/`.

Most of it is logic by [rule 5](#design-rules): flag parsing, mode detection, backup naming, JSON, exit-code tables and prompts. odoo-nix's equivalent is a click package (`lib/odoo_nix_cli/`). Track D moves all of frappe-nix's logic to Python and leaves bash only where rule 5 keeps it.

This is a product track, not shared code. The CLI stays in frappe-nix. It calls the base's tools (backup fetch, sources, banner) as Track A ships them.

### Shape

-   **One package.** The commands become click subcommands in `py/`, in the same distribution as `frappe_nix_tools` and built the same way, so frappe-nix ships one Python tool package.
-   **The tool interpreter.** The CLI imports no Frappe code. Anything that needs Frappe runs `bench …` or the bench's own interpreter as a subprocess. That puts the CLI on frappe-nix's tool interpreter (`python314`, which `frappe-nix-tools` already uses), not the bench's. version-16 and develop benches gain no second interpreter. version-15 benches gain one, in development shells only. The code is written to 3.11 like everything else (see [Python versions](#python-versions)).
-   **Configuration as JSON, not interpolation.** Today Nix interpolates store paths and options straight into the bash: `benchBin`, `nodeLocksBin`, `restore.*`, `offlineMigrate.*` and the secret names. The port writes them to one generated JSON file, which the wrapper points the CLI at with `FRAPPE_NIX_CLI_CONFIG`. The CLI reads it into a typed dataclass and fails on an unknown or missing key. This is the same pattern as devguard's `_baked.json` and the banner's JSON input.
-   **The dispatcher stays bash.** `bench` is a `case` that hands frappe-nix's commands over and `exec`s upstream bench for everything else. Moving it to Python would add an interpreter start to every passthrough command. The `bench-update`, `bench-restore` and other `bench-*` names stay as two-line shims that `exec` the CLI, because `frappe_benchcli` and the docs name them.
-   **Drop-in scripts keep working.** `lib/scripts.d/` and its argument contract (app standards spec §1.2, §5) hand bash snippets to drop-ins: `atBench`, `regenNodeLocks`, `refreshNodeModules`, `syncRegistry` and others. Snippets that wrap logic become one-line calls to CLI subcommands, so a bash drop-in and the CLI share one implementation. The spec is updated in the same pull request. A drop-in may itself be Python once the CLI exposes a registration point. Until then it stays bash.
-   **Prompts.** gum (33 call sites in `lib/sh/`) is replaced by rich's `Prompt` and `Confirm`, and the gum dependency goes. Non-TTY behaviour does not change: migrating still needs `-y`.
-   **Process semantics are spelled out.**
    -   `exec` becomes `os.execvp`.
    -   `set -euo pipefail` becomes `check=True` on each subprocess.
    -   Long-running steps forward Ctrl-C to their child and exit 130.
    -   Output from `git`, `uv`, `yarn` and `mc` streams through unchanged.
-   **Lint and types.** Ruff and ty, with the repository's `ruff.toml` and `ty.toml`, replace shellcheck for the ported code.

### What stays bash

These are glue under rule 5:

-   the `bench` dispatcher and the `bench-*` shims;
-   `lib/mariadbd-wrapper.nix`;
-   `lib/db-nocow.nix` (shared in Phase 5);
-   the container entrypoints;
-   `frappe-upstream`;
-   the `nixfmt` shim;
-   `backup-access.sh` (shared in Phase 2);
-   the secret wrappers that only decrypt and `exec`;
-   the NixOS unit scripts in `modules/nixos.nix`.

The migrate unit's snapshot flow is logic, but it is production code. It is handled when Phase 6 extracts `lib.nixos.migrate`, not here.

`bench-console`, `bench-clear-cache`, `bench-build`, `bench-migrate` and `bench-offline-migrate` are each under 15 lines that set up the environment and `exec`. They stay bash unless a later change adds a decision to them.

### Order

Each step is one pull request, or a few, and leaves frappe-nix working. A command is ported once, together with any Track A or B change to the same surface.

| Step | Ported | Bash replaced | Lands with |
| --- | --- | --- | --- |
| D1 | `bench restore` and the backup layout parser | `bench-restore` in `lib/scripts.nix` (about 275 lines), `lib/sh/backup-fetch.sh` (357) | Phase 2, and the restore rows of Track B |
| D2 | The scaffolder, `frappe-init` | `lib/sh/` main, detect, init, migrate, template, apps, app-init, app-sync, pipeline, common, secrets (about 1,900 lines) | Track B's scaffolder rows: version flag, exit codes, presets schema, seeds |
| D3 | Sources and apps | `lib/apps-report.nix` (385), `bench-get-app`, `bench-remove-app`, `bench-new-app`, `reconcile-apps`, `lib/rename/`, `lib/root-sync.nix` (about 700 together) | Phase 5's `lib.sources` |
| D4 | Update and dependencies | `bench-update` (about 400), `update-deps`, the relock tool in `modules/devenv.nix`, `lib/node-locks.nix`, `lib/node-modules.nix`, `lib/bench-patches.nix`, `bench-setup-requirements` (about 1,100 together) | Track B's relock row |
| D5 | Remaining commands with logic | `provision-site` and the secret scripts in `lib/scripts.nix` | Track B's command layout (`bench provision`) |
| D6 | App-standards tools | `lib/sh/frappe-test.sh` (579), `frappe-demo.sh` (217), `frappe-shots.sh` (188) | Nothing; any time after D2 |

odoo-nix's scaffolder (`lib/odoo-init.sh`, 240 lines, and `lib/oca-lib.sh`, 217) is bash too. It falls under the same rule and moves into `odoo_nix_cli` when odoo-nix adopts Track B's scaffolder flags, so those flags are written once, in Python. That pull request belongs to odoo-nix, not to Track D.

### Behaviour contract

-   **The shell tests are the acceptance suite.** `tests/*.sh` (about 6,200 lines) and the standards self-tests run unchanged against each ported command. A pull request that changes a message or an exit code changes the test in the same pull request and lists the change in its description. Accidents of bash (word splitting, an exit status lost in a pipeline) are not reproduced. Where a test depended on one, the description says so.
-   **New Python unit tests** cover the pieces the shell tests reach only indirectly: argument parsing, mode detection (`detect.sh`), backup naming and `--at`, preset resolution, and the configuration schema.
-   **Shell entry time** is measured before and after D3, because sources run on every direnv reload. A regression is fixed before merge. Imports of rich and click are deferred to the commands that use them.

### Risks

-   **Size.** This is the largest change frappe-nix has had. 21 of the last month's commits touched `lib/scripts.nix` or `lib/sh/`, so branches in flight will conflict. Keep each step short-lived, and say in the tracking issue which commands are mid-port, so nobody fixes a bug in bash that is about to be deleted.
-   **Coverage gaps.** A path the shell tests never take can change behaviour silently. Before each step, list the command's flags and branches against the tests that reach them, and add the missing tests to the bash suite first, while the bash version is still the reference.
-   **Two languages during the port.** Until D5, a fix may land in a bash command that is next in line. Ports start from the current head, not from a snapshot taken when the track began.
-   **No production effect.** Track D changes the development shell and the scaffolder only. The production `bench` wrapper and the NixOS units are untouched, so [rule 7](#design-rules) holds.

### Done when

-   `lib/sh/` holds only `backup-access.sh`, or nothing once Phase 2 ships it from the base.
-   `lib/scripts.nix` holds the dispatcher, the shims, the glue listed above and the drop-in machinery.
-   Every shell test passes against the Python commands.
-   The CLI's unit tests pass.
-   gum is gone from the development shell.

## What stays in the products

| Piece | Why it stays |
| --- | --- |
| modules/devenv.nix as a whole | Different services (MariaDB and Redis on Unix sockets, PostgreSQL on TCP), processes and tooling. frappe-nix has app mode, the runtime, Node builds, watch and assets; odoo-nix has odooConf, auto-reload and the test browser. |
| modules/nixos.nix as a whole | frappe-nix is multi-site (sites = attrsOf siteModule) with Redis, chromium and wkhtmltopdf; odoo-nix is one instance with xSendfile, ensureExtensions and dbFilter. |
| modules/containers.nix as a whole | Different roles. Only the naming and the labels follow Track B. |
| The Python workspace shapes | frappe-nix's bench workspace against odoo-nix's OCB plus OCA submodules. Only the pieces in Phase 4 move. |
| Application guards (mail, backups, objectstore, webhooks, scheduler and crons, integrations, google, plaid, iap) | They patch Frappe or Odoo APIs. Their settings follow Track B. |
| Restore internals | frappe-nix's offline migrate (pt-osc) and odoo-nix's migrate plan (checksums) do different jobs. |
| What a failed migrate on deploy leaves running | frappe-nix serves in Frappe's native maintenance mode. Odoo has none, so odoo-nix stops the service. Phase 6's `lib.nixos.migrate` takes the product's failure behaviour as an argument. |
| frappe-nix's app standards and app mode; odoo-nix's OCA catalog and bundles | Product features with no counterpart. |
| The command-line tools themselves | frappe-nix's bench commands and odoo-nix's odoo, both click after Track D. Their commands and flags follow Track B. Each CLI calls the base's tools, but the CLIs themselves stay in the products. |

## How products consume it

```nix
inputs.python-devops-nix = {
  url = "github:Avunu/python-devops-nix";
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
8.  Track D in the order its own section gives. Its first step lands with Phase 2. Where a later step touches a surface that Track A or B also changes (sources with Phase 5, the scaffolder's flags with Track B), the two land in one pull request, so a command is rewritten once.

## Open decisions

None. The following were settled on 2026-10-16 and are recorded in Track B:

-   After a failed migrate on deploy, frappe-nix serves in Frappe's maintenance mode and odoo-nix stops the service. Odoo has no maintenance mode, so this stays product-specific.
-   The docs pipeline is `Avunu/site-docs`.
-   The formatter is `nixfmt-rs`.
-   Restore overwrites an existing database by default (`--no-force` refuses).
-   The project identity is `name` in both products. frappe-nix's `siteName` becomes `defaultSite`, odoo-nix keeps `dbName`, and option names follow the [naming rule](#naming-rule).
-   The NixOS modules live at `services.frappe-nix` and `services.odoo-nix`.
-   A block rendered into the framework's config file is called `settings`.
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
-   Both products' command lines are Python, and the bash left in either is glue under rule 5.
-   Every in-process piece is tested on every interpreter a preset names, and adding a preset adds its interpreter to the matrix.

## Appendix: how the overlap was measured

The first pass paired files in frappe-nix and odoo-nix by file name. Where a name occurs more than once, each frappe-nix file was paired with its closest odoo-nix match. Lock files, fixtures, `__init__.py`, images and licenses were skipped. Before comparing, the following were normalised away: tabs to four spaces, the words frappe, odoo and bench (case-insensitive), surrounding whitespace, and blank lines. Similarity is Python's `difflib.SequenceMatcher` ratio over the remaining lines. That pass was made against frappe-nix at `f470690` (the head of #82) and odoo-nix at `ca7e648` (the head of #46).

The second pass, on 2026-10-16 against frappe-nix at `68caed8` and odoo-nix at `0b42a97`, compared by job instead of by name. It read both products area by area: scaffolding, shell entry, source checkouts, restore, the CLI, devguard settings, the NixOS modules, logging, containers, Python packaging, flake structure, CI and tests. It classified each difference as shared code, a convention, a parity gap, or product-specific. The same method was used for wordpress-nix against both, with wordpress and wp added to the normalised words.
