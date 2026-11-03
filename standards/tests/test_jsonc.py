"""``jsonc-merge`` and its editor (DESIGN §5.5): comments and foreign keys kept, the
indentation and trailing-comma style inferred, ``set`` per sub-key, ``union`` with the
opposite list, ``forbidden`` and its drift, a reformat is no drift, retraction (an
edited leaf stays with a warning), a non-object top level, the created file's form,
inline containers with and without spaces, the missing final newline, prefix forms."""

import json
import unittest

from support import KIT, STUB
from support.checkouts import CheckoutCase

from standards_core.common.report import ConfigError
from standards_core.scaffold import jsonc, jsonfmt, semantic
from standards_core.scaffold.globs import ManifestError
from standards_core.testing.fakes import git

# The target repositories' shapes, neutral: a one-line extensions.json with no spaces and
# no final newline, and a settings.json with 2-space indentation, inline arrays and flat
# dotted keys.
EXTENSIONS = '{"recommendations":["Vendor.ext"]}'
SETTINGS = """{
  "files.watcherExclude": {
    "**/.devenv/**": true,
    "**/.direnv/**": true
  },
  "git.detectSubmodules": false,
  "py-envs.defaultEnvManager": "vendor.python:system",
  "x.analysis.extraPaths": [".devenv/state/pythonpath"],
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  // the shell provides one
  "Ide.serverConfigPath": "/tmp/x",
  "Ide.selectedProfile": "default"
}
"""


def spec(**raw):
    return jsonc.parse_spec(raw, "t")


class TestParse(unittest.TestCase):
    def test_values_ignore_comments_and_trailing_commas(self):
        text = '// c\n{\n  /* a */ "a": [1, 2,], // b\n  "b": {"c": null,},\n}\n'
        self.assertEqual(jsonc.loads(text, "f"), {"a": [1, 2], "b": {"c": None}})

    def test_syntax_errors_name_the_line(self):
        for text in (
            '{\n  "a": 1\n  "b": 2\n}',
            '{\n  "a": \n}',
            '{"a": "x',
            "{} x",
            "/* {}",
        ):
            with self.subTest(text=text), self.assertRaises(ConfigError) as caught:
                jsonc.parse(text, "f.json")
            self.assertRegex(str(caught.exception), r"^f\.json:\d+: ")

    def test_indent_unit(self):
        self.assertEqual(jsonc.indent_unit('{\n\t"a": 1\n}'), "\t")
        self.assertEqual(jsonc.indent_unit('{\n  "a": 1\n}'), "  ")
        self.assertEqual(jsonc.indent_unit('{"a": 1}'), "    ")


