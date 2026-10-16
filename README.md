# python-devops-nix

Shared building blocks and conventions for Avunu's Nix packaging of Python application frameworks: [frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix).

It holds the pieces that are independent of either product and expensive to get wrong: the secrets toolkit and backup access, the core of the development guard rails (devguard), the Python environment fixes, small development-shell and NixOS helpers, and repository hygiene. The products' development shells, NixOS modules and containers stay in the products.

-   [PLAN.md](PLAN.md): what moves here, in what order, and how products consume it.
-   [CONVENTIONS.md](CONVENTIONS.md): how both products name and do the jobs they share. Product maintainers read this before adding an option, flag, environment variable or command that the other product also has.

## Layout

The flake is built on flake-parts, and its only inputs are nixpkgs and flake-parts. Each output is read from one directory, so adding a piece means adding a file:

| Path | Output |
| --- | --- |
| `lib/<name>.nix` | `lib.<name>`: functions that take `pkgs` and the product's names as arguments |
| `modules/<name>.nix` | `flakeModules.<name>`: flake-parts modules a product imports |
| `checks/<name>.nix` | `checks.<system>.<name>`, built in CI |

Code that is not Nix sits beside it: `python/` (standard library only, written to 3.11), `sh/` (bash glue), and `tests/`, with the stub products and fixtures the checks run against.

What it provides so far, the secrets toolkit (PLAN.md, Phase 2) and repository hygiene (Phase 7):

| Piece | What it does |
| --- | --- |
| `lib.secrets.schema { varPrefix, projectRoles ? [ roles.backupAccess ], groups ? { } }` | The pure derivation of a product's secret set: `.age` paths, recipients, shell variables, the agenix-shell secret set and the rules JSON. `groups` are named sets of units with roles of their own (frappe-nix's sites) |
| `lib.secrets.mkModule { optionPath, label, varPrefix, projectRoles ? …, groups ? { }, agenix-shell, libName ? "secrets", libAliases ? [ ] }` | The flake-parts module: the option tree (`enable`, `dir`, `relDir`, `recipients`, `hostRecipients`, `identityPaths`, each project role, each group, `extra`, `declared`) at the product's path, agenix-shell (the product's input) wired to it, and `flake.lib.secrets` |
| `lib.secrets.tools { pkgs, cfg, label, optionPath ? "<label>.secrets", restoreCommand, bucketExample, verifyListing }` | agecheck (the recipient-drift check), the rules file, `edit-secret`, `rekey-secrets`, `check-secrets` and `setup-backup-access` as devenv scripts (`.scripts { fetch ? null }`), and the on-demand decryption snippets |
| `lib.backups.fetch { pkgs, label, envPrefix, layout, legacyEnv ? { } }` | `<label>-backup-fetch {list,folders,fetch}`: list, download and cache backups over S3 (minio-client) or from a directory. `layout` is the product's parser from an object listing to backup sets (python/backups.py, `Layout`) |
| `lib.backups.module` | python/backups.py, for a product that imports the fetch layer in-process |
| `lib.backups.check { pkgs, product ? …, interpreters ? … }` | The fetch layer's tests, on every interpreter a preset names |
| `lib.python.interpreters { presets ? [ ] }` | The interpreters the products' presets files name, for a test matrix |
| `lib.checks.mk { pkgs, product }` | The helper a product registers its checks with: `script`, `eval` and `nixosTest`, each named `<product>-<x>-check` (CONVENTIONS.md, Tests) |
| `lib.checks.nixLint { pkgs, src, product, ignore ? [ ], compareStatix ? true }` | nixfmt-rs, statix and deadnix over the tree, and `statix.toml` compared with this repository's copy |
| `lib.checks.sharedConfig { pkgs, src, product, files ? [ … ], exceptions ? { } }` | The product's `.editorconfig`, `.pre-commit-config.yaml` and `ruff.toml` carry this repository's settings (CONVENTIONS.md, Repository files) |
| `.github/workflows/dependabot-auto-merge.yml` | A reusable workflow that auto-merges Dependabot pull requests by a policy the caller passes (CONVENTIONS.md, CI) |
| `.editorconfig`, `.pre-commit-config.yaml`, `ruff.toml`, `statix.toml` | The shared copies those checks compare with |

## Development

```sh
direnv allow              # or: nix develop
nix fmt                   # nixfmt-rs over the tree
nix flake check           # every check, as CI builds it
prek run --all-files      # the hooks CI's lint job runs
```

CI also builds frappe-nix's and odoo-nix's checks against each pull request, once a product takes this flake as an input.

## License

MIT. See [LICENSE](LICENSE).
