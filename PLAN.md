# nix-dev-base: plan

Status: proposed, 2026-10-16. Nothing is extracted yet.

## Why this repository exists

Three products are built on one skeleton: [frappe-nix](https://github.com/Avunu/frappe-nix), [odoo-nix](https://github.com/Avunu/odoo-nix) and [wordpress-nix](https://github.com/Avunu/wordpress-nix). Each is a flake-parts flake with a devenv development shell, a NixOS module, container images and scaffolding templates. Pieces of it have been copied from one to another, and the copies are now maintained twice.

**The same change keeps landing twice.** On 14 and 15 October 2026 alone, odoo-nix merged five explicit ports from frappe-nix: partial-clone fixes for submodules (#39), the root wrapper (#40), the btrfs copy-on-write fix for the database datadir (#41), the nix-lint check (#43) and paired-branch clones (#45, mirrored back as frappe-nix#81).

**Copies drift, and the drift is where bugs live.** odoo-nix's egress guard never matched a hostname on its allowlist, because clients resolve a name and connect to the address. frappe-nix's port of that guard, written a week later, fixed it ([frappe-nix#82](https://github.com/Avunu/frappe-nix/pull/82)), and the fix then had to be carried back ([odoo-nix#46](https://github.com/Avunu/odoo-nix/pull/46)). frappe-nix also carries the same post-import hook five times internally: devguard, unixsock, nodebuild, benchcli and journald.

**The duplicated pieces are the ones it is costliest to get wrong.** They check that secrets are encrypted to the right people, hold production backup credentials, and stop a development copy of production from reaching production. When those drift, the result is not a failing build but a leak, or a guard that quietly stopped guarding.

### Goal and non-goal

The goal is one tested copy of the pieces that are product-independent and expensive to get wrong, consumed by every product that needs them.

The non-goal is a framework the products are built on. The products' development shells, NixOS modules, containers and Python environments have diverged far enough (see [What stays in the products](#what-stays-in-the-products)) that sharing them would mean an option for every difference.

## What is shared today

Files paired by name across frappe-nix and odoo-nix and scored for similarity. Method in the [appendix](#appendix-how-the-overlap-was-measured).

| Similarity | Files | Lines |
| --- | --- | --- |
| 0.95 and up: copies | `lib/agecheck.py` (differs only in the product name, in three strings), `lib/secrets-tools.nix`, `tests/secrets-cli.sh`, `lib/sh/backup-access.sh`, devguard `_hook.py`, `statix.toml`, the docs workflows (already shared, through Avunu/docusystem) | about 1,000 |
| 0.5 to 0.9: same design, drifted | `lib/secrets-schema.nix`, `modules/secrets.nix`, devguard `_settings.py`, `guards/mail_stdlib.py`, `guards/egress.py`, `lib/db-nocow.nix`, `tests/secrets-cli.nix`, `.github/workflows/dependabot-auto-merge.yml` | about 1,500 |
| below 0.3: separate products | everything else, including the modules, the Python environments and the guards that patch application APIs | most of each repository |

## Scope

In scope, in order:

1. [The secrets toolkit](#phase-1-the-secrets-toolkit)
2. [The devguard core](#phase-2-the-devguard-core)
3. [Repository hygiene](#phase-3-repository-hygiene)

[Later candidates](#later-candidates) are listed but not scheduled. [What stays in the products](#what-stays-in-the-products) says what is out and why.

## Design rules

These hold for everything in this repository.

1. **No product names.** The base never says Frappe, Odoo or WordPress. Each product passes its names in: option paths (`frappe-nix.secrets`), environment prefixes (`FRAPPE_DEVGUARD`), package names (`frappe_devguard`), log prefixes and command names (`bench restore`, `odoo db restore`). Migrating a product therefore changes nothing a user of it can see.
2. **A small lock.** The base's own inputs are nixpkgs and flake-parts, and consumers `follows` both, so adopting it adds one node to a product's lock and none to a second nixpkgs. Heavier dependencies are taken as arguments from the product rather than as inputs. For example, the secrets module takes the product's `agenix-shell` input, and ragenix comes from nixpkgs.
3. **Python is stdlib-only and runs on 3.11.** The oldest interpreter any product supports is 3.11 (Odoo 18). Frappe 15 runs on 3.12, Frappe 16 on 3.14, Odoo 19 on 3.12, and frappe-nix's own code already holds a 3.11 floor.
4. **Python is vendored at build time, not installed as a dependency.** Shared Python is copied into the product's package by Nix (for example, the devguard core into `frappe_devguard/`). Each virtualenv still contains exactly one guard package, under the product's name, with no new distribution to resolve and no version skew inside a process.
5. **No change to production.** Nothing here may reach a production virtualenv, NixOS service or container that does not already reach it today. devguard in particular stays development-only by construction: frappe-nix grafts it into the development environment only, and odoo-nix's `odoo-init-*` checks assert that it is absent from the production one. frappe-nix should gain an equivalent check as part of Phase 2.
6. **Tests live with the code.** The base tests what it ships, against a stub product. Product suites keep their product-specific tests and drop the duplicated ones.

## Phase 0: the repository

- `flake.nix` on flake-parts, exposing `flakeModules.*`, `lib.*` and `checks.*`.
- `nix fmt` (nixfmt) and the nix-lint check below, run on this repository itself.
- CI: a `check` workflow, plus a **downstream** job that builds the relevant checks of frappe-nix and odoo-nix against the pull request's head with `--override-input nix-dev-base`. This is the main defence against one bad release breaking every product at once.
- Dependabot for the nix and github-actions ecosystems, and the auto-merge workflow from Phase 3.
- A license. The products are MIT; see [Open decisions](#open-decisions).

## Phase 1: the secrets toolkit

The secrets toolkit consists of age ciphertext committed to the repository, agenix-shell decryption into the development shell, generated agenix rules, the recipient-drift check, `edit-secret` / `rekey-secrets` / `check-secrets`, and `setup-backup-access`. It goes first because it is nearly identical in both products, so the extraction changes no behaviour and can be verified by comparing outputs.

| Piece | frappe-nix | odoo-nix | Difference |
| --- | --- | --- | --- |
| Recipient-drift check | `lib/agecheck.py` | `lib/agecheck.py` | The product name in three messages. |
| Rules, on-demand decryption, setup tool | `lib/secrets-tools.nix` | `lib/secrets-tools.nix` | Comments only. |
| Backup-access prompt | `lib/sh/backup-access.sh` | `lib/sh/backup-access.sh` | The restore command it names, the example bucket, and how it confirms the credentials work: frappe-nix counts backups and names the newest, odoo-nix counts database folders. |
| Secret scripts | inside `lib/scripts.nix` | `lib/secret-scripts.nix` | Packaging. |
| Secret set schema | `lib/secrets-schema.nix` | `lib/secrets-schema.nix` | frappe-nix has bench-level roles and per-site roles (encryption key, database password, site config). odoo-nix has project-level roles only. The variable prefix differs (`frappe_`, `odoo_`). |
| flake-parts module | `modules/secrets.nix` | `modules/secrets.nix` | The same option tree (`enable`, `dir`, `relDir`, `recipients`, `hostRecipients`, `identityPaths`, `backupAccess`, `extra`, `declared`) under `frappe-nix.secrets` and `odoo-nix.secrets`. frappe-nix adds `sites`. Outputs `flake.lib.frappeSecrets` and `flake.lib.odooSecrets`. |
| Tests | `tests/secrets-cli.{sh,nix}`, `tests/agecheck.sh` | `tests/secrets-cli.{sh,nix}`, `eval-secrets-schema-*` checks | Fixture keys and product names. |

### Shape in the base

- `lib.secrets.schema { lib, varPrefix, projectRoles, groups ? { } }` is the pure schema. `groups` generalises frappe-nix's sites: a named set of units, each with its own roles.
- `lib.secrets.mkModule { optionPath, label, varPrefix, groups, agenix-shell }` returns the flake-parts module that declares the options at the product's path and wires agenix-shell. The product's own `modules/secrets.nix` shrinks to this call plus whatever is truly its own: frappe-nix's `sites` option feeding `groups`, and the `flake.lib.<product>Secrets` output under its current name.
- `lib.secrets.tools { pkgs, label, restoreCommand, bucketExample, verifyListing }` provides agecheck, the rules file, the scripts and `setup-backup-access`. `verifyListing` is the product's snippet for confirming the credentials reach its backups.

### Done when

- The generated rules file, the agenix-shell secret set and the `flake.lib.<product>Secrets` output evaluate identically before and after, in both products and in one real consumer of each (a frappe-nix bench and an odoo-nix project). Compare the evaluated values as JSON.
- Both products' secrets checks pass, and `check-secrets` passes in the real consumers.
- The product copies are deleted.

## Phase 2: the devguard core

devguard keeps a development copy of production from reaching production. It is a Python package grafted into the development virtualenv with a `.pth` file, so every interpreter started from that environment loads it below the application.

The core is the part that knows nothing about the application:

- the post-import hook;
- settings resolution (environment, then an optional runtime file, then baked values, then defaults);
- the patch helpers;
- the two transport-level guards: `egress`, which refuses non-loopback `connect()` with an allowlist, and `mail_stdlib`, which points `smtplib` at the mail catcher and refuses IMAP and POP3.

The guards that patch application APIs stay in the products.

| Piece | Similarity | Difference |
| --- | --- | --- |
| `_hook.py` | 0.99 | Tabs against spaces. frappe-nix has four more copies, in its other grafts. |
| `_settings.py` | 0.65 | frappe-nix adds the runtime-file layer (`$DEVENV_RUNTIME/devguard-runtime.json`) and bakes a Python literal (`_baked.py`). odoo-nix bakes `_baked.json`. Each has its own environment prefix and defaults. |
| `_patch.py` | 0.36 | Shared subset: `warn`, `announce`, `block`, `mark`, `is_guarded`, `no_op`, `blocking`, `require`, `require_any`. frappe-nix adds Frappe helpers (`throw`, `throwing`, `rewhitelist`, `disarm_subclasses`, `redirect_kwargs`, `assert_not_overridden`, `with_in_install`), and its `warn` also writes to Frappe's logger. odoo-nix adds `user_error`. |
| `guards/mail_stdlib.py` | 0.84 | Comments and type-checker annotations. |
| `guards/egress.py` | 0.54 | After frappe-nix#82 and odoo-nix#46 both match a hostname against the name a connection was resolved from. frappe-nix's also has `passage()` (used by its object-store guard), shares its allowlist with the integrations guard, unwraps IPv4-mapped loopback and treats `*.localhost` as local. |

### Shape in the base

- `python/devguard/` contains `_hook.py`, `_settings.py`, `_patch.py`, `guards/egress.py` and `guards/mail_stdlib.py`, all with relative imports so they work under any package name.
- The product supplies `_product.py`, which holds:
  - its package name and environment prefix;
  - its default settings;
  - whether the runtime file is used;
  - two hooks: an extra log sink (Frappe's logger) and a desk-facing error (`frappe.throw`, Odoo's `UserError`);
  - optionally, extra allowlist entries for egress (frappe-nix's `integrations.allowHosts`).
- The product keeps its own guards and its own helpers, which move out of `_patch.py` into a product module such as `_frappe.py`.
- `lib.devguard.mkPackage { pkgs, name, src, settings }` merges the base tree and the product's tree, failing on any file present in both, and writes `_baked.json`. frappe-nix moves from `_baked.py` to JSON, which removes its `toPy` helper.
- `lib.python.withGrafts { python, env, grafts }` is the `.pth` graft, generalised from frappe-nix's `graftFor` and `withGrafts`. odoo-nix's `withDevguard` becomes one call. frappe-nix's other grafts (unixsock, nodebuild, benchcli, journald) take `_hook.py` from here too.
- The base's tests cover the hook, settings precedence, the egress matrix and passage, and the mail transport against stub SMTP and POP3 servers, all with a stub product. They move here from both products' suites.
- The base takes frappe-nix's egress as the starting point, since it is a superset. odoo-nix thereby gains `passage()`, IPv4-mapped loopback and `*.localhost`. All three only allow traffic to the local machine, and none loosens a refusal to a remote host.

### Done when

- frappe-nix's `checks.devguard` and odoo-nix's `checks.devguard`, `checks.devguard-baked` and fixture tests (`odoo-test-*`) pass.
- Environment variable names, log lines (`frappe_devguard[egress] …`), `.pth` file names and option paths are unchanged.
- devguard is still absent from every production environment: by odoo-nix's `odoo-init-*` checks, and by a new frappe-nix check that `prodPythonEnv` carries no `frappe_devguard`.
- The product copies of the core are deleted, including frappe-nix's internal `_hook.py` copies.

Phase 2 starts after frappe-nix#82 and odoo-nix#46 merge, so that the two copies already agree on egress before they become one.

## Phase 3: repository hygiene

- **nix-lint.** Both products run the same check, nixfmt, statix and deadnix over the tree excluding fixtures and templates: frappe-nix in `dev/checks.nix`, odoo-nix in `flake.nix`. Ship it as `lib.checks.nixLint { pkgs, src, ignore }`. `statix.toml` stays in each repository, because tools read it from the root, and the check confirms it matches the base's copy.
- **Dependabot auto-merge.** The two workflows differ only in a comment. Ship a reusable workflow (`workflow_call`). Products call it pinned to a commit, with the version in a comment, and Dependabot's github-actions entry moves the pin. This is how they already consume Avunu/docusystem.
- **wordpress-nix** can adopt this phase immediately, and Phase 1 whenever it starts committing secrets.

## Later candidates

These are not scheduled. Each is worth extracting when its next port comes due.

- **Submodule clone and update.** frappe-nix `lib/apps-report.nix` and odoo-nix `lib/submodules-init.nix` implement the same contract (paired branch, partial clone, no pack pile-up), and it has been ported in both directions twice.
- **btrfs NOCOW for database datadirs** (`lib/db-nocow.nix`, 0.66). The marking logic is generic. Which directories to mark (MariaDB's or PostgreSQL's) stays in the product.
- **OS-level egress.** Run the development processes in a network namespace that has only loopback plus an allowlisting proxy. That would cover what the in-process guard cannot: subprocesses, C extensions, PHP and Node. It would serve all three products, including wordpress-nix, whose PHP also has its own lever (`WP_HTTP_BLOCK_EXTERNAL`, `WP_ACCESSIBLE_HOSTS`). It is Linux-only, while the products also build for macOS, so it would complement the Python guard rather than replace it.

## What stays in the products

| Piece | Similarity | Why it stays |
| --- | --- | --- |
| `modules/devenv.nix` | 0.12 (3,288 against 1,208 lines) | Different services, processes, ports and tooling per stack. |
| `modules/nixos.nix`, `modules/containers.nix` | 0.09, 0.12 | Different services and deployment models. |
| `lib/python.nix` | 0.21 | Both use uv2nix, over different workspace shapes. Only the graft step moves. |
| Application guards (mail, backups, objectstore, webhooks, …) | 0.08 to 0.30 | They patch Frappe or Odoo APIs. |
| Banners, command-line tools, journald formatters, documentation | below 0.3 | Product surfaces. |

## How products consume it

```nix
inputs.nix-dev-base = {
  url = "github:Avunu/nix-dev-base";
  inputs.nixpkgs.follows = "nixpkgs";
  inputs.flake-parts.follows = "flake-parts";
};
```

Products depend on the base. Their consumers (benches, Odoo projects, sites) do not: they reach it through the product's lock, and the product's Dependabot nix group moves the pin, as it already does for the product's other inputs. A product keeps exporting what it exports today, under the same names. For example, frappe-nix's `flakeModules.default` imports the base's secrets module at `frappe-nix.secrets`.

## Releases and compatibility

- Tags `v0.x.y` until both products have adopted Phases 1 and 2, then `v1`.
- **A breaking change** is anything a product's users could see: an option path, an environment variable, a log prefix, a generated file, the layout under `secrets/`. Before 1.0 one ships in a minor release, with a migration note and the product pull requests opened alongside.
- The downstream CI job has to pass before a release.
- Rollback is the product's: it pins a base revision, and reverting the product's adoption commit restores its own copy.

## Migration order

1. Phase 0.
2. Phase 1, with frappe-nix's copy as the starting point (it is the superset: sites). One pull request here, then one per product. Each product pull request shows the before-and-after evaluation comparison.
3. Phase 2, once frappe-nix#82 and odoo-nix#46 have merged. Same pattern.
4. Phase 3, at any point.

## Open decisions

- **License.** The products are MIT and this repository has none yet. Code cannot be moved here from the products until it does.
- **Python style.** Ruff's defaults (four spaces, as odoo-nix writes) or Frappe's tabs (as frappe-nix writes). The base is product-neutral, which argues for Ruff's defaults. Vendored files are not linted by the product, so either works at run time.
- **Baked settings format.** JSON everywhere is proposed. frappe-nix currently writes a Python literal.
- **wordpress-nix.** Adopt Phase 3 now, and Phase 1 when it needs secrets?

## Success looks like

- One copy of each piece above, and the product copies deleted.
- A fix to egress, agecheck or the backup-access tool lands once and reaches every product through a Dependabot bump.
- No user of any product notices the migration.

## Appendix: how the overlap was measured

Files in frappe-nix and odoo-nix were paired by file name. Where a name occurs more than once, each frappe-nix file was paired with its closest odoo-nix match. Lock files, fixtures, `__init__.py`, images and licenses were skipped. Before comparing, the following were normalised away: tabs to four spaces, the words frappe, odoo and bench (case-insensitive), surrounding whitespace, and blank lines. Similarity is Python's `difflib.SequenceMatcher` ratio over the remaining lines, and line counts are those non-blank lines. The comparison was made on 2026-10-16 against frappe-nix at `f470690` (the head of #82) and odoo-nix at `ca7e648` (the head of #46). Pairing by name misses pieces that do the same job under different names, such as the submodule logic, so the shared total is a floor.
