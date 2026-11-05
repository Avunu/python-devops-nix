# standards-core

The engine behind a product's "standards" tools: managed files kept in sync from
templates, organisation profiles, CI steps and fleet-wide repository policy. A product
(a Nix engine for some framework) builds its own command line on this package by
describing itself as a `standards_core.product.Product` and calling
`standards_core.cli.main(PRODUCT)`.

[DESIGN.md](DESIGN.md) is the binding contract: the Product API (§3), the data model
(§4), the strategies (§5), the commands (§6) and the implementation stages (§12).

## Layout

| Path | Content |
|---|---|
| `standards_core/product.py` | the product plugin contract |
| `standards_core/cli.py` | the dispatcher a product's console script calls |
| `standards_core/commands/` | the core's subcommands: `ci`, `config`, `data-path`, `pin-path`, `policy`, `profile`, `repo` (`apply`, `audit`, `doctor`, `rollout`, `targets`), `sync` |
| `standards_core/ci/` | the `ci` steps: the auto-merge verdict, relock, dispatch and sweep, the release-branch fast-forward, the post-merge bump, update-branches, drift |
| `standards_core/common/` | exit codes and reports, data roots, schemas and their composition, configuration resolution, `flake.lock`, NAR hashing, pin fetches, GitHub, git, the config file, repo-policy rendering, version skew |
| `standards_core/scaffold/` | the manifest, rendering, the template context, discovery, the changed-defaults warning, globs, the formatter-shaped JSON emitter, semantic comparison, local regions, floors; the engine (the plan), the strategy handlers, the TOML and JSON key groups, fixture checkouts and sync's external steps |
| `standards_core/testing/` | what product test suites use: fakes, the conformance check and the pins check |
| `standards_core/data/` | the data every product shares ([README](standards_core/data/README.md)) |
| `tests/` | the core's suite, run against two stub products in `tests/stubs/` |

## Depending on it

- **pip / uv:** `"standards-core @ git+https://github.com/<owner>/python-devops-nix@<sha>#subdirectory=standards"`
  in the product's `[project].dependencies`.
- **Nix:** `python-devops-nix.lib.standards.package { inherit pkgs python; rev = inputs.python-devops-nix.rev; }`
  in the product package's dependencies.
- **Pins:** `lib.standards.pins` (the product's `standards-core-pins` check) keeps the flake input, that URL and
  the `standards-*` actions' `@<sha>` on one commit; CI adds `python -m standards_core.testing.pins --reachable
  https://github.com/<owner>/python-devops-nix`.
- **CI:** the composite actions in `.github/actions/standards-*` (install, pr-policy, release-ff,
  deps-automerge, update-branches, fleet-audit).

It supports Python 3.11 and later, and has no console script of its own.

## Development

```sh
uv run --no-project --python 3.11 --with-editable standards --with pytest pytest standards/tests -q
nix build .#checks.x86_64-linux.lib-standards          # the suite on every preset interpreter
nix build .#checks.x86_64-linux.lib-standards-neutral  # no product or organisation names
# uv tool install of a product from a git URL, a path and an override (needs the index)
STANDARDS_CORE_INSTALL_CHECK=1 uv run --no-project --python 3.11 --with-editable standards --with pytest \
    pytest standards/tests/test_install.py -q
```
