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

Only `lib.checks.nixLint` exists so far; nothing has been extracted from the products yet.

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
