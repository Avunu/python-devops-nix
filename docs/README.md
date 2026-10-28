---
title: python-devops-nix documentation
description: The shared building blocks and conventions behind frappe-nix and odoo-nix.
nav_title: Overview
order: 0
tags: [nix, overview, conventions]
updated: 2026-10-28
---

# python-devops-nix documentation

python-devops-nix is the shared base of Avunu's Nix packaging for Python application frameworks, [frappe-nix](https://github.com/Avunu/frappe-nix) and [odoo-nix](https://github.com/Avunu/odoo-nix). It holds the pieces that do not depend on either product and are expensive to get wrong: the secrets toolkit and backup access, the devguard core, the Python environment fixes, development-shell helpers, the pieces inside the NixOS modules (unit hardening, migrate on deploy, sockets, nginx and journald logging), the parts the container images share (their labels, user and healthcheck), and repository hygiene. The products' development shells, NixOS modules and container images stay in the products.

It is for the maintainers of those products. If you deploy or develop with frappe-nix or odoo-nix, start with that product's documentation instead.

-   [Conventions](conventions.md): how both products name and do the jobs they share, and where each stands. Read it before adding an option, flag, environment variable or command that the other product also has.
-   [Library reference](library.md): what the flake exports, and the design rules it follows.

Open work is tracked in the [issues](https://github.com/Avunu/python-devops-nix/issues).
