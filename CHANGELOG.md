# Changelog

## [1.2.0](https://github.com/Avunu/python-devops-nix/compare/v1.1.0...v1.2.0) (2026-10-31)


### Features

* **ci:** auto-merge every Dependabot PR with a GitHub App and keep them up to date ([#52](https://github.com/Avunu/python-devops-nix/issues/52)) ([943029b](https://github.com/Avunu/python-devops-nix/commit/943029bfb2b56888c38514e4efce20d34eb9d2bc))


### Bug Fixes

* **deps:** bump Avunu/site-docs/.github/workflows/check-docs.yml ([#54](https://github.com/Avunu/python-devops-nix/issues/54)) ([d5e06f5](https://github.com/Avunu/python-devops-nix/commit/d5e06f5c8776b81689538388e128a6bf859f47ff))

## [1.1.0](https://github.com/Avunu/python-devops-nix/compare/v1.0.0...v1.1.0) (2026-10-31)


### Features

* **node:** lib.node, one JavaScript toolchain for both products ([#48](https://github.com/Avunu/python-devops-nix/issues/48)) ([c07df3e](https://github.com/Avunu/python-devops-nix/commit/c07df3edb5c3ab98d91ca06e7c406fea134b03cf))


### Bug Fixes

* **deps:** bump Avunu/site-docs/.github/workflows/check-docs.yml ([#47](https://github.com/Avunu/python-devops-nix/issues/47)) ([8f91074](https://github.com/Avunu/python-devops-nix/commit/8f91074496a452a567ca5164a7e99afdf6eb5db0))
* **deps:** bump Avunu/site-docs/.github/workflows/check-docs.yml ([#51](https://github.com/Avunu/python-devops-nix/issues/51)) ([b2e9b75](https://github.com/Avunu/python-devops-nix/commit/b2e9b758326d8058e4328d91d223f3d5b47f0ff8))

## 1.0.0 (2026-10-29)


### ⚠ BREAKING CHANGES

* **conventions:** lib.checks.sharedConfig now compares committed.toml. A product's check fails until its committed.toml makes the shared settings and lists every shared scope. Both products carry the list (Avunu/odoo-nix#114, Avunu/frappe-nix#206).
* **backups:** <P>_NIX_MC is <P>_NIX_RCLONE, and a <P>_NIX_BACKUP_SOURCE other than a directory is an rclone target (<remote>:<bucket>[/prefix]) rather than an mc one. The old forms, and a product's legacyEnv.MC name, are refused with what replaced them. lib.backups.fetch takes `rclone` instead of `mc`.

### Features

* **backups:** read the backup store with rclone, not minio-client ([#30](https://github.com/Avunu/python-devops-nix/issues/30)) ([56f8de1](https://github.com/Avunu/python-devops-nix/commit/56f8de17e2716a4fab2c98ca2b878c5486ca1d05))
* **containers:** lib.containers, the image parts both products write by hand ([#36](https://github.com/Avunu/python-devops-nix/issues/36)) ([e1be0b9](https://github.com/Avunu/python-devops-nix/commit/e1be0b999139209356c9b71dc3d47c6afddc4de8))
* **conventions:** env-vars and command-flags, the checks over a product's interface ([#39](https://github.com/Avunu/python-devops-nix/issues/39)) ([ff0186d](https://github.com/Avunu/python-devops-nix/commit/ff0186d171e91a69757467b31e46b8ffc0a3459d))
* **conventions:** lib.checks.conventions, with the CI pin, Dependabot and check-name checks ([#35](https://github.com/Avunu/python-devops-nix/issues/35)) ([564b5ac](https://github.com/Avunu/python-devops-nix/commit/564b5acddf962636205b468c1bc65db80343310c))
* **conventions:** option-paths and config-blocks, the checks over a product's modules ([#38](https://github.com/Avunu/python-devops-nix/issues/38)) ([901ed52](https://github.com/Avunu/python-devops-nix/commit/901ed52fc49385bad7fb8457bb1e73226571e84d))
* **conventions:** own the commit scope list, compared by lib.checks.sharedConfig ([16272e4](https://github.com/Avunu/python-devops-nix/commit/16272e4476263ee06f4b2d9725be5a4074fc40c1))
* **nixos:** nginx configuration builders for TCP and unix-socket deployments ([#29](https://github.com/Avunu/python-devops-nix/issues/29)) ([4c4025f](https://github.com/Avunu/python-devops-nix/commit/4c4025f854078767d0709f80f17fc5e400f39e9d))
* Phase 1 scaffold (flake, CI, Dependabot, CONVENTIONS.md) ([#1](https://github.com/Avunu/python-devops-nix/issues/1)) ([ed2b4e1](https://github.com/Avunu/python-devops-nix/commit/ed2b4e117307dea35cc17df12a63dd13a981fc6c))
* Phase 2, the secrets toolkit (lib.secrets, lib.backups) ([#4](https://github.com/Avunu/python-devops-nix/issues/4)) ([2ea6d8c](https://github.com/Avunu/python-devops-nix/commit/2ea6d8c98ee3ce791c78ab5b2b4f9afd37df146f))
* Phase 3, the devguard core (lib.devguard, lib.python.withGrafts) ([#6](https://github.com/Avunu/python-devops-nix/issues/6)) ([06bf498](https://github.com/Avunu/python-devops-nix/commit/06bf49835f733c2478bc1d9fed03dbdb4755728e))
* Phase 4, the Python environment (lib.python.overrides, rich, lockAudit, buildDeps, threadCaps) ([#7](https://github.com/Avunu/python-devops-nix/issues/7)) ([623a19b](https://github.com/Avunu/python-devops-nix/commit/623a19bb8d4f493f09c4909bd6d671ebef403140))
* Phase 5, the development shell pieces (lib.ports, ciMode, sources, dbNocow, banner) ([#8](https://github.com/Avunu/python-devops-nix/issues/8)) ([e56b3de](https://github.com/Avunu/python-devops-nix/commit/e56b3de2d8647d0f72d055300ec512a99c77435d))
* Phase 6 production pieces (hardening, migrate, sockets, nginx, journald) ([#14](https://github.com/Avunu/python-devops-nix/issues/14)) ([ef72bb9](https://github.com/Avunu/python-devops-nix/commit/ef72bb9958c320def2f6a6658db587635e113c0a))
* Phase 7 repository hygiene (lib.checks.mk, sharedConfig, reusable auto-merge) ([#3](https://github.com/Avunu/python-devops-nix/issues/3)) ([de4a886](https://github.com/Avunu/python-devops-nix/commit/de4a886f3be800585402289dce6ac602af75d7ea))
* **tui:** --only store, the store alone as a product's picker ([#46](https://github.com/Avunu/python-devops-nix/issues/46)) ([1acf472](https://github.com/Avunu/python-devops-nix/commit/1acf4729278f156d7e55b55caf837165635f798a))
* **tui:** lib.tui, a Textual menu for the products' dev shells ([#31](https://github.com/Avunu/python-devops-nix/issues/31)) ([8a6ae19](https://github.com/Avunu/python-devops-nix/commit/8a6ae19c23866654e5c113aa493465a34b053e2c))


### Bug Fixes

* **backups:** keep the download cache owner-only ([#5](https://github.com/Avunu/python-devops-nix/issues/5)) ([62fa6d3](https://github.com/Avunu/python-devops-nix/commit/62fa6d3ecd5f8bec2e9ebd869ac4331081ab958b))
* **deps:** bump the github-actions group across 2 directories with 2 updates ([#45](https://github.com/Avunu/python-devops-nix/issues/45)) ([e7c1e32](https://github.com/Avunu/python-devops-nix/commit/e7c1e32a91f325d47ee9e7218eb5477c78c9a9db))
* **devguard:** name the product's tree without its path ([#13](https://github.com/Avunu/python-devops-nix/issues/13)) ([8ea0fea](https://github.com/Avunu/python-devops-nix/commit/8ea0fea19489a18de7d8fe8b3b7219792b31cb10))
* **sources:** finish a cut-short first checkout, deepen nested clones, explain retired checkouts ([#12](https://github.com/Avunu/python-devops-nix/issues/12)) ([daa0ee4](https://github.com/Avunu/python-devops-nix/commit/daa0ee419329667d07eff16a95b28ad002956e26))


### Performance Improvements

* **python:** byte-compile a command's modules at build time ([#9](https://github.com/Avunu/python-devops-nix/issues/9)) ([c31bdb1](https://github.com/Avunu/python-devops-nix/commit/c31bdb1af014829c381810468f8b5f645db017a7))
