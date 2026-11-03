"""The conformance check (DESIGN §11.4), static, render and CI parts: both stubs
conform, and a broken copy of a stub fails each rule."""

import contextlib
import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB, product_copy, without_fixtures

from standards_core.ci.verdict import DecisionRule
from standards_core.common import repo
from standards_core.product import CiConfig, Fixture, Modules
from standards_core.scaffold import handlers
from standards_core.testing import conformance


class TestStubsConform(unittest.TestCase):
    def test_both_stubs_conform(self):
        self.assertEqual(conformance.check(STUB), [])
        self.assertEqual(conformance.check(KIT), [])

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = conformance.main(["stub_nix_tools.product:PRODUCT"])
        self.assertEqual((code, out.getvalue()), (0, "stub-nix: conforms\n"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(conformance.main([]), 2)
        self.assertIn("usage:", err.getvalue())


class TestBrokenCopies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.product = product_copy(STUB, Path(self.tmp.name))
        self.data = self.product.data_root

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, product=None):
        return conformance.check(product or self.product)

    def assertProblem(self, text, product=None):
        problems = self.problems(product)
        self.assertTrue(any(text in p for p in problems), problems)

    def edit_json(self, rel, edit):
        path = self.data / rel
        doc = json.loads(path.read_text())
        edit(doc)
        path.write_text(json.dumps(doc))

    def broken(self, **fields):
        """A copy whose fields break __post_init__'s rules (which refuse to build it)."""
        product = dataclasses.replace(self.product)
        for name, value in fields.items():
            object.__setattr__(product, name, value)
        return product

    def test_the_product_builds(self):
        product = self.broken(ci=CiConfig(gates=("lint", "lint")))
        self.assertProblem("Product: ci.gates lists a gate twice", product)

    def test_every_core_module_is_present(self):
        names = tuple(n for n in STUB.modules.names if n != "commits")
        product = self.broken(
            modules=dataclasses.replace(STUB.modules, names=names, needs={})
        )
        self.assertProblem("modules.names lacks the core module(s) commits", product)

    def test_the_module_schema_defines_exactly_the_modules(self):
        self.edit_json(
            "schema/modules.schema.json",
            lambda d: d["$defs"]["modules"].pop("extras"),
        )
        self.assertProblem("$defs/modules must define exactly modules.names")

    def test_the_module_name_enum(self):
        self.edit_json(
            "schema/modules.schema.json",
            lambda d: d["$defs"]["module-name"]["enum"].pop(),
        )
        self.assertProblem("$defs/module-name must enumerate modules.names")

    def test_every_module_has_enable(self):
        self.edit_json(
            "schema/modules.schema.json",
            lambda d: d["$defs"]["modules"]["extras"]["properties"].pop("enable"),
        )
        self.assertProblem("module extras has no enable property")

    def test_the_composed_schemas_load(self):
        self.edit_json(
            "schema/modules.schema.json",
            lambda d: d["$defs"]["modules"]["hygiene"]["properties"].update(
                {"x": {"$ref": "#/$defs/nothing"}}
            ),
        )
        self.assertProblem("profile.schema.json: RuntimeError")

    def test_the_fleet_entry_requires_repo(self):
        self.edit_json("schema/fleet-entry.schema.json", lambda d: d.pop("required"))
        self.assertProblem("fleet-entry.schema.json must require repo")

    def test_every_builtin_profile_validates(self):
        path = self.data / "profiles" / "recommended@1.0.toml"
        path.write_text(path.read_text() + "\n[extras.nope]\nx = 1\n")
        self.assertProblem("profile recommended@1.0: ConfigError: invalid profile")

    def test_minimal_turns_on_only_the_required_module(self):
        path = self.data / "profiles" / "minimal.toml"
        path.write_text(
            path.read_text().replace(
                "[hygiene]\nenable = false", "[hygiene]\nenable = true"
            )
        )
        self.assertProblem("profile minimal turns on ['dev-shell', 'hygiene']")

    def test_minimal_exists(self):
        (self.data / "profiles" / "minimal.toml").unlink()
        self.assertProblem("profiles/minimal.toml is missing")

    def test_every_fragment_parses(self):
        self.edit_json(
            "manifest.d/core.json",
            lambda d: d["entries"].append(
                {"path": "x", "strategy": "nope", "module": "ci"}
            ),
        )
        self.assertProblem(
            "manifest: ManifestError: manifest.d/core.json: x: unknown strategy"
        )

    def test_every_handler_is_registered(self):
        self.edit_json(
            "manifest.d/core.json",
            lambda d: d["entries"][0].update({"handler": "stub-flake"}),
        )
        self.assertProblem("flake.nix: handler 'stub-flake' is not registered")

    def test_every_template_exists(self):
        (self.data / "templates" / "envrc.j2").unlink()
        self.assertProblem(".envrc: templates/envrc.j2 is in no data root")

    def test_every_uses_key_resolves(self):
        self.edit_json(
            "manifest.d/core.json",
            lambda d: d["entries"][2]["uses"].append("editorconfig.nope"),
        )
        self.assertProblem(".editorconfig: uses 'editorconfig.nope'")

    def test_core_variables_carry_the_prefix(self):
        template = self.data / "templates" / "envrc.j2"
        template.write_text(template.read_text() + "export OTHER_NIX_ALLOW_SKEW=1\n")
        self.assertProblem(
            "templates/envrc.j2: OTHER_NIX_ALLOW_SKEW is a core variable; spell it"
            " STUB_NIX_ALLOW_SKEW"
        )
        template.write_text("export STUB_NIX_ALLOW_SKEW=1\n")
        self.assertFalse([p for p in self.problems() if "core variable" in p])


class Drifting(handlers.WholeHandler):
    """Renders the commit it runs on: stable within one sync, changed by the next."""

    name = "drifting"

    def render(self, plan, entry, path, current, templates):
        head = repo.git(plan.root, "rev-parse", "HEAD").strip()
        return [handlers.item(entry, path, current, f"{head}\n")]


class AlwaysShort(handlers.SeedHandler):
    """A lock seed no lock satisfies: --write --offline leaves it, --check reports it."""

    name = "always-short"

    def lock_problem(self, plan, entry, path):
        return "never enough"


class TestRenderPart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def copy(self, entries=(), **changes):
        product = product_copy(STUB, Path(self.tmp.name), **changes)
        if entries:
            (product.data_root / "manifest.d" / "zz.json").write_text(
                json.dumps({"entries": list(entries)})
            )
        return product

    def assertProblem(self, product, text):
        problems = conformance.check(product)
        self.assertTrue(any(text in p for p in problems), problems)

    def test_every_fixture_syncs(self):
        fixtures = {**STUB.fixtures, "broken": Fixture(table="nope = 1\n")}
        self.assertProblem(
            self.copy(fixtures=fixtures),
            "fixture broken x minimal: sync --write --offline exited 2",
        )

    def test_a_second_write_changes_nothing(self):
        product = self.copy(
            [
                {
                    "path": "DRIFT.md",
                    "template": "CONTRIBUTING.md.j2",
                    "strategy": "whole",
                    "handler": "drifting",
                    "module": "dev-shell",
                }
            ],
            handlers={**STUB.handlers, "drifting": Drifting()},
        )
        self.assertProblem(
            product, "fixture plain x minimal: a second sync --write changed DRIFT.md"
        )

    def test_check_is_clean_after_write(self):
        product = self.copy(
            [
                {
                    "path": "x.lock",
                    "strategy": "seed",
                    "handler": "always-short",
                    "command": "make-lock",
                    "module": "dev-shell",
                }
            ],
            handlers={**STUB.handlers, "always-short": AlwaysShort()},
        )
        self.assertProblem(
            product,
            "fixture plain x minimal: sync --check after --write exited 1: x.lock"
            " (seed): never enough",
        )

    def test_the_static_part_alone(self):
        fixtures = {**STUB.fixtures, "broken": Fixture(table="nope = 1\n")}
        self.assertEqual(
            conformance.check(self.copy(fixtures=fixtures), render=False), []
        )

    def test_a_static_problem_skips_the_render_part(self):
        product = self.copy(fixtures={"broken": Fixture(table="nope = 1\n")})
        (product.data_root / "templates" / "envrc.j2").unlink()
        problems = conformance.check(product)
        self.assertEqual(len(problems), 1, problems)

    def test_a_product_without_fixtures_has_no_render_part(self):
        self.assertEqual(conformance._render(without_fixtures(KIT)), [])

    def test_anchors(self):
        flake = '{\n  inputs = {\n    a.url = "x";\n  };\n  outputs = { };\n}\n'
        toml = '[project]\nname = "x"\ndependencies = [\n]\n\n[tool.x]\nlist = []\n'
        for anchor, text, found in (
            ("eof", "", True),
            ("nix-attrset inputs", flake, True),
            ("nix-attrset outputs", flake, True),
            ("nix-attrset inputs.a", flake, False),
            ("nix-attrset nope", flake, False),
            ("toml-array project.dependencies", toml, True),
            ("toml-array tool.x.list", toml, True),
            ("toml-array project.optional", toml, False),
            ("toml-array tool.y.list", toml, False),
            ("toml-array .deps", "deps = [\n]\n", True),
            ("toml-array project.dependencies", 'project.dependencies = ["a"]\n', True),
            ("other x", toml, False),
        ):
            with self.subTest(anchor=anchor):
                self.assertEqual(conformance.anchor_found(anchor, text), found)


class TestModulesCheckUsesTheProductsNames(unittest.TestCase):
    def test_a_renamed_module_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(
                KIT,
                Path(tmp),
                schemas=dataclasses.replace(
                    KIT.schemas, modules="kit-modules.schema.json"
                ),
            )
            schema_dir = product.data_root / "schema"
            (schema_dir / "modules.schema.json").rename(
                schema_dir / "kit-modules.schema.json"
            )
            self.assertEqual(conformance.check(product), [])
            self.assertIsInstance(product.modules, Modules)


class TestCiPart(unittest.TestCase):
    """The CI part: the decision table, the labels and contexts, and the push trigger
    the post-merge bump's required checks need."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def assertProblem(self, product, text, *, render=True):
        problems = conformance.check(product, render=render)
        self.assertTrue(any(text in p for p in problems), problems)

    def ci(self, base=STUB, **changes):
        return dataclasses.replace(base, ci=dataclasses.replace(base.ci, **changes))

    def test_decision_rules_use_known_values(self):
        product = self.ci(
            decision_table=(
                DecisionRule("uv", "merge"),
                DecisionRule("uv", "auto", update="patch"),
            )
        )
        self.assertProblem(
            product, "ci.decision_table[0] (uv): verdict 'merge'", render=False
        )
        self.assertProblem(
            product, "ci.decision_table[1] (uv): update 'patch'", render=False
        )

    def test_labels_name_modules_and_colors(self):
        product = self.ci(labels={"x": ("c5def5", "x", "nope"), "y": ("blue", "y", "")})
        self.assertProblem(
            product, "ci.labels['x']: module 'nope' is not a module", render=False
        )
        self.assertProblem(
            product, "ci.labels['y']: color 'blue' is not six hex digits", render=False
        )

    def test_contexts_are_gate_contexts_of_workflow_files(self):
        product = self.ci(contexts=(("ci / docs", "docs.yml"), ("ci / lint", "x/ci")))
        self.assertProblem(
            product,
            "ci.contexts: 'ci / docs' is no gate's required context",
            render=False,
        )
        self.assertProblem(
            product, "ci.contexts: 'x/ci' is not a workflow file name", render=False
        )

    def test_gates_get_distinct_contexts(self):
        product = dataclasses.replace(STUB)
        object.__setattr__(
            product,
            "ci",
            dataclasses.replace(
                STUB.ci, gates=("lint", "list"), check_context="ci / {gate[1]}"
            ),
        )
        self.assertProblem(
            product,
            "ci.check_context gives two gates the same required context",
            render=False,
        )

    def kit_copy(self, **ci):
        product = product_copy(KIT, Path(self.tmp.name))
        if ci:
            product = self.ci(product, **ci)
        return product

    def edit_ci_yml(self, product, old, new):
        path = product.data_root / "templates" / ".github" / "workflows" / "ci.yml.j2"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_the_workflow_must_run_on_push(self):
        product = self.kit_copy()
        self.edit_ci_yml(
            product, "  push:\n    branches: [{{ branches.integration }}]\n", ""
        )
        self.assertProblem(
            product,
            "fixture bundle x develop+series: .github/workflows/ci.yml does not run on"
            " push: the bump commit gets no required checks",
        )

    def test_on_push_to_the_integration_branch(self):
        product = self.kit_copy()
        self.edit_ci_yml(product, "[{{ branches.integration }}]", "[main]")
        self.assertProblem(
            product,
            "fixture workspace x develop+series: .github/workflows/ci.yml runs on push"
            " to ['main'], not to develop",
        )

    def test_every_context_workflow_is_written(self):
        product = self.kit_copy(contexts=(("ci / lint", "lint.yml"),))
        self.assertProblem(
            product,
            "fixture bundle x develop+series: .github/workflows/lint.yml is not"
            " written, but ci names it",
        )

    def test_only_post_merge_bump_branchings_are_rendered(self):
        self.assertEqual(conformance._ci_push(STUB), [])
        self.assertEqual(conformance._ci_push(without_fixtures(KIT)), [])

    def test_push_problem(self):
        branch = "develop"
        for text, want in (
            ("on: push\n", None),
            ("on: [push, pull_request]\n", None),
            ("on:\n  push:\n", None),
            ("on:\n  push:\n    branches: [develop]\n", None),
            ("on:\n  push:\n    branches: ['dev*']\n", None),
            ("on:\n  push:\n    branches: develop\n", None),
            ("on:\n  push:\n    tags: ['v*']\n", None),
            ("on: pull_request\n", "does not run on push"),
            ("name: x\n", "does not run on push"),
            (
                "on:\n  push:\n    branches-ignore: [x]\n",
                "filters push branches",
            ),
            ("on:\n  push: [x]\n", "has an unreadable on.push"),
            (
                "on:\n  push:\n    branches: [main]\n",
                "runs on push to ['main'], not to develop",
            ),
            ("on: [\n", "is not YAML"),
        ):
            with self.subTest(text=text):
                got = conformance.push_problem(text, branch)
                if want is None:
                    self.assertIsNone(got)
                else:
                    self.assertIn(want, got or "")


if __name__ == "__main__":
    unittest.main()
