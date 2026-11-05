"""The composed schemas (DESIGN §4.1): profile, app and fleet documents built from the
core's definitions and the product's, the core-module merge, and a product's own
document winning over composition."""

import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from support import KIT, STUB, product_copy

from standards_core.common import compose, config, schema
from standards_core.product import CORE_MODULES, HeaderKind


def load(product, name):
    return schema.load(product, name)


def validator(product, name):
    return schema.Validator(product, name, "x")


class TestProfileSchema(unittest.TestCase):
    def test_shape(self):
        doc = load(STUB, "profile.schema.json")
        self.assertEqual(doc["required"], ["schema", "name"])
        self.assertIs(doc["additionalProperties"], False)
        self.assertEqual(
            list(doc["properties"])[:9],
            [
                "schema",
                "name",
                "description",
                "extends",
                "requires-stub-nix",
                "org",
                "retire",
                "extra-files",
                "replace-units",
            ],
        )
        self.assertEqual(list(doc["properties"])[9:], list(STUB.modules.names))
        self.assertEqual(list(doc["$defs"]["modules"]), list(STUB.modules.names))

    def test_extends_names_the_builtins(self):
        pattern = load(STUB, "profile.schema.json")["properties"]["extends"]["pattern"]
        self.assertEqual(pattern, r"^(minimal|recommended|recommended@1\.0)$")
        v = validator(STUB, "profile.schema.json")
        base = {"schema": 1, "name": "org"}
        self.assertEqual(v.errors({**base, "extends": "recommended@1.0"}), [])
        self.assertIn("does not match", v.errors({**base, "extends": "x@1.0"})[0])

    def test_org_unites_the_core_and_the_product(self):
        org = load(STUB, "profile.schema.json")["$defs"]["org"]
        self.assertIn("publisher", org["properties"])
        self.assertIn("website-url", org["properties"])
        # The product's definition of a core key wins: its own default.
        defaults = schema.defaults(STUB, "profile.schema.json")["org"]
        self.assertEqual(defaults["dev-docs-url"], "https://example.org/stub-nix/docs")
        self.assertEqual(
            schema.defaults(KIT, "profile.schema.json")["org"]["dev-docs-url"], ""
        )
        v = validator(STUB, "profile.schema.json")
        errors = v.errors({"schema": 1, "name": "o", "org": {"nope": ""}})
        self.assertEqual(errors, ["x.org.nope: unknown key"])

    def test_retire_and_extra_files_name_the_products_modules(self):
        v = validator(STUB, "profile.schema.json")
        base = {"schema": 1, "name": "o"}
        good = {
            "path": "S.md",
            "template": "S.md.j2",
            "strategy": "whole",
            "module": "hygiene",
        }
        self.assertEqual(v.errors({**base, "extra-files": [good]}), [])
        bad = {**good, "module": "nope", "header": "md"}
        self.assertEqual(len(v.errors({**base, "extra-files": [bad]})), 2)
        self.assertEqual(
            v.errors({**base, "retire": [{"paths": ["a"], "module": "ci"}]}), []
        )


