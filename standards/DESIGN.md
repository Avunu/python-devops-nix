# standards-core: design contract

Status: binding for the implementation stages (§12) and for product plugin authors. Written 2026-11-03
against frappe-nix-tools 2.2.1 (`py/frappe_nix_tools/frappe_nix_tools/`, read only) and the approved plan
(`your-suggested-approach-makes-scalable-toucan.md`, "Amendments (2026-11-03)", A2-A5, B1-B6).

Revised the same day after an independent review: §13 lists each critique item and where it was resolved.

`standards-core` (import `standards_core`) is a pip-installable distribution at `python-devops-nix/standards/`.
It is a generalised **copy** of frappe-nix's app-standards engine. Every Frappe concern sits behind a `Product`
object (§3). odoo-nix is the first product. frappe-nix moves onto the core later, in a separate coordinated
phase. Nothing in frappe-nix changes in this round.

This file is the only file under `standards/` allowed to name products or organisations. `lib-standards-neutral`
(§10) excludes it.

Terms used below:

- **product**: the plugin, such as odoo-nix-tools or a test stub;
- **engine**: the product's Nix flake, the thing a repository locks as a flake input (`odoo-nix`);
- **checkout**: the repository being synced. frappe-nix calls it "app";
- **target**: one repository in the fleet;
- **frappe:** a frappe-nix-tools path relative to `py/frappe_nix_tools/frappe_nix_tools/`.

---

## 1. Scope and non-goals

### 1.1 In the core, v1

| Area | Contents |
|---|---|
| Common | exit codes, errors and the report formats; the JSON-Schema subset validator and schema composition; profile resolution (built-ins, org, in-repo, merge, defaults, module rules, `requires-<product>`); `pyproject.toml` table reading and the opt-in spelling rule; git helpers; `flake.lock` reading; NAR hashing; pin fetch (`pin-path`); the GitHub transport; repo-policy rendering; skew rows |
| Scaffold | manifest fragments; Jinja rendering (sandboxed, multi-root); the `when` evaluator; the plan and engine; history and retraction; retire rules; floors; globs; jsonfmt; semantic compare; local regions; the defaults warning; the two sync phases (bootstrap); the generic TOML key-group merge machinery; **new**: the generic JSON key-group merge (`JsonGroups`), anchored sentinel blocks, the Nix attrset scanner and the JSONC editor |
| CI | auto-merge verdict (the table is data the product supplies), auto-merge under the safety rule, relock input/patch/refusal, dispatch-ci and sweep, the fast-forward guard, update-branches, drift issues; **new**: conventional-commit parsing and the develop+series post-merge bump |
| Commands | `config`, `profile show/validate/list`, `data-path`, `pin-path`, `policy`, `ci <step>`, `sync`, `repo apply/audit/rollout/doctor/targets` |
| Packaging | `standards/pyproject.toml` (flit), `lib/standards.nix`, the checks `lib-standards` and `lib-standards-neutral`, and six composite actions |
| Testing | `standards_core.testing` (conformance, pins, fakes), plus the core's own suite against two neutral stub products |

### 1.2 Frappe-only: not copied, and the product supplies them later

| frappe module | Why it stays |
|---|---|
| `listing/*`, `commands/listing.py`, `icon/*`, `commands/icon.py` | Marketplace listing, README blocks (`listing/readme.py`), semgrep baseline (`listing/baseline.py`), icons. The README `blocks` handler and the `semgrep-baseline` seed validator become product handlers (§5.1). |
| `app_tools/*`, `bench/*`, `bench_cli.py`, `apps/*`, `apps_catalog.py`, `frappe_init/*`, `restore/*`, `deps/*` | Product CLIs and the bench. |
| `scaffold/configs.py` (oxlint, tsconfig, release-please config generators, `Gen`) | Frappe tool configs. They reach templates through `Product.hooks.template_globals` (the `gen` helper). |
| `scaffold/package_json.py` | `package.json` json-merge with Frappe scripts and floors, `yarn.lock` keys, stylelint. Its merge, retract and history machinery is generalised as `JsonGroups` (§5.3); the Frappe groups stay here. |
| `scaffold/tomlmerge.py`: `groups`, `BENCH_NEW_APP`, `bench_baseline`, `static_whitelist`, `CANON_TABLES`, `HEAD_TABLES`, `KEY_ORDER`, `problems` | The Frappe key groups. The machinery around them is generalised (§5.3). |
| `scaffold/blocks.py` (`gitignore`, `init_py`, `init_py_unblocked`) | Frappe's legacy markers (`# >>> frappe-nix >>> (managed block …)`) and the `__version__` block. The new sentinel blocks (§5.6) replace them for new products. frappe-nix keeps them as product handlers until it migrates. |
| `scaffold/ranges.py`, `scaffold/hooks.py`, `common/known_apps.py`, `data/known-apps.json` | `hooks.py` literals, sibling apps, `[tool.bench.frappe-dependencies]`. |
| `scaffold/discover.py` (all Frappe facts), `scaffold/fixtures.py` (`CONTEXTS`) | These become `Product.hooks.discover` and `Product.fixtures`. |
| `commands/gate.py`, `commands/compat.py`, `commands/ratchet.py`, `commands/testmap.py`, `commands/test_report.py`, `commands/unchecked_js.py`, `commands/minibench.py`, `commands/apps_catalog.py` | Gates and checks with Frappe semantics. A product adds its own as `extra_commands` (§6.1). compat runs inside pr-policy through `Product.hooks.pr_checks`. |
| `common/gh.py` | It has no user in the copied modules (only a docstring mention in `common/github.py`). `common/github.py` replaces it, so `test_gh.py` is not ported either; `test_github.py` (§11.3) tests `github.py` directly. |
| `data/node-locks/**`, `data/ci/nightly.sh`, `data/semgrep/**`, `data/tsconfig/**`, `data/app-template/**`, `data/frappe-apps.json`, `data/schema/frappe-test-report.schema.json`, and every template and manifest fragment | Product data. |

### 1.3 Non-goals for v1

- No frappe-nix migration. The shims, the S0 harness and S1-S5 come later. The core is built so that frappe-nix *can*
  be byte-identical on it: every Frappe string becomes a product value with the same text (§2, "Frappe value").
  That includes what the S0 harness compares: report JSON, header lines, `profile show`, first-opt-in text and
  `--help` (§6.1). The frappe parity harness (§11.3) checks this from v1 on, outside `standards/`.
- No reusable workflows in python-devops-nix. Only composite actions; the job graph stays in each product (A5).
- No tags or GitHub releases for the develop+series flow (B3 "Deferred").
- No Nix evaluation inside `--check` (unchanged: `--check` runs no `nix`, `uv`, `npm` or `yarn`).

---

## 2. File-by-file inventory

Python 3.11 rules apply to every core file:

- ruff defaults with `target-version = "py311"` (repository `ruff.toml`): 4-space indentation and line length 88.
- Each copied file is re-indented (tabs → 4 spaces) and reformatted with `ruff format`.
- Lines over 88 are wrapped. The long `raise ConfigError(f"…")` messages become implicit string concatenation, with the
  text unchanged.
- Under `ast.parse(..., feature_version=(3, 11))` the copied frappe modules parse cleanly (checked).
- Only one frappe call needs a 3.11 guard: `tarfile.extractall(..., filter="tar")` in `common/pins.py`. `filter`
  exists from 3.11.4. The core passes it only when `hasattr(tarfile, "tar_filter")`, and otherwise raises
  `EnvError("this Python lacks tarfile filters (3.11.4+)")`.
- `datetime.UTC`, `str.removeprefix`, `X | Y` in `isinstance` and `tomllib` are all 3.11-safe.
- Never use PEP 695, `itertools.batched`, `typing.override` or 3.12 f-string quote reuse.
- A dataclass field default must be hashable on 3.11. `MappingProxyType` is not hashable there (it is on 3.12), so
  `@dataclass` refuses it as a "mutable default". Mapping fields use `field(default_factory=…)` (`mapping_field()`,
  §3.1). `lib-standards` runs the suite on python311, and `test_product` imports `product.py` and builds every
  dataclass with its defaults, so a regression fails there.
- Every copied file is rewritten to the neutrality rule on copy (§10.2): docstrings, comments, `$comment` texts and
  test strings that name a product or organisation are reworded, never kept "for now".

Every function that needs product data takes `product: Product` as its first parameter. **No module-level
product state exists.** Caches are `functools.cache` on functions whose first argument is the product.
`Product` is `@dataclass(frozen=True, eq=False)`, so it hashes by identity and two products never share a cache
entry.

"Frappe value" in the tables below means the product value frappe-nix will pass to stay byte-identical.

### 2.1 Package root

| Core file | Source | Copied as is | Generalised |
|---|---|---|---|
| `standards_core/__init__.py` | frappe `__init__.py` | the release-please `__version__` block | adds `revision() -> str \| None` (§9.4) |
| `standards_core/product.py` | **new** (A1 seams) | — | the whole API of §3 |
| `standards_core/cli.py` | frappe `cli.py` | the try/except ladder: `StandardsError` → its code; any other exception → 3, with the traceback under `<PREFIX>_DEBUG` | `main(product, argv, extra_commands)`. `prog` = `product.identity.cli`; description = `identity.description` (default built from `identity.docs_url`). Commands come from a list, not from `pkgutil`, ordered by `Command.sort_key` (§6.1) |

### 2.2 `standards_core/common/`

| Core file | Source | Copied as is | Generalised / replaced |
|---|---|---|---|
| `report.py` | frappe `common/report.py` | the exit codes, `STATUS`, `Finding`, `worst`, the annotation escaping, `::stop-commands::` | `FrappeNixError` → `StandardsError` (`ConfigError`, `EnvError` subclass it). `render(findings, fmt, code, engine, *, product)`: the JSON key `"frappe_nix"` becomes `product.identity.report_key`, in frappe's position (§6.4); the last text line becomes `f"{identity.name}: {n} file(s) drifted — run \`{identity.sync_command}\`"` / `f"{identity.name}: clean"` |
| `data.py` | frappe `common/__init__.py` (`data_path`) | the path rules (relative, no `..`, not zipped, exists) | `DataRoots` (§4.6). `data_path(product, rel)` searches the product root, then the core root |
| `schema.py` | frappe `common/schema.py` | the keyword subset and its error texts; `deref`, `_ecma`, `defaults`, `node_at`, the `x-app-only` keyword | `load(product, name)` goes through `DataRoots`, and composed documents are registered by name (§4.1). The `$ref` file part resolves through the roots (product first), so a core def can reference a product file and the other way round. The cache key is `(product, name)` |
| `compose.py` | **new** | — | builds the effective `profile.schema.json`, `app.schema.json` and `fleet.schema.json` (§4.1) |
| `config.py` | frappe `common/config.py` | `Resolved` (`source`, `get`); `_merge`, `_record`, `_fill_defaults`, `_switch`, `_app_turns_off`, `_modules` (needs loop, provisional rule, host-off rule); `read_profile_dir`, `org_profile_dir` (symlink refusals, prefix rule); `resolve_doc` and `resolve` | `MODULES`/`NEEDS`/`GITHUB_ONLY`/`PROFILE_ONLY`/`APP_ONLY`/`RESOLVED_FROM_PROFILE` → `product.modules.*` united with the core's own keys (§4.2). `__version__` → `product.identity.version`. `requires-frappe-nix` → `identity.requires_key`. The `listing.publish` special case → `hooks.host_rules`. The `site` default and the `integration-branch` default → `hooks.derive(cfg, checkout)` / `hooks.integration_default`, in frappe's order (integration branch, then derive, then `repo`: §4.4), so `cfg`'s key order is unchanged. `_repo` uses `org.github-owner` + `checkout name` (core). `snapshots` read `profiles/` through `DataRoots`. `NotOptedIn` takes the product's hint |
| `pyproject.py` | frappe `common/pyproject.py` | `read`, `parse`, `load`, `project_name`; the opt-in line regex shape | `TABLE` → `identity.table`. `_OPT_IN_LINE` is compiled per product: `r"[ \t\n\v\f\r]*\[{1,2}tool\." + re.escape(table) + r"[].].*"`. `tool_frappe_nix` → `tool_table(product, doc)`. `OPT_IN_HINT` → `identity.opt_in_hint`. `load(product, root)`: a missing `identity.config_file` is `EnvError` (exit 3, frappe) when `identity.config_required`, else the empty document `{}`, which is "not opted in" (exit 2 with the hint) and which `--standards` creates |
| `repo.py` | frappe `common/repo.py` | `git`, `toplevel`, `find_up`, `ls_files`, `parse_remote`, `origin_repo`, `NO_AUTO_MAINTENANCE` | `app_name` (which checks for `hooks.py`) is dropped. `Product.hooks.load_checkout` names a checkout |
| `flakelock.py` | frappe `common/flakelock.py` | `Pin`, `parse`, `load`, `node_at`, `locked_pin` validation, `locked_rev`, `PROFILE_INPUT = "standards-profile"` | `FRAPPE_NIX_PINS` → `product.pins.engine_pinned`. The `"frappe-nix"` node → `product.engine.flake_input`. `frappe_nix_rev` → `engine_rev(product, lock)`. New: `base_rev(product, lock)` = the rev at `[engine.flake_input, engine.base_input]` (§9.4) |
| `nar.py` | frappe `common/nar.py` | all | — |
| `pins.py` | frappe `common/pins.py` | fetch, redirect handling, token rules, git clone, `copy_tree`, `_holds`, `pin_path` | `PINS_DIR` → `product.pins.dir`. `FRAPPE_NIX_FETCH_TOKEN` / `FRAPPE_NIX_PIN_URL` → `f"{env_prefix}_FETCH_TOKEN"` / `f"{env_prefix}_PIN_URL"`. The `filter` guard above |
| `github.py` | frappe `common/github.py` | `GitHubError`, `Result`, `Transport`, `use`, `request`, `get`, `get_or_none`, `cli`, `required_contexts`, `ACTIONS_APP_ID` | `gate_contexts(product, gates)` uses `product.ci.check_context` (Frappe value `"ci / {gate}"`). New: `check_state(repo, sha, contexts) -> Literal["green", "pending", "failed"]` (§8.2). The frappe docstring that names `gh.py` is reworded |
| `repo_policy.py` | frappe `common/repo_policy.py` | `Policy`, `fill`, `_settings`, `_strip_status_checks`, `from_dir`, `ruleset_files`, `bypass_actors`, the placeholder grammar | `GATE_ORDER` → the order of `product.ci.gates`. `LABEL_MODULES` → `product.ci.labels`. `self_dir` → `repo-policy/self` in the product root. `bypass_actors` also accepts `integration:<id>` (an App: `actor_type: "Integration"`). `values()` adds `release_pattern`, `release_label`, `strict_checks` and `integration_bypass`, then merges `hooks.repo_policy_values` (§7.3). `for_app` → `for_target(product, …)`, which picks files with `hooks.rulesets` |
| `repoconfig.py` | frappe `common/appgates.py` | `AppConfig` fields → `RepoConfig(root, name, resolved, facts, gates)` | `load(product, root)` loads the checkout, resolves, and computes `facts` (core + `hooks.discover`) from the checkout. `gates_for` calls `hooks.gates`. `release_branch` → `hooks.release_branch(cfg, modules, facts)`, else `Branching.release_branch(cfg, facts)`. **Resolves the blocker** appgates → `scaffold.context`/`discover` |
| `skew.py` | frappe `scaffold/engine.py` `skew_problems` | the caller-SHA and expect-rev rows | the caller regex comes from `engine.caller_stem` and the engine owner/name. Adds the core-revision row (§9.4). `FRAPPE_NIX_ALLOW_SKEW` → `<PREFIX>_ALLOW_SKEW` |

### 2.3 `standards_core/scaffold/`

