# standards_core/data

Files every product shares. A product's own `data/` directory is searched first, so a
product replaces any of these by shipping a file at the same relative path
(`standards_core.common.data.DataRoots`, DESIGN §4.6).

| Path | Content | Stage |
|---|---|---|
| `README.md` | this table | 1 |
| `schema/core.schema.json` | the core `$defs`: `org`, `retire-item`, `extra-file`, `header-kind`, `core-modules`, `profile-ref`, `fleet-entry-core` | 2 |
| `schema/github-ruleset.schema.json` | the fields and rule types the core's repo-policy rulesets use | 2 |
| `repo-policy/repo-settings.json` | repository settings `repo apply` writes | 8 |
| `repo-policy/rulesets/*.json` | the integration, provisional integration, release-branch and tags rulesets | 8 |

The core ships no manifest fragments, templates or profiles: those are product data.
