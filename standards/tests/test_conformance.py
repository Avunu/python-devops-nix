"""The conformance check (DESIGN §11.4), static part: both stubs conform, and a broken
copy of the stub fails each rule."""

import contextlib
import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB, product_copy

from standards_core.product import CiConfig, Modules
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


if __name__ == "__main__":
    unittest.main()