| Core file | Source | Copied as is | Generalised / new |
|---|---|---|---|
| `globs.py` | frappe `scaffold/globs.py` | all (no brace syntax: `match` is unchanged) | New: `expand_braces(pattern) -> list[str]` (`a.{x,y}` → `a.x`, `a.y`; nested braces and an unbalanced brace are `ManifestError`). Only product data is expanded, at manifest load (§4.3); `match` never sees braces |
| `jsonfmt.py` | frappe `scaffold/jsonfmt.py` | the algorithm (expanded objects, one-group arrays, the fit rule) | `TAB_WIDTH`, `PRINT_WIDTH` and the tab/trailing-comma choices become a `JsonStyle` argument (§3.1 `Product.json_style`). The default `JsonStyle()` is frappe's (oxfmt's form), so frappe output is unchanged; a product whose formatter is prettier sets its own (odoo: 2 spaces, width 80, no trailing commas). A JSONC merge target created from nothing is `dumps(value, product.json_style, jsonc=False)` (§5.5) |
| `semantic.py` | frappe `scaffold/semantic.py` | `parse`, `_jsonc`, `equal` | `kind(product, path)`: the JSONC set is `*.jsonc` plus `product.jsonc_globs`, matched with `globs.match` on the full path. Frappe value `("**/tsconfig*.json",)` (frappe matches the basename anywhere, so `portal/tsconfig.json` too; `**/` also matches the root); for odoo `(".vscode/*.json",)` |
| `regions.py` | frappe `scaffold/regions.py` | `parse`, `splice`, `bodies`, `filled` and their errors | the marker regex is built from `markers.local` and the header kind's leader (`#` or `//`). `check_redefinitions(path, text, header, managed_inputs)` moves here from engine `_check_regions`: hook ids, dependabot `updates` entries keyed on `(ecosystem, directory, target-branch)` (§5.2), ini sections, and Nix inputs through `nixattrs` |
| `floors.py` | frappe `scaffold/floors.py` | all | — |
| `render.py` | frappe `scaffold/render.py` | the sandbox, `StrictUndefined` / trim / lstrip / keep_trailing_newline, `_NoLinkLoader`, `template_files`, `render_string`, `evaluate` (the `when` evaluator, unchanged), the filters `json`, `jsonc`, `json_string`, `nix_string`, `nix_attr`, `yaml_json`, `glob_to_regex`, and the `rev`/`pin` helpers | `MARKER`/`BASE`/`header()` → `header(product, kind, local, note)` (§5.1). `environment(product)` uses a `ChoiceLoader` over the roots (§4.6). `render(product, template, ctx, current, *, directory)` builds its helpers per render over the final context, as frappe does: `rev`, `pin`, plus `hooks.render_helpers(ctx, current)` (Frappe value: `{"gen": configs.Gen(ctx)}`; the **render → configs** blocker). A helper name that shadows `rev`, `pin` or a context key is `RuntimeError`. `url_template` → `{key}` substitution from `hooks.url_placeholders(ctx)`, an **ordered** sequence of `(key, value)` pairs applied in order (Frappe value: `app_hyphen`, `app`, `repo`, so `{app_hyphen}` is replaced before `{app}`). Extra filters come from `product.jinja_filters` |
| `manifest.py` | frappe `scaffold/manifest.py` | `Entry`, `Retire`, `Manifest`, `parse` (duplicate refusal, floors merge), `profile_retire`, `extra_entries` | the module check uses `product.modules.names`; `STRATEGIES` adds `jsonc-merge` and `sentinel`; `HEADERS` comes from the core kinds plus `product.header_kinds`. New entry keys (§4.3). Duplicates are keyed on `(path, block)`. Retire `paths`/`except` are brace-expanded on load; `only_section` is read as `only_sections: [it]`. `VITE_REGISTER` is dropped, and `ships(product, path)` stays generic. **Resolves the blocker** manifest → `config.MODULES` |
| `context.py` | frappe `scaffold/context.py` | `NS`, `ns`, `ShallowHistory`, `History`, `ever`, `first_commit_year` | `App` → `Checkout`. `build()` makes only the core keys (§3.5) and merges `hooks.template_globals` (context keys only; per-render helpers such as `gen` are `hooks.render_helpers`, see `render.py`). Dropped from the core: `PYTHON`, `FRAPPE_NIX_REPO`, `GATES`, `COMPAT_MODULES`, `siblings`, `tools_deps`, `app_version`, every `*_shipped` flag, `vue_typescript5`, `package_json_live`, `precommit_live`. `UNMANAGED_DIRS` → `product.paths.not_source`. `frappe_nix_major` → `engine_major(version)`. `default_frappe_nix_url` → `engine_url(product, cfg)`. **Resolves the blockers** context → `known_apps`, `discover`, `hooks` and `manifest.ships`, and the `policy_shipped` `find_spec` (the plan's hidden dependency becomes a product flag through `template_globals`) |
| `discover.py` | frappe `scaffold/discover.py` | `gitmodules`, `github_workflows` | `facts(product, checkout, cfg, modules) = {**core facts, **hooks.discover(checkout, cfg, modules)}`. A product key that shadows a core key is a `RuntimeError` |
| `defaults.py` | frappe `scaffold/defaults.py` | `snapshot_at`, `changes`, `_flat`, `warn_changed` | `_CALLER` is built from `engine.caller_stem`. Messages use `identity.name` and `identity.table` |
| `fixtures.py` | frappe `scaffold/fixtures.py` + `commands/profile.py` `_throwaway` | the throwaway git repository (isolated env, no hooks, no signing) | `Fixture` dataclass (§3.4); `throwaway(product, root, profile_dir, name, fixture)` calls `hooks.write_fixture` |
| `tomlmerge.py` | frappe `scaffold/tomlmerge.py` | `_table_text`, `_after_tools`, `_table_position`, `_headers`, `_insert_at`, `_place_tables`, `_put_key`, `_insert_tables`, `_value_text`, the merge loop, `managed_view`, `prune` | `TomlGroups` (§5.3) carries `groups`, `on`, `canon_tables`, `head_tables`, `key_order`, `baseline`, `reconcile`, `extra`, `problems`. `add_tool_frappe_nix` → `add_table(product, text, values)`. The turn-off warning names the entry's path, not a hard-coded `pyproject.toml` (the text is otherwise frappe's, so frappe's output is unchanged for its `pyproject.toml`) |
| `jsonmerge.py` | **new**, the generic part of frappe `scaffold/package_json.py` | the merge, retract-when-equal-and-was-on, turn-off warning and history logic | `JsonGroups` and `JsonGroupsHandler` (§5.3), the JSON counterpart of `TomlGroups`; output through `jsonfmt` with `product.json_style` |
| `nixattrs.py` | **new**, from engine's `_FLAKE_*` regexes, `flake_input_urls`, `flake_input_specs`, `input_spec`, `locked_spec`, `strip_nix_comments` | `input_spec`, `locked_spec`, `strip_nix_comments` unchanged | an indentation-inferring attrset scanner over a Nix lexer (§5.6.2). `flake_input_specs(text)` is reimplemented on it with frappe's input rule plus `self` excluded (§5.6.4), and gives identical results on frappe's 2/4/6-space flakes |
| `jsonc.py` | **new** | — | the JSONC CST editor (§5.5) |
| `blocks.py` | **new** (frappe's `blocks.py` stays Frappe) | — | anchored sentinel blocks (§5.6) |
| `handlers.py` | **new**, from engine `_entry_item`, `_retract`, `_absent`, `_whole`, `_seed_text`, `_validate_seed`, `blame_problems`, `_first_opt_in` | the behaviour of each strategy branch | the `Handler` protocol, the base classes per strategy and the registry (§5.1). **Resolves the blockers** engine → `listing.baseline`/`readme`, `package_json`, `tomlmerge` |
| `engine.py` | frappe `scaffold/engine.py` | `Item`, `Plan`, `inside`, `unmanaged_dir`, `read`, `profile_locked`, `resolve` (bootstrap/provisional), `_in_repo_dir`, `_committed_profile`, `_profile_at`, `_has_github_dir`, `_committed_doc`, `previous`, `history`, `profile_templates`, `check_overrides`, `check_uses`, `_retired`, `_rule_applies`, `_requirement_names`, `_missing_deps`, `_was_on`, `_on_at_head`, `entries_for`, `stale_inputs`, `lock_problems`, `untracked_locks`, `untracked_lock_problems` | `build(product, root, *, only, options, phases, profile_dir, standards, engine_lock, args)`. `new_config` → `hooks.new_target_table`. `with_integration_branch` uses `hooks.integration_default`. `hooks.checks(plan)` items are added to the plan's items in `build`, so `--check` and phase B's final report both see them (§6.3). `check_excludes`/`check_preset` → `hooks.preflight`. `check_frappe_nix_url` → core `check_engine_url` (keyed on `engine.url_key` and module `ci`). `dependabot_ignore_notices` → `hooks.notices`. `GITHUB_MARKS` → `product.paths.github_marks`. `UNMANAGED_DIRS` → `{".git"} \| product.paths.never_write`. `LOCK_PATHS` → `("flake.lock", *product.pins.lock_paths)`. `MANAGED_INPUTS` → `product.pins.managed_inputs(ctx)`. `APP_TEMPLATE`/`legacy_app_files` → `product.first_opt_in` (§5.9). `locked_frappe_nix` → `locked_engine(product, root)`. `flake_facts`/`_sibling_entry`/`load_package`/`effective_version`/`_spa_config`/`_yarn_lock_missing`/`_js_oxc`/`uv_floors`/`check_excludes`/`check_preset` are Frappe-only. **Resolves the blockers** engine → `listing`, `tomlmerge`, `package_json`, `context`, `discover`, `hooks`, `known_apps` |
| `bootstrap.py` | frappe `scaffold/bootstrap.py` | `Runner`, `report_result`, `handover` (result-file protocol), `lock_reasons`, `phase_a_lock`, `release_branch_exists`, `release_branch_rev`, `require_release_branch`, `maybe_reexec`, `ensure_tools` | env names → `<PREFIX>_SYNC_RESULT`, `<PREFIX>_SYNC_LOCK_CHANGED`, `<PREFIX>_SYNC_REEXEC`, `<PREFIX>_SYNC_REENTERED`, `<PREFIX>_URL_OVERRIDE`, `<PREFIX>_ALLOW_SKEW`, `<PREFIX>_CI`, `<PREFIX>_LOCKED_REV`. `FRAPPE_NIX_REPO` → `https://github.com/{engine.repo}`. The re-exec argv → `sync.reexec_argv`; the re-entry argv → `sync.reentry_argv`; `uv`/`yarn` → `sync.required_tools`; the pre-re-entry relock → `sync.before_reentry`. `NODE_LOCKS`/`seed_node_locks` → Frappe's `sync.phase_b_after`. `version.txt` → `engine.version_file` |

### 2.4 `standards_core/ci/`

| Core file | Source | Copied as is | Generalised / new |
|---|---|---|---|
| `__init__.py` | frappe `ci/__init__.py` | `output`, `notice`, `error` | `LABELS` → `product.ci.labels` |
| `verdict.py` | frappe `ci/verdict.py` | `AUTO/REVIEW/RELOCK/CLOSE`, `POLICIES`, `names`, `_matches`, `_minor_or_patch`, `nix_verdict` | `_table` → a first-match evaluator over `product.ci.decision_table` (§8.3). `TEST_UTILS`, `PILOT_PATTERNS` and `frappe_nix_repo` become rule data or parameters |
| `automerge.py` | frappe `ci/automerge.py` | `ensure_label`, `label`, `auto_merge_enabled`, `enable`, `close`, `apply` | `DOCTOR` names `f"{cli} repo doctor"`; `close`'s text names `rollout_inputs` and `f"{cli} repo rollout"` |
| `relock.py` | frappe `ci/relock.py` | `_lock_doc`, `input_node`, `moved_inputs`, `merge_base`, `lock_at`, `workflow_paths`, `make_patch`, `changed`, `WORKFLOW` | `ROLLOUT_INPUTS` → `product.rollout_inputs` = `(engine.flake_input, "standards-profile")` |
| `release.py` | frappe `ci/release.py` | `release_branches`, `_has_check`, `_workflow_exists`, `ensure_ci`, `_pr_list`, `dispatch_ci`, `sweep`, `BOTS`, `FastForward.peeled/_write/run` and its 403 text | `CI_CONTEXTS` → `product.ci.contexts`. `version_scheme`/`frappe_major` → `scheme: VersionScheme` + `cfg` (`tag_problem`). `tag` becomes optional: with no tag, the guard is "the branch equals `sha`". `pilot_assets` → `dispatch_after: tuple[str, ...]`. New: `wait_checks` (§8.2) |
| `conventional.py` | **new** | — | conventional-commit parsing and bump levels (§8.2) |
| `bump.py` | **new** | — | the develop+series post-merge bump (§8.2) |
| `branches.py` | frappe `ci/branches.py` | all (`is_bot_pr`, `behind_by`, `Updater`) | `BOT_BRANCHES = "standards/"` stays: it is the core's rollout branch prefix |
| `drift.py` | frappe `ci/drift.py` | `ls_remote`, `profile_remote`, `_issues`, `keep_issue`, `_version`, `run` | the title `f"{engine.flake_input} v{X} available"`; the commands name `cli`; `defaults.head_version` reads `engine.caller_stem` |

### 2.5 `standards_core/commands/`

| Core file | Source | Notes |
|---|---|---|
| `__init__.py` | frappe `commands/__init__.py` | `Command` dataclass (with `sort_key`) and `CORE_COMMANDS` (§6.1). The contract is `register(subparsers, product)`. Descriptions and flag help go through `help_text(product, key, default)` (§6.1) |
| `config.py` | frappe `commands/config.py` | copied. `--github-output modules` is unchanged |
| `profile.py` | frappe `commands/profile.py` | `show`/`--explain`/`list` copied (stage 2). `validate` (stage 3, since it calls `engine.build` and `render`) uses `product.fixtures` and `hooks.write_fixture` (**resolves the blocker** profile → `fixtures`, `_throwaway`'s hard-coded `hooks.py`). The frappe `pilot-assets` skip becomes `Fixture.requires` |
| `paths.py` | frappe `commands/paths.py` | `data-path` prints the first root that has the file. `pin-path` is copied |
| `policy.py` | frappe `commands/policy.py` | `Rules.major` → `Rules.scheme` + `cfg` + `facts`. The `compat` lazy import → `hooks.pr_checks(PrContext)` (**resolves the blocker** policy → `compat`). `--major-free` is kept (an engine repository's own rules); its reason text is `f"a {identity.name} major is made only with a \`Release-As: <N>.0.0\` footer"`, which is frappe's text for `name = "frappe-nix"`. `committed_cmd` → `hooks.committed_argv(root)` (default: frappe's rule, the tools project when `tools/pyproject.toml` exists, else `committed` on PATH) |
| `ci.py` | frappe `commands/ci.py` | the same steps, plus `bump`; `fast-forward` gains `--wait-checks`, and `--tag` becomes optional. `appgates.load` → `repoconfig.load(product, …)`. `--frappe-nix-repo` → `--engine-repo`; `--docs-patterns` → `--param KEY=JSON` (repeatable); `--frappe-major N` → `--set KEY=JSON` (a cfg override for the step); `--version-scheme` choices = `"semver"` + `product.version_schemes`. frappe's reusable workflows keep working through `ci.arg_aliases` (§3.1, §8.1): Frappe value `{"--frappe-nix-repo": "--engine-repo", "--docs-patterns": "--param docs-patterns", "--frappe-major": "--set frappe-major"}` |
| `sync.py` | frappe `commands/sync.py` | §6.3. `_passthrough` → `sync.passthrough`. `--frappe-version`, `--site` and `--init-listing` move to `sync.add_arguments`, which is called at each core flag's slot so a product flag keeps its frappe position (§6.2). `_create_table` uses `pyproject.add_table`. `_repo_settings` keys on `releases` and `dependabot` (core modules). Formatting → `sync.post_write`. Node-lock seeds and relock → `sync.phase_b_after` |
| `repo/__init__.py` | frappe `commands/repo/__init__.py` | adds `targets` |
| `repo/targets.py` | frappe `commands/repo/targets.py` | `Target` gains `branches`; `FLEET_SCHEMA` is composed; fleet list key → `product.fleet_key`. `resolve_profile` builds its table with `hooks.fleet_table`. **New subcommand** `repo targets` (§7.1). Clone and fetch prefixes `f"{identity.name}-repo-"`. `clone` checks out into `<tmp>/<repo name>` (frappe: `<tmp>/repo`), so a checkout named from its directory reads the same in a fleet clone (§3.3). `Target.release_branch` passes the target's facts (§7.1) |
| `repo/apply.py` | frappe `commands/repo/apply.py` | `SELF_REPO` → `product.self_repo`; `documentation_url` → `hooks.documentation_url`. The `BYPASS_FALLBACKS` text names `release_pattern` |
| `repo/audit.py` | frappe `commands/repo/audit.py` | core rows A1, A2, A3, A4, A5, A9, A10, A11 are generic (§7.4). The product's `audit_rows` add to or replace them. `run_tool` uses `<PREFIX>_ALLOW_SKEW` and `engine.tool_python`; `tool_spec` → `hooks.tool_spec`; `<PREFIX>_ISSUE_TOKEN` |
| `repo/doctor.py` | frappe `commands/repo/doctor.py` | core rows for releases, dependabot auto-merge, labels and the private profile fetch token. `hooks.doctor_rows` appends the rest (listing/registry rows are Frappe's) |
| `repo/rollout.py` | frappe `commands/repo/rollout.py` | `nix flake update <engine.flake_input>`; the commit subject `f"chore(deps): {engine.flake_input} {to}"`; branches `standards/{engine.flake_input}-{to}`; `.#<engine.init_app> -- --sync`; `set_profile_ref` edits `[tool.<table>] profile`. The relock step comes from `sync.rollout_steps` (Frappe value `[["nix","run","--no-pure-eval",".#relock"]]`; odoo `[]`) |

### 2.6 `standards_core/testing/`, `data/`

| Path | Content |
|---|---|
| `testing/conformance.py` | `check(product) -> list[str]` (§11.4), also `python -m standards_core.testing.conformance <module:attr>` |
| `testing/pins.py` | `python -m standards_core.testing.pins --root DIR --owner OWNER [--reachable URL]` (§10.3) |
| `testing/fakes.py` | `FakeGitHub` (from frappe `tests/repo_helpers.py`), `run_cli(product, *argv, cwd=None)` (from frappe `tests/helpers.py`), `git(root, *args)` (from `scaffold_helpers.py`) |
| `data/schema/core.schema.json` | the core `$defs` (§4.1) |
| `data/schema/github-ruleset.schema.json` | copied from frappe; its `$comment` is reworded without the product name ("the fields and rule types the core's repo-policy rulesets use") |
| `data/repo-policy/repo-settings.json`, `rulesets/{integration,integration-provisional,release-branch,tags}.json` | copied from frappe. `release-branch.json` uses `{release_label}`/`{release_pattern}`. `integration.json` has `"bypass_actors": "{integration_bypass}"` (§7.3; `[]` unless the flow is `post-merge-bump`, so frappe's rendering is unchanged). The `screenshots` label moves out of the core settings and becomes a Frappe override |
| `data/README.md` | the data table |

---

## 3. The Product API

`standards_core/product.py` imports nothing from the engine at run time (`TYPE_CHECKING` only). That makes
`product.py` → `engine` one-way, and a product module can build its `Product` at import time.

### 3.1 Code

```python
"""The product plugin contract. See DESIGN.md §3."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from standards_core.ci.verdict import DecisionRule
    from standards_core.commands.policy import PrContext
    from standards_core.commands.repo.audit import Audit, RowSpec
    from standards_core.commands.repo.doctor import Row as DoctorRow
    from standards_core.commands.repo.targets import Target
    from standards_core.common.config import Resolved
    from standards_core.scaffold.bootstrap import Runner
    from standards_core.scaffold.context import NS, Checkout
    from standards_core.scaffold.engine import Item, Plan
    from standards_core.scaffold.handlers import Handler

Level = Literal["major", "minor", "patch"]
Facts = Mapping[str, Any]  # discovery facts of one checkout: core (§4.7) plus hooks.discover
EMPTY: Mapping[str, Any] = MappingProxyType({})  # function defaults only, never a dataclass field default


def mapping_field() -> Any:
    """An empty read-only mapping as a dataclass field default.

    A `MappingProxyType` is unhashable on Python 3.11, and `@dataclass` refuses an
    unhashable default ("mutable default … use default_factory"), so it is built per instance.
    """
    return field(default_factory=lambda: MappingProxyType({}))


def default_committed_argv(root: Path) -> list[str]:
    """`committed` from the tools project when `tools/pyproject.toml` exists, else from PATH."""
    if (root / "tools" / "pyproject.toml").is_file():
        return ["uv", "run", "--frozen", "--project", str(root / "tools"), "committed"]
    return ["committed"]


FIRST_OPT_IN_MESSAGE = (
    "{path} is the checkout's own and differs from what {name} writes: sync would replace it"
    " with the managed file. {hint} then pass --force to replace it. The replacement:\n{diff}"
)


# --- identity, markers, engine, pins, paths ------------------------------------------


@dataclass(frozen=True)
class Identity:
    name: str  # "kit-nix": message prefix ("kit-nix sync: …"), clone prefixes
    cli: str  # "kit-nix": console script; argparse prog; `<cli> repo doctor` in hints
    distribution: str  # "kit-nix-tools": what `uv tool install` names
    version: str  # the product tools' __version__ (requires-<name> is checked against it)
    table: str  # "kit-nix": [tool.<table>], also the dev shell's opt-in line match
    env_prefix: str  # "KIT_NIX": every variable the core reads is f"{env_prefix}_<X>"
    report_key: str  # "kit_nix": the JSON report key and the context alias of `engine`
    sync_command: str  # "kit-init --sync": the fix named in drift output
    opt_in_hint: str  # the NotOptedIn message (exit 2)
    docs_url: str  # printed by --help
    config_file: str = "pyproject.toml"  # the file holding [tool.<table>], relative to the root
    config_required: bool = True  # False: a missing config_file reads as {} (not opted in; --standards creates it)
    description: str = ""  # argparse description; "" → f"{name} standards tools. See {docs_url}."

    @property
    def requires_key(self) -> str:  # "requires-kit-nix", the profile-only version range key
        return f"requires-{self.name}"


@dataclass(frozen=True)
class Markers:
    managed: str  # "kit-nix:managed": first-line marker; its presence makes a file sync's
    header: str  # "kit-nix:managed — generated by kit-nix (`kit-init --sync`);"
    header_local: str  # "edit only inside the kit-nix:local region."
    local: str  # "kit-nix:local": regions are "<leader> kit-nix:local-begin <name>"
    block_begin: str  # ">>> kit-nix: {name} (managed) >>>"  (leader prepended, §5.6)
    block_end: str  # "<<< kit-nix: {name} (managed) <<<"
    html_begin: str  # "<!-- kit-nix:begin {name} -->"
    html_end: str  # "<!-- kit-nix:end {name} -->"
    header_whole: str = "do not edit."


@dataclass(frozen=True)
class HeaderKind:
    leader: str  # "#", "//" or "<!--"
    closer: str = ""  # "-->" for html


@dataclass(frozen=True)
class Engine:
    flake_input: str  # "kit-nix": the root input every checkout locks
    repo: str  # "example/kit-nix": owner/name when the lock names none
    release_branch: str  # "release-{major}": the moving branch the default URL follows
    url_key: str  # "dev-shell.kit-nix-url": cfg key that replaces the default flake URL
    install_spec: str  # "kit-nix-tools @ git+https://github.com/{owner}/{repo}@{rev}#subdirectory=py/kit_nix_tools"
    caller_stem: str  # "kit-*": reusable workflow file stem glob, matched as <stem>.ya?ml
    init_app: str  # "kit-init": flake app run as `nix run --no-pure-eval .#<init_app> -- --sync`
    version_file: str = "version.txt"  # read from the locked engine tree to decide a re-exec
    tool_python: str = "3.12"  # `uv tool run --python` for audit's pinned tool runs
    base_input: str = "python-devops-nix"  # the engine's input that locks this core (§9.4)


@dataclass(frozen=True)
class Pins:
    dir: str  # ".dev-dist/pins": where pin-path fetches, relative to the lock
    engine_pinned: Mapping[str, tuple[str, str]] = mapping_field()  # input → (owner, repo) read only via the engine node
    lock_paths: tuple[str, ...] = ()  # lock files sync's steps make, besides flake.lock (untracked check)
    managed_inputs: Callable[[NS], tuple[str, ...]] = lambda ctx: ()  # inputs a local region may not define


@dataclass(frozen=True)
class Paths:
    never_write: frozenset[str] = frozenset()  # dirs refused in any managed path; ".git" is always added
    not_source: tuple[str, ...] = ()  # ctx.unmanaged_dirs: dirs a checkout-walking hook must skip
    github_marks: tuple[str, ...] = (".github",)  # paths whose presence means "set up for GitHub" (history)


# --- modules, data, schemas -------------------------------------------------------------


@dataclass(frozen=True)
class Modules:
    names: tuple[str, ...]  # every module, in documentation order; must include CORE_MODULES (§4.2)
    needs: Mapping[str, tuple[str, ...]] = mapping_field()  # module → modules it needs on (exit 2 otherwise)
    github_only: tuple[str, ...] = ()  # off (with a notice) on a checkout hosted elsewhere
    profile_only: tuple[str, ...] = ()  # top-level keys only a profile sets, beyond the core's
    app_only: tuple[str, ...] = ()  # top-level keys only the checkout's table sets, beyond the core's
    resolved_from_profile: tuple[str, ...] = ()  # org-only lists copied into cfg, beyond retire/extra-files
    required: str = "dev-shell"  # the module every opted-in checkout has on (enable = false is exit 2)


@dataclass(frozen=True)
class Schemas:
    modules: str = "modules.schema.json"  # $defs/modules/<m>, org-extra, profile-extra, app-extra (§4.1)
    fleet_entry: str = "fleet-entry.schema.json"  # one fleet entry; must declare "repo"


@dataclass(frozen=True)
class Fixture:
    table: str  # extra [tool.<table>] lines
    files: Mapping[str, str] = mapping_field()  # path → text written into the throwaway checkout
    requires: tuple[str, ...] = ()  # skip unless these modules are on under the profile alone


@dataclass(frozen=True)
class FirstOptIn:
    path: str  # "flake.nix": an unmanaged file sync would replace
    hint: str  # where the checkout's own content goes instead (printed with the diff)
    legacy: Callable[[str, Checkout], list[str]] = lambda current, checkout: []  # texts replaced silently
    message: str = FIRST_OPT_IN_MESSAGE  # format fields {path}, {name} (identity.name), {hint}, {diff}


@dataclass(frozen=True)
class JsonStyle:
    """How jsonfmt writes JSON: the default is oxfmt's form (frappe)."""

    indent: str = "\t"  # one level; "  " for prettier
    tab_width: int = 2  # a tab's width when measuring a line
    width: int = 110  # print width
    trailing_commas: Literal["jsonc", "none"] = "jsonc"  # "jsonc": after the last member of broken JSONC containers


@dataclass(frozen=True)
class Branching:
    name: str  # "develop+version" | "main+tags" | "develop+series"
    flow: Literal["tags", "post-merge-bump", "none"]  # what moves the release branch
    integration: Callable[[Mapping[str, Any]], str]  # integration-branch default (cfg → name)
    release_branch: Callable[[Mapping[str, Any], Facts], str]  # (cfg, facts) → "" when there is none
    release_pattern: str = ""  # ruleset ref include, "refs/heads/*.0"; "" → no release ruleset
    release_label: str = ""  # ruleset name stem, "*.0"
    tags: bool = False  # the tags ruleset and audit rows A2/A11 apply


# --- protocols ----------------------------------------------------------------------


class VersionScheme(Protocol):
    """Every method gets the resolved cfg and the checkout's facts (a series file, the lock, …)."""

    name: str  # "semver", "kit-series"

    def valid(self, version: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        """The problem with `version` under this scheme, or None."""

    def bump(self, version: str, level: Level, cfg: Mapping[str, Any], facts: Facts) -> str:
        """`version` raised by `level` (a bump of x resets y and z)."""

    def message_problems(self, text: str, cfg: Mapping[str, Any], facts: Facts) -> list[str]:
        """Commit-message and title rules this scheme adds; [] for none."""

    def tag_problem(self, tag: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        """Why the fast-forward must not move to `tag` (checked before any write), or None."""


@dataclass(frozen=True)
class ReleaseUnit:
    name: str  # "kit_alpha": named in commit subjects
    path: str  # "kit_alpha/": commits touching it count
    version_file: str  # "kit_alpha/__unit__.toml": where its version lives


class ReleaseTool(Protocol):
    name: str  # "unit-manifest"
    commit_subject: str  # "chore(release): bump {units}"

    def units(self, root: Path, ctx: NS) -> list[ReleaseUnit]:
        """The units sync and bump see, from tracked files only."""

    def read_version(self, text: str, unit: ReleaseUnit) -> str | None:
        """The version in `text` (the version file at some revision)."""

    def write_version(self, text: str, unit: ReleaseUnit, version: str) -> str:
        """`text` with the version replaced, every other byte kept."""

    def exempt(self, unit: ReleaseUnit, path: str) -> bool:
        """A change to `path` alone never bumps (tests, README, i18n, …)."""


# --- hook bundles -------------------------------------------------------------------


@dataclass(frozen=True)
class Hooks:
    """Checkout, configuration and context hooks. Defaults are the core's neutral behaviour."""

    load_checkout: Callable[[Path], Checkout]  # root → Checkout; EnvError (exit 3) when it is not one
    discover: Callable[[Checkout, Mapping[str, Any], Mapping[str, bool]], dict[str, Any]]  # facts (§4.7)
    gates: Callable[[Mapping[str, bool], Mapping[str, Any], Mapping[str, Any]], list[str]]  # enabled gates, in product.ci.gates order
    template_globals: Callable[[Checkout, Resolved, NS], dict[str, Any]]  # product keys added to the context
    render_helpers: Callable[[NS, str | None], Mapping[str, Any]] = lambda ctx, current: {}  # per render, final ctx (gen)
    derive: Callable[[dict[str, Any], Checkout | None], None] = lambda cfg, checkout: None  # derived defaults (mutates)
    integration_default: Callable[[Mapping[str, Any]], str] | None = None  # None → Branching.integration
    release_branch: Callable[[Mapping[str, Any], Mapping[str, bool], Facts], str] | None = None  # None → Branching.release_branch when releases is on
    host_rules: Callable[[dict, Mapping, bool, list[str]], None] = lambda cfg, app, gh, notices: None  # extra off-GitHub rules
    url_placeholders: Callable[[NS], Sequence[tuple[str, str]]] = lambda ctx: (("repo", ctx.get("repo") or ""),)  # applied in order
    preflight: Callable[[Checkout, Resolved], None] = lambda checkout, resolved: None  # raise ConfigError (exit 2)
    notices: Callable[[Plan], list[str]] = lambda plan: []  # phase-B notices
    checks: Callable[[Plan], list[Item]] = lambda plan: []  # whole-repo problem items engine.build adds (--check and phase B)
    committed_argv: Callable[[Path], list[str]] = default_committed_argv  # how policy runs `committed`
    new_target_table: Callable[[Path, Checkout, argparse.Namespace, str], dict[str, Any]] | None = None  # table --standards creates
    semantic_compare: Callable[[NS], bool] = lambda ctx: False  # whole data files compared as data
    pr_checks: Callable[[PrContext], list[str]] = lambda pr: []  # extra pr-policy problems
    write_fixture: Callable[[Path, str, Fixture, str], None] | None = None  # profile validate's throwaway checkout
    fleet_table: Callable[[str, Mapping[str, Any], str], dict[str, Any]] = lambda repo, entry, ref: {}  # app layer for a not-opted-in target
    documentation_url: Callable[[Target], str] = lambda t: t.entry.get("documentation", "")  # {documentation}
    tool_spec: Callable[[Audit], str | None] | None = None  # None → engine.install_spec at the locked rev
    repo_policy_values: Callable[[Mapping, Mapping, Sequence[str]], Mapping[str, Any]] = lambda cfg, modules, gates: {}
    rulesets: Callable[[Mapping, Mapping, str, str], list[str]] | None = None  # None → core selection (§7.3)
    doctor_rows: Callable[[str, Any], tuple[list[DoctorRow], list[str]]] = lambda repo, rc: ([], [])


@dataclass(frozen=True)
class SyncHooks:
    """The sync phases' product steps (§6.3)."""

    add_arguments: Callable[[argparse.ArgumentParser, str], None] = lambda parser, after: None  # once per core flag slot (§6.2)
    options: Callable[[argparse.Namespace], dict[str, Any]] = lambda args: {}  # merged into ctx.options
    passthrough: Callable[[argparse.Namespace, bool], list[str]] = lambda args, init: []  # product flags a handover repeats
    reexec_argv: Callable[[list[str]], list[str]] | None = None  # None → nix run --no-pure-eval .#<init_app> -- --sync <argv>
    reentry_argv: Callable[[list[str]], list[str]] | None = None  # None → nix develop --no-pure-eval -c <cli> sync --write --phase b <argv>
    required_tools: tuple[str, ...] = ("uv",)  # phase B re-enters the dev shell when one is missing
    before_reentry: Callable[[Runner], None] = lambda runner: None
    phase_a_before: Callable[[Plan, Runner, argparse.Namespace], None] = lambda plan, runner, args: None
    phase_a_after: Callable[[Plan, Runner, argparse.Namespace, bool], None] = lambda plan, runner, args, changed: None
    post_write: Callable[[Plan, Runner, list[str]], list[str]] = lambda plan, runner, touched: []  # formatters; returns more touched
    phase_b_after: Callable[[Plan, Runner, argparse.Namespace, bool, list[str]], list[str]] = (
        lambda plan, runner, args, changed, touched: []
    )
    rollout_steps: tuple[tuple[str, ...], ...] = ()  # argv run after `nix flake update` in repo rollout


@dataclass(frozen=True)
class CiConfig:
    gates: tuple[str, ...] = ("pr-policy", "lint", "test")  # every gate name, in required-check order
    check_context: str = "ci / {gate}"  # the required context of a gate
    contexts: tuple[tuple[str, str], ...] = (("ci / lint", "ci.yml"), ("ci / pr-policy", "pr-policy.yml"))  # dispatch-ci defaults
    labels: Mapping[str, tuple[str, str, str]] = mapping_field()  # name → (color, description, module); "" module = always
    decision_table: tuple[DecisionRule, ...] = ()  # §8.3
    ci_workflow: str = "ci.yml"  # audit A5's workflow; under post-merge-bump it must run on push (§11.4)
    nightly_workflow: str = ""  # audit A5's nightly; "" → none
    arg_aliases: Mapping[str, str] = mapping_field()  # old `ci` flag → "--new-flag" or "--param <k>"/"--set <k>" (§8.1)


# --- the product --------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Product:
    identity: Identity
    markers: Markers
    engine: Engine
    pins: Pins
    modules: Modules
    data_root: Path  # the product's data directory (importlib.resources files(pkg)/"data")
    hooks: Hooks
    sync: SyncHooks = field(default_factory=SyncHooks)
    ci: CiConfig = field(default_factory=CiConfig)
    paths: Paths = field(default_factory=Paths)
    schemas: Schemas = field(default_factory=Schemas)
    header_kinds: Mapping[str, HeaderKind] = mapping_field()  # added to the core kinds (§5.1)
    jsonc_globs: tuple[str, ...] = ()  # full-path globs semantic.kind reads as JSONC
    json_style: JsonStyle = field(default_factory=JsonStyle)  # how managed JSON is written (§2.3 jsonfmt)
    jinja_filters: Mapping[str, Callable[..., Any]] = mapping_field()  # added to the core filters; no shadowing
    handlers: Mapping[str, Handler] = mapping_field()  # handler name → instance; no shadowing of core names
    version_schemes: Mapping[str, VersionScheme] = mapping_field()  # added to "semver"
    branching: Mapping[str, Branching] = mapping_field()  # releases.branching values (§8.1)
    release_tool: ReleaseTool | None = None  # units and versions for the post-merge bump
    first_opt_in: tuple[FirstOptIn, ...] = ()  # §5.9
    fixtures: Mapping[str, Fixture] = mapping_field()  # profile validate renders every template for each
    fleet_key: str = "repos"  # the fleet file's list key
    self_repo: str = ""  # `repo apply --self` target; "" → --self is exit 2
    audit_rows: Mapping[str, RowSpec] = mapping_field()  # id → spec; adds to or replaces core rows (§7.4)
    help: Mapping[str, str] = mapping_field()  # argparse text overrides: "<command>" or "<command> <flag>" (§6.1)

    def __post_init__(self) -> None:
        """Fail at import (a product bug, exit 3 through cli.main), never at sync."""
        ...  # rules in §3.2

    @property
    def roots(self) -> tuple[Path, ...]:  # (data_root, core data root): product first
        ...

    @property
    def rollout_inputs(self) -> tuple[str, str]:
        return (self.engine.flake_input, "standards-profile")
```

### 3.2 `__post_init__` rules

Any violation raises `ValueError` naming the field.

- `identity.name`, `identity.cli` and `identity.table` match `^[a-z][a-z0-9-]*$`.
- `env_prefix` matches `^[A-Z][A-Z0-9_]*$`. `report_key` matches `^[a-z][a-z0-9_]*$`.
- `modules.names` are unique and kebab-case.
- `modules.needs`, `modules.github_only` and `modules.required` refer only to names in `modules.names`.
- `modules.names` includes every core module (§4.2).
- Every `version_schemes`, `branching` and `handlers` key is new: shadowing a core name is refused.
- `markers.block_begin` and `markers.block_end` each contain `{name}` exactly once. So do `html_begin` and `html_end`.
- `markers.block_begin != markers.block_end`.
- `ci.gates` are unique, and `check_context` contains `{gate}`.
- `fixtures` is non-empty when `hooks.write_fixture` is set, and the other way round.
- `data_root` is an existing directory.
- `json_style.indent` is a tab or a run of spaces; `width` and `tab_width` are positive.
- `ci.arg_aliases` keys start with `--`; values are `--<flag>`, `--param <key>` or `--set <key>`.
- `first_opt_in` paths are unique, and each `message` formats with exactly the fields `path`, `name`, `hint`, `diff`.

`build_parser` checks `help` keys against the registered commands and flags (an unknown key is `RuntimeError`),
because the extra commands are only known there.

### 3.3 Hook contracts not covered by inline comments

- `load_checkout(root)`: returns a `Checkout` with:
  - `root`;
  - `name`: the unit name used in context and messages. The core default reads, in order: `[project].name`; the
    name part of `[tool.<table>] repo`; the name part of the origin remote (`repo.origin_repo`); and only for a
    checkout with no origin remote (fixtures, throwaways), `root.name`. A checkout's name therefore never depends
    on where it is checked out when it has a remote; fleet clones also land in `<tmp>/<repo name>` (§2.5);
  - `config`: the parsed config file (`{}` when it is missing and `identity.config_required` is false);
  - `tracked`: `repo.ls_files(root)`, sorted;
  - `extra`: a mapping for product data (Frappe value: `{"hooks": hooks.read(...)}`).

  The core ships `default_load_checkout(product)`, and products wrap it.
- `discover(checkout, cfg, modules)`: pure, and computed from `checkout.tracked` and tracked file contents only
  (§3.4 determinism). `--check` recomputes it, so a newly tracked file can be drift.
- `gates(modules, cfg, facts)`: the gates in `ci.gates` order. `repo-policy` required checks and the
  `--gates` default of `ci automerge` use exactly this list (frappe's `appgates` rule).
- `template_globals(checkout, resolved, base_ctx)`: returns the keys to add. Shadowing a core key (§3.5) is a
  `RuntimeError`. Frappe value: `app`, `app_hyphen`, `frappe`, `siblings`, `site`, the `*_shipped` flags,
  `tools_deps`, `version`, `listing`, `app_type`.
- `render_helpers(ctx, current)`: called once per template render with the final context (after `previous` and
  `history` are set) and the file on disk. Frappe value: `{"gen": configs.Gen(ctx)}`.
- `derive(cfg, checkout)`: runs after the `integration-branch` default and before `repo` (§4.4 step 8), as
  frappe's `site` default does. It may set derived defaults in place, and may read the checkout's tracked files
  (`.kit-series`, manifests, `flake.lock` nodes), which is how a series-dependent config differs by shape.
  `checkout` is `None` when a profile is resolved with no checkout. Frappe value: `site` from `[project].name`.
  The core already sets `org.copyright-holder ← org.publisher`, `integration-branch` and `repo`.
- `release_branch(cfg, modules, facts)` and `Branching.release_branch(cfg, facts)`: `facts` are the checkout's
  discovery facts, so a release branch can be the series a file names (`.kit-series`). Every caller has them:
  `context.build`, `repoconfig.load`, `Target.release_branch` (from the target's clone) and the `ci` steps.
- `checks(plan)`: whole-repo problems (a missing per-unit README, a submodule without `branch = <series>`).
  `engine.build` appends them to the plan's items as items with `wanted == current`, so `--check` and phase B's
  final report (§6.3 step 9) both count them, and `--write` cannot exit 0 where `--check` fails.
- `committed_argv(root)`: the argv `policy` runs `committed` with. The default is frappe's `committed_cmd`. A
  product whose tools project lacks `committed` returns `["committed"]` (from its dev shell) or adds `committed`
  to its tools dependencies when `commits` is on.
- `new_target_table(root, checkout, args, profile)`: the `[tool.<table>]` that `--standards` creates. The core
  adds `schema = 1` and `profile` before the product's keys, and `integration-branch` afterwards (§4.4). `None`
  means the core writes `{schema, profile}` only.
- `pr_checks(pr)`: `PrContext` is
  `(root, base_ref, head_sha, head_ref, title, author, rules, show(rev, path) -> str | None, changed() -> list[str])`.
  `show` reads a file at a revision. The check is allowed file access at base and head (A3, for a manifest bump
  check).
- `write_fixture(root, name, fixture, profile_rel)`: writes the config file with
  `[tool.<table>] schema = 1, profile = "<profile_rel>"` plus `fixture.table`, then the product's minimum files,
  then `fixture.files`. The core makes the git repository and commits.

### 3.4 Determinism

Hooks that feed the plan (`discover`, `template_globals`, `render_helpers`, `derive`, `gates`, `checks`, handlers)
must be deterministic:

- they never read the clock;
- they iterate in sorted order;
- they read only tracked files and the git history (frappe §3.4).

The conformance check (§11.4) renders every fixture twice and compares the results.

### 3.5 Core template context

`scaffold.context.build(product, checkout, resolved, *, lock, floors, options)` returns an `NS` with exactly these
keys, plus `template_globals`:

| Key | Value |
|---|---|
| `name`, `name_hyphen` | `checkout.name`, with `_` → `-` |
| `repo`, `repo_host` | `cfg["repo"]` or `""`; `resolved.repo_host` or `""` |
| `cfg`, `modules`, `org`, `profile` | the resolved values (`org` is a copy) |
| `profile_input` | `cfg["profile"]` when it is a flake URL (`github:`/`gitlab:`/`git+https://`), else `""` |
| `branches` | `{"integration": cfg["integration-branch"], "release": release_branch or None}`, with `release_branch` from the facts (§3.3). Other branch lists (Dependabot's per-series targets) are product module parameters (§5.2) |
| `gates` | `hooks.gates(...)` |
| `discover` | core facts plus product facts |
| `engine`, and `ctx[identity.report_key]` (the same object) | `{rev, version, major, owner, name, url}` from the lock node, the product version and `engine_url` |
| `floors` | the manifest floors |
| `options` | `{"force": bool, **sync.options(args)}` |
| `first_commit_year` | `context.first_commit_year(root)` |
| `unmanaged_dirs` | `list(paths.not_source)` |
| `previous`, `history` | set by `engine.build` |

---

## 4. Data model

### 4.1 Schemas: what the core owns, what the product supplies

The core ships `data/schema/core.schema.json` with `$defs`:

| `$defs` key | Content (from frappe `profile.schema.json` / `tool-frappe-nix.schema.json` / `fleet.schema.json`) |
|---|---|
| `org` | `properties` for `publisher`, `email`, `copyright-holder`, `license`, `github-owner`, `docs-url`, `dev-docs-url`, `support-url`, `repo-url`, each a string with default `""` |
| `retire-item` | `{paths, contains, module}`, where `module` is `{"$ref": "modules.schema.json#/$defs/module-name"}` |
| `extra-file` | `{path, template, strategy: whole\|seed, module (as above), when, header}`, where `header` is `{"$ref": "#/$defs/header-kind"}` |
| `header-kind` | composed: core kinds plus `product.header_kinds` |
| `core-modules` | the parameter schemas of the core modules (§4.2) |
| `profile-ref` | the `profile` pattern: `minimal`, `recommended(@N.M)?`, `github:`, `gitlab:`, `git+https://`, `./` |
| `fleet-entry-core` | `repo` (pattern `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`), `private` (bool), `documentation` (`^https://`), `profile` (string), `integration_branch` (string), `branches` (array of strings, `minItems` 1) |

The product ships `schema/modules.schema.json`, whose `$defs` hold:

- `module-name`: `{"enum": [...product.modules.names]}`;
- `modules/<m>`: one object schema per module, which must have `enable` (boolean). For a core module, the product's
  def may add properties. Composition merges the core's `core-modules/<m>` properties under the product's, and
  the product's win per key;
- `org-extra`: `{properties: {...}}`, org keys the product adds;
- `profile-extra`: `{properties: {...}}`, top-level profile-only keys. Frappe value: `known-apps`, `replace-apps`;
- `app-extra`: `{properties: {...}, required: [...]}`, top-level app keys. Frappe value: `frappe-major`, `siblings`, …

The product also ships `schema/fleet-entry.schema.json`, a complete object schema for one fleet entry. It may
`$ref` `core.schema.json#/$defs/fleet-entry-core/properties/<k>`, and it must declare `repo`.

`common/compose.py` builds three documents in memory and registers them in the schema cache under the names
`profile.schema.json`, `app.schema.json` and `fleet.schema.json`, so every `$ref` and `node_at` works on them.

```python
def profile_schema(product: Product) -> dict: ...  # required [schema, name]; additionalProperties false
def app_schema(product: Product) -> dict: ...  # required [schema, *app-extra.required]
def fleet_schema(product: Product) -> dict: ...  # {schema: const 1, profile?, <fleet_key>: [entry]}
```

- **Profile properties**:
  - `schema` (const 1), `name` (`^[a-z0-9][a-z0-9-]*$`), `description`;
  - `extends`: a pattern built from the product's built-in names;
  - `<requires_key>`;
  - `org`: core and `org-extra` properties, `additionalProperties` false;
  - `retire`, `extra-files`;
  - `profile-extra`;
  - one `{"$ref": "modules.schema.json#/$defs/modules/<m>"}` per module.
- **App properties**:
  - `schema`, `profile` (`profile-ref`), `repo`, `integration-branch`, `retire-keep`;
  - `app-extra`, `org`;
  - modules as above.

A product that ships a complete `schema/profile.schema.json`, `app.schema.json` or `fleet.schema.json` replaces
composition for that document: lookup is product-first. frappe-nix will do this, so its schemas, and every error
message keyed on them, stay byte-identical. Conformance (§11.4) validates both paths.

### 4.2 Core modules and keys

**Core modules.** The core reads parameters of six module names, which every product must include:

| Module | Parameters the core reads (default) | Read by |
|---|---|---|
| `dev-shell` | `enable` | the module rules (`modules.required`, default `"dev-shell"`) |
| `commits` | `allow-stacked` (true), `pr-allowed-bases` ([]), `subject-length` (100) | `policy` |
| `releases` | `branching` (enum = `product.branching` keys; default = the product's first), `version-scheme` (enum = `"semver"` + `product.version_schemes`; default `"semver"`), `tag-prefix` (`"v"`), `tool` | `policy`, `ci fast-forward`, `ci bump`, audit A2/A11, `repo-policy` values, sync's repo-settings notice |
| `dependabot` | `auto-merge` (true), `auto-merge-policy` (`"decision-table"`), `update-branches` (false) | sync notice, doctor |
| `ci` | `github-app` (`{app-id, private-key}` names), `schedules` (object) | doctor notice, `check_engine_url` |
| `repo-policy` | `enable`, `required-approvals` (1), `require-code-owner-review` (false), `required-checks` (`"gates"`, or a list from `ci.gates`), `release-bypass` (`"github-actions"`, `"user:<id>"` or `"integration:<app id>"`; default `"github-actions"`), `secret-scanning` (true), `merge-methods` (`["squash"]`), `require-thread-resolution` (true), `delete-branch-on-merge` (true), `manage-default-branch` (true), `manage-features` (true) | `repo apply/audit`, rollout's merge method |

`tool = "none"` turns any module off, as in frappe `_switch`.

**Core keys.**

| Kind | Keys |
|---|---|
| Profile-only | `name`, `description`, `extends`, `<requires_key>`, `retire`, `extra-files` |
| App-only | `profile`, `repo`, `integration-branch`, `retire-keep` |
| Resolved from profile | `retire`, `extra-files` |

`product.modules.*` add to these sets and never remove from them.

### 4.3 Manifest fragments

These live at `data/manifest.d/*.json` in the product root. The core ships none. A fragment is
`{"$comment"?, "entries"?, "retire"?, "floors"?}`, and fragments are united in file-name order.

**Entry keys.** Unknown keys are refused, as `ManifestError`:

| Key | Type | Meaning |
|---|---|---|
| `path` | Jinja string | rendered against the context; then `inside()` checks it (relative, no `..`, no control character, no symlink, not under `never_write`) |
| `strategy` | `whole` \| `toml-merge` \| `json-merge` \| `jsonc-merge` \| `seed` \| `blocks` \| `sentinel` | §5 |
| `module` | name or list | live while any listed module is on and `when` holds |
| `when` | expression | frappe's `when` evaluator (unchanged) |
| `template` | string | template path under `templates/`. Required for `whole` and `sentinel`. For `jsonc-merge` it renders the merge spec (§5.5) |
| `uses` | list of dotted keys | the profile values it reads; an `org.*` key must be non-empty while the entry is live, unless suffixed `?` (S37) |
| `overridable` | bool | an org profile's `templates/<same path>` may replace the template. A path ending in `/` covers a folder |
| `header` | header kind | `whole` and seeds rendered from `[[extra-files]]` |
| `header_note` | string | appended to the header line |
| `local_regions` | list of names | `whole` only; the header kind's leader must be `#` or `//` |
| `handler` | string | a registered handler (§5.1). Optional: each strategy has a default |
| `command` | string | a seed made by a command (a lock). Shown in reports; the handler's `lock_argv` runs it |
| `relock_when` | list of paths | **new**: phase B runs the lock command when one of these was written. Frappe value: `tools/uv.lock` → `["tools/pyproject.toml"]` |
| `floors` | string | **new**: the floors kind a lock handler checks (Frappe value `"uv"`) |
| `phase` | `a` \| `b` | `a` = written before `nix flake lock` |
| `block` | name `^[a-z0-9][a-z0-9-]*$` | **new**, `sentinel` only |
| `seed_template` | string | **new**, `sentinel` only: rendered when the file is missing; it must contain the anchor (for `nix-attrset`, the attrset path). Without it a missing file is exit 2 (§5.6.2) |
| `anchor` | `eof` \| `nix-attrset <path>` \| `toml-array <table.key>` | **new**, `sentinel` only (§5.6) |
| `comment` | `#` \| `//` \| `html` | **new**, `sentinel` only (default `#`) |
| `absorb` | bool | **new**, `sentinel` only: removes identical lines outside the block |
| `claims` | `nix-inputs` | **new**, `sentinel` with a `nix-attrset` anchor only (§5.6.4) |

`(path, block)` is unique across fragments: two `sentinel` entries may share a path with different blocks. A
non-sentinel path still appears once. A file two modules contribute keys to is **one** merge entry whose `module`
lists both, with one group per module (odoo's `tools/pyproject.toml`: python-lint's `[project]`/`[tool.uv]` groups
and tests' `[tool.coverage.*]` group, §5.3).

**Retire rule keys.** These are copied from frappe: `paths`, `rule`, `module`, `with`, `when`, `contains`, `except`,
`unmanaged`, `only_section`, `deps_in_project`. New: `only_sections` (a list): the rule applies when the file's
`[section]` set is non-empty and a subset of the list (`setup.cfg` holding only `[flake8]`, `[isort]`, …).
`only_section: "x"` is read as `only_sections: ["x"]` with frappe's exact-set meaning kept for it (the set must
equal `{x}`, which for one section is the same test). `paths` and `except` accept `{a,b}` braces, expanded at
load (`eslint.config.{js,mjs,ts}`).

**Floors.** `floors` is `{kind: {package: version}}`. The product names the kinds. A floor set twice is refused.

### 4.4 Built-in profiles and resolution

The product root holds `profiles/minimal.toml` and `profiles/recommended@<minor>.toml`. Both are frozen once
released (S42). Plain `recommended` means the newest snapshot. `builtin_names`, `builtin_name`, `builtin_profile`
and `is_builtin` are copied, reading through the roots.

Merge order, unchanged from frappe §8.4:

1. built-in (`profile` itself, or the org profile's `extends`) ← org (locked `standards-profile`, or `./<dir>`) ←
   app table;
2. each layer is validated on its own: profile-only keys are refused in the app, and app-only keys and
   `x-app-only` parameters are refused in a profile;
3. tables merge recursively; scalars and arrays are replaced; `resolved_from_profile` lists come from the org
   profile only;
4. schema defaults are filled (profile schema, then app schema);
5. `cfg["profile"] = name`;
6. `org.copyright-holder` falls back to `org.publisher`;
7. `integration-branch` defaults to `hooks.integration_default(cfg)`, else `branching[cfg.releases.branching].integration(cfg)`;
8. `hooks.derive(cfg, checkout)` (frappe derives `site` here, before `_repo`, so `cfg`'s key order, and therefore
   `profile show --format json`, which prints with `sort_keys=False`, is unchanged);
9. `repo` (§2.2);
10. the module rules (required module, `tool = "none"`, GitHub-only off-host with `hooks.host_rules`, needs, the
    provisional rule);
11. `<requires_key>` is checked against `identity.version` (`SpecifierSet.contains(..., prereleases=True)`; a
    mismatch is exit 3; a bad spec is exit 2).

`profile show --explain` prints each value's layer: `builtin:recommended@N.M`, `org:<source>@<rev>`, `app` or
`default`.

`--standards <profile>` creates the table, and the config file itself when it is missing and
`identity.config_required` is false (a file holding only the table). The core calls `hooks.new_target_table`, prepends `schema` and `profile`,
then adds `integration-branch` when `origin/HEAD` names a branch other than the default (frappe
`with_integration_branch`).

### 4.5 `requires-<product>` and fleet ranges

`check_requires(product, spec, label)` is frappe's `check_requires` with the product version. `profile validate`
calls it too.

### 4.6 DataRoots

```python
@dataclass(frozen=True)
class DataRoots:
    roots: tuple[Path, ...]  # (product.data_root, core data root)

    def find(self, rel: str) -> Path | None: ...  # first root with rel; the path rules apply
    def path(self, rel: str) -> Path: ...  # find or ConfigError("no such data file: …")
    def listdir(self, rel: str) -> dict[str, Path]: ...  # union by file name; product shadows core
```

- `data_path(product, rel) = product_roots(product).path(rel)`. `product_roots` is cached per product.
- **Schemas.** `schema.load(product, name)` returns the composed document for the three composed names, unless the
  product ships the file; otherwise `roots.path(f"schema/{name}")`. A `$ref` file part `"x.json"` resolves the
  same way, so `core.schema.json` refers to the product's `modules.schema.json`.
- **Jinja.** `render.environment(product)` uses a `ChoiceLoader` over the `templates/` directory of each root,
  product first, with the fixed configuration. `profile_environment(product, directory)` is frappe's
  `_NoLinkLoader` environment with the product's filters. Both are cached per `(product, directory)`.
- **Other caches.** The manifest, snapshots and repo-policy reads are also cached per product.

### 4.7 Core discovery facts

The core contributes only these facts, so it never guesses shape:

- `gitmodules`: `.gitmodules` is tracked;
- `github_workflows`: any tracked `.github/workflows/*`.

Shape detection (projects vs addons, series, …) is `hooks.discover`. The kit stub (§11.1) proves that shape-dependent
discovery reaches `when` expressions and paths.

---

## 5. Strategies and handlers

### 5.1 Headers, the handler protocol and the registry

**Headers.** The core kinds are:

| Kinds | Leader |
|---|---|
| `yaml`, `toml`, `shell`, `ini`, `nix` | `#` |
| `jsonc`, `ts` | `//` |
| `none` | no header |

`product.header_kinds` adds kinds, such as `"js": HeaderKind("//")` and `"md": HeaderKind("<!--", "-->")`. The
header function is:

```python
def header(product: Product, kind: str, local: bool, note: str = "") -> str:
    # f"{leader} {markers.header} {markers.header_local if local else markers.header_whole}[ {note}]{(' ' + closer) if closer}\n"
```

Frappe value: `markers.header = "frappe-nix:managed — generated by frappe-nix (\`frappe-init --sync\`);"`,
`header_local = "edit only inside the frappe-nix:local region."`. This reproduces frappe `render.header` byte for
byte.

**The handler protocol.**

```python
class Handler(Protocol):
    name: str
    strategies: tuple[str, ...]  # the strategies it may serve
    merged: bool  # rendered on every run, module on or off (key-group retraction happens in render)

    def render(self, plan: Plan, entry: Entry, path: str, current: str | None, templates: Path | None) -> list[Item]: ...
    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None: ...  # every module off
    def absent(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None: ...  # module on, when false
    def whole_precheck(self, plan: Plan, entry: Entry, path: str, current: str | None) -> Item | None: ...
    def validate(self, plan: Plan, entry: Entry, path: str, current: str) -> list[Item]: ...  # seeds on disk
    def seed(self, plan: Plan, entry: Entry, templates: Path | None) -> str | None: ...  # None → render template
    def lock_argv(self, plan: Plan, item: Item, touched: Sequence[str]) -> list[str] | None: ...
```

**Base classes.** `scaffold/handlers.py` ships one base class per strategy. Each implements the frappe behaviour
of that branch. A product handler subclasses one and overrides methods.

| Base class (registry name) | Behaviour (frappe source) |
|---|---|
| `WholeHandler` (`whole`) | render the template; splice regions; `check_redefinitions`; prepend the header. Then the first-opt-in check (§5.9), then `semantic_compare`. Retract: delete when the file opens with the header and its regions are empty; local content is exit 2; a headerless file is deleted only when it equals the render and the module was on (history). Absent: delete only what sync wrote (frappe `_absent`). `whole_precheck` returns `None` |
| `SeedHandler` (`seed`) | write when missing; then `validate`. Retract: a notice on the run that turns the module off. With `command`: missing → drift with the command; a floors shortfall → drift |
| `UvLockHandler(packages=None)` (`uv-lock`) | seed + command. `lock_argv` = `["uv","lock","--project",dir]` plus `--upgrade-package` for each shortfall of `ctx.floors[entry.floors]`. `packages: Callable[[NS], Collection[str]] \| None` filters the floors to the packages the project declares; `None` = no filter. The core reads no product context key: frappe registers `UvLockHandler(packages=lambda ctx: ctx.tools_deps)` (frappe `uv_floors`) |
| `NpmLockHandler` (`npm-lock`) | **new**: seed + command. `lock_argv` = `["npm","install","--package-lock-only","--ignore-scripts","--no-audit","--no-fund"]` in the lock's directory. Runs when the lock is missing or a `relock_when` path was written |
| `BlameIgnoreRevsHandler` (`blame-ignore-revs`) | seed + `validate` (frappe `blame_problems`) |
| `TomlMergeHandler(groups: TomlGroups)` (instantiated per product) | §5.3 |
| `JsoncMergeHandler` (`jsonc-merge`) | §5.5 |
| `SentinelHandler` (`sentinel`) | §5.6 |
| `BlocksHandler` (`blocks`) | abstract: a product's marker-delimited handler (frappe readme, `init-py`, `gitignore`). `render` must be overridden; `absent` leaves the file (frappe) |
| `JsonGroupsHandler(groups: JsonGroups)` (`json-merge`, instantiated per product) | §5.3: the JSON counterpart of `TomlMergeHandler`. A product with special needs (frappe package-json, stylelint) subclasses it |

**Dispatch.** The registry is the core handlers plus `product.handlers`. An entry's handler is `entry.handler`, or
the strategy's base. `engine.build` dispatches as frappe did:

- merged handlers render on every run;
- a live entry calls `render`;
- module on with `when` false calls `absent`;
- every module off calls `retract`;
- each returned item gets `module = ",".join(entry.modules)` unless it already has one.

### 5.2 `whole` and local regions

These are unchanged except for the parameterised markers:

- regions parse and splice by name;
- an unknown, duplicate or unbalanced region is exit 2;
- a missing region is empty;
- `check_redefinitions` refuses, inside a region, a managed hook `id`, an `updates` entry with the same
  `(package-ecosystem, directory, target-branch)` (Dependabot's own key), an ini section, or a managed flake input.
  Managed inputs are `pins.managed_inputs(ctx)` plus every input defined outside the regions.
- **Dependabot entries** are read per list item (from one `- package-ecosystem:` to the next), so
  `target-branch:` may come before or after `directory:`. An entry without `target-branch` keys on the empty
  branch, and its problem text is frappe's `updates entry <eco> <dir>`; one with it appends ` → <branch>`. frappe
  writes no `target-branch`, so its output is unchanged. A local-region entry for another series branch is
  therefore allowed.
- **Per-branch entries.** Sync never sees the fleet's `branches`. A product that renders one entry per branch reads
  the list from its own module parameter (odoo: a `dependabot` parameter such as `target-branches`, default
  `[integration-branch]`, set in the repo table or the profile), documented in its module schema. `repo audit`
  may compare it with the fleet entry's `branches` through a product audit row.

### 5.3 `toml-merge`

```python
@dataclass(frozen=True)
class TomlGroups:
    groups: Callable[[NS], dict[str, list[tuple[tuple[str, ...], Any]]]]  # group → [(key path, value)], file order
    on: Callable[[NS, str], bool]  # whether a group is managed now
    canon_tables: tuple[str, ...] = ()  # managed tables in layout order
    head_tables: tuple[str, ...] = ()  # tables that follow [project] in order
    key_order: Mapping[tuple[str, ...], tuple[str, ...]] = mapping_field()
    baseline: Callable[[NS, tuple[str, ...], Any, Any], bool] = lambda ctx, path, have, rendered: False  # never retracted
    reconcile: Callable[[NS, tuple[str, ...], Any, Any], Any] = lambda ctx, path, have, rendered: rendered  # value to keep
    seeds: Callable[[NS, dict], list[tuple[tuple[str, ...], Any]]] = lambda ctx, doc: []  # written once when missing (fail_under)
    extra: Callable[[Any, NS], None] = lambda doc, ctx: None  # in-place tomlkit edits beyond groups
    view_extra: Callable[[dict, NS], dict[str, Any]] = lambda doc, ctx: {}  # added to managed_view
    problems: Callable[[dict, NS], list[tuple[int, str]]] = lambda doc, ctx: []  # rules on app-owned keys

class TomlMergeHandler:
    def __init__(self, name: str, groups: TomlGroups, *, creates_table: bool = False): ...
```

The semantics are frappe's `merge` and `managed_view`:

- set the keys of each `on` group;
- a dict value is an exact table;
- a missing table is placed by `canon_tables`/`head_tables`, else after the last `[tool.*]`;
- a missing key is placed by `key_order`, else by group order;
- for a group that is off, a key still holding its rendered value is deleted when the group was on in history
  (`ever`), and empty tables are pruned;
- an edited value is left, with a warning on the run that turns it off. The warning is frappe's text with the
  entry's path: `f"{path} {dotted key} is the app's now that {module} is off (it differs from what sync wrote)"`
  (for frappe, `path` is `pyproject.toml`, so the text is unchanged);
- for a group that is on, the value written is `reconcile(ctx, key_path, have, rendered)` (default: `rendered`).
  This is the floor rule for hand-raised pins: a product returns `have` when it is at or above `rendered`, or merges
  per item (a dependency list where one pin was raised by hand and a new tool was added). `baseline` still decides
  retraction;
- `--check` compares `managed_view`, which is parsed values only, against the reconciled values;
- a missing file starts as the empty document.

`creates_table=True` marks the handler of `identity.config_file`. `plan.created_config` is then added with
`add_table` (frappe's pyproject handler). Each group owns its keys, and each path is one entry (§4.3). Odoo's
`tools/pyproject.toml` is one `toml-merge` entry with `module: ["python-lint", "tests"]` and one `TomlGroups`:
python-lint's groups own `[project]` (the exact series pins, reconciled as floors) and `[tool.uv]`; the tests group
owns `[tool.coverage.*]`, with `fail_under` seeded from `initial-floor` through `seeds`.

**`json-merge` (`scaffold/jsonmerge.py`).** The same machinery over JSON:

```python
@dataclass(frozen=True)
class JsonGroups:
    groups: Callable[[NS], dict[str, list[tuple[tuple[str, ...], Any]]]]  # group → [(key path, value)]
    on: Callable[[NS, str], bool]
    key_order: Mapping[tuple[str, ...], tuple[str, ...]] = mapping_field()  # parent → key order for inserts
    baseline: Callable[[NS, tuple[str, ...], Any, Any], bool] = lambda ctx, path, have, rendered: False
    reconcile: Callable[[NS, tuple[str, ...], Any, Any], Any] = lambda ctx, path, have, rendered: rendered  # floors
    problems: Callable[[dict, NS], list[tuple[int, str]]] = lambda doc, ctx: []  # rules on checkout-owned keys

class JsonGroupsHandler:
    def __init__(self, name: str, groups: JsonGroups): ...
```

- Set, retract-when-equal-and-was-on (`ever`), turn-off warning, `--check` on parsed values: as `toml-merge`.
- Unmanaged keys keep their order; a new key goes by `key_order`, else at the end of its parent.
- The file is written with `jsonfmt.dumps(doc, product.json_style)`. A file not already in that form is drift only
  when its parsed values differ (the formatter, prettier or oxfmt, owns the layout).
- A missing file is created from the groups that are on.

A product's `package.json` that already exists (a frontend's own scripts and dependencies) is therefore a
`json-merge` entry owning only the dev tool pins; a product without such files may keep `package.json` whole.

### 5.4 `seed`, lock steps, floors

- **Seeds.** A seed is written only when missing; validation follows; on turn-off it gets a notice.
- **Command seeds.** `NpmLockHandler` and `UvLockHandler` set `Item.command`. `--check` reports "missing" or
  "below its floor …" as drift (1). `--write --offline` reports `(offline: run \`<command>\`)`.
- **Phase B.** For each item of a handler with `lock_argv`, phase B runs it when `item.command` is set or when any
  `entry.relock_when` path is in `touched`. It then stages the lock.
- **Floors.** `rev(url, floor)` and `pin(action, sha, version)` in templates, `range_ok`/`caret` for npm, and
  `lock_shortfalls` for `uv.lock`. They are copied unchanged, and none is ever lowered.

### 5.5 NEW: `jsonc-merge`

**Purpose.** A file owned by the checkout and its editor (`.vscode/settings.json`, `.vscode/extensions.json`)
where sync owns only some leaves.

**Spec.** The entry's `template` renders JSON (not JSONC) of this shape:

```json
{
  "set": {"python.languageServer": "None", "[python]": {"editor.defaultFormatter": "charliermarsh.ruff"},
          "ruff.path": ["${workspaceFolder}/tools/.venv/bin/ruff"]},
  "union": {"recommendations": {"ensure": ["ms-python.python"], "opposite": "unwantedRecommendations"}},
  "forbidden": [["Ide.serverConfigPath"]],
  "never": [["Ide.selectedProfile"], {"prefix": "python-envs."}]
}
```

The keys are interpreted as follows:

- Paths are JSON key lists. A top-level string is shorthand for `[string]`. Keys are literal (`"python.languageServer"`
  is one key), never split on dots.
- **Prefix form.** In `forbidden` and `never`, `{"prefix": "p", "in": [parent path]?}` matches every key of the
  parent object (default: the top level) that starts with `p`. VS Code settings are flat dotted keys, so
  `python-envs.*` is `{"prefix": "python-envs."}`. A forbidden prefix deletes every matching key; a `never`
  prefix refuses any `set`/`union` path whose key at that level starts with it.
- **`set`.**
  - A dict value merges per sub-key, recursively: only the listed leaves are managed.
  - A non-dict value (scalar or list) is a managed leaf, set whole.
- **`union`.** The value at the path is an array.
  - Every `ensure` id is present (appended in spec order when absent).
  - Every `ensure` id is removed from the array at sibling key `opposite`, when given.
  - Other items are untouched.
- **`forbidden`.** Each path is deleted.
- **`never`.** Declarative only: these paths may not appear in `set`, `union` or `forbidden` (a `ManifestError` at
  render). They document keys sync must not touch.

**Editor (`scaffold/jsonc.py`).**

```python
@dataclass
class Node: kind: Literal["object", "array", "value"]; start: int; end: int; members: list[Member]
@dataclass
class Member: key: str | None; key_span: tuple[int, int] | None; value: Node; line_start: int; line_end: int; comma: int | None

def parse(text: str, path: str) -> Node  # ConfigError(f"{path}:{line}: …") on a syntax error
def value(node: Node, text: str) -> Any  # semantic value (comments, trailing commas ignored)
def set_leaf(text: str, path: Sequence[str], value: Any, *, indent: str) -> str
def delete(text: str, path: Sequence[str]) -> str
def ensure_items(text: str, path: Sequence[str], ids: Sequence[Any], *, indent: str) -> str
def remove_items(text: str, path: Sequence[str], ids: Sequence[Any]) -> str
def indent_unit(text: str) -> str  # "\t" if the first indented line starts with a tab, else its run of spaces, else 4 spaces
```

- **Tokens.** Strings (with escapes), numbers, `true`/`false`/`null`, `{ } [ ] : ,`, `//…` and `/*…*/` comments, and
  whitespace. Comments are kept wherever they are.
- **Replacing a value.** Only the value's span is rewritten. A scalar is `json.dumps(v, ensure_ascii=False)`. A
  list or dict is dumped with `json.dumps(v, indent=<unit>)`, re-indented to the member's column.
- **Layout of a container.** An object or array is *inline* when its opener and closer are on the same line
  (`{"recommendations":["Vendor.ext"]}`), else *broken*.
- **Inserting a member (broken container).** It goes before the closing brace, on its own line, at the indentation
  of the last member (else the parent's plus one unit). A comma goes after the previous member when it has none. A
  trailing comma is added to the new member only when the previous last member had one (the file's style is kept).
  Missing intermediate objects are created the same way.
- **Inserting into an inline container.** The container stays inline: `,<sep>member` goes before the closer (no
  comma into an empty container). The separators are the file's own: a space after `:` and `,` when the existing
  members have one (`{"a": 1}`), none when they don't (`{"a":1}`); an empty container in a file with no evidence
  uses `json.dumps`'s `", "`/`": "`. A new value inside an inline container is dumped inline with the same
  separators. A union item appended to an inline array (`["Odoo.odoo"]` → `["Odoo.odoo","Vendor.ruff"]`) is the
  common case. The editor never expands an inline container; formatters may.
- **End of file.** The editor keeps the file's final newline or its absence (both target repositories'
  `extensions.json` have none). Only a file the editor creates gets one.
- **Deleting a member.** Its lines (`line_start..line_end`) are deleted when the member owns them whole. Otherwise
  only its span and its comma are deleted. Comment lines above it stay. The previous member's comma is fixed when
  the deleted member was last and the style has no trailing comma.
- **Deleting from an inline container** removes the member and one adjacent comma, keeping the line.
- **A missing file** is written as `jsonfmt.dumps(applied, product.json_style, jsonc=False)`: the product's JSON
  form (so a prettier-formatted repository stays prettier-stable), with a trailing newline and no header.
- **Not an object.** A file whose top level is not an object, or a path that crosses a non-object, is
  `ConfigError` (exit 2).

**Write.** Apply `set`, then `union`, then `forbidden`, in spec order. The result must re-parse and have the
intended semantic values; this is asserted.

**Check.** `--check` compares parsed values only. It yields one item per problem:

| Problem | Exit |
|---|---|
| `"<path>: differs"` | 1 |
| `"<path>: missing"` | 1 |
| `"<path>: <id> missing"` / `"<path>: <id> listed in <opposite>"` | 1 |
| `"forbidden key <path>"` | 1 |

A comment or formatting change is never drift. Item `wanted` is the edited text, so `--write` and the diff
agree.

**Retract** (every module off, and on in history):

- each `set` leaf whose current value equals the rendered value is deleted;
- each `ensure` id is removed from its array;
- a differing leaf is left, with the warning `"<path> <key> is the checkout's now that <module> is off …"`;
- `forbidden` keys are not restored;
- the file is never deleted.

`absent` (module on, `when` false) does the same.

**Semantic kind.** `semantic.kind` returns `"jsonc"` for `product.jsonc_globs`, so `.vscode/*.json` whole files
would also parse as JSONC.

**Fixtures.** `test_jsonc` uses neutral copies with the exact shapes of the target repositories' files: a one-line
`extensions.json` with no final newline and no spaces (`{"recommendations":["Vendor.ext"]}`), and a
`settings.json` with 2-space indentation, inline arrays (`"x.extraPaths": [".devenv/state/pythonpath"]`) and
flat dotted keys. odoo-nix-tools' own tests run the same edits on the real files.

### 5.6 NEW: anchored sentinel blocks (`sentinel`)

#### 5.6.1 Markers and parsing

A block is:

```
<indent><L> <block_begin with {name}>
<indent>…body…
<indent><L> <block_end with {name}>
```

`L` comes from `entry.comment`: `#` or `//`. With `html`, the lines are `markers.html_begin` and
`markers.html_end`, and there is no leader.

Parsing (`blocks.scan(text, product, comment) -> list[Found]`):

- **Matching.** A line matches a marker when, stripped, it equals the marker with some name `[a-z0-9][a-z0-9-]*`.
  Names with spaces (such as a foreign `# >>> kit-nix: install set (managed from units.txt) >>>`) never match. So
  existing product blocks with free-text names are always foreign.
- **Owned names.** These are the `block` values of every `sentinel` entry for this path (live or not).
- **Foreign recognizer.** Existing generators write markers that match neither shape, and whose begin and end
  differ (odoo-nix today: `# >>> odoo-nix: install set (managed from modules.txt) >>>` … `# <<< odoo-nix: install
  set <<<`). So, separately from owned markers, any line that is not an owned marker and whose text after the
  leader (`#`, `//`, or inside `<!-- -->`), stripped, matches `^>>> .* >>>$` is a *foreign begin*, and one
  matching `^<<< .* <<<$` a *foreign end*. Names are not compared: a foreign begin opens a foreign region that the
  next foreign end closes (foreign regions do not nest; a second begin before an end is ignored). An unclosed
  foreign region runs to the end of the file. A stray foreign end is ignored.
- **Foreign regions** are never read, moved or validated. They are excluded from `absorb` and from `claims`
  (§5.6.3, §5.6.4).
- **Errors** (exit 2, `path:line`) for an owned name:
  - a begin without an end;
  - an end without a begin;
  - an owned block opened inside another owned block, or inside a foreign region;
  - the same owned name twice;
  - an anchor whose insertion point lies inside a foreign region: `"<path>:<line>: the <name> block would go inside
    the foreign block that starts at line <n>; move one of them"`.

#### 5.6.2 Anchors

`eof`:

- **Insert** at the end of the file. One `\n` is added first when the file doesn't end with one. A missing file
  is created holding only the block.

`nix-attrset <a.b…>`:

- `scaffold/nixattrs.py` scans the file with a lexer that knows comments (`#…`, `/*…*/`), braces, and strings,
  and records each attribute set's span and its direct children. Strings:
  - `"…"`: `\` escapes the next character (`\"`, `\\`, `\$`); `$${` is a literal `${`; `${` opens an
    interpolation, which nests braces and strings until its matching `}`;
  - `''…''` (indented): `'''` is a literal `''`; `''$` is a literal `$` (so `''${X}` is the text `${X}`, not an
    interpolation, and not the end of the string); `''\` escapes the next character (`''\n`, `''\t`); `$${` is a
    literal `${`; `${` opens an interpolation as above; any other `''` ends the string;
  - paths and URIs need no rule: they contain no braces or quotes.

  odoo-ruckus's `flake.nix` (`port="''${ODOO_NIX_HTTP_PORT:-8069}"` inside an indented string) is the regression
  shape (neutral copy in `test_nixattrs`).
- The path is followed from the outermost `{ … }` of the file: each step is a child `name = {` whose value is an
  attrset (`name = {` on its line).
- **Indent:** the first direct child's indentation, else the opener's indentation plus the file's unit. The unit
  is the smallest positive indentation step seen, else 2 spaces.
- **Insert:** before the line holding the matching `};`.
- **Errors:**
  - an attrset on one line (`inputs = { a.url = "…"; };`) is exit 2: `"<path>: write \`inputs = {\` with its
    attributes on lines of their own; the managed block needs a line to go in"`;
  - a missing path is exit 2, naming the anchor.
- **Missing file** (any anchor): when the entry has `seed_template`, the seed is rendered first and the block is
  inserted into it (the seed must contain the anchor; conformance renders it). Without `seed_template`, `eof`
  creates the file holding only the block, and the other anchors are exit 2: `"<path> does not exist: the <name>
  block needs <anchor>"`. A product whose checkouts may lack the file (odoo addon repositories have no
  `flake.nix`) ships a `seed_template`.

`toml-array <t.k>`:

- **Finding the array.** It is the multi-line array `k = [` inside table `[t]`, or at the document root when `t`
  is empty. Dotted keys (`project.dependencies` under the root table) are also accepted.
- **Inline empty arrays.** An inline empty array `k = []` is rewritten `k = [\n<indent>]` first.
- **Errors.** Any other inline array is exit 2. So is a missing key.
- **Indent:** the first item's indentation, else the key's plus 4 spaces.
- **Insert:** before the closing `]` line.
- **Body:** the body must be valid array items: lines like `"x",`. The handler re-parses the result with `tomllib`
  and raises exit 2 on failure.

#### 5.6.3 Write, check and retract

- **Body.** `render(entry.template, ctx)` with the trailing newline stripped. Each non-empty line is prefixed by
  the inferred indent; empty lines stay empty.
- **Live entry.**
  - Block present: the body is replaced in place, wherever the block is (no move to the anchor).
  - Block absent: inserted at the anchor.
  - `--check` compares the body bytes. A difference is drift "the <name> block differs"; absence is drift
    "missing the <name> block".
- **Not live** (module off, or `when` false), with the block present: the block and its markers are removed
  (problem `"the <name> block goes with <module>"`). No history is needed, because markers prove sync wrote the
  content. This differs on purpose from merged keys.
- **`absorb: true`.**
  - Lines outside every owned block and every foreign region (§5.6.1) whose stripped text equals a stripped
    non-empty body line are removed on write.
  - `--check` reports them as drift: `"line N is in the <name> block"`.
  - Comments and blank lines are never absorbed.
  - Example: the `.gitignore` block absorbs a hand-written `/.venv`.
- **Several blocks in one file.** Each entry's edit is applied in entry order to the text the previous one
  produced. The plan merges them into one `Item` per path, whose problems are joined with `"; "`.

#### 5.6.4 `claims: nix-inputs` (first opt-in)

**Claimed names.** These are the attribute names the rendered body defines at its top level (the first
component of `name = …`, `name.url = …`, `name.inputs.x.follows = …`).

**Foreign definitions.** These are direct children of the anchor attrset outside every block whose first
component is claimed. Each is compared by `input_spec` (`url` or `follows`, normalised) plus the remaining
attributes, as whitespace-normalised text. The comparison is with the definition the block renders.

| Case | `--check` | `--write` |
|---|---|---|
| equal | drift "moves into the managed <name> block" | removed silently |
| differing, first opt-in (the block is absent from `HEAD:<path>` and from the working file) | exit 2 with a unified diff of the replacement | exit 2 unless `--force`; with `--force`, removed and replaced |
| differing, after opt-in | exit 2 `"<path> defines <input> outside the managed block"` | the same, unless `--force` |

Definitions inside a foreign region are never claimed.

**What the inputs block owns** is exactly its claimed names: the block renders only the inputs the product
manages for every checkout, and other inputs stay with whoever wrote them. For odoo-nix (plan Amendments, which
replace B3's dev-shell row where they conflict): the block owns `odoo-nix` and `standards-profile` in both shapes;
`ocb` and the `oca-*` inputs in addon repositories stay owned by addons mode, so they are neither claimed nor in
`pins.managed_inputs` (a hand-written `ocb` outside the block is left alone and never refused). The kit stub
tests this: in `bundle` shape a hand-written `core` input sits outside the block and survives write, check and
`--force` unchanged (§11.3 `test_kit`).

**Locks.** `lock_problems` and `lock_reasons` read inputs with `nixattrs.flake_input_specs`, which sees inputs
inside and outside blocks the same way. **Input rule** (frappe's regexes, made explicit): a direct child of the
`inputs` attrset is an input when it is `name.url = "…"` or `name.follows = "…"` (spec from that attribute), or
`name = { … }` (spec from its `url`/`follows`, else `None`, as frappe). Any other dotted child (`name.flake = …`,
`name.inputs.x.follows = …`) adds nothing on its own. `self` is never an input: `self.submodules = true;` (both
target projects' flakes) would otherwise read as an input with no node, so `stale_inputs` would report
"self (no node)" forever and `lock_reasons` (`have != set(inputs)`) would relock on every run.

### 5.7 Floors

See §5.4. The template helpers are bound to the file being rendered.

### 5.8 Retract and retire

- **Retract** is per handler (above). Frappe semantics are kept:
  - a whole file is deleted only with an intact header and empty regions;
  - local content is exit 2;
  - seeds stay;
  - merged keys are removed only when they equal the rendered value and the module was on in history (`ever`). A
    shallow clone that cannot answer is exit 3.
- **History.** `engine.history` walks commits that changed `identity.config_file`, `flake.lock` or an in-repo
  profile, up to `HISTORY_DEPTH = 300`. It resolves each state against the profile committed beside it, and adds a
  GitHub-hosted reading when `paths.github_marks` exist.
- **Retire.** The rules are the product manifest's `retire` plus the org profile's `[[retire]]`
  (`profile_retire`), minus `retire-keep` paths and globs. Never in `never_write` dirs. A link is retired unread
  when the rule doesn't look inside it. `deps_in_project` reads `[project].dependencies`. `contains` is as in
  frappe; `only_section` is as in frappe and `only_sections` generalises it (§4.3). The plan's odoo rules
  ("workflows whose content runs …") are `contains` rules; "`setup.cfg` holding only lint sections" is an
  `only_sections` rule; `eslint.config.{js,mjs,ts}` is a brace path.

### 5.9 First opt-in refusal

`product.first_opt_in` entries generalise frappe's `APP_TEMPLATE`.

**Refusal.** For a `whole` entry whose path is listed, sync refuses with exit 2 when all of these hold:

- the current file exists and differs from the render;
- its first line lacks `markers.managed`;
- `--force` is not given;
- `HEAD:<path>` is not managed;
- the current text is not one of `FirstOptIn.legacy(current, checkout)`.

**Message.** `FirstOptIn.message.format(path=…, name=identity.name, hint=…, diff=…)`. The core default
(`FIRST_OPT_IN_MESSAGE`) is `"<path> is the checkout's own and differs from what <identity.name> writes: sync would
replace it with the managed file. <hint> then pass --force to replace it. The replacement:\n<diff>"`. frappe passes
its own template, `"{path} is the app's own and differs from what frappe-init --app writes: … then pass --force to
replace it. The replacement:\n{diff}"` with its hint text inline, so its message is byte-identical.

The plan's odoo uses:

- `.pre-commit-config.yaml` (B3);
- `.envrc` (the dev-shell module's whole file with a local region), with `legacy` returning the `.envrc` texts
  `odoo-init` has written (for example odoo-ruckus's `watch_file pyproject.toml uv.lock modules.txt` +
  `use flake . --no-pure-eval`), so those opt in silently and any other hand-written `.envrc` is refused with its
  diff, as frappe does with `APP_TEMPLATE[".envrc"]`;
- `flake.nix` through `claims` (§5.6.4).

---

## 6. Commands, output and the sync phases

### 6.1 CLI composition

```python
@dataclass(frozen=True)
class Command:
    name: str
    register: Callable[[argparse._SubParsersAction, Product], None]  # adds the subcommand(s); set_defaults(func=run)
    sort_key: str = ""  # "" → name; frappe's order is by module name, so data-path/pin-path sort as "paths"

CORE_COMMANDS: tuple[Command, ...]  # ci, config, data-path, pin-path (sort_key "paths"), policy, profile, repo, sync

def help_text(product: Product, key: str, default: str) -> str: ...  # product.help.get(key, default)
def build_parser(product: Product, extra_commands: Sequence[Command] = ()) -> argparse.ArgumentParser: ...
def main(product: Product, argv: Sequence[str] | None = None, extra_commands: Sequence[Command] = ()) -> int: ...
```

- **Parser.** `build_parser` registers `CORE_COMMANDS + extra_commands` sorted by `(sort_key or name, name)`, so
  frappe's extra commands (module names `minibench`, `paths`, …) interleave as they do today. A duplicate name is
  `RuntimeError`. `--version` prints `identity.version`. Every handler receives `args.product = product`.
- **Help text.** The parser description is `identity.description` (frappe value
  `"frappe-nix's app standards tools. See <docs>."`). Each core command's description is a neutral core text,
  overridable by `product.help["<command>"]`; each core flag's help by `product.help["<command> <flag>"]` (frappe
  supplies its module docstrings and texts such as `--skip-lock`'s "relock the bench" and `--offline`'s "also
  FRAPPE_NIX_OFFLINE=1"). Product flags keep their frappe positions through `sync.add_arguments(parser, after)`
  (§6.2). Together these let frappe's `--help` stay byte-identical; the parity harness (§11.3) compares it.
- **Exit codes in `main`.** No command is exit 2 with help. A `StandardsError` prints `"<cli> <command>: <msg>"`
  and returns its code. Any other exception (import failures included) prints
  `"<cli> <command>: internal error: <Type>: <msg>"` and returns 3, never 1. The traceback prints under
  `<PREFIX>_DEBUG`.
- **Product entry point.** A product's console script is
  `def main() -> int: return standards_core.cli.main(PRODUCT, extra_commands=EXTRA)`.

### 6.2 Commands, flags, outputs

| Command | Flags (core) | Output | Exit |
|---|---|---|---|
| `config [KEY]` | `--json`, `--github-output modules`, `--pyproject`, `--lock`, `--profile-path` | one item per line; tables as JSON | 0/2/3 |
| `profile show` | `--explain`, `--format toml\|json`, `--pyproject`, `--lock`, `--profile-path` | `[profile]`, `[modules]`, `[config]` (with `# <layer>` under `--explain`) | 0/2/3 |
| `profile validate FILE` | `--templates DIR` | `"<file>: valid (extends <x>)"` | 0/2/3 |
| `profile list` | — | built-ins with descriptions; `(recommended)` marks the newest | 0 |
| `data-path REL` | — | absolute path | 0/2 |
| `pin-path INPUT` | `--lock`, `--writable` | directory | 0/2/3 |
| `policy` | `--commit-msg-file F` \| `--pr`; `--major-free`; `--base-branch` | problems on stderr (`::error::` under Actions); `"<cli> policy: ok"` | 0/1/2 |
| `ci <step>` | §8 | step text, plus `$GITHUB_OUTPUT` keys | 0/1/2/3 |
| `sync` | `--write`\|`--check`, `--standards`, `--profile-path`, `--force`, `--dry-run`, `--skip-lock`, `--offline` (also `<PREFIX>_OFFLINE=1`), `--only PATH[,…]`, `--format text\|json\|github`, `--expect-rev`, `--phase all\|a\|b\|preflight` (hidden), plus `sync.add_arguments` (below) | §6.4 | 0/1/2/3 |
| `repo apply\|audit\|rollout\|doctor\|targets` | §7 | §7 | §7 |

**Sync flag slots.** The core adds its sync flags in frappe's order. After each one it calls
`sync.add_arguments(parser, after=<that flag>)`, and once more with `after=""` at the end, so a product adds each
of its flags at the slot frappe had it (frappe: `--init-listing` after `--only`).

**Exit codes.** These are shared by every command, and the highest applicable code wins:

| Code | Meaning |
|---|---|
| 0 | clean |
| 1 | drift (`--check`), or a failed policy/guard/repo |
| 2 | invalid configuration or argument |
| 3 | environment: not a checkout, not git, unreadable lock, fetch/narHash failure, skew, `requires-<product>` mismatch, a bug |

### 6.3 Sync phases with product hooks

**Phase A** (`sync --write`, `--phase all|a`):

1. Build the plan for phase `a`. This creates the table with `--standards`, resolves provisionally when the org
   profile is not locked, and runs `hooks.preflight` and `check_engine_url`. Any item ≥ 2 → exit.
2. `sync.phase_a_before(plan, runner, args)`. For odoo projects, this is the submodule bootstrap.
3. `require_release_branch`: unless dry-run, offline, URL override, or a lock already on the branch. It checks the
   engine repository URL `https://github.com/{engine.repo}` and the branch `engine.release_branch.format(major=…)`.
4. Write the table. Apply the phase-`a` items (including the `sentinel` inputs block). Stage.
5. Lock: `nix flake lock` when the input set or a spec differs; `nix flake update <engine.flake_input>` when its
   `original` is not the rendered URL; `--override-input <engine> $<PREFIX>_URL_OVERRIDE` under the override.
6. Build again with the now-readable org profile. Re-apply, re-lock, stage.
7. `sync.phase_a_after(plan, runner, args, lock_changed)`.
8. `maybe_reexec`: when the locked engine's `version_file` differs from `identity.version`, hand over to
   `sync.reexec_argv(passthrough)` once (`<PREFIX>_SYNC_REEXEC=1`, exit code through `<PREFIX>_SYNC_RESULT`).

**Phase B** (`--phase all|b`):

1. `ensure_tools`: re-enter through `sync.reentry_argv` when any `sync.required_tools` is missing.
   `sync.before_reentry` runs first.
2. Build the plan for phase `b`. Print notices and the defaults warning. Any item ≥ 2 → exit.
3. Print the repo-settings notice for `releases` and `dependabot`+`auto-merge` turned on.
4. Apply items (write/delete) and stage.
5. Lock steps (§5.4).
6. `touched += sync.post_write(plan, runner, touched)`.
7. `touched += sync.phase_b_after(plan, runner, args, lock_changed, touched)`. Frappe value: node-lock seeds and
   `.#relock`.
8. Stage the touched and untracked lock paths.
9. Rebuild. Report what remains: app-owned rules, `hooks.checks` items (they are plan items, §3.3), and offline
   commands. Return the worst code.

**Offline phase A.** Under `--offline` (or `--skip-lock`), phase A runs steps 1, 2, 4 and 7 only: no
`require_release_branch`, no lock, no second build against a newly locked profile, no re-exec. This part of
phase A, `Runner` and `ensure_tools` come with the engine (stage 3, §12), so phase-`a` entries (the kit
`flake.nix` block) are testable before the Nix steps exist.

**`--check`** builds phases `a`+`b` in memory and runs nothing. Its items include `hooks.checks(plan)` (added by
`engine.build`). It adds:

- `"[tool.<table>] is missing"` when `--standards` would create it;
- `lock_problems` and `untracked_lock_problems` (not with `--only`);
- `skew.problems(product, plan, expect_rev)`.

`check_reported` turns any raised error into a one-finding report in `json`/`github`.

### 6.4 Report formats

- **text.** `"<path> (<strategy>): <problem>"` plus the diff, per finding. The last line is
  `"<name>: N file(s) drifted — run \`<sync_command>\`"` or `"<name>: clean"`.
- **json.**
  `{"status": "clean|drift|invalid|error", "<report_key>": {"rev", "version"}, "files": [{"path","strategy","module","problem","diff"}], "profile": {...}, "modules": {...}}`.
  The key order is fixed and is frappe's: `report.render` writes `status`, the engine key and `files`; `sync`
  then adds `profile` and `modules` when a plan exists.
- **github.** The text inside `::stop-commands::<token>`, then `::error file=…::` annotations. Diagnostics on stderr
  get their own stop-commands block.

---

## 7. Fleet and repo policy

### 7.1 Fleet

- **File.** `fleet.json` is validated against the composed fleet schema:
  `{schema: 1, profile?: str, <fleet_key>: [entry]}`.
- **Integration branch.** An entry's integration branch is `integration_branch`, else `branches[0]`, else the
  resolved `integration-branch`.
- **`private: true`** refuses `repo apply --phase full` (exit 1 for that repository) and marks rulesets n/a in
  audit A3.

`commands/repo/targets.py`:

```python
@dataclass
class Target:
    repo: str
    entry: dict
    root: Path | None = None
    opted_in: bool = False
    config: RepoConfig | None = None
    resolved: Resolved | None = None
    gates: list[str] = field(default_factory=list)
    profile_dir: Path | None = None
    # properties: cfg, modules, facts (config.facts, from the clone; {} without one), integration,
    # release_branch (hooks.release_branch / Branching.release_branch with facts), branches (entry["branches"] or [integration])

def load_fleet(product: Product, path: str | None) -> dict: ...
def entries(product: Product, repos: list[str], all_: bool, fleet_path: str | None) -> tuple[list[tuple[str, dict]], dict]: ...
def clone(product: Product, repo: str, branch: str | None = None, *, depth: int | None = 1) -> Path | None: ...  # tests patch
def fetch_profile(product: Product, ref: str) -> Path: ...  # tests patch
def load(product: Product, repo: str, entry: dict, fleet: dict, profile: str | None = None) -> Target: ...
```

`load` clones the default branch; when the integration branch differs and is opted in, it uses that branch. A
target that has not opted in is resolved with `{schema: 1, profile: ref, repo, **hooks.fleet_table(repo, entry, ref)}`
on GitHub.

`repo targets [--repo …] [--all --fleet F] [--format json|text]` is **new**. It prints, per target,
`{repo, branches, integration, opted_in, profile, private}`. This is the matrix source for the fleet-audit action.

### 7.2 apply / audit / rollout / doctor

These are frappe's §5.6-5.8 with the substitutions in §2.5. Their exit codes are unchanged:

- **apply:** 0, 1 when a repository failed, 2.
- **audit:** 0, 1 on any amber or red, 2.
- **rollout:** 0, 1, 2.
- **doctor:** 0, 1 when a setting is missing.

### 7.3 Repo policy rendering

**Settings.** `repo-policy/repo-settings.json`, with an org override at `templates/repo-policy/<same>`, filled by
`values()`:

| Placeholders | Source |
|---|---|
| `integration_branch`, `merge_methods`, `required_approvals`, `require_code_owner_review`, `require_thread_resolution`, `delete_branch_on_merge`, `required_checks`, `release_bypass`, `tag_prefix`, `tag_pattern`, `documentation` | frappe |
| `release_pattern`, `release_label` | `Branching` of `releases.branching` |
| `integration_bypass` | `bypass_actors(release_bypass)` when `releases` is on and the `Branching.flow` is `post-merge-bump`, else `[]` |
| `strict_checks` | default `false` |
| anything else | `hooks.repo_policy_values` (may override any of the above) |

`required_checks` is `[{"context": check_context.format(gate=g), "integration_id": 15368}]` in `ci.gates` order.

**Labels** are filtered by `ci.labels[name].module`. A label with no module is always applied.

**Rulesets (core default selection).**

- `integration-provisional.json` under `--phase provisional`, else `integration.json`.
- Under `full`, add `release-branch.json` when a release branch exists and `release_pattern` is non-empty.
- Under `full`, add `tags.json` when `releases` is on and `Branching.tags`.

`hooks.rulesets` replaces this selection.

**Branch patterns.** Two branch kinds are protected under `develop+series`:

- `integration.json` protects `refs/heads/{integration_branch}`, such as `develop`, with
  `"bypass_actors": "{integration_bypass}"`. Its `pull_request` and `required_status_checks` rules would refuse
  the bump commit that `ci bump --push` writes straight to the integration branch (§8.2), so under
  `post-merge-bump` the release-bypass actor may bypass it. Under every other flow the placeholder renders `[]`,
  exactly frappe's file;
- `release-branch.json`, named `"{release_label} (release automation only)"`, protects `{release_pattern}`, such
  as `refs/heads/*.0`: creation, update (no fetch-and-merge), deletion and non-fast-forward, with
  `{release_bypass}` actors.

Both the bump push and the fast-forward run with an App token, so a develop+series repository sets
`repo-policy.release-bypass = "integration:<app id>"`. `bypass_actors` (core code) accepts three forms:
`"github-actions"` (frappe), `"user:<id>"` (frappe) and `"integration:<id>"` →
`{"actor_type": "Integration", "actor_id": <id>, "bypass_mode": "always"}`. Its refusal text lists all three. The
core module schema's `release-bypass` pattern admits the three; frappe's own shipped schema keeps its two (§4.1),
so frappe never reaches the new form. The bump commit then gets its required checks from the push-triggered CI
run (§8.2, §11.4).

The frappe text is reproduced when `release_label = "version-*"` and `release_pattern = "refs/heads/version-*"`.

**Private repos.**

- Rulesets 403 on a private repository → `StandardsError` (`PRIVATE_RULESETS` text).
- A 422 on the Actions bypass gives the fallbacks text.
- `apply` never deletes unknown rulesets; it lists them.

### 7.4 Audit rows

```python
@dataclass(frozen=True)
class RowSpec:
    item: str  # the scorecard text
    module: str | None  # n/a when off; None = always
    evaluate: Callable[[Audit], tuple[str, str]]  # (green|amber|red|n/a|unknown, detail)
```

The core supplies these rows:

| Row | Check |
|---|---|
| A1 | the default branch is the integration branch |
| A2 | `releases`: the release branch equals the newest release tag (tags flow), or the integration head that passed its checks (post-merge-bump flow: release branch == integration branch head, or behind it only by commits whose checks are pending) |
| A3 | settings and rulesets |
| A4 | `sync --check` via the pinned tool |
| A5 | gates green on `ci.ci_workflow`, plus pr-policy and the nightly when configured |
| A9 | legacy files from the sync report |
| A10 | the last nix PR relocked and merged by a bot; the engine and profile current |
| A11 | a release tag made by a bot; tags flow only, otherwise n/a |

`product.audit_rows` adds rows or replaces a row by id. Rows print sorted by the integer after `A`.

---

## 8. CI helpers and composite actions

### 8.1 Steps (`<cli> ci <step>`)

These are frappe's table plus `bump`. `--repo` defaults to `$GITHUB_REPOSITORY`. Steps that read the release
configuration accept `--set KEY=JSON` (repeatable; a dotted cfg key overridden for this step) and
`--param KEY=JSON` (decision-table parameters). `ci.arg_aliases` registers a product's old flags on the steps that
have the target flag: `"--x": "--engine-repo"` is a plain alias; `"--x": "--param k"` and `"--x": "--set k"` turn
`--x V` into `--param k=V` / `--set k=V`. Aliases are listed in `--help` with `product.help` texts, so frappe's
reusable workflows (`--frappe-nix-repo`, `--docs-patterns`, `--frappe-major`) run unchanged.

| Step | Output to `$GITHUB_OUTPUT` | Notes |
|---|---|---|
| `verdict` | `verdict` | §8.3 |
| `automerge` | `result` | |
| `enable-automerge` | `result` | |
| `relock-inputs` | `state`, `moved` | |
| `relock-patch` | `empty` | |
| `refuse-workflows` | — | |
| `dispatch-ci` | `dispatched` | |
| `sweep` | `dispatched` | |
| `update-branches` | `updated` | |
| `fast-forward` | `moved` = `true` / `pending` / `false` | |
| `bump` | `bumped` (comma-separated units), `sha` | **new** |
| `drift` | — | |

**Exit codes:** 0; 1 when a refusal or guard fails; 2 for a bad argument; 3 for GitHub or git.

### 8.2 Release flows

**`tags` flow** (frappe `develop+version`). `ci fast-forward --release-branch B --sha S --tag T` works as
frappe's `FastForward`:

1. `scheme.tag_problem(T, cfg, facts)`, before any write (`cfg` and `facts` from `repoconfig.load` of the
   checkout, with `--set` overrides).
2. PATCH `heads/B` `{sha, force: false}` (POST on the first release).
3. Read back and peel the tag. B must equal T (the D8 guard).
4. `dispatch_after` workflows.

A 403 gives the workflow-file text.

**`post-merge-bump` flow** (`develop+series`, A-amendment) has two steps. The composite action runs both in order.

`ci bump --base origin/<series> [--push] [--dry-run]`:

```python
@dataclass(frozen=True)
class Bump:
    unit: ReleaseUnit
    old: str
    new: str
    level: Level
    commits: tuple[str, ...]  # short shas that set the level

def plan_bumps(root: Path, base: str, tool: ReleaseTool, scheme: VersionScheme, ctx: NS) -> list[Bump]: ...
def apply_bumps(root: Path, bumps: list[Bump], tool: ReleaseTool) -> str: ...  # writes; commits; returns the new sha
```

1. `units = tool.units(root, ctx)`. `cfg` and `facts` come from `ctx`.
2. For each unit, find `since`: the newest commit in `base..HEAD` that `ci bump` wrote and that changed the unit's
   version file (its last bump, not yet fast-forwarded), else `base`. A bump commit is recognised by its subject:
   `tool.commit_subject` turned into a regex (`{units}` → `.+`, the rest escaped), matched on the whole subject,
   with the unit's name in the `{units}` part. A feature commit that also edits the version file is therefore never
   `since`, and is counted in its own bump.
3. Take the non-merge commits in `since..HEAD -- unit.path`, excluding bump commits. Drop each commit whose files
   under `unit.path` are all `tool.exempt`.
4. Parse each message with `conventional.parse`:

   ```python
   @dataclass(frozen=True)
   class Commit:
       type: str
       scope: str | None
       breaking: bool
       subject: str

   def parse(message: str) -> Commit | None  # None: not conventional (no level)
   def level(commits: Iterable[Commit], types: Mapping[str, Level] = DEFAULT_TYPES) -> Level | None
   # DEFAULT_TYPES = {"feat": "minor", "fix": "patch", "perf": "patch"}
   ```

   `breaking` is true for `type!:` or a `BREAKING[ -]CHANGE:` footer, and gives `"major"`.
5. Skip a unit with no level. `old` is the version in the unit's version file at `since`. When the version at
   `HEAD` differs from `old`, someone edited it by hand since the last bump: the unit is skipped with the notice
   `"<unit>: version edited by hand since <since> (<old> → <head>); not bumped"`, so a hand edit is never bumped a
   second time. Otherwise `new = scheme.bump(old, level, cfg, facts)`, and `scheme.valid(new, cfg, facts)` must be
   `None`. (A product that wants hand edits refused outright adds a `pr_checks` rule.)
6. Write each version with `tool.write_version`. Commit `tool.commit_subject.format(units=", ".join(names))` as
   the git identity of the token (the action sets it). With `--push`, `git push origin HEAD:<integration>`,
   authenticated with the App token; the integration ruleset lets that App bypass its pull-request and
   status-check rules under this flow (§7.3). A rejected push (the branch moved) is exit 1, and the next run
   recomputes.
7. **Loop safety.** The bump commit's type is `chore`, so the run its push triggers computes no level and goes
   straight to the fast-forward step.

`ci fast-forward --release-branch <series> --sha <integration head> [--wait-checks SECONDS]` with no `--tag`:

```python
def wait_checks(repo: str, sha: str, contexts: set[str], timeout: float, sleep: Callable[[float], None] = time.sleep) -> str: ...
# polls github.check_state every 15 s until "green", "failed" or the timeout ("pending")
```

- The contexts are `github.required_contexts(repo, integration)`. When none are known, the step is exit 1:
  "nothing protects <integration>: refusing to fast-forward".
- `failed` → exit 1. `pending` → `moved=pending`, exit 0; the next trigger, `workflow_run` completed, retries.
- `green` → PATCH `heads/<series>` `{sha, force: false}`, POST on first creation. Read back; it must equal `sha`.
- The 403 text names the App with Workflows write.

**`check_state(repo, sha, contexts)`.** It reads `repos/{r}/commits/{sha}/check-runs?per_page=100` (paginated) and
the combined status `repos/{r}/commits/{sha}/status`. Per context name it uses only the newest check run (highest
`started_at`, then `id`), so a re-run replaces a failed attempt; a commit status counts when no check run has the
name. Then:

- every context present and green → green. A check run is green when `completed` with conclusion `success`,
  `neutral` or `skipped` (what GitHub's required checks accept); a status is green when `success`;
- any context failed → failed: conclusion `failure`, `cancelled`, `timed_out`, `action_required`,
  `startup_failure` or `stale`, or status `failure`/`error`;
- otherwise (missing, queued, in progress, status `pending`) pending.

**Required checks on the bump commit.** The contexts appear on the bump commit only when CI runs on `push` to
the integration branch. Conformance (§11.4) therefore requires, for every `Branching` whose flow is
`post-merge-bump`, that `ci.ci_workflow` and every workflow in `ci.contexts` (the workflows that produce required
contexts) trigger on `push` to the integration branch. A job that only makes sense on pull requests stays in the
push run and is skipped by its `if:`, which counts as green.

### 8.3 Auto-merge verdict with a product decision table

```python
@dataclass(frozen=True)
class DecisionRule:
    ecosystem: str  # fetch-metadata package-ecosystem: "github_actions", "nix", "submodules", …
    verdict: Literal["auto", "review", "relock", "close"]
    groups: tuple[str, ...] = ()  # match when dependency-group is one of these
    names: tuple[str, ...] = ()  # fnmatch, case-insensitive, any dependency name; tokens below
    directories: tuple[str, ...] = ()  # match when the "/"-normalised directory is one of these
    update: Literal["any", "major", "minor-patch"] = "any"  # minor-patch includes a grouped PR without a type
    ungrouped_named: bool = False  # match only when there is no group and at least one name
    sticky: bool = False  # policy "all" never lifts this verdict to auto

def decide(product: Product, ecosystem: str, update_type: str = "", group: str = "", dependency_names: str = "",
           directory: str = "/", params: Mapping[str, list[str]] = EMPTY, engine_repo: str = "",
           policy: str = DECISION_TABLE) -> str: ...
```

**Rule matching.**

- Rules are evaluated in order, filtered by ecosystem. All a rule's set conditions must hold, and the first
  matching rule wins.
- No match → `review`.

**Tokens in `names`.**

| Token | Expands to |
|---|---|
| `$engine` | `engine_repo` |
| `$rollout-inputs` | `product.rollout_inputs` |
| `$param:<k>` | `params[k]` (`--param k=<JSON list>`) |

**Policy `all`.** It turns `review` into `auto` unless the matched rule is `sticky`. `relock` and `close` are
unchanged. `nix_verdict(relock_state)` is unchanged.

**Frappe value.** The §4.5 table as rules:

- github_actions:
  - `$engine` → review, sticky;
  - groups docs-actions/pilot → review;
  - names `$param:docs-patterns`, `frappe/pilot*` → review;
  - group actions → auto;
  - `ungrouped_named` → auto.
- npm: directory /docs-site → review; minor-patch → auto.
- uv: minor-patch → auto.
- pre_commit: group test-utils or name `*agritheory/test_utils*` → review; group hooks → auto; `ungrouped_named` → auto.
- nix: `$rollout-inputs` → close; any → relock.
- submodules → auto.

**Plan value for odoo.** `submodules` → `relock` ("submodules → relock").

### 8.4 update-branches and drift

These are copied (§2.4). The update-branches bot rule:

- Dependabot PRs, plus bot PRs on `standards/` branches;
- never release-please, `autorelease:` labels or forks;
- `expected_head_sha`, a limit per run, and dispatch on a `GITHUB_TOKEN` update.

### 8.5 Composite actions (`python-devops-nix/.github/actions/`)

Every action:

- is `runs: using: composite`;
- pins every `uses:` by SHA with a version comment (the `ci-pins` check);
- passes inputs through `env:`, never interpolated into scripts (the zizmor template-injection rule);
- names the product CLI through the `cli` input, so no product name is hard-coded.

| Action | Inputs (default) | Does |
|---|---|---|
| `standards-install` | `cli` (req.), `engine-input` (req.), `install-spec` (req., with `{owner}`, `{repo}`, `{rev}`), `env-prefix` (req.), `python` (`3.12`), `override` (`""`, a checkout path to install instead), `fetch-token` (`""`) | Reads `.nodes[.nodes.root.inputs[<engine-input>]].locked` from `flake.lock` and runs `uv tool install --python <python> "<spec>"`. Exports `<P>_EXPECT_REV=<rev>`, or, with `override`, installs the path, exports `<P>_ALLOW_SKEW=1` and an empty `<P>_EXPECT_REV`. Exports `<P>_FETCH_TOKEN` when given. Outputs `rev`, `version` |
| `standards-pr-policy` | `cli`, `integration` (req.), `token` (`github.token`), `major-free` (`false`), `base-branch` (`main`) | Runs `<cli> policy --pr` with `EVENT_NAME`, `BASE_REF`, `HEAD_SHA`, `HEAD_REF`, `PR_TITLE`, `REF_NAME`, `PR_AUTHOR`, `INTEGRATION` from the event context |
| `standards-release-ff` | `cli`, `release-branch` (req.), `sha` (`github.sha`), `tag` (`""`), `bump` (`false`), `base` (`""` → `origin/<release-branch>`), `wait-checks` (`0`), `token` (req.: an App token for a push or a workflow-file move), `git-user` (`""` → the token's bot), `dry-run` (`false`) | With `bump`: `<cli> ci bump --base … --push`, then fast-forward to the new sha. Without it: `<cli> ci fast-forward …`. Outputs `moved`, `bumped` |
| `standards-deps-automerge` | `cli`, `integration` (req.), `gates` (`""` → the checkout's), `merge-method` (`squash`), `policy` (`decision-table`), `params` (`""`, newline `k=JSON`), `engine-repo` (`""`), `relock` (`""`), `moved` (`""`), `token` | Runs `dependabot/fetch-metadata` (SHA-pinned v3), then `<cli> ci automerge --pr-url … --ecosystem … --update-type … --dependency-group … --dependency-names … --directory …`. Output `result` |
| `standards-update-branches` | `cli`, `integration` (req.), `dispatch-ci` (`false`), `contexts` (`""`, newline `CHECK=WORKFLOW`), `limit` (`10`), `token` | `<cli> ci update-branches …`. Output `updated` |
| `standards-fleet-audit` | `cli`, `fleet` (req.), `issue-repo` (`""`), `against` (`pinned`), `audit-token` (req., read-only), `issue-token` (`github.token`) | `<cli> repo audit --all --fleet … --format md --out "$RUNNER_TEMP/scorecard.md" [--issue --issue-repo …]` with `GH_TOKEN=<audit-token>` and `<P>_ISSUE_TOKEN=<issue-token>`. Appends to `$GITHUB_STEP_SUMMARY`; exit is the audit's |

`selftest-standards.yml` (pull requests touching `standards/` or `.github/actions/standards-*`):

1. installs the stub product from `standards/tests/stubs/stub` through `standards-install` with `override`;
2. runs `standards-pr-policy` on the PR;
3. runs `standards-release-ff` with `dry-run: true` against a throwaway ref;
4. runs `<cli> ci verdict` for each recorded fetch-metadata case;
5. runs `standards-update-branches` with `limit: 1` against a repository with no bot PRs (no-op).

---

## 9. Pins and skew

### 9.1 `flake.lock`

- `input_node(product, lock, name)` checks two exceptions before the general rule:
  - for `product.pins.engine_pinned` names: read only through the engine node, when the lock has one;
  - for `standards-profile`: the root's only.
- Otherwise it returns the root's input, else the engine's.
- `locked_pin` validates names, hosts and revs as in frappe. An engine-pinned input must lock the declared
  `(owner, repo)` on github.com.

### 9.2 `pin-path` and profile fetch

- `pin_path(product, name, lock_path, *, lock=None, writable=False)` is copied: the store path when present (and
  not `writable`), else `<pins.dir>/<name>-<rev>/`. The fetched tree is checked against the narHash, and a mismatch
  is exit 3.
- `<P>_FETCH_TOKEN` is sent over https or loopback http only; redirects drop it.
- `<P>_PIN_URL` gives mirrors and tests a tarball URL template.
- **The org profile** is always the root input `standards-profile`. Phase A renders and locks it through the
  product's managed inputs. `--check` reads it with `pin_path`. No-Nix CI runs `<cli> pin-path standards-profile`.
- **Not-opted-in fleet targets** fetch the profile with `targets.fetch_profile` (a shallow clone of the ref).

### 9.3 `requires-<product>`

See §4.4 step 11 and §4.5.

### 9.4 Skew rows (`common/skew.py`)

```python
def problems(product: Product, plan: Plan, expect_rev: str | None) -> list[Item]: ...  # [] under <P>_ALLOW_SKEW=1
```

1. **expect-rev:** `--expect-rev` ≠ the engine rev locked in `flake.lock` → exit 3. The message is "this
   <distribution> was installed from X, flake.lock pins <engine> Y".
2. **Callers:** every
   `{owner}/{name}/.github/workflows/<caller_stem>.ya?ml@<sha>[ # v<ver>]` in tracked `.github/workflows/*.y*ml`
   must have `sha` = the locked rev and `ver` = `identity.version`. Otherwise exit 3, one item per file. The owner
   and name come from the locked engine node.
3. **Core (new):**
   - `core_rev = standards_core.revision()`;
   - `locked = flakelock.base_rev(product, lock)`, the rev at node path `[engine.flake_input, engine.base_input]`;
   - when both are known and differ → exit 3: `"version skew: standards-core <core_rev[:12]> is not the
     <base_input> <locked[:12]> that <engine.flake_input> locks"`.

   A path or editable install has neither `direct_url.json` `vcs_info` nor `_source.json`, so the row is skipped.

`standards_core.revision() -> str | None`:

1. `json.loads(importlib.metadata.distribution("standards-core").read_text("direct_url.json"))["vcs_info"]["commit_id"]`,
   when it is present and is 40 hex characters;
2. else `json.loads((files("standards_core") / "_source.json").read_text())["rev"]`, when it is 40 hex characters;
3. else `None`.

`<P>_ALLOW_SKEW=1` (self-tests, `audit --against latest`, drift) suppresses all three rows. `lock_problems` also
skips the engine input under it.

---

## 10. Packaging

### 10.1 `standards/pyproject.toml`

```toml
[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[project]
name = "standards-core"
version = "1.3.0"                    # release-please extra-file; starts at 1.3.0 (python-devops-nix v1.2.x released)
description = "Managed files, profiles, CI steps and fleet tools for product plugins"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = ["jinja2>=3.1,<4", "tomlkit>=0.13", "packaging>=24", "pyyaml>=6"]

[project.optional-dependencies]
test = ["pytest>=8"]

[tool.flit.module]
name = "standards_core"
```

- No console script: products own the CLI.
- Every runtime file lives in `standards_core/data/` (flit includes the package directory).
- `_source.json` exists only in the Nix build.
- The repository `ruff.toml` already covers `standards/`.
- release-please gains `standards/pyproject.toml` and `standards/standards_core/__init__.py` as extra-files.

### 10.2 `lib/standards.nix`

`{ lib }:` returns `{ package, pins, conformance, check }`. `lib.standards` follows the flake's `lib/<name>.nix` rule.

```nix
package = { pkgs, python ? pkgs.python3, rev ? null }:   # buildPythonPackage over ../standards (fileset minus tests, *.pyc)
  # flit-core; deps from python.pkgs (jinja2 tomlkit packaging pyyaml); pythonRuntimeDepsCheck on;
  # postInstall writes $out/${python.sitePackages}/standards_core/_source.json = { rev } when rev != null.
  # Products pass rev = inputs.python-devops-nix.rev.
pins = { pkgs, src, product, owner }:                    # lib.checks.mk script: flake.lock node "python-devops-nix" rev
  # == the sha in src/py/**/pyproject.toml's standards-core URL == every <owner>/python-devops-nix/...@sha in
  # .github/** (python -m standards_core.testing.pins --root src --owner <owner>)
conformance = { pkgs, product, package, module }:        # runs python -m standards_core.testing.conformance <module>
  # with the product package on PYTHONPATH (module = "kit_nix_tools.product:PRODUCT")
check = { pkgs, interpreters ? pythonLib.defaultInterpreters }:  # the core suite: pytest standards/tests on each
```

**Checks:**

- `checks/lib-standards.nix`: `self.lib.standards.check { inherit pkgs; }`. It runs pytest on every preset
  interpreter, with git in PATH and `GIT_CONFIG_GLOBAL=/dev/null`.
- `checks/lib-standards-neutral.nix`: a `lib.checks.mk` script.
  - It runs `grep -rniE 'frappe|odoo|avunu' standards/ .github/actions/standards-*/ lib/standards.nix checks/lib-standards*.nix --exclude=DESIGN.md --exclude-dir=__pycache__`.
  - Any hit fails, with the matches printed.
  - The pattern is assembled from fragments (`"fra" + "ppe"`, …) in the Nix file, so the check file does not
    match itself.
  - It exists from stage 1 (§12), so every copied file is rewritten on copy. Known spots: the `product.py`
    comments, `github-ruleset.schema.json`'s `$comment`, docstrings in `github.py`/`relock.py`/`release.py` that
    name the product, frappe's `Release-As` text (formatted from `identity.name`, §2.5), and test strings.
  - Product *concepts* are a review matter, not a grep: the core reads no product context key (for example
    `tools_deps`; `UvLockHandler` takes a `packages` callable instead, §5.1).
- The frappe parity harness (§11.3) names the product and so lives outside the grep's paths, in
  `python-devops-nix/parity/frappe/`.

### 10.3 How products depend on the core

- **pip.** The product declares
  `"standards-core @ git+https://github.com/<owner>/python-devops-nix@<sha>#subdirectory=standards"` in
  `[project].dependencies`. uv and flit accept the URL dependency inside a URL-installed product (A4). In
  development, a path source (`[tool.uv.sources] standards-core = { path = "…/standards", editable = true }`) is
  used until push (plan execution mechanics).
- **Nix.** The product package lists `base.lib.standards.package { inherit pkgs python; rev = inputs.python-devops-nix.rev; }`
  in `dependencies`.
- **Pins check.** The product adds `lib.standards.pins` as a `standards-core-pins` check: the lock node, the URL and
  the action `@sha` must be equal.
- **Reachability.** "Reachable from main or a `v*` tag" needs the network, so it is the same module with
  `--reachable https://github.com/<owner>/python-devops-nix` in the product's CI (a `git fetch` plus
  `git merge-base --is-ancestor`), not a sandboxed check.
- **Moving pins.** The product's `bump-base` rewrites all three, plus the dev `uv.lock`.

**docs/library.md:**

- Design rule 6 is amended. Shared Python stays vendored, except `standards-core`, which is a real distribution
  because no-Nix CI installs product tools with `uv tool install` from git.
- A "Standards" section lists `lib.standards.*`, the checks and the six actions.

---

## 11. Tests

### 11.1 Stub products (`standards/tests/stubs/`)

Each stub is an installable directory: a `pyproject.toml` depending on `standards-core` by path, and a package with
`data/`.

**`stub/` (`stub_nix_tools`), the general engine:**

| Field | Value |
|---|---|
| `identity` | name and cli `stub-nix`, table `stub-nix`, `STUB_NIX`, report key `stub_nix`, `stub-init --sync` |
| `engine` | `example/stub-nix`, `release-{major}`, caller stem `stub-*` |
| modules | the core six plus `editorconfig`, `python-lint`, `tests`, `hygiene`, `extras`. `extras` needs `commits`, and is GitHub-only |
| branching | `develop+version` (tags flow; `release_pattern` `refs/heads/version-*`) and `main+tags` |
| version scheme | `stub-major`: it refuses `!`, `BREAKING CHANGE` and `Release-As` with another major |

Manifest:

- `flake.nix` and `.envrc`, `whole`, phase `a`, with local regions;
- `.editorconfig`, `whole`, ini, local region;
- `committed.toml`;
- `tools/pyproject.toml` (whole) and `tools/uv.lock` (`uv-lock`, `relock_when`, floors kind `uv`);
- `pyproject.toml`, `toml-merge`, `TomlGroups` for `[tool.ruff]` and `[tool.coverage.*]`, with a `fail_under` seed;
- `.git-blame-ignore-revs`, a seed;
- `.github/dependabot.yml`, with an `updates` region;
- `.github/workflows/ci.yml`, a caller with `pin()`;
- an overridable template;
- retire rules (`.flake8`, `setup.cfg` `only_section` and a second `setup.cfg`-shaped rule with `only_sections`
  `[lintcheck, sortcheck]`, `lint.config.{js,mjs}` braces, `requirements.txt` `deps_in_project`, workflow
  `contains`);
- floors.

`first_opt_in` covers `flake.nix` and `.envrc`, each with one legacy text. `fixtures` are `plain` and
`with-extras`. The decision table is frappe's, with neutral names. The stub keeps every default that reproduces
frappe (`JsonStyle()`, `config_required = True`, the default first-opt-in message), and the parity harness runs
it with Frappe string values (§11.3).

**`kit/` (`kit_nix_tools`), the new capabilities:**

| Area | Value |
|---|---|
| Identity | `kit-nix`, `KIT_NIX`, markers `# >>> kit-nix: {name} (managed) >>>`; `config_required = False` (a `bundle` checkout has no root `pyproject.toml` and no `flake.nix`) |
| Shape-dependent discovery | `shape` from: the explicit `[tool.kit-nix] shape`; else root `*/__unit__.toml` → `bundle`; else a `core` gitlink or `units.txt` → `workspace`. `series` comes from unit versions, else the `.kit-series` file, else a `^\d+\.0$` branch name. `hooks.derive` reads the same sources (through the checkout) to set the cfg's series default; `Branching.release_branch(cfg, facts)` returns `facts["series"]` |
| JSON style | `JsonStyle(indent="  ", width=80, trailing_commas="none")` (prettier's form) |
| `jsonc-merge` | `.vscode/settings.json` (a forbidden key `Ide.serverConfigPath`; a `never` list with a `{"prefix": "py-envs."}` form) and `.vscode/extensions.json` (`recommendations`/`unwantedRecommendations` union), both fixtures in the target repositories' one-line, inline-array, no-final-newline shapes |
| `json-merge` | `package.json` with a `JsonGroups` owning `devDependencies` pins (reconciled as floors) beside the checkout's own scripts |
| `toml-merge` | `tools/pyproject.toml`, one entry for modules `python-lint` (`[project]` pins reconciled as floors, `[tool.uv]`) and `tests` (`[tool.coverage.*]`, `fail_under` seed) |
| `sentinel` | `flake.nix` `inputs` block (anchor `nix-attrset inputs`, `claims: nix-inputs`, phase `a`, a `seed_template` for `bundle` checkouts that have no `flake.nix`); `pyproject.toml` (anchor `toml-array project.dependencies`); `.gitignore` (anchor `eof`, `absorb`). The fixture flake has `self.submodules = true;` in `inputs`, a `''${PORT:-8069}` indented string, and, in `bundle` shape, a hand-written `core` input the block does not own. Foreign blocks `# >>> kit-nix: install set (managed from units.txt) >>>` … `# <<< kit-nix: install set <<<` (begin and end differ) sit in the fixture and must stay byte-identical |
| Dependabot | `.github/dependabot.yml` with one entry per (ecosystem, branch) from a `dependabot.target-branches` parameter, and a local-region entry for another branch |
| Series scheme | `kit-series` (`<series>.x.y.z`, `!` → x, feat → y, fix/perf → z; `valid` checks the series prefix against `facts["series"]`). `ReleaseTool` `unit-manifest` reads `version = "…"` in `<unit>/__unit__.toml`; exempt `tests/**`, `README.md`, `i18n/**` |
| Branching | `develop+series` (`release_pattern` `refs/heads/*.0`, flow `post-merge-bump`); its `ci.yml` template triggers on push to the integration branch |
| Checks | `hooks.checks`: every unit has a `README.md`; every submodule has `branch = <series>` |
| Fleet | `fleet-entry.schema.json` with `repo`, `shape`, `branches`, `private`, `documentation`, `integration_branch` |
| Decision table | `submodules` → `relock` |
| First opt-in | `.pre-commit-config.yaml`, and `.envrc` with legacy texts |
| Policy | `committed_argv` returns `["committed"]` |

**Org fixture.** `tests/fixtures/profiles/example-org/` holds `profile.toml` (extends `recommended@1.0`), a
`templates/SECURITY.md.j2` extra file, a `[[retire]]`, and an override.

### 11.2 Ported frappe tests

Every test is a `unittest.TestCase` collected by pytest. The rewrite rules:

- tabs → 4 spaces;
- `frappe_nix_tools` → `standards_core`;
- `cli.main(argv)` → `run_cli(STUB, *argv)`;
- "frappe-nix" strings → `stub-nix`;
- `demo_app` → `demo_unit`;
- the `FakeGitHub`, `write_app` and `AppCase` helpers move to `tests/support/`.

| frappe test | Core test | Port notes |
|---|---|---|
| `test_report.py` | `test_report.py` | `report_key` in the JSON |
| `test_nar.py` | `test_nar.py` | as is (the `TREE_NAR_HASH` tree hash is product-neutral) |
| `test_flakelock.py` | `test_flakelock.py` | the engine-pinned names come from the stub |
| `test_pins.py` | `test_pins.py` | env vars `STUB_NIX_*` |
| `test_paths.py` | `test_paths.py` | data-path product-first and core fallback |
| `test_schema.py` | `test_schema.py` | adds composition and cross-root `$ref` |
| `test_config.py` | `test_config.py` | `known-apps`/siblings cases dropped; `host_rules` hook case added |
| `test_profiles.py` | `test_profiles.py` | stub built-ins |
| `test_pyproject.py`, `test_repo.py`, `test_common.py`, `test_cli.py` | same names | `app_name` cases → `load_checkout` |
| `test_scaffold.py` | `test_scaffold.py` | globs, jsonfmt, manifest. Frappe-only: compat, unchecked-js, npm floors |
| `test_sync.py` | `test_sync.py` (skew cases → `test_skew.py`) | round trip, json (frappe's key order), `--only`, raised errors, untracked locks, local regions, retire, floors (uv), `hooks.checks` failing `--write` too. Frappe-only: package.json, init-py, discovery (tsconfig), zizmor. Its version-skew and `--expect-rev` cases move to `test_skew.py` |
| `test_standards.py` | `test_standards.py` | opt-out, minimal, toggle off/on, round trip, retraction after a commit, origin move, in-repo profile, locked profile bump, retire-by-function, first opt-in |
| `test_bootstrap.py` | `test_bootstrap.py` | lock reasons, pinned engine, phase-A lock, re-exec, passthrough via the hook, re-entry, handover result, preflight, org profile first lock |
| `test_policy.py` | `test_policy.py` | scheme `stub-major` replaces frappe-major; `pr_checks` hook case |
| `test_relock.py`, `test_ci_branches.py`, `test_ci_steps.py` | same names | verdict through the stub's decision table; fast-forward with the scheme guard; tagless fast-forward; `wait_checks` with fake sleep; `ci.arg_aliases` |
| `test_repo_policy.py`, `test_repo_apply.py`, `test_repo_audit.py`, `test_repo_doctor.py`, `test_repo_rollout.py` | same names | `release_pattern` values; `integration:<id>` bypass and `{integration_bypass}` (`[]` under the tags flow, the App under post-merge-bump); private; audit core rows only; rollout steps from the hook |
| — | `test_github.py`, `test_jsonc.py`, `test_blocks.py`, `test_nixattrs.py`, `test_jsonmerge.py`, `test_bump.py`, `test_conventional.py`, `test_compose.py`, `test_product.py`, `test_skew.py`, `test_kit.py`, `test_repo_targets.py`, `test_conformance.py` | **new** (§11.3) |

**Not ported** (Frappe-only or replaced): `test_gh.py` (tests `common/gh.py`, which is not copied; §1.2),
`test_ci_callers.py`, `test_assets.py`, `test_listing.py`, `test_icon.py`,
`test_compat`, `test_gate.py`, `test_ratchet.py`, `test_testmap.py`, `test_test_report.py`, `test_known_apps.py`,
`test_deps.py`, `test_apps*.py`, `test_app_tools.py`, `test_frappe_init.py`, `test_restore.py`,
`test_runtime_pin.py`, `test_composition.py`, `test_minibench.py`, `test_nightly.py`.

### 11.3 New tests

| Test | Covers |
|---|---|
| `test_github.py` | `common/github.py` directly: transport injection, pagination, `required_contexts`, `gate_contexts`; `check_state` with `success`, `neutral` and `skipped` (green), each failed conclusion including `startup_failure` and `stale`, a missing context (pending), a re-run whose newest attempt wins, and a status-only context |
| `test_jsonc.py` | comments and foreign keys kept; tabs vs spaces inferred; trailing-comma style kept; `set` per sub-key; `union` with opposite removal; forbidden deletion and its drift; `--check` ignores reformatting; retract leaves edited leaves with a warning; non-object top level is exit 2; created file form (`product.json_style`); inline object and inline array insertion with and without spaces after separators; a file with no final newline stays without one; `never`/`forbidden` prefix forms |
| `test_blocks.py` | insert at each anchor; replace in place; inferred indent (2, 4, tab); retract on module off and on `when` false; unbalanced, duplicate and nested owned blocks are exit 2 with `path:line`; foreign blocks untouched byte for byte, including free-text foreign markers whose begin and end differ; an owned block anchored inside a foreign region is exit 2; `absorb` skips foreign regions; two blocks in one file; missing file with and without `seed_template` |
| `test_nixattrs.py` | `flake_input_specs` parity with frappe's regex results on frappe-shaped flakes; strings with `{`, `''` strings with `''${`, `'''` and `''\n`, `$${` in both string kinds, comments; `self.submodules = true;` is not an input (no "self (no node)", no relock from `lock_reasons`); neutral copies of both target projects' flakes; inline attrset refusal; claims equal → moved, differing → exit 2 / `--force`; an unclaimed input outside the block survives |
| `test_jsonmerge.py` | `JsonGroups` set, retract when equal and was on, turn-off warning, `reconcile` keeping a hand-raised pin, unmanaged keys untouched, output in `json_style` |
| `test_conventional.py`, `test_bump.py` | levels; exempt-only commits; `since` after an unforwarded bump; a feature commit that also edits the version is counted, not taken as `since`; a hand edit since the last bump skips the unit with its notice; push rejection is exit 1; loop safety; `kit-series` arithmetic (`18.0.1.2.3` + minor → `18.0.1.3.0`) with the series from `.kit-series`; the integration ruleset renders the App bypass under post-merge-bump |
| `test_compose.py` | profile/app/fleet composition; core-module merge; product-shipped document wins |
| `test_product.py` | `product.py` imports and every dataclass builds with its defaults (run on python311 by `lib-standards`; the 3.11 `mappingproxy` default failure is the regression); every `__post_init__` refusal; identity hashing (two equal products → separate caches) |
| `test_skew.py` | the three rows, `direct_url.json` and `_source.json` readers, ALLOW_SKEW; the version-skew and `--expect-rev` cases moved from frappe's `test_sync.py` |
| `test_kit.py` | shape-dependent discovery; offline sync then check on both shapes (including a `bundle` checkout with no config file: exit 2 with the hint, then `--standards` creates it); `minimal` retracts blocks and jsonc leaves; series-dependent `release_branch` and derived cfg; `hooks.checks` failing both `--write` and `--check`; multi-section `setup.cfg` retirement; per-branch Dependabot entries and a local-region entry for another branch; `.envrc` first opt-in; exit codes 1/2/3 |
| `test_repo_targets.py` | `repo targets` text and JSON rows, `branches` fallback, `private`, a not-opted-in target, facts from the clone, clone directory named after the repository |
| `test_conformance.py` | both stubs conform; a broken stub copy fails each rule of §11.4 once |

**Frappe parity harness** (`python-devops-nix/parity/frappe/`, outside `standards/` so the neutrality grep does not
see it). A script, not a flake check (python-devops-nix has no frappe-nix input, and must not: frappe-nix locks
python-devops-nix). Given a frappe-nix checkout path, it builds the stub product with Frappe string values
(identity, markers, help texts, first-opt-in message, `url_placeholders`, `ci.arg_aliases`) and compares, on a
fixture app, against frappe-nix-tools' own output: `sync --format json` (key order), header lines, `profile show
--format json` (cfg key order), the first-opt-in refusal text, `repo-policy` renderings and `--help`. It runs
from stage 3 on (§12) and in python-devops-nix CI when a frappe-nix ref is configured; S0 later takes it over.

### 11.4 Conformance (`standards_core.testing.conformance.check(product)`)

The products run it too. It returns a list of problems; empty means conformant. It grows with the stages (§12):
each part arrives with the code it exercises, so a product building on an early core PR can already run it.

**Static part (stage 2, core PR 2):**

- `Product` builds (`__post_init__`).
- Every core module is present. `modules.schema.json` defines exactly `modules.names`, each with `enable`.
- The composed schemas load. Every built-in profile validates. `minimal` turns on only `modules.required`.
- Every manifest fragment parses; every handler named is a core strategy name or in `product.handlers`; every
  template and `seed_template` exists in a root; every `uses` key resolves (`schema.node_at`).
- Every env var the product names with the core's prefix rule starts with `env_prefix + "_"`.

**Render part (stage 3, core PR 3):**

- Every fixture: sync `--write --offline` then `--check` exits 0 under `minimal` and every `recommended@*`, and two
  renders are byte-identical.
- Every `seed_template` contains its entry's anchor.

**CI part (stage 7, core PR 4):**

- Every `DecisionRule` uses a verdict and an `update` value that exist.
- `ci.gates` and `ci.labels` are consistent with `repo-policy` placeholders.
- For every `Branching` with flow `post-merge-bump`: rendering each fixture under it, `ci.ci_workflow` and every
  workflow in `ci.contexts` (YAML-parsed) have `on.push.branches` matching the integration branch (§8.2).

---

## 12. Implementation stages

Each stage leaves `nix flake check` (lib-standards, lib-standards-neutral) and `pytest standards/tests` green, with
only the files it lists. Each stage depends only on earlier ones. Stages are grouped into the four core pull
requests the plan's milestones name ("core PR 1–4"; plan "Workstreams" and B7).

| Core PR | Stages | What a product can build on it (plan B7) |
|---|---|---|
| PR 1 | 1 | nothing yet: the package, the leaves and the checks |
| PR 2 | 2 | B7.3: schemas, series data, built-ins, `profile show/list`, fixtures, discovery; the static conformance part |
| PR 3 | 3, 4, 5, 6 | B7.4: templates and manifest entries, `sync`, `init --sync/--check`, both phases; the render conformance part |
| PR 4 | 7, 8, 9 | B7.5 and the release: gates, workflows, policy, `repo apply\|audit\|rollout\|targets`, fleet audit, the actions; the CI conformance part |

1. **Skeleton and leaves** (PR 1).
   - Package and infrastructure: `standards/pyproject.toml`, `README.md`, `standards_core/__init__.py`,
     `product.py`, `common/{report,data,nar,flakelock,pins,github,repo,pyproject}.py`,
     `scaffold/{globs,jsonfmt,semantic,regions,floors}.py`, `testing/fakes.py`, `data/README.md`,
     `lib/standards.nix` (`package`, `check`), `checks/lib-standards.nix`, `checks/lib-standards-neutral.nix` (the
     neutrality grep runs from here on, so every copy is rewritten on copy).
   - Tests: `tests/support/*`, stub product skeletons (identity, markers, engine, pins only), and
     `test_{product,report,nar,flakelock,pins,github,repo,pyproject,common}.py`, plus the globs (braces),
     jsonfmt (`JsonStyle`) and regions (Dependabot triple) parts of `test_scaffold.py`.
2. **Schemas, configuration, CLI** (PR 2).
   - Core: `common/{schema,compose,config,repoconfig}.py`, `data/schema/{core,github-ruleset}.schema.json`,
     `scaffold/{manifest,render,context,discover,defaults}.py`, `cli.py`,
     `commands/{__init__,config,paths}.py`, `commands/profile.py` (`show`, `--explain`, `list`),
     `testing/conformance.py` (static part), `lib/standards.nix` (`conformance`).
   - Stubs: `modules.schema.json`, `fleet-entry.schema.json`, `profiles/`, the stub manifest and templates.
   - Tests: `test_{schema,compose,config,profiles,cli,paths}.py`, the static half of `test_conformance.py`.
3. **Engine, existing strategies, offline phases** (PR 3).
   - Core: `scaffold/{handlers,tomlmerge,jsonmerge,engine,fixtures}.py`; from `scaffold/bootstrap.py`: `Runner`,
     `report_result`, `ensure_tools` and the offline phase A (§6.3); `commands/sync.py` for `--check` and
     `--write --offline` (both phases, no Nix); `commands/profile.py validate`; the `whole`, `seed`, `uv-lock`,
     `npm-lock`, `blame-ignore-revs`, `toml-merge` and `json-merge` strategies; `hooks.checks` in the plan;
     conformance render part.
   - Tests: the ported `test_sync.py` (without its skew cases), `test_jsonmerge.py`, the rest of
     `test_scaffold.py`, the render half of `test_conformance.py`.
   - The frappe parity harness starts here (report JSON, header lines, `profile show`); each later stage adds the
     comparison for the code it ships (first-opt-in text in 4, `repo-policy` in 8, `--help` in 9).
4. **History, retraction, retire, first opt-in** (PR 3).
   - Core: history and `ever`, retract per handler, retire rules (`only_sections`, braces), floors, first opt-in
     with the message template.
   - Tests: the ported `test_standards.py`.
5. **New capabilities** (PR 3).
   - Core: `scaffold/{nixattrs,blocks,jsonc}.py`, the `sentinel` and `jsonc-merge` handlers, claims,
     `seed_template`, the foreign recognizer, the `semantic` JSONC globs.
   - The `kit` stub complete (discovery, both shapes, fixtures). Its phase-`a` entries run through the offline phase
     A of stage 3.
   - Tests: `test_{nixattrs,blocks,jsonc,kit}.py`.
6. **Nix phases and skew** (PR 3).
   - Core: the rest of `scaffold/bootstrap.py` (`lock_reasons`, `phase_a_lock`, `require_release_branch`,
     `maybe_reexec`, handover, re-entry); full `commands/sync.py` (phase A with locks, re-exec, re-entry, lock
     steps); `common/skew.py` with the core-revision row.
   - Tests: `test_{bootstrap,skew}.py`.
7. **Policy and CI steps** (PR 4).
   - Core: `ci/conventional.py`, `commands/policy.py` (schemes with facts, `pr_checks`, `committed_argv`),
     `ci/{__init__,verdict,automerge,relock,release,bump,branches,drift}.py`, `commands/ci.py` (`--set`,
     `--param`, `arg_aliases`), `github.check_state`; conformance CI part.
   - Tests: `test_{policy,conventional,relock,ci_branches,ci_steps,bump}.py`, the CI half of
     `test_conformance.py`.
8. **Repo policy and fleet** (PR 4).
   - Core: `common/repo_policy.py` (`integration:<id>`, `{integration_bypass}`), `data/repo-policy/**`,
     `commands/repo/{__init__,targets,apply,audit,doctor,rollout}.py`.
   - Tests: `test_{repo_policy,repo_targets,repo_apply,repo_audit,repo_doctor,repo_rollout}.py`.
9. **Actions, pins, docs and release wiring** (PR 4).
   - `testing/pins.py`, `lib/standards.nix` (`pins`).
   - Actions: `.github/actions/standards-{install,pr-policy,release-ff,deps-automerge,update-branches,fleet-audit}/action.yml`,
     `.github/workflows/selftest-standards.yml`.
   - `docs/library.md` (rule 6, Standards section), `docs/conventions.md` (shared exit codes and sync flags, the
     `<P>_*` names the core reads), and the release-please extra-files for `standards/`.
   - A check that `uv tool install` works from a git URL, a path and an override (Verification, python-devops-nix).

---

## 13. Review log

Review of 2026-11-03 (31 items). Each was checked against frappe-nix-tools (read only), the plan and the target
repositories before it was resolved. All 31 were accepted, some with a refinement; none was rejected.

| # | Critique item | Verified | Resolution |
|---|---|---|---|
| 1 | `MappingProxyType` dataclass defaults fail on 3.11 | yes: `ValueError: mutable default <class 'mappingproxy'>` on 3.11.17, fine on 3.12.15 | Accepted. `mapping_field()` (`default_factory`) for every mapping field; `EMPTY` kept for function defaults only (§2 rules, §3.1, §5.3); import-and-build test on python311 (§11.3 `test_product`) |
| 2 | The bump push is refused by the integration ruleset | yes: `integration.json` `"bypass_actors": []`; `bypass_actors()` has only two forms | Accepted. Core `bypass_actors` gains `integration:<id>`; `integration.json` uses `{integration_bypass}` (`[]` except under `post-merge-bump`) (§2.2, §2.6, §4.2, §7.3, §8.2); tests in `test_repo_policy`/`test_bump` |
| 3 | Series- and shape-dependent config cannot be computed | yes | Accepted. `derive(cfg, checkout)`, `Branching.release_branch(cfg, facts)`, `Hooks.release_branch(cfg, modules, facts)`, scheme methods take `facts`; `RepoConfig.facts`, `Target.facts` (§2.2, §3.1, §3.3, §7.1, §8.2, §11.1) |
| 4 | `self.submodules = true;` breaks lock checks | yes: both flakes; frappe ignores it only by regex | Accepted. Explicit input rule with `self` excluded (§5.6.4); both flakes' shapes in `test_nixattrs` |
| 5 | Addon repos have no root `pyproject.toml` / `flake.nix`; name from the directory | yes: `millrun/modules/*-addons` hold only `.github`, `.gitignore`, `LICENSE`, `.odoo-series`, `README.md` and addons; `pyproject.read` raises `EnvError`; clones land in `<tmp>/repo` | Accepted. `Identity.config_required` (frappe keeps exit 3; odoo reads `{}`, exit 2 with the hint, `--standards` creates the file) (§2.2, §3.1, §4.4); `seed_template` for sentinel entries (§4.3, §5.6.2); the name falls back to the table's `repo`, then origin, and to `root.name` only with no remote; clones go to `<tmp>/<repo name>` (§2.5, §3.3). Refinement: the directory is kept as the last resort for remote-less fixtures, which have no other name |
| 6 | `tools/pyproject.toml` claimed by two strategies | yes: Amendments put coverage in the managed `tools/pyproject.toml` | Accepted. One `toml-merge` entry with groups per module (§4.3, §5.3); `TomlGroups.reconcile` for hand-raised pins (refinement of the suggested `accept`: it returns the value, so a list can be merged per item); the warning names the entry path (§2.3) |
| 7 | `hooks.checks` runs only under `--check` | yes: frappe phase B's final report reads `after.problems` | Accepted. `engine.build` adds the items to the plan (§2.3, §3.3, §6.3) |
| 8 | `check_state` never green on skipped or neutral | yes | Accepted. Green = success/neutral/skipped; failed list extended; newest run per name; conformance requires push triggers under `post-merge-bump` (§8.2, §11.4); `test_github` |
| 9 | `since` wrong after a hand edit | yes | Accepted. `since` is the newest bump commit recognised by `commit_subject`; `old` is read at `since`; a hand-edited unit is skipped with a notice (§8.2); `test_bump` |
| 10 | The JSONC editor cannot handle the target files | yes: one-line `extensions.json` with no final newline; inline arrays in millrun's settings | Accepted. Inline insertion with the file's separators, final-newline preservation, prefix form for `never`/`forbidden` (§5.5); shape-identical neutral fixtures in core, real files in odoo-nix-tools' tests (the real files name the product, so they cannot live under `standards/`) |
| 11 | Foreign blocks and `absorb` contradict each other | yes: odoo-ruckus `pyproject.toml` markers (the `module sources` pair even has different begin and end texts) | Accepted. Loose foreign recognizer, foreign regions excluded from `absorb` and `claims`, owned insertion inside a foreign region is exit 2 (§5.6.1, §5.6.3, §5.6.4) |
| 12 | The Nix lexer must handle indented-string escapes | yes: odoo-ruckus `flake.nix:69` | Accepted. `''$`, `'''`, `''\`, `$${` and interpolation nesting specified (§5.6.2); `test_nixattrs` |
| 13 | `policy` runs `committed` from a tools env without it | yes: `committed_cmd` and B3's python-lint tool list | Accepted. `hooks.committed_argv(root)` with frappe's rule as default (§2.5, §3.1, §3.3) |
| 14 | `only_section` takes one section; no brace globs | yes: exact-set test at engine.py; `globs.py` has no braces | Accepted. `only_sections` (subset), `only_section` kept; braces expanded in product data at load, `globs.match` unchanged (§2.3, §4.3, §5.8) |
| 15 | Per-series Dependabot entries are not expressible | yes: key `(ecosystem, directory)` | Accepted. Key `(ecosystem, directory, target-branch)` with frappe's text unchanged when no branch; branch list as a product module parameter (§2.3, §3.5, §5.2) |
| 16 | The JSON emitter is fixed to oxfmt's form | yes: `jsonfmt` constants | Accepted. `Product.json_style` (`JsonStyle`, frappe's form by default); created JSONC files use it (§2.3, §3.1, §5.5) |
| 17 | No generic JSON key merge | yes: `JsonMergeHandler` was abstract | Accepted. `JsonGroups`/`JsonGroupsHandler` in `scaffold/jsonmerge.py` (§1.1, §2.3, §5.1, §5.3); `test_jsonmerge` |
| 18 | `.envrc` missing from odoo's first-opt-in list | yes: frappe's `APP_TEMPLATE`; odoo-ruckus `.envrc` | Accepted. Listed with odoo-init's variants as `legacy` (§5.9); kit stub and `test_kit` |
| 19 | JSON report key order changes | yes: frappe writes `status`, `frappe_nix`, `files`, then `profile`, `modules` | Accepted. Frappe's order (§2.2, §6.4) |
| 20 | Resolve order changes cfg key order | yes: `site` before `_repo`; `sort_keys=False` | Accepted. `derive` is step 8, `repo` step 9 (§2.2, §3.3, §4.4) |
| 21 | `--help` output changes | yes: module-name order, module docstrings, sync flag order, cli description | Accepted (kept in scope rather than dropped from S0). `Command.sort_key`, `Identity.description`, `Product.help`, sync flag slots (§2.1, §3.1, §6.1, §6.2); parity harness compares `--help` |
| 22 | First-opt-in message text changes | yes: engine.py first-opt-in text | Accepted. `FirstOptIn.message` template with a neutral core default (§3.1, §5.9) |
| 23 | `jsonc_globs` Frappe value is wrong | yes: basename match anywhere vs full-path globs | Accepted. `("**/tsconfig*.json",)`; `**/` also matches the root (§2.3) |
| 24 | `gen` is built at the wrong time; placeholder order | yes: `render` builds `Gen(ctx)` per render; `{app_hyphen}` before `{app}` | Accepted. `hooks.render_helpers(ctx, current)`; `url_placeholders` is an ordered sequence (§2.3, §3.1, §3.3) |
| 25 | CI flag renames break frappe's reusable workflows | yes: `--frappe-nix-repo`, `--docs-patterns`, `--frappe-major`, `--version-scheme` choices | Accepted. `ci.arg_aliases`, `--set`, choices from the schemes (§2.5, §3.1, §8.1); the `Release-As` text is formatted from `identity.name`, which reproduces frappe's (§2.5) |
| 26 | Test inventory gaps | yes: `test_gh.py` imports `common.gh`; skew cases in `test_sync.py` | Accepted. `test_gh` dropped, `test_github` and `test_repo_targets` added, skew cases move to `test_skew` (§1.2, §11.2, §11.3) |
| 27 | Product strings would reach the core | yes: the `product.py` comment, the schema `$comment`, `tools_deps` | Accepted. Comment reworded; `$comment` rewritten; `UvLockHandler(packages=…)`; rewrite-on-copy rule and the grep from stage 1 (§2, §2.6, §3.1, §5.1, §10.2) |
| 28 | Stage order breaks at four places | yes: `profile validate` → engine; `sync.py` → `Runner`/`ensure_tools`; kit phase-A entry; conformance late | Accepted. `profile validate`, `Runner`, `ensure_tools` and offline phase A in stage 3; conformance split into static/render/CI parts by stage (§6.3, §11.4, §12) |
| 29 | Stages 3 and 6 too large; no PR mapping | yes: the plan names core PR 1–4 | Accepted. Nine stages, mapped to core PR 1–4 with the B7 milestone each enables (§12) |
| 30 | Missing tests | yes | Accepted. All listed cases added (§11.3), plus the frappe parity harness outside `standards/` (§10.2, §11.3) |
| 31 | Which inputs the addon inputs block owns | yes: B3 says `ocb` too; the Amendments say only `odoo-nix` and `standards-profile` | Settled for the Amendments, which replace earlier sections where they conflict: the block owns `odoo-nix` and `standards-profile`; `ocb`/`oca-*` stay with addons mode, unclaimed and not managed inputs (§5.6.4); kit test with an unclaimed `core` input (§11.1, §11.3) |

---

## 14. Implementation notes

Deviations made while implementing, with the reason for each.

### Stage 1

- **`standards/ruff.toml`** (new: `extend = "../ruff.toml"`, `extend-exclude = ["DESIGN.md"]`). ruff 0.17 formats
  Python code blocks inside Markdown, so `ruff format standards` rewrote this file's API listings. The repository
  `ruff.toml` is a shared file that products' copies are compared with, so the exclusion lives in a nested config
  that extends it rather than in the shared one.
- **`ManifestError`** is defined in `scaffold/globs.py`, because `expand_braces` (stage 1) raises it before
  `scaffold/manifest.py` exists. `manifest.py` (stage 2) imports it from there.
- **`regions.check_redefinitions(product, path, text, header, managed_inputs)`** takes the product first: the marker
  regex needs `markers.local`. Until `scaffold/nixattrs.py` (stage 5), flake inputs are found with frappe's two
  regexes (inputs defined in a region; `^\s{4}<name> = {` outside it), and stage 5 swaps in the scanner.
  `regions.leader_of(product, header)` maps a header kind to its leader (`#` or `//`); `parse`, `splice`, `bodies`
  and `filled` take `leader=` (default `#`).
- **`github.check_state`** is left to stage 7, as §12 lists it there. Stage 1's `test_github.py` covers the
  transport seam, pagination, `required_contexts`, `gate_contexts` and `gates_protected`; stage 7 adds the
  `check_state` cases of §11.3. `gates_protected(product, repo, branch, gates)` is kept from frappe (the auto-merge
  safety rule) with the product first, since it calls `gate_contexts`.
- **`product.py` additions** beyond §3.1: `CORE_MODULES`, `CORE_VERSION_SCHEMES` (`"semver"`), `CORE_BRANCHING`
  (empty), `CORE_HANDLERS` (every strategy name, so a product registers its `TomlMergeHandler`/`JsonGroupsHandler`
  under its own handler names), `CORE_HEADER_KINDS` with `header_kind(product, kind)`, `core_data_root()`, and
  `Product._problems()`. `__post_init__` raises one `ValueError` that lists every broken rule, each naming its
  field. Trailing comments that pushed a line past 88 columns sit on the line above their field, so `ruff format`
  leaves the fields readable; the text is §3.1's.
- **Signatures of the copied leaves** (product first, per §2): `report.render(findings, fmt, code, engine, *,
  product)`; `flakelock.input_node/locked_pin/locked_rev(product, lock, name)`, `engine_rev(product, lock)`,
  `base_rev(product, lock)`; `pins.pin_path(product, name, lock_path, store_dir, *, lock, writable)` with
  `pins.token_var(product)`/`url_var(product)`; `pyproject.load(product, root)` plus `config_path`, `config_text`,
  `opt_in_hint`, and `opt_in_line/tool_table/check_opt_in_spelling/opted_in(product, …)`;
  `semantic.kind/equal(product, …)`; `jsonfmt.dumps(value, style=JsonStyle(), *, jsonc=False)` and
  `stringify(value, style)`, where one indent level measures as the style's indent with each tab counted at
  `tab_width`. One message changed wording: a missing input is "neither the checkout's nor `<engine>`'s" (frappe:
  "the app's").
- **Stub skeletons**: both stubs list only `CORE_MODULES`, and their four required hooks raise
  `NotImplementedError` until the stage that needs them. The kit already sets its `JsonStyle` (prettier's form),
  which `test_scaffold.py`'s jsonfmt part uses. The stub's engine-pinned inputs are `lint-rules`, `registry` and
  `pilot` (all `example/<name>`).
- **`test_repo.py`**: frappe's `app_name` cases are dropped with `app_name`. The `load_checkout` cases that replace
  them need `Checkout` and `default_load_checkout`, which come with `scaffold/context.py` in stage 2, so they move
  there.
- **`testing.fakes.run_cli(product, *argv, cwd=None, main=None)`**: `main` lets a product pass its own entry point
  (with its extra commands); the default imports `standards_core.cli` when called, so the module imports before
  stage 2.
- **Running the suite**: `uv run --no-project --python 3.11 --with-editable standards --with pytest pytest
  standards/tests -q` (from the repository root), which leaves no `uv.lock` or `.venv` under `standards/`.
  `tests/conftest.py` puts `tests/` (for `support`) and both stub directories on `sys.path`. `lib-standards` runs
  pytest in a copy of `standards/tests` against the installed package, so the suite tests what products get.
- **Version markers**: `standards/pyproject.toml` and `standards_core/__init__.py` carry release-please's
  `x-release-please-start-version`/`-end` block around the version, the form the generic updater reads.

### Stage 2

- **`default_load_checkout(product, root)`** takes the root as well: a product's `Hooks` are built before its
  `Product`, so its `load_checkout` is `lambda root: default_load_checkout(PRODUCT, root)` (the stubs do this). The
  checkout must be a git work tree (`ls_files`), else `EnvError`.
- **The checkout reaches resolution.** `config.resolve_doc(product, doc, *, root, checkout, …)` and
  `config.resolve(product, path, *, …, checkout)` take the `Checkout`, which `hooks.derive` gets and whose name is the
  `repo` default's (`[project].name` without one). `root` defaults to `checkout.root`. `Resolved` gains a `product`
  field (not compared, not printed) so `Resolved.get` can read the product's schemas and key sets, and `RepoConfig`
  a `product` field for its `release_branch` property. `config` and `profile show` load the checkout when the
  product can (a git work tree) and resolve without it otherwise, as frappe's `config` works outside git.
- **Integration-branch default** with no `hooks.integration_default` and no `Branching` for `releases.branching`
  (a product with no branching at all): `"main"`.
- **`hooks.host_rules`** runs on every resolution, after the GitHub-only loop and before its notice, with the host
  answer as its `gh` argument (a frappe rule checks `if not gh`; it adds no notice, so frappe's notice order holds).
- **Composition.** `core.schema.json` is served per product with its product-dependent enums filled in
  (`header-kind`, `releases.branching` with the first branching as default, `releases.version-scheme`,
  `repo-policy.required-checks` items), and `releases.tool` is a plain string the product's module schema
  narrows. Each composed document carries its own `$defs/org` and `$defs/modules`: a core module's merged schema is
  inline (document-local `$ref`s of either source rewritten to `core.schema.json#…`/`modules.schema.json#…` by
  `compose.rebase`), any other module is `{"$ref": "modules.schema.json#/$defs/modules/<m>"}`. A `$ref` to
  `modules.schema.json` or `fleet-entry.schema.json` resolves to `Schemas.modules`/`Schemas.fleet_entry`. `extends`
  is `^(minimal|recommended|<each snapshot>)$`. `schema.raw(product, name)` reads a file as shipped.
- **Help keys.** `help_text(product, key, default)` takes and returns `str | None` (`None`: argparse's no text).
  `build_parser` accepts `"<command>"`, `"<command> <flag>"` (a positional by its dest), and below a command group
  `"<command> <subcommand>[ <flag>]"`. A command's one-line summary in the command list is neutral core text with no
  key yet: the `--help` parity comparison arrives in stage 9, which adds a key form for it if frappe needs one.
- **`CORE_COMMANDS`** is a module attribute built on first access (PEP 562 `__getattr__`): the command modules import
  `help_text` from `standards_core.commands`, so they cannot be imported while it initialises. It holds `config`,
  `data-path`, `pin-path` and `profile` now; the later stages add theirs. `profile` has `show` and `list`
  (`<show|list>`); `validate` comes with stage 3.
- **Manifest.** `Retire.only_section` is gone: `only_section: x` is stored as `only_sections = ("x",)` (the same test
  for one section). The sentinel-only keys on another strategy, a `claims` without a `nix-attrset` anchor, a bad
  `anchor`, `comment` or `phase` are `ManifestError`s at load. Handler names are checked by conformance (and by the
  engine in stage 3), not at load. The stub's `pyproject.toml` `toml-merge` entry names no handler until stage 3
  registers its `TomlGroups` handler.
- **Render.** `org_url` stays a core filter (over `url_template`). `render.helpers(product, ctx, current)` builds the
  per-render functions; `header` raises `ManifestError` for an unknown kind. `context.build` takes an optional
  `facts` (callers that computed them already) and refuses a `template_globals` key that shadows a core key, including
  `previous` and `history`. `first_commit_year` catches `EnvError` only (ruff 0.17's defaults refuse a blind
  `except`). `History.MESSAGE` names "the config file" rather than `pyproject.toml`.
- **Conformance, static part.** The variable rule scans the product's data root (templates, manifest, profiles, …)
  and its `help` texts for `<X>_<core suffix>` (the `ENV_SUFFIXES` the core reads) and refuses any `<X>` other than
  `env_prefix`. `lib.standards.conformance` names its check `<product>-standards-conformance-check`.
- **Stubs.** Both stubs get working configuration hooks now (`load_checkout`, `discover`, `gates`,
  `template_globals`, `derive`), their `Branching` values and minimal version schemes (`stub-major`, `kit-series`),
  because resolution and the context need them. The kit's shape and series discovery (§11.1) is in place ahead of
  stage 5, which completes its manifest and fixtures. The stub has a required checkout key `major` (frappe's
  `frappe-major` analogue: `version-<major>` is its release branch), a derived `site`, and the profile-only list
  `replace-units` (frappe's `replace-apps` analogue).
- **Tests beyond the stage's list.** `test_repo.py` gains the `load_checkout` cases stage 1 deferred, and
  `test_scaffold.py` gains manifest, render and context cases for the modules this stage adds (the engine-level rest
  of `test_scaffold.py` stays with stage 3). `test_profiles.py` also covers `profile show/list` and the
  changed-defaults warning (`scaffold/defaults.py`).


### Stage 3

- **The engine is ported whole.** History and `ever`, retract per handler, the retire rules (`only_sections`,
  braces), floors and the first-opt-in refusal with its message template come with stage 3: they are branches of
  the same frappe functions (`_entry_item`, `_retract`, `_absent`, `_retired`) the engine and the handlers are
  built from, and the ported `test_sync.py` cases (retire, floors, local regions) need them. Stage 4 adds
  `test_standards.py` over them.
- **Flake inputs before `nixattrs.py`.** `input_spec`, `locked_spec`, `strip_nix_comments` and
  `flake_input_specs` (frappe's regular expressions, plus `self` excluded, §5.6.4) live in `scaffold/engine.py`
  so `--check`'s `lock_problems` works now; stage 5's scanner replaces them and they move to `nixattrs.py`.
- **Offline phase A only.** Without `--offline`, phase A runs steps 1, 2, 4 and 7 as with `--skip-lock`; the
  release-branch check, the lock, the rebuild against the locked profile and the re-exec are stage 6's. So are
  `--expect-rev` and the skew rows: `sync` has no `--expect-rev` flag yet. `bootstrap.py` already has `handover`,
  `override_url`, `nix_args`, `reentry_argv` and the lock-changed helpers, because `ensure_tools` needs them.
- **`Item.entry`** (not compared, not printed): the manifest entry an item was rendered for, so phase B's lock step
  finds the handler's `lock_argv` and the entry's `relock_when`. `Plan` carries `product`, `checkout` (frappe's
  `app`) and `templates` (the org profile's `templates/`, which `retract` and `absent` read: their protocol
  signatures have no `templates`). frappe's `Plan.version` is Frappe-only and dropped.
- **Handler registry.** `handlers.core_handlers()` holds a base per strategy name; `toml-merge` and `json-merge`
  map to a placeholder that refuses (a product registers its `TomlMergeHandler`/`JsonGroupsHandler` under its own
  name), and so do `jsonc-merge` and `sentinel` until stage 5. `handlers.for_entry` also refuses a handler that does
  not serve the entry's strategy; each refusal is a `ManifestError` (exit 3). `engine.check_handlers(product)` runs
  it over the manifest. `SeedHandler` gains `lock_problem(plan, entry, path)` (what a command seed on disk lacks:
  `UvLockHandler`'s floors) and `seed_text`. `NpmLockHandler.lock_argv` runs in the lock's directory through
  `--prefix <dir>`.
- **`tomlmerge.py`.** `merge(groups, text, ctx, path)`, `managed_view(groups, doc, ctx)` and
  `add_table(product, text, values)`: `add_table` lives here (§2.3), not in `common/pyproject.py` (§2.5), with
  the TOML text machinery it uses. `TomlGroups.on` is also asked about each committed state and about `previous`
  (namespaces with `modules` and `cfg` only). `seeds` are written after every group, where missing. The turn-off
  warning keeps §5.3's text ("the app's", with the entry's path), and `jsonmerge` uses the same text; the whole-file
  and seed notices, which no parity output compares, say "the checkout's".
- **`jsonmerge.py`.** `JsonGroupsHandler.merge(plan, doc, path)` is the point a product subclass extends (frappe's
  scripts and retired steps). An emptied parent object is removed with its last managed key.
- **Fixtures.** `fixtures.throwaway(product, root, profile_dir, name, fixture, *, profile=None)`: `profile` names a
  built-in for a throwaway with no org profile directory (the conformance render part). `fixtures.commit` and
  `fixtures.git` run git in the isolated environment.
- **Conformance, render part.** `check(product, *, render=True)`; the render part runs only when the static part
  found nothing. The locks an offline sync leaves are faked with `testing.fakes.flake_lock` (from the rendered
  `flake.nix`) and, per command seed, `fakes.uv_lock` at a `UvLockHandler`'s floors (any other lock seed: an empty
  file). The `seed_template` anchor rule is textual (`conformance.anchor_found`) until stage 5's scanners.
- **`sync`.** Product flags in `passthrough` follow `--offline`. `profile validate` builds its plans with
  `{"force": False, "throwaway": True, **sync.options(Namespace())}`.
- **Stub.** The stub gains its stage-3 hooks: the `stub-pyproject` `TomlMergeHandler` (`[tool.ruff]` for
  python-lint, `[tool.coverage.*]` for tests with a `fail_under` seed, a `line-length = 88` baseline, rules on
  `extend-select` and `fail_under`), the `plain` and `with-extras` fixtures (`with-extras` requires `commits`) with
  `write_fixture`, `first_opt_in` for `flake.nix` and `.envrc` with one legacy text each,
  `pins.lock_paths = ("tools/uv.lock",)` and `pins.managed_inputs`. `test_product.py` asserts the defaults on the
  kit where the stub now sets a value.
- **§15 requests in the stage-3 code.** CR1: the engine and `sync` read the config file through
  `engine.config_file(product, root)`, which wraps `pyproject.config_path` (history, committed states,
  `entries_for`, the created-table item, the error path, `_create_table`, which creates the file only where its
  directory exists); every resolution passes the checkout. `Identity.config_paths`, `Hooks.config_file` and
  `config.resolve`'s root are stage 10's retrofit. CR2: `context.build` always sets `options.throwaway` (`False`),
  `profile validate` and the conformance render part set it, the latter through a hidden `sync --throwaway`. CR5:
  `Runner.unstaged` holds the tracked paths with unstaged changes when the run starts; sync writes them but never
  stages them, and prints `"<path>: left unstaged: it had changes of yours"` at the end.
- **Tests beyond the stage's list.** `test_scaffold.py` gains the engine's parts (managed paths, `Item`, handlers,
  lock handlers, `TomlGroups`, flake inputs, fixtures); `test_profiles.py` covers `profile validate`; `test_sync.py`
  also covers retraction, the shallow-clone exit 3, `--standards`, symlinks, the product sync hooks and flag slots,
  `--phase`, the repo-settings notice, CR2 and CR5. Frappe-only cases are not ported (package.json, init-py,
  discovery, zizmor, node locks, sites).
- **Parity harness.** `parity/frappe/check.py <frappe-nix checkout>` (run with Python 3.12: frappe-nix-tools needs
  it) compares the header lines, the three report formats, `sync --check --format json` (outside a checkout, and
  the key order on an opted-in fixture) and `profile show --format json` (document keys, `profile`, the order of the
  shared `config` keys). Against frappe-nix-tools 2.2.1: no difference.

## 15. Change requests from the first product plugin (odoo-nix-tools)

These requests come from the odoo-nix-tools design. Each names a product, so read it as the product's needs, not core text; the neutrality rule still applies to code.
Stage 3 onwards: implement each request in any code the stage writes or touches. In particular, CR1: every read of the config file goes through `common/pyproject.config_path(product, root)` with an explicit checkout root; never derive the root from the config file's location.
Stage 10, added after stage 9, retrofits whatever remains into the stage 1-2 code and adds the tests.


Changes the core contract (`pdn-standards-core/standards/DESIGN.md`) must make for this design. Each names the
core text it changes, the exact proposal and what odoo-nix does until it lands. CR1 is blocking for addon
repositories; CR2 and CR3 have product fallbacks; CR4-CR6 are improvements with product fallbacks; CR7 is text
only. None is applied here: the core is being implemented separately and is not edited from this repository.

### 15.CR1: the config file is per checkout (blocking for addons mode)

**Why.** The user decision puts `[tool.odoo-nix]` in odoo-nix's own uv project file: root `pyproject.toml` in a
project, `nix/pyproject.toml` in addons mode; addon repositories have no root `pyproject.toml`. Core §4.4 and §13
item 5 instead settled "`config_required = False`, and `--standards` creates the root file". The stage-1/2 core code
reads one fixed path and also derives the checkout root from the file's location:

- `common/pyproject.py:55` `config_path(product, root)` = `root / identity.config_file`;
- `common/config.py:583` `resolve()` sets `root = config_path.resolve().parent` and (`:283`) the default lock to
  `root / "flake.lock"`, so `nix/pyproject.toml` would resolve with root `nix/` and look for `nix/flake.lock`;
- `commands/config.py:66` finds the file with `repo.find_up(identity.config_file)`, and its `--pyproject`/`--lock`
  help says the lock is "beside the" config file.

**Proposal.**

1. `product.py`:
   ```python
   @dataclass(frozen=True)
   class Identity:
       ...
       config_file: str = "pyproject.toml"  # the default file holding [tool.<table>], relative to the root
       config_paths: tuple[str, ...] = ()  # every file the table may live in; () → (config_file,)
       config_required: bool = True

   @dataclass(frozen=True)
   class Hooks:
       ...
       # root → the config file relative to the root, from tracked files and their text only (§3.4);
       # None → identity.config_file. Must return one of identity.config_paths when those are set.
       config_file: Callable[[Path], str] | None = None
   ```
2. `pyproject.config_path(product, root)` returns `root / (hooks.config_file(root) if hooks.config_file else
   identity.config_file)`, and every core read and write of the table goes through it: `config_text`, `load`,
   `context.default_load_checkout`, `repoconfig.load`, `sync` (`_create_table`/`add_table`: with
   `config_required = False` it creates the *file* only when its directory exists; a product preflight may refuse
   first), `rollout.set_profile_ref`, `fixtures.throwaway` (the commit holds what `write_fixture` wrote),
   `targets.load` (the opted-in test on a clone), and the help texts of `config`/`profile` (`--pyproject`).
3. `config.resolve(product, config_path, *, root: Path | None = None, lock_path=None, profile_dir=None,
   checkout=None)`: `root` defaults to `config_path.resolve().parent` (unchanged for one-file products), and
   the default lock is `root / "flake.lock"`. `repoconfig.load` and every caller that has a checkout pass
   `root=checkout.root`.
4. `commands/config.resolve_args` (and `profile show`): without `--pyproject`, the root is the nearest directory
   `d` at or above the current directory, up to the work-tree root, for which `config_path(product, d)` is a file,
   else the work-tree root; the file is `config_path(product, root)`. With `--pyproject F`, the root is the nearest
   ancestor `d` of `F` with `config_path(product, d) == F`, else `F`'s parent. The `--lock` default is
   `root / "flake.lock"` ("beside the checkout's flake.nix").
5. `engine.history` walks commits that changed any of `identity.config_paths or (identity.config_file,)` (plus
   `flake.lock` and an in-repo profile, as now). `_committed_doc(rev)` and the defaults' HEAD read
   (`defaults.head_version`'s config read) take the first of those paths whose text at `rev` has the opt-in line,
   else the current `config_path`.
6. Text: §4.4 "`--standards <profile>` creates the table in `config_path(product, root)`, and the file when it is
   missing, its directory exists and `identity.config_required` is false"; §13 item 5's resolution amended the same
   way.
7. Kit stub (§11.1): the `bundle` shape keeps its table in `kit/pyproject.toml` (a generated project file, as
   odoo's `nix/pyproject.toml`), with `Identity.config_paths = ("pyproject.toml", "kit/pyproject.toml")` and a
   `hooks.config_file`. `test_kit` adds: `config` and `profile show` from the root and from `kit/`, both resolving
   the root and its `flake.lock`; `sync --check`; retraction after a commit (history reads `kit/pyproject.toml`);
   `repo rollout`'s `set_profile_ref` on that file; `--standards` refused by the stub's preflight when `kit/` is
   missing.

**Until it lands:** `PRODUCT` sets neither field (§2.1), and the addons fixture and every addon-repository
command are skipped or refused; no product-side workaround exists (two `Product` objects chosen per checkout
would still hit `resolve()`'s root rule).

### 15.CR2: `ctx.options["throwaway"]` in throwaway renders

**Why.** `profile validate` and the conformance render part sync a throwaway checkout built from
`product.fixtures` under profiles whose module sets are not known in advance, and cannot run `uv lock` offline.
odoo-nix's stale-lock item (§3.4) must then accept a superset lock there, and only there.

**Proposal.** `scaffold.fixtures.throwaway` and `testing.conformance` set `options["throwaway"] = True` in the
sync they run (core §3.5 `options` row: `{"force": bool, "throwaway": bool, **sync.options(args)}`); it is
`False` in every other sync. Lock handlers keep their behaviour; products may read the flag.

**Until it lands:** the stale-lock item compares `rendered ⊆ locked` everywhere.

### 15.CR3: no integration bypass without a release branch

**Why.** Core §7.3 renders `integration_bypass` = the release-bypass actor whenever `releases` is on and the flow
is `post-merge-bump`. A project inherits `releases` (on) from the Avunu profile and has no release branch
(`release_branch` is `""`), so turning `repo-policy` on there would let the App bypass the pull-request and
status-check rules on `main`/`master`.

**Proposal.** §7.3 `integration_bypass` row: "`bypass_actors(release_bypass)` when `releases` is on, the
`Branching.flow` is `post-merge-bump` **and the checkout's release branch is non-empty** (the condition that adds
`release-branch.json`), else `[]`". `test_repo_policy` adds the empty-release-branch case.

**Until it lands:** `hooks.repo_policy_values` returns `integration_bypass: []` when `cfg["shape"] == "project"`
(§2.2).

### 15.CR4: `absorb` takes a comment paragraph its lines leave empty (improvement)

**Why.** Absorbing the addon repositories' `.gitignore` lines leaves their comment paragraphs ("# odoo-nix addons
mode: links the dev shell makes …") with nothing under them (core §5.6.3: comments are never absorbed).

**Proposal.** §5.6.3 `absorb`: "a paragraph (lines between blank lines, outside every block and foreign region)
whose non-comment lines were all absorbed loses its comment lines too; `--check` reports them with the absorbed
lines". `test_blocks` adds it.

**Until it lands:** a product notice names the emptied paragraphs (§3.4), and the adoption commit deletes them.

### 15.CR5: sync does not stage a file that had the user's changes (improvement)

**Why.** Core §6.3 stages every touched path. In a dirty tree (the real ruckus checkout: 26 changed entries,
`.vscode/settings.json` among them) that stages the user's own edits together with sync's.

**Proposal.** §6.3: "paths with unstaged changes when the run starts (`git diff --name-only`) are written but not
staged; the final report lists them as 'left unstaged: it had changes of yours'". `test_sync` adds it.

**Until it lands:** a product notice (§3.4), and the adoption steps commit or stash first (§8.1).

### 15.CR6: a bump push rejected because the branch moved is not a failure (improvement)

**Why.** Core §8.2 step 6 makes a rejected `git push origin HEAD:<integration>` exit 1. Under the
`post-merge-bump` flow the new integration head that caused the rejection triggers its own release run, which
recomputes the bump, so the red run reports nothing actionable.

**Proposal.** §8.2 step 6: "a push rejected as non-fast-forward is exit 0 with `bumped` empty and the notice
'<integration> moved since <sha>; the run for its new head bumps'; any other push failure stays exit 1".
`test_bump` adds it.

**Until it lands:** `odoo-release.yml` checks out `ref: <integration>` (not `github.sha`) under a non-cancelling
`release` concurrency group, which leaves only the seconds between checkout and push (§6.4).

### 15.CR7: text corrections in the core contract

- §2.5 `repo/rollout.py`: "(Frappe value `[["nix","run","--no-pure-eval",".#relock"]]`; odoo `[]`)" → odoo also
  sets the relock step (§0.2 C3: a new odoo-nix can change the install set).
- §5.6.2 "A product whose checkouts may lack the file (odoo addon repositories have no `flake.nix`) ships a
  `seed_template`": odoo-nix ships none and refuses such a checkout in preflight (§3.1 dev-shell); keep the sentence
  for the kit stub without naming odoo.
- §5.3 and §4.3: odoo's `tools/pyproject.toml` entry is `module: TOOLS_MODULES` (every module that contributes a
  tool, §3.1), not `["python-lint", "tests"]`, and its `[project].dependencies` is one group (`deps`) reconciled by
  package name.
- §4.1 `fleet-entry-core`: unchanged; the odoo fleet uses `branches` (C2 withdrawn).

---
