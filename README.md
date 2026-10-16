# python-devops-nix

Shared building blocks and conventions for Avunu's Nix packaging of Python application frameworks: [frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix).

It holds the pieces that are independent of either product and expensive to get wrong: the secrets toolkit and backup access, the core of the development guard rails (devguard), the Python environment fixes, small development-shell and NixOS helpers, and repository hygiene. It also holds `CONVENTIONS.md`, which says how both products name and do the same jobs. The products' development shells, NixOS modules and containers stay in the products.

Nothing has been extracted yet. See [PLAN.md](PLAN.md) for what moves, in what order, and how products consume it.
