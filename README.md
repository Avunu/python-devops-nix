# python-devops-nix

Shared building blocks and conventions for Avunu's Nix packaging of Python application frameworks, [frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix): the secrets toolkit and backup access, the devguard core, the Python environment fixes, development-shell helpers, the production pieces inside the NixOS modules, and repository hygiene, each kept as one tested copy that both products consume.

-   [Documentation](docs/README.md)
-   [Conventions](docs/conventions.md): how both products name and do the jobs they share.
-   [Library reference](docs/library.md): what the flake exports.
-   [Releases](docs/releases.md): versions, breaking changes, and how the products follow a release.

## Development

```sh
direnv allow              # or: nix develop
nix fmt                   # nixfmt-rs over the tree
nix flake check           # every check, as CI builds it
prek run --all-files      # the hooks CI's lint job runs
```

CI also builds frappe-nix's and odoo-nix's checks against each pull request.

## License

MIT. See [LICENSE](LICENSE).
