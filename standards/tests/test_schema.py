"""The JSON-Schema subset validator (DESIGN §4.1): keywords, references across files and
across data roots, the app-only keyword, defaults and ``node_at``."""

import unittest

from support import KIT, STUB

from standards_core.common import schema
from standards_core.common.report import ConfigError


class TestValidator(unittest.TestCase):
    def errors(self, value, node, app_only_allowed=True):
        return schema.Validator(
            STUB, "profile.schema.json", "x", app_only_allowed=app_only_allowed
        ).errors(value, node)

    def test_types_and_keywords(self):
        self.assertEqual(self.errors(1, {"type": "integer", "minimum": 0}), [])
        self.assertIn("expected integer", self.errors(True, {"type": "integer"})[0])
        self.assertEqual(self.errors(None, {"type": ["string", "null"]}), [])
        self.assertIn("must be one of", self.errors("c", {"enum": ["a", "b"]})[0])
        self.assertIn(
            "does not match",
            self.errors("a\n", {"type": "string", "pattern": "^a$"})[0],
        )
        self.assertIn(
            "unique", self.errors([1, 1], {"type": "array", "uniqueItems": True})[0]
        )
        any_of = {"anyOf": [{"const": "gates"}, {"type": "array"}]}
        self.assertEqual(self.errors("gates", any_of), [])
        self.assertTrue(self.errors("x", any_of))

    def test_objects(self):
        node = {
            "type": "object",
            "additionalProperties": False,
            "required": ["a"],
            "properties": {"a": {"type": "string"}},
        }
        self.assertEqual(self.errors({"a": "1"}, node), [])
        self.assertEqual(self.errors({}, node), ["x: a is required"])
        self.assertEqual(self.errors({"a": "1", "b": 2}, node), ["x.b: unknown key"])
        names = {
            "type": "object",
            "propertyNames": {"enum": ["npm"]},
            "additionalProperties": {"enum": ["daily"]},
        }
        self.assertEqual(self.errors({"npm": "daily"}, names), [])
        self.assertEqual(len(self.errors({"pip": "hourly"}, names)), 2)

    def test_refs_across_files(self):
        node = {"$ref": "profile.schema.json#/$defs/modules/editorconfig"}
        self.assertEqual(self.errors({"enable": True, "indent": "tab"}, node), [])
        self.assertIn("must be one of", self.errors({"indent": "tabs"}, node)[0])

    def test_refs_across_roots(self):
        # The core's retire rule names the product's module names...
        retire = {"$ref": "core.schema.json#/$defs/retire-item"}
        self.assertEqual(self.errors({"paths": ["a"], "module": "extras"}, retire), [])
        self.assertIn(
            "must be one of",
            self.errors({"paths": ["a"], "module": "nope"}, retire)[0],
        )
        # ...and the product's fleet entry takes the core's keys.
        entry = {"$ref": "fleet-entry.schema.json"}
        self.assertEqual(self.errors({"repo": "o/r", "unit": "demo_unit"}, entry), [])
        self.assertIn("does not match", self.errors({"repo": "o"}, entry)[0])

    def test_app_only(self):
        node = {"$ref": "#/$defs/modules/extras"}
        self.assertEqual(self.errors({"browser": False}, node), [])
        message = self.errors({"browser": False}, node, app_only_allowed=False)[0]
        self.assertIn("app-only key", message)
        self.assertIn("[tool.stub-nix]", message)

    def test_unknown_keyword_is_a_bug(self):
        with self.assertRaises(RuntimeError):
            self.errors(1, {"oneOf": []})

    def test_a_ref_that_names_nothing_is_a_bug(self):
        with self.assertRaises(RuntimeError):
            self.errors(1, {"$ref": "core.schema.json#/$defs/nope"})

    def test_defaults_and_node_at(self):
        d = schema.defaults(STUB, "app.schema.json")
        self.assertEqual(d["profile"], "minimal")
        self.assertEqual(d["nightly-suites"], [])
        self.assertIs(d["extras"]["browser"], True)
        self.assertEqual(d["tests"]["coverage"]["target"], 80)
        self.assertEqual(d["releases"]["branching"], "develop+version")
        self.assertEqual(d["repo-policy"]["release-bypass"], "github-actions")
        self.assertNotIn("major", d)
        app = "app.schema.json"
        self.assertIsNotNone(schema.node_at(STUB, app, "tests.coverage.target"))
        self.assertIsNotNone(schema.node_at(STUB, app, "org.website-url"))
        self.assertIsNotNone(
            schema.node_at(STUB, app, "dependabot.cadence-overrides.uv")
        )
        self.assertIsNone(schema.node_at(STUB, app, "tests.nonsense"))

    def test_loads_are_per_product(self):
        self.assertIs(
            schema.load(STUB, "profile.schema.json"),
            schema.load(STUB, "profile.schema.json"),
        )
        stub = schema.load(STUB, "profile.schema.json")["properties"]
        kit = schema.load(KIT, "profile.schema.json")["properties"]
        self.assertIn("extras", stub)
        self.assertNotIn("extras", kit)
        self.assertIn("requires-kit-nix", kit)

    def test_unknown_file(self):
        with self.assertRaises(ConfigError):
            schema.load(STUB, "nope.schema.json")
        self.assertIn("$defs", schema.load(STUB, "github-ruleset.schema.json"))


if __name__ == "__main__":
    unittest.main()
