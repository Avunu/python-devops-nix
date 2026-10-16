# nix-dev-base

Shared building blocks for Avunu's Nix development environments: [frappe-nix](https://github.com/Avunu/frappe-nix), [odoo-nix](https://github.com/Avunu/odoo-nix) and [wordpress-nix](https://github.com/Avunu/wordpress-nix).

It is meant to hold only the pieces that are independent of any one product and expensive to get wrong: the secrets toolkit, the core of the development guard rails (devguard) and repository hygiene. The products' development shells, NixOS modules and containers stay in the products.

Nothing has been extracted yet. See [PLAN.md](PLAN.md) for what moves, in what order, and how products consume it.
