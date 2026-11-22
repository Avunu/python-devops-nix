---
title: "Proposal: standards-core hooks for deployment projects"
description: The issue texts filed before the standards-core relock hooks, the deployment profile family and the WordPress backup layout, for wordpress-nix v3's site repositories.
nav_title: "Proposal: deployment hooks"
order: 10
tags: [proposals, standards, backups, wordpress-nix]
updated: 2026-11-21
---

# Proposal: standards-core hooks for deployment projects

wordpress-nix v3 builds its site repositories (one Bedrock project per WordPress site, deployed by the cluster) on standards-core, as odoo-nix builds its tools. Four changes to this repository come first, each with an issue filed before its pull request. The issue texts are below, to be filed as written. Each change is generic: it names no product in `standards/`, defaults to off, and leaves frappe-nix and odoo-nix unchanged.

## Issue 1: `ci.relock_patch_paths` and `ci apply-relock-patch`

**Title:** standards-core: the relock patch check as a core step, with the paths a product names

**Body:**

Each product's relock-push job carries the same shell step, "Check and apply the relock patch": the plan job ran the pull request's code, so its patch is data, and before any write token is minted the push job refuses a rename, copy or mode change, applies the patch to the index, and refuses any entry that is not an added, modified or deleted regular file at a path it pushes, a symlink, or a path under `.github/`. frappe-nix's `app-deps.yml` and odoo-nix's `odoo-deps.yml` each have a copy, which differ only in their `ALLOW` regular expression. wordpress-nix's `site-deps.yml` would be a third.

Proposal:

- `CiConfig.relock_patch_paths: tuple[str, ...] = ()`: the globs (`globs.match`'s dialect) of the paths a relock patch may touch. `Product` refuses an absolute glob, `..`, `.`, an empty segment or `.github`.
- `<cli> ci apply-relock-patch --patch <file>`: the shell step in Python. The patch text first (no `rename`/`copy`/`similarity`/`old mode`/`new mode` header, no mode but `100644`), then `git apply --index`, then each `git diff --cached --no-renames --raw HEAD` entry. A refusal is exit 1 with an `::error::` per problem; a patch that does not apply or changes nothing is refused too. A product that names no paths gets exit 2.

wordpress-nix's paths are `("nix/vendor-hash",)`. frappe-nix's and odoo-nix's shell steps can move to the core step when each next touches its deps workflow; nothing makes them.

## Issue 2: the `verify-relock` callback

**Title:** standards-core: `ci verify-relock`, with the product's check of what the relocked files say

**Body:**

frappe-nix-tools has `frappe-nix ci verify-relock --base <sha> --head <tree> [--repo-dir]`, which reads the lock files of the tree to push and of a commit the integration branch holds, from git only, and decides `ok`, `needs-review` or `reject` ("the URL names the same package and version", never a vendor host allowlist). standards-core has no such step, so the frappe parity harness reports `ci verify-relock: a core step` missing, and odoo-nix pushes its relock without one. wordpress-nix needs one for `composer.lock` (a `wp-plugin/*` dist URL is `downloads.wordpress.org/plugin/<slug>.<version>.zip`, a Packagist dist URL is the GitHub zipball of `source.url` at `source.reference`, a new package is `needs-review`).

Proposal:

- `CiConfig.verify_relock: Callable[[Path, str, str], Verification] | None = None`: `(root, base, head)` → a `standards_core.ci.relock.Verification(rejects, reviews)`, whose `decision()` is `ok`, `needs-review` or `reject` and `reasons()` the lines to print.
- `<cli> ci verify-relock --base --head [--repo-dir .]`: frappe's flags and output. `--base` and `--head` are full object ids (exit 2 otherwise); it prints the decision and each reason, writes `decision` to `$GITHUB_OUTPUT`, and exits 1 on `reject` with an `::error::` per reject. A product with no callback gets exit 2.

## Issue 3: a deployment project profile

**Title:** standards-core: a `deployment@<minor>` built-in profile family, with no release flow

**Body:**

The built-in profiles are `minimal` and `recommended@<minor>`, and both assume a released package: `recommended` turns on `releases` (release-please, tags, a release branch). A deployment project, such as a WordPress site repository that the cluster deploys from `main`, has no release, but wants everything else `recommended` sets: conventional commits, Dependabot with the decision table, CI, the repository policy. Today a product can only give it an org profile, which no built-in can mark as "no releases".

Proposal:

- A second snapshot family, `profiles/deployment@<minor>.toml`, which a product ships only when it has deployment projects. Plain `deployment` means the newest snapshot, as `recommended` does, in resolution, `extends`, `profile list` and the changed-defaults warning.
- A deployment project has no release flow. Conformance refuses a `deployment@*` that turns `releases` on, and resolution refuses a checkout (or an org profile) built on one that turns it on, as a `ConfigError` (exit 2).
- A product with no `deployment@*` sees no change: the built-in names, the `extends` pattern, `profile list` and every error text stay as they are.

wordpress-nix's `wordpress-site` profile is then an org-free `deployment@1.0` with its own managed files.

## Issue 4: a WordPress backup layout for `lib.backups.fetch`

**Title:** lib.backups: ship the WordPress development-restore layout, `wordpress/<slug>/<ts>.sql.gz`

**Body:**

wordpress-nix's `wp restore [--at]` is `lib.backups.fetch` with a layout. Unlike frappe-nix's and odoo-nix's, the naming is not the product's: the cluster's backup writes it (a host-side command after its own logical dump, gzipped and uploaded to `wordpress/<slug>/<ts>.sql.gz`). So the layout belongs beside the fetch layer, where the cluster and the product read one definition.

Proposal:

- `lib.backups.layouts.wordpress` (python/backup_layouts/wordpress.py): one role, `database`; a key `<YYYYMMDD-HHMMSS>.sql.gz` (backup-core's own `stamp`), or `<YYYYMMDD_HHMMSS>.sql.gz`, read one site at a time with the scope `wordpress/<slug>` so `--at` never picks another site's dump; `folders wordpress` lists the sites; an empty dump is listed and refused when picked, before any download.
- `lib.backups.check` runs the layout's unit tests on every interpreter and drives a command built with it.

Found while adding it: the fetch command fails with an `AttributeError` on `fetch` for any layout without `OPTIONAL_ROLES` (`args.roles` is set only by a role's flag). It is fixed in the same pull request.

## Also

- `docs/conventions.md` lists wordpress-nix with `flake` and `smoke` as its gate (it was its OCI build), and records the two conventions above.
- The exit-code table is unchanged.
