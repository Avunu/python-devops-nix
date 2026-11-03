# kit_nix_tools/data

The kit stub product's data root (DESIGN §11.1).

| Path | Content |
|---|---|
| `schema/modules.schema.json` | the kit's modules and the checkout keys it adds (`shape`, `series`) |
| `schema/fleet-entry.schema.json` | one fleet entry, with its `shape` and `branches` |
| `profiles/` | `minimal` and `recommended@1.0` |
| `manifest.d/core.json` | the dev shell (`flake.nix` inputs block, `.envrc`, `.gitignore` blocks, a workspace's `pyproject.toml` dependency block), the editor's `.vscode` leaves, `package.json` and `tools/pyproject.toml` key groups, `.pre-commit-config.yaml`, the `setup.cfg` retire rule, floors |
| `manifest.d/ci.json` | `.github/dependabot.yml` (one entry per ecosystem and branch) and the CI workflow |
| `templates/` | the block bodies, the seed flake, the merge specs and the whole files |