class TestEditor(unittest.TestCase):
    def test_set_keeps_comments_and_foreign_keys(self):
        text = '{\n  // mine\n  "a": 1, // trailing\n  "b": 2\n}\n'
        out = jsonc.set_leaf(text, ["a"], 5, indent="  ")
        self.assertEqual(out, '{\n  // mine\n  "a": 5, // trailing\n  "b": 2\n}\n')
        out = jsonc.set_leaf(text, ["c"], {"d": True}, indent="  ")
        self.assertEqual(
            out,
            '{\n  // mine\n  "a": 1, // trailing\n  "b": 2,\n  "c": {\n    "d": true\n  }\n}\n',
        )

    def test_tabs_and_the_trailing_comma_style(self):
        text = '{\n\t"a": 1,\n}\n'
        out = jsonc.set_leaf(text, ["b"], [1], indent=jsonc.indent_unit(text))
        self.assertEqual(out, '{\n\t"a": 1,\n\t"b": [\n\t\t1\n\t],\n}\n')
        out = jsonc.delete(out, ["b"])
        self.assertEqual(out, text)
        # No trailing comma: the previous member's comma goes with the last one.
        self.assertEqual(
            jsonc.delete('{\n  "a": 1,\n  "b": 2\n}', ["b"]), '{\n  "a": 1\n}'
        )

    def test_missing_objects_on_the_way_are_created(self):
        out = jsonc.set_leaf('{\n  "a": 1\n}', ["x", "y", "z"], 2, indent="  ")
        self.assertEqual(jsonc.loads(out, "f"), {"a": 1, "x": {"y": {"z": 2}}})
        self.assertIn('  "x": {\n    "y": {\n      "z": 2\n    }\n  }', out)

    def test_inline_containers_keep_their_separators(self):
        self.assertEqual(
            jsonc.ensure_items(
                EXTENSIONS, ["recommendations"], ["Vendor.ruff"], indent="  "
            ),
            '{"recommendations":["Vendor.ext","Vendor.ruff"]}',
        )
        self.assertEqual(
            jsonc.ensure_items('{"r": ["a"]}', ["r"], ["b"], indent="  "),
            '{"r": ["a", "b"]}',
        )
        self.assertEqual(
            jsonc.set_leaf('{"a":1}', ["b"], [1, 2], indent="  "), '{"a":1,"b":[1,2]}'
        )
        self.assertEqual(
            jsonc.set_leaf('{"a": 1}', ["b"], {"c": 1}, indent="  "),
            '{"a": 1, "b": {"c": 1}}',
        )
        # An empty container with no evidence in the file: json.dumps's separators.
        self.assertEqual(
            jsonc.set_leaf("{}", ["a"], [1, 2], indent="  "), '{"a": [1, 2]}'
        )
        self.assertEqual(
            jsonc.ensure_items('{"r":[]}', ["r"], ["a", "b"], indent="  "),
            '{"r":["a","b"]}',
        )
        # A replaced inline value stays inline.
        self.assertEqual(
            jsonc.set_leaf(
                SETTINGS, ["x.analysis.extraPaths"], ["a", "b"], indent="  "
            ).count('"x.analysis.extraPaths": ["a", "b"],'),
            1,
        )

    def test_deleting_from_inline_containers(self):
        self.assertEqual(jsonc.delete('{"a": 1, "b": 2}', ["a"]), '{"b": 2}')
        self.assertEqual(jsonc.delete('{"a": 1, "b": 2}', ["b"]), '{"a": 1}')
        self.assertEqual(jsonc.delete('{"a":1}', ["a"]), "{}")
        self.assertEqual(
            jsonc.remove_items('{"r":["a","b","c"]}', ["r"], ["b"]), '{"r":["a","c"]}'
        )
        self.assertEqual(jsonc.delete('{"a": 1}', ["missing"]), '{"a": 1}')

    def test_a_comment_line_above_a_deleted_member_stays(self):
        out = jsonc.delete(SETTINGS, ["Ide.serverConfigPath"])
        self.assertIn(
            '  // the shell provides one\n  "Ide.selectedProfile": "default"\n}', out
        )

    def test_paths_that_cross_a_non_object(self):
        with self.assertRaisesRegex(ConfigError, "a is not an object"):
            jsonc.set_leaf('{"a": 1}', ["a", "b"], 2, indent="  ")
        with self.assertRaisesRegex(ConfigError, "the top level is not an object"):
            jsonc.set_leaf("[1]", ["a"], 2, indent="  ")


