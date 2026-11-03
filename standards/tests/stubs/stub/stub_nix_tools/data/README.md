# stub_nix_tools/data

The stub product's data root (DESIGN §11.1).

| Path | Content |
|---|---|
| `schema/modules.schema.json` | the stub's modules, their parameters, and the org, profile and checkout keys it adds |
| `schema/fleet-entry.schema.json` | one fleet entry |
| `profiles/` | `minimal` and `recommended@1.0` |
| `manifest.d/` | the managed files, retire rules and floors |
| `templates/` | the templates the manifest renders |
| `repo-policy/self/` | the stub engine repository's own settings and rulesets (`repo apply --self`) |
