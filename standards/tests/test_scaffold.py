"""The engine's leaves: globs (with braces), the formatter-shaped JSON, local regions."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB

from standards_core.common.report import ConfigError
from standards_core.product import JsonStyle
from standards_core.scaffold import floors, globs, jsonfmt, regions, semantic

PRETTIER = JsonStyle(indent="  ", width=80, trailing_commas="none")


class TestGlobs(unittest.TestCase):
    def test_match(self):
        self.assertTrue(globs.match("**/forms/**", "a/forms/x.js"))
        self.assertTrue(globs.match("**/forms/**", "forms/x.js"))
        self.assertTrue(globs.match("a/**/*.js", "a/b.js"))
        self.assertTrue(globs.match("a/**/*.js", "a/x/y/b.js"))
        self.assertFalse(globs.match("a/*.js", "a/x/b.js"))
        self.assertFalse(globs.match("*.bundle.js", "a/x.bundle.js"))

    def test_match_knows_no_braces(self):
        self.assertFalse(globs.match("lint.config.{js,mjs}", "lint.config.js"))
        self.assertTrue(globs.match("lint.config.{js,mjs}", "lint.config.{js,mjs}"))

    def test_glob_to_regex(self):
        self.assertEqual(
            globs.glob_to_regex("unit/public/js/generated/**"),
            "unit/public/js/generated/.*$",
        )
        self.assertEqual(globs.glob_to_regex("components.d.ts"), r"components\.d\.ts$")
        self.assertEqual(globs.glob_to_regex("a/*.ts"), "a/[^/]*\\.ts$")

    def test_expand_braces(self):
        self.assertEqual(
            globs.expand_braces("lint.config.{js,mjs,ts}"),
            ["lint.config.js", "lint.config.mjs", "lint.config.ts"],
        )
        self.assertEqual(globs.expand_braces("setup.cfg"), ["setup.cfg"])
        self.assertEqual(
            globs.expand_braces("{a,b}/x.{c,d}"), ["a/x.c", "a/x.d", "b/x.c", "b/x.d"]
        )
        self.assertEqual(globs.expand_braces("x{,.bak}"), ["x", "x.bak"])
        self.assertEqual(globs.expand_braces("**/{one}.md"), ["**/one.md"])
        expanded = globs.expand_braces("eslint.config.{js,mjs}")
        self.assertTrue(globs.match_any(expanded, "eslint.config.mjs"))

    def test_bad_braces_are_manifest_errors(self):
        for pattern in ("a.{js,{x,y}}", "a.{js", "a.js}", "a}.{b"):
            with self.subTest(pattern=pattern), self.assertRaises(globs.ManifestError):
                globs.expand_braces(pattern)
        self.assertTrue(issubclass(globs.ManifestError, RuntimeError))


class TestJsonfmt(unittest.TestCase):
    """Outputs taken from oxfmt 0.72.0 with useTabs and printWidth 110 (the default style),
    and from prettier 3 with two spaces and printWidth 80."""

    def test_objects_expand_and_short_arrays_stay_inline(self):
        self.assertEqual(
            jsonfmt.dumps({"a": ["x", "y"], "b": {"c": 1}, "e": []}),
            '{\n\t"a": ["x", "y"],\n\t"b": {\n\t\t"c": 1\n\t},\n\t"e": []\n}\n',
        )

    def test_width_counts_tabs_as_two_and_the_comma(self):
        def line(n: int) -> str:
            return jsonfmt.dumps({"o": {f"k{n}": ["x" * n], "z": 1}}).split("\n")[2]

        self.assertTrue(line(94).endswith("],"), "110 columns fits")
        self.assertTrue(line(95).endswith("["), "111 columns breaks")

    def test_the_default_style_is_the_products_default(self):
        self.assertEqual(jsonfmt.DEFAULT_STYLE, JsonStyle())
        self.assertEqual(jsonfmt.DEFAULT_STYLE, STUB.json_style)
        value = {"a": [{"b": 1}], "c": ["x"]}
        self.assertEqual(jsonfmt.dumps(value), jsonfmt.dumps(value, STUB.json_style))

    def test_jsonc_trailing_commas(self):
        self.assertEqual(
            jsonfmt.dumps({"a": [{"b": 1}]}, jsonc=True),
            '{\n\t"a": [\n\t\t{\n\t\t\t"b": 1,\n\t\t},\n\t],\n}\n',
        )

    def test_prettier_style(self):
        self.assertEqual(KIT.json_style, PRETTIER)
        self.assertEqual(
            jsonfmt.dumps({"a": [{"b": 1}], "c": ["x"]}, PRETTIER, jsonc=True),
            '{\n  "a": [\n    {\n      "b": 1\n    }\n  ],\n  "c": ["x"]\n}\n',
        )

    def test_prettier_width_counts_spaces_and_the_comma(self):
        def line(n: int) -> str:
            text = jsonfmt.dumps({"o": {f"k{n}": ["x" * n], "z": 1}}, PRETTIER)
            return text.split("\n")[2]

        # '    "k64": ["<64 x>"],' is 4 + 7 + 68 + 1 = 80 columns.
        self.assertEqual(len(line(64)), 80)
        self.assertTrue(line(64).endswith("],"), "80 columns fits")
        self.assertTrue(line(65).endswith("["), "81 columns breaks")

    def test_wider_indent_is_measured(self):
        four = JsonStyle(indent="    ", width=20)
        self.assertEqual(
            jsonfmt.dumps({"k": ["aaaa", "bbbb"]}, four),
            '{\n    "k": [\n        "aaaa",\n        "bbbb"\n    ]\n}\n',
        )
        wide_tabs = JsonStyle(tab_width=8, width=24)
        self.assertEqual(
            jsonfmt.dumps({"k": ["aaaa", "bbbb"]}, wide_tabs),
            '{\n\t"k": [\n\t\t"aaaa",\n\t\t"bbbb"\n\t]\n}\n',
        )
        self.assertEqual(
            jsonfmt.dumps({"k": ["aaaa", "bbbb"]}, JsonStyle(width=24)),
            '{\n\t"k": ["aaaa", "bbbb"]\n}\n',
        )

    def test_stringify(self):
        self.assertEqual(jsonfmt.stringify({"a": [1]}), '{\n\t"a": [\n\t\t1\n\t]\n}\n')
        self.assertEqual(jsonfmt.stringify({"a": 1}, PRETTIER), '{\n  "a": 1\n}\n')
        self.assertEqual(json.loads(jsonfmt.stringify({"é": 1})), {"é": 1})


class TestSemantic(unittest.TestCase):
    def test_kind_uses_the_products_jsonc_globs(self):
        product = _with_jsonc_globs(("**/tsconfig*.json",))
        self.assertEqual(semantic.kind(product, "tsconfig.json"), "jsonc")
        self.assertEqual(semantic.kind(product, "portal/tsconfig.app.json"), "jsonc")
        self.assertEqual(semantic.kind(STUB, "tsconfig.json"), "json")
        vscode = _with_jsonc_globs((".vscode/*.json",))
        self.assertEqual(semantic.kind(vscode, ".vscode/settings.json"), "jsonc")
        self.assertEqual(semantic.kind(vscode, "sub/.vscode/settings.json"), "json")
        for path, kind in (
            ("a.jsonc", "jsonc"),
            ("ci.yml", "yaml"),
            ("x.yaml", "yaml"),
            ("pyproject.toml", "toml"),
            ("flake.nix", None),
        ):
            self.assertEqual(semantic.kind(STUB, path), kind, path)

    def test_equal(self):
        rendered = "# header\na: [1, 2]\n"
        self.assertTrue(
            semantic.equal(STUB, "x.yml", "# header\na:\n  - 1\n  - 2\n", rendered)
        )
        self.assertFalse(
            semantic.equal(STUB, "x.yml", "# other\na: [1, 2]\n", rendered)
        )
        self.assertFalse(semantic.equal(STUB, "x.yml", None, rendered))
        self.assertFalse(semantic.equal(STUB, "x.nix", rendered, rendered))
        jsonc = _with_jsonc_globs((".vscode/*.json",))
        self.assertTrue(
            semantic.equal(
                jsonc,
                ".vscode/s.json",
                '// h\n{\n\t"a": 1, // c\n\t"b": 2,\n}\n',
                '// h\n{\n  "a": 1,\n  "b": 2\n}\n',
            )
        )


def _with_jsonc_globs(patterns):
    return dataclasses.replace(STUB, jsonc_globs=patterns)


LOCAL = "# stub-nix:local-begin {name}\n{body}# stub-nix:local-end {name}\n"


def region(name: str, body: str = "", leader: str = "#") -> str:
    text = LOCAL.format(name=name, body=body)
    return text.replace("# stub-nix", f"{leader} stub-nix")


class TestRegions(unittest.TestCase):
    def test_splice_keeps_the_checkouts_lines(self):
        rendered = "a\n" + region("extra") + "b\n"
        current = "a\n" + region("extra", "mine\n") + "b\n"
        self.assertEqual(
            regions.splice(STUB, rendered, current, "f", ("extra",)),
            "a\n" + region("extra", "mine\n") + "b\n",
        )
        self.assertEqual(
            regions.splice(STUB, rendered, None, "f", ("extra",)), rendered
        )
        self.assertEqual(regions.filled(STUB, current, "f", ("extra",)), ["extra"])
        self.assertEqual(regions.bodies(STUB, current, "f", ("extra",)), "mine")

    def test_markers_are_the_products(self):
        kit_text = region("extra", "x\n").replace("stub-nix", "kit-nix")
        self.assertEqual(
            regions.parse(KIT, kit_text, "f", ("extra",)), {"extra": ["x"]}
        )
        # Another product's markers are ordinary lines.
        self.assertEqual(regions.parse(STUB, kit_text, "f", ("extra",)), {})

    def test_slash_leader(self):
        text = region("extra", "// mine\n", leader="//")
        self.assertEqual(regions.parse(STUB, text, "f", ("extra",)), {})
        self.assertEqual(
            regions.parse(STUB, text, "f", ("extra",), leader="//"),
            {"extra": ["// mine"]},
        )
        self.assertEqual(regions.leader_of(STUB, "jsonc"), "//")
        self.assertEqual(regions.leader_of(STUB, "nix"), "#")
        self.assertEqual(regions.leader_of(STUB, None), "#")

    def test_errors_are_exit_2(self):
        for text, message in (
            (region("other"), "unknown local region 'other'"),
            (region("extra") + region("extra"), "appears twice"),
            ("# stub-nix:local-begin extra\n", "never closed"),
            ("# stub-nix:local-end extra\n", "does not close"),
            (
                "# stub-nix:local-begin extra\n# stub-nix:local-begin extra\n",
                "begins inside",
            ),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConfigError, message),
            ):
                regions.parse(STUB, text, "f", ("extra",))


DEPENDABOT = """version: 2
updates:
  - package-ecosystem: pip
    directory: /
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    target-branch: "17.0"
    directory: /
{region}"""


class TestRedefinitions(unittest.TestCase):
    def check(self, text: str, header: str = "yaml", managed=()) -> None:
        regions.check_redefinitions(STUB, "f", text, header, managed)

    def test_dependabot_entries_key_on_the_target_branch(self):
        # Another branch for the same ecosystem and directory is an addition.
        ok = region(
            "updates",
            "  - package-ecosystem: pip\n    directory: /\n    target-branch: '16.0'\n",
        )
        self.check(DEPENDABOT.format(region=ok))
        ok = region(
            "updates",
            "  - package-ecosystem: github-actions\n    directory: /\n",
        )
        self.check(DEPENDABOT.format(region=ok))

    def test_dependabot_redefinitions(self):
        same = region("updates", "  - package-ecosystem: pip\n    directory: /\n")
        with self.assertRaisesRegex(
            ConfigError, r"redefines what sync manages: updates entry pip /$"
        ):
            self.check(DEPENDABOT.format(region=same))
        # target-branch before directory, quoted either way.
        branch = region(
            "updates",
            "  - package-ecosystem: github-actions\n    target-branch: '17.0'\n"
            "    directory: /\n",
        )
        with self.assertRaisesRegex(
            ConfigError, "updates entry github-actions / → 17.0"
        ):
            self.check(DEPENDABOT.format(region=branch))

    def test_hook_ids_sections_and_flake_inputs(self):
        hooks = "repos:\n  - repo: x\n    hooks:\n      - id: ruff\n"
        with self.assertRaisesRegex(ConfigError, "hook id ruff"):
            self.check(hooks + region("hooks", "      - id: ruff\n"))
        self.check(hooks + region("hooks", "      - id: mine\n"))
        ini = "[*]\nindent_style = space\n"
        with self.assertRaisesRegex(ConfigError, r"section \[\*\]"):
            self.check(ini + region("extra", "[*]\n"), header="ini")
        self.check(ini + region("extra", "[*]\n"), header="yaml")
        flake = '{\n  inputs = {\n    nixpkgs = {\n      url = "x";\n    };\n' + region(
            "inputs", '    mine.url = "y";\n'
        )
        self.check(flake + "  };\n}\n", header="nix", managed=("stub-nix",))
        bad = flake.replace("mine.url", "nixpkgs.url")
        with self.assertRaisesRegex(ConfigError, "flake input nixpkgs"):
            self.check(bad, header="nix")
        managed = flake.replace("mine.url", "stub-nix.url")
        with self.assertRaisesRegex(ConfigError, "flake input stub-nix"):
            self.check(managed, header="nix", managed=("stub-nix",))


class TestUvFloors(unittest.TestCase):
    def test_lock_shortfalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "uv.lock"
            self.assertEqual(floors.lock_versions(lock), {})
            lock.write_text(
                'version = 1\n\n[[package]]\nname = "ruff"\nversion = "0.6.0"\n\n'
                '[[package]]\nname = "pytest"\nversion = "8.3.0"\n'
            )
            self.assertEqual(
                floors.lock_shortfalls(
                    lock, {"ruff": "0.7.0", "pytest": "8.0", "mypy": "1"}
                ),
                ["mypy", "ruff"],
            )
            lock.write_text("[[package]\n")
            with self.assertRaises(ConfigError):
                floors.lock_versions(lock)

    def test_rev_and_pin_keep_what_is_there(self):
        current = "repos:\n  - repo: https://x/y\n    rev: v2.0.0\n"
        self.assertEqual(floors.rev(current, "https://x/y", "1.0.0"), "v2.0.0")
        self.assertEqual(floors.rev(current, "https://x/y", "3.0.0"), "3.0.0")
        sha = "c" * 40
        self.assertEqual(
            floors.pin(f"uses: o/r@{sha} # v9\n", "o/r", "d" * 40, "v1"),
            f"o/r@{sha} # v9",
        )
        self.assertEqual(floors.pin(None, "o/r", sha, "v1"), f"o/r@{sha} # v1")


if __name__ == "__main__":
    unittest.main()