class TestSpec(unittest.TestCase):
    def test_set_is_per_sub_key(self):
        s = spec(set={"[python]": {"editor.formatOnSave": True}})
        text = '{\n  "[python]": {\n    "editor.tabSize": 4\n  }\n}\n'
        out = jsonc.apply(text, s, "f")
        self.assertEqual(
            jsonc.loads(out, "f"),
            {"[python]": {"editor.tabSize": 4, "editor.formatOnSave": True}},
        )
        self.assertEqual(
            jsonc.problems(jsonc.loads(text, "f"), s),
            ["[python]/editor.formatOnSave: missing"],
        )

    def test_union_with_its_opposite(self):
        s = spec(
            union={
                "recommendations": {
                    "ensure": ["a", "b"],
                    "opposite": "unwantedRecommendations",
                }
            }
        )
        text = (
            '{\n  "recommendations": ["a"],\n  "unwantedRecommendations": ["b", "c"]\n}'
        )
        self.assertEqual(
            jsonc.problems(jsonc.loads(text, "f"), s),
            [
                "recommendations: b missing",
                "recommendations: b listed in unwantedRecommendations",
            ],
        )
        out = jsonc.apply(text, s, "f")
        self.assertEqual(
            out,
            '{\n  "recommendations": ["a", "b"],\n  "unwantedRecommendations": ["c"]\n}',
        )
        # Other items are untouched; a missing array is created.
        self.assertEqual(
            jsonc.loads(jsonc.apply("{}", s, "f"), "f"), {"recommendations": ["a", "b"]}
        )

    def test_forbidden_and_its_prefix_form(self):
        s = spec(forbidden=[["Ide.serverConfigPath"], {"prefix": "py-envs."}])
        doc = jsonc.loads(SETTINGS, "f")
        self.assertEqual(
            jsonc.problems(doc, s),
            [
                "forbidden key Ide.serverConfigPath",
                "forbidden key py-envs.defaultEnvManager",
            ],
        )
        out = jsonc.apply(SETTINGS, s, "f")
        self.assertNotIn("Ide.serverConfigPath", out)
        self.assertNotIn("py-envs.", out)
        self.assertIn('"Ide.selectedProfile": "default"', out)
        nested = spec(forbidden=[{"prefix": "x", "in": ["files.watcherExclude"]}])
        self.assertEqual(jsonc.problems(doc, nested), [])

    def test_never(self):
        for raw in (
            {"set": {"Ide.selectedProfile": "x"}, "never": ["Ide.selectedProfile"]},
            {"set": {"py-envs.a": 1}, "never": [{"prefix": "py-envs."}]},
            {"forbidden": [{"prefix": "py-"}], "never": [{"prefix": "py-envs."}]},
            {"set": {"a": {"b": 1}}, "never": [["a"]]},
            {
                "union": {"r": {"ensure": ["x"], "opposite": "u"}},
                "never": [["u"]],
            },
        ):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ManifestError, "must never touch"),
            ):
                spec(**raw)
        spec(set={"a": 1}, never=[["b"], {"prefix": "c"}])

    def test_malformed_specs(self):
        for raw in (
            {"set": []},
            {"other": 1},
            {"forbidden": [1]},
            {"union": {"r": {"ensure": "x"}}},
        ):
            with self.subTest(raw=raw), self.assertRaises(ManifestError):
                jsonc.parse_spec(raw, "t")

    def test_a_reformat_is_no_drift(self):
        s = spec(set={"a": [1, 2]}, union={"r": {"ensure": ["x"]}})
        for text in (
            '{"a":[1,2],"r":["x"]}',
            '{\n\t// mine\n\t"a": [\n\t\t1,\n\t\t2,\n\t],\n\t"r": ["x",],\n}\n',
        ):
            self.assertEqual(jsonc.problems(jsonc.loads(text, "f"), s), [])

    def test_the_final_newline_is_kept_or_left_out(self):
        s = spec(union={"recommendations": {"ensure": ["Vendor.ruff"]}})
        self.assertFalse(jsonc.apply(EXTENSIONS, s, "f").endswith("\n"))
        self.assertTrue(jsonc.apply(EXTENSIONS + "\n", s, "f").endswith("}\n"))

    def test_settings_shape(self):
        s = spec(
            set={
                "python.languageServer": "None",
                "[python]": {"editor.defaultFormatter": "vendor.ruff"},
            },
            forbidden=[["Ide.serverConfigPath"]],
            never=[["Ide.selectedProfile"], {"prefix": "py-envs."}],
        )
        out = jsonc.apply(SETTINGS, s, "f")
        self.assertTrue(
            out.startswith('{\n  "files.watcherExclude": {\n    "**/.devenv/**": true,')
        )
        self.assertIn('  "x.analysis.extraPaths": [".devenv/state/pythonpath"],\n', out)
        self.assertIn(
            '  "Ide.selectedProfile": "default",\n  "python.languageServer": "None",\n'
            '  "[python]": {\n    "editor.defaultFormatter": "vendor.ruff"\n  }\n}\n',
            out,
        )


class TestSemanticKind(unittest.TestCase):
    def test_product_jsonc_globs(self):
        self.assertEqual(semantic.kind(KIT, ".vscode/settings.json"), "jsonc")
        self.assertEqual(semantic.kind(KIT, "sub/.vscode/settings.json"), "json")
        self.assertEqual(semantic.kind(STUB, ".vscode/settings.json"), "json")
        self.assertTrue(
            semantic.equal(
                KIT, ".vscode/x.json", '{\n  // c\n  "a": 1,\n}', '{\n"a": 1}'
            )
        )


