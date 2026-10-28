---
title: Releases
description: How python-devops-nix is versioned and released, what a breaking change is, and how a product follows a release and goes back from one.
nav_title: Releases
order: 3
tags: [releases, versioning, ci]
updated: 2026-10-28
---

# Releases

python-devops-nix is released with [release-please](https://github.com/googleapis/release-please), as frappe-nix and odoo-nix are. A release is a `vX.Y.Z` tag on `main`, a GitHub release, and an entry in `CHANGELOG.md`.

## How a release is cut

`.github/workflows/release.yml` runs on every push to `main`. release-please keeps one release pull request open, `chore(main): release X.Y.Z`, which moves `version.txt`, `.release-please-manifest.json` and `CHANGELOG.md`. Merging it tags `vX.Y.Z` and moves the `release-<major>` branch to the tag (`release-1` for every `v1.*`). The branch only ever fast-forwards, and a new major starts a new branch.

The version comes from the commits merged since the last release:

-   `fix:` makes a patch release, and `feat:` a minor one.
-   A breaking change makes a major one. It is marked as conventional commits mark it: `!` after the type, and a `BREAKING CHANGE:` footer that says what changed and what a product does about it.
-   `docs:`, `ci:`, `chore:`, `refactor:`, `test:` and `style:` release nothing by themselves.
-   A `Release-As: X.Y.Z` footer sets the version outright. The first release used it.

Dependabot's prefix follows what a bump ships ([Dependabot](conventions.md#dependabot)). The action pins reach the products through the reusable workflow, so their bumps are `fix(deps)` and cut a patch release. The flake inputs and the prek hook revs reach no product, so their bumps are `chore(deps)`: each product `follows` this flake's nixpkgs and flake-parts, and its copy of `.pre-commit-config.yaml` is compared without `rev`.

The products mark a major differently: their `pr-policy` refuses `!` and BREAKING CHANGE, and a major is a deliberate `Release-As: N.0.0` footer ([Commits](conventions.md#commits)). This repository has no `pr-policy` yet ([#21](https://github.com/Avunu/python-devops-nix/issues/21)).

## What a breaking change is

A breaking change is anything a product's users could see: an option path, an environment variable, a log prefix, a generated file, or the layout under `secrets/`. A change that makes a product edit its own code to keep building is one too: a removed or renamed export, a new required argument, or a renamed input of the reusable workflow or an action.

A breaking change ships with a migration note, which is its BREAKING CHANGE footer and so its changelog entry, and with the products' pull requests opened alongside it. Neither product is left on a release it cannot move past.

## Before and after 1.0

The plan was to tag `v0.x.y` until both products had adopted the secrets toolkit and the devguard core, with breaking changes in minor releases. Both had adopted them before the first release, and both already depend on this repository in their own 1.x lines. So there is no 0.x line: the first release is `v1.0.0`, set by a `Release-As: 1.0.0` footer. From 1.0 on, a breaking change ships only in a major release.

## Before a release

The `downstream` job in `check.yml` builds frappe-nix's and odoo-nix's checks against the pull request's head. It has to pass before a release. `main` requires it, with `lint` and `checks`, so the release pull request cannot merge until it passes on the release's own commit.

The release pull request is pushed with `GITHUB_TOKEN`, and a push made with it starts no workflow. `release.yml` dispatches `check.yml` on it instead, while its `checks` check is missing on the head.

## Following a release

A product pins a release, never a branch head:

-   The flake input at a release. `github:Avunu/python-devops-nix/release-1` locks to the latest 1.x release, and Dependabot's nix entry moves the lock only when a release moves the branch.
-   The reusable workflow and the actions at a tag's commit, with the version in a comment, as every other action is pinned: `@<sha> # v1.0.0`. Dependabot's github-actions entry moves both.

Until the first release, both products pin a commit of `main` ([CI](conventions.md#ci)).

## Rollback

Rollback is the product's: it pins a base revision. A product that needs to go back moves its pin to an earlier release, the flake input's lock or the `uses:` commit, and nothing here changes. Tags are never moved or deleted. A bad release is fixed by the next one.