class TestCoreModules(unittest.TestCase):
    def test_core_parameters_merge_under_the_products(self):
        releases = load(STUB, "profile.schema.json")["$defs"]["modules"]["releases"]
        props = releases["properties"]
        self.assertEqual(
            list(props),
            [
                "enable",
                "tool",
                "branching",
                "version-scheme",
                "tag-prefix",
                "bootstrap-sha",
            ],
        )
        # The product's tool wins over the core's.
        self.assertEqual(props["tool"]["enum"], ["release-please", "none"])
        self.assertEqual(
            props["branching"],
            {"enum": ["develop+version", "main+tags"], "default": "develop+version"},
        )
        self.assertEqual(props["version-scheme"]["enum"], ["semver", "stub-major"])
        self.assertIs(releases["additionalProperties"], False)
        kit = load(KIT, "profile.schema.json")["$defs"]["modules"]["releases"][
            "properties"
        ]
        self.assertEqual(kit["branching"]["enum"], ["develop+series"])
        self.assertEqual(kit["version-scheme"]["enum"], ["semver", "kit-series"])

    def test_a_core_module_the_product_does_not_extend(self):
        policy = load(STUB, "profile.schema.json")["$defs"]["modules"]["repo-policy"]
        self.assertIn("release-bypass", policy["properties"])
        checks = policy["properties"]["required-checks"]["anyOf"][1]["items"]
        self.assertEqual(checks, {"enum": ["pr-policy", "lint", "test"]})
        v = validator(STUB, "profile.schema.json")
        node = {"$ref": "#/$defs/modules/repo-policy"}
        for bypass in ("github-actions", "user:1", "integration:42"):
            self.assertEqual(v.errors({"release-bypass": bypass}, node), [])
        self.assertTrue(v.errors({"release-bypass": "team:1"}, node))
        self.assertTrue(v.errors({"required-checks": ["typecheck"]}, node))

    def test_every_core_module_is_composed(self):
        defs = load(KIT, "app.schema.json")["$defs"]["modules"]
        for name in CORE_MODULES:
            self.assertIn("enable", defs[name]["properties"], name)
        self.assertEqual(
            defs["editor"], {"$ref": "modules.schema.json#/$defs/modules/editor"}
        )

    def test_header_kinds_are_the_products_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(
                STUB,
                Path(tmp),
                header_kinds=MappingProxyType({"md": HeaderKind("<!--", "-->")}),
            )
            kinds = load(product, "core.schema.json")["$defs"]["header-kind"]["enum"]
            self.assertEqual(kinds[-1], "md")
            self.assertIn("nix", kinds)
            self.assertNotIn(
                "md", load(STUB, "core.schema.json")["$defs"]["header-kind"]["enum"]
            )

    def test_document_local_refs_are_rebased(self):
        self.assertEqual(
            compose.rebase(
                {"a": {"$ref": "#/$defs/x"}, "b": [{"$ref": "y.json#/z"}]}, "m.json"
            ),
            {"a": {"$ref": "m.json#/$defs/x"}, "b": [{"$ref": "y.json#/z"}]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(STUB, Path(tmp))
            path = product.data_root / "schema" / "modules.schema.json"
            doc = json.loads(path.read_text())
            doc["$defs"]["cadence"] = {"enum": ["daily", "weekly"]}
            doc["$defs"]["modules"]["dependabot"]["properties"]["cadence"] = {
                "$ref": "#/$defs/cadence"
            }
            path.write_text(json.dumps(doc))
            v = validator(product, "profile.schema.json")
            node = {"$ref": "#/$defs/modules/dependabot"}
            self.assertEqual(v.errors({"cadence": "daily"}, node), [])
            self.assertIn("must be one of", v.errors({"cadence": "hourly"}, node)[0])


class TestAppSchema(unittest.TestCase):
    def test_shape(self):
        doc = load(STUB, "app.schema.json")
        self.assertEqual(doc["required"], ["schema", "major"])
        self.assertEqual(load(KIT, "app.schema.json")["required"], ["schema"])
        props = list(doc["properties"])
        self.assertEqual(
            props[:5],
            ["schema", "profile", "repo", "integration-branch", "retire-keep"],
        )
        self.assertLess(props.index("major"), props.index("org"))
        self.assertEqual(props[-len(STUB.modules.names) :], list(STUB.modules.names))
        self.assertNotIn("retire", props)
        self.assertNotIn("replace-units", props)

    def test_validates_a_table(self):
        v = validator(STUB, "app.schema.json")
        self.assertEqual(v.errors({"schema": 1, "major": 2, "profile": "./p"}), [])
        self.assertEqual(v.errors({"schema": 1}), ["x: major is required"])
        self.assertIn(
            "does not match", v.errors({"schema": 1, "major": 1, "profile": "x"})[0]
        )
        self.assertEqual(
            v.errors({"schema": 1, "major": 1, "extras": {"browser": False}}), []
        )


class TestFleetSchema(unittest.TestCase):
    def test_shape_and_entries(self):
        doc = load(STUB, "fleet.schema.json")
        self.assertEqual(doc["required"], ["schema", "repos"])
        v = validator(STUB, "fleet.schema.json")
        self.assertEqual(
            v.errors({"schema": 1, "repos": [{"repo": "o/r", "private": True}]}), []
        )
        self.assertEqual(
            v.errors({"schema": 1, "repos": [{}]}), ["x.repos[0]: repo is required"]
        )
        self.assertEqual(
            v.errors({"schema": 1, "repos": [{"repo": "o/r", "shape": "bundle"}]}),
            ["x.repos[0].shape: unknown key"],
        )
        kit = validator(KIT, "fleet.schema.json")
        entry = {"repo": "o/r", "shape": "bundle", "branches": ["18.0", "17.0"]}
        self.assertEqual(kit.errors({"schema": 1, "repos": [entry]}), [])
        self.assertIn(
            "at least 1",
            kit.errors({"schema": 1, "repos": [{**entry, "branches": []}]})[0],
        )


class TestShippedDocumentWins(unittest.TestCase):
    def test_a_shipped_profile_schema_replaces_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(STUB, Path(tmp))
            shipped = {
                "type": "object",
                "required": ["schema", "name", "flavour"],
                "properties": {"flavour": {"enum": ["plain"]}},
            }
            (product.data_root / "schema" / "profile.schema.json").write_text(
                json.dumps(shipped)
            )
            self.assertEqual(load(product, "profile.schema.json"), shipped)
            self.assertEqual(
                validator(product, "profile.schema.json").errors(
                    {"schema": 1, "name": "n"}
                ),
                ["x: flavour is required"],
            )
            # The other documents are still composed, and so is the stub's own.
            self.assertIn("major", load(product, "app.schema.json")["properties"])
            self.assertNotIn("flavour", load(STUB, "profile.schema.json")["properties"])

    def test_composed_and_shipped_resolve_the_same(self):
        """A product shipping the composed document verbatim resolves exactly as before."""
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(STUB, Path(tmp))
            for name in ("profile.schema.json", "app.schema.json", "fleet.schema.json"):
                (product.data_root / "schema" / name).write_text(
                    json.dumps(load(STUB, name))
                )
            doc = {
                "tool": {
                    "stub-nix": {"schema": 1, "major": 1, "profile": "recommended"}
                }
            }
            ours = config.resolve_doc(product, doc)
            theirs = config.resolve_doc(STUB, doc)
            self.assertEqual(ours.cfg, theirs.cfg)
            self.assertEqual(ours.modules, theirs.modules)


if __name__ == "__main__":
    unittest.main()