# --- through the engine: the kit's .vscode files ---------------------------------------------


class KitEditor(CheckoutCase):
    product = KIT
    profile = "recommended"

    def setUp(self) -> None:
        super().setUp()
        self.write(
            "pyproject.toml", '[tool.kit-nix]\nschema = 1\nprofile = "recommended"\n'
        )
        git(self.root, "rm", "-q", "--cached", "-r", "demo_unit")
        (self.root / "demo_unit" / "__init__.py").unlink()
        self.write("alpha/__unit__.toml", 'version = "18.0.1.0.0"\n')
        self.write("alpha/README.md", "# alpha\n")
        self.commit("bundle")

    def table(self, extra: str) -> None:
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                "[tool.kit-nix]\n", f"[tool.kit-nix]\n{extra}", 1
            ),
        )


class TestKitFiles(KitEditor):
    def test_created_in_the_products_json_style(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        settings = self.read(".vscode/settings.json")
        want = {
            "python.languageServer": "None",
            "[python]": {"editor.defaultFormatter": "vendor.ruff"},
            "ruff.path": ["${workspaceFolder}/tools/.venv/bin/ruff"],
        }
        self.assertEqual(settings, jsonfmt.dumps(want, KIT.json_style))
        self.assertTrue(settings.endswith("}\n"))
        self.assertNotIn("\t", settings)
        self.assertEqual(
            json.loads(self.read(".vscode/extensions.json")),
            {"recommendations": ["vendor.python", "vendor.ruff"]},
        )

    def test_the_target_shapes(self):
        self.write(
            ".vscode/extensions.json",
            '{"recommendations":["Vendor.ext"],"unwantedRecommendations":["vendor.ruff"]}',
        )
        self.write(".vscode/settings.json", SETTINGS)
        self.commit("editor files")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        for problem in (
            ".vscode/extensions.json (jsonc-merge): recommendations: vendor.python missing",
            "recommendations: vendor.ruff listed in unwantedRecommendations",
            ".vscode/settings.json (jsonc-merge): python.languageServer: missing",
            "forbidden key Ide.serverConfigPath",
        ):
            self.assertIn(problem, out)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.read(".vscode/extensions.json"),
            '{"recommendations":["Vendor.ext","vendor.python","vendor.ruff"],"unwantedRecommendations":[]}',
        )
        settings = self.read(".vscode/settings.json")
        self.assertIn("// the shell provides one", settings)
        self.assertIn('"py-envs.defaultEnvManager"', settings)  # never touched
        self.assertNotIn("Ide.serverConfigPath", settings)
        self.fake_locks()
        self.commit("synced")
        self.assertEqual(self.check()[0], 0)
        # A reformat by the editor is not drift.
        doc = jsonc.loads(self.read(".vscode/settings.json"), "f")
        self.write(".vscode/settings.json", json.dumps(doc, indent=4))
        self.assertEqual(self.check()[0], 0)

    def test_a_non_object_top_level_is_exit_2(self):
        self.write(".vscode/settings.json", "[]\n")
        self.commit("odd")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("the top level is not an object", out)

    def test_retracted_when_the_module_turns_off(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        settings = jsonc.set_leaf(
            self.read(".vscode/settings.json"), ["ruff.path"], ["mine"], indent="  "
        )
        settings = jsonc.set_leaf(settings, ["own.key"], 1, indent="  ")
        self.write(".vscode/settings.json", settings)
        self.write(
            ".vscode/extensions.json",
            '{"recommendations":["vendor.python","vendor.ruff","own.ext"]}',
        )
        self.fake_locks()
        self.commit("synced")
        self.table("editor.enable = false\n")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertIn(
            ".vscode/settings.json ruff.path is the checkout's now that editor is off",
            err,
        )
        self.assertEqual(
            jsonc.loads(self.read(".vscode/settings.json"), "f"),
            {"ruff.path": ["mine"], "own.key": 1},
        )
        self.assertEqual(
            self.read(".vscode/extensions.json"), '{"recommendations":["own.ext"]}'
        )


if __name__ == "__main__":
    unittest.main()
