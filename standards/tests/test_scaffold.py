"""The engine's leaves: globs (with braces), the formatter-shaped JSON, local regions."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB

from standards_core.common.report import ConfigError
from standards_core.product import HeaderKind, JsonStyle
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


def _manifest(product, *entries, retire=(), floors=None):
    from standards_core.scaffold import manifest

    doc = {"entries": list(entries), "retire": list(retire)}
    if floors:
        doc["floors"] = floors
    return manifest.parse(product, {"a.json": doc})


class TestManifest(unittest.TestCase):
    def test_the_stub_manifest(self):
        from standards_core.scaffold import manifest

        m = manifest.load(STUB)
        self.assertIs(m, manifest.load(STUB))
        paths = [e.path for e in m.entries]
        # Fragments are united in file-name order: ci.json before core.json.
        self.assertEqual(
            paths[:2], [".github/dependabot.yml", ".github/workflows/ci.yml"]
        )
        self.assertIn("flake.nix", paths)
        lock = next(e for e in m.entries if e.path == "tools/uv.lock")
        self.assertEqual(lock.modules, ("python-lint", "tests"))
        self.assertEqual(
            (lock.relock_when, lock.floors), (("tools/pyproject.toml",), "uv")
        )
        self.assertEqual(m.floors["uv"]["ruff"], "0.6.0")
        rules = {r.rule: r for r in m.retire}
        sections = [r.only_sections for r in m.retire if r.paths == ("setup.cfg",)]
        self.assertEqual(sections, [("flake8",), ("lintcheck", "sortcheck")])
        braces = next(r for r in m.retire if "lint.config.js" in r.paths)
        self.assertEqual(braces.paths, ("lint.config.js", "lint.config.mjs"))
        self.assertTrue(any(r.unmanaged and r.contains for r in rules.values()))
        self.assertTrue(manifest.ships(STUB, "committed.toml"))
        self.assertFalse(manifest.ships(STUB, "nope"))
        self.assertEqual(manifest.load(KIT).entries, ())

    def test_entry_rules(self):
        from standards_core.scaffold.manifest import ManifestError

        whole = {"path": "a", "strategy": "whole", "module": "ci", "template": "a.j2"}
        sentinel = {
            "path": "f",
            "strategy": "sentinel",
            "module": "dev-shell",
            "template": "b.j2",
            "block": "inputs",
            "anchor": "nix-attrset inputs",
            "claims": "nix-inputs",
        }
        for bad, why in (
            ({**whole, "nope": 1}, "unknown key"),
            ({**whole, "strategy": "copy"}, "unknown strategy"),
            ({**whole, "module": "nope"}, "unknown module"),
            ({k: v for k, v in whole.items() if k != "template"}, "needs a template"),
            ({**whole, "header": "md"}, "unknown header"),
            ({**whole, "local_regions": ["x"]}, "local regions need"),
            ({**whole, "phase": "c"}, "phase must be"),
            ({**whole, "block": "x"}, "are sentinel keys"),
            ({**sentinel, "block": "Inputs"}, "a block name"),
            ({**sentinel, "anchor": "top"}, "needs an anchor"),
            ({**sentinel, "anchor": "eof"}, "claims is nix-inputs"),
            ({**sentinel, "comment": ";"}, "comment must be"),
        ):
            with self.subTest(why=why), self.assertRaisesRegex(ManifestError, why):
                _manifest(STUB, bad)
        m = _manifest(
            STUB, {**whole, "header": "ini", "local_regions": ["x"]}, sentinel
        )
        self.assertEqual(m.entries[1].anchor, "nix-attrset inputs")
        self.assertEqual(m.entries[1].comment, "#")
        md = dataclasses.replace(STUB, header_kinds={"md": HeaderKind("<!--", "-->")})
        self.assertEqual(
            _manifest(md, {**whole, "header": "md"}).entries[0].header, "md"
        )
        with self.assertRaisesRegex(ManifestError, "local regions need"):
            _manifest(md, {**whole, "header": "md", "local_regions": ["x"]})

    def test_duplicates_are_keyed_on_path_and_block(self):
        from standards_core.scaffold.manifest import ManifestError

        sentinel = {
            "path": ".gitignore",
            "strategy": "sentinel",
            "module": "dev-shell",
            "template": "g.j2",
            "block": "dev",
            "anchor": "eof",
        }
        m = _manifest(
            STUB, sentinel, {**sentinel, "block": "lint", "module": "python-lint"}
        )
        self.assertEqual([e.block for e in m.entries], ["dev", "lint"])
        whole = {"path": ".gitignore", "strategy": "seed", "module": "ci"}
        for entries in (
            (sentinel, sentinel),
            (sentinel, whole),
            (whole, sentinel),
            (whole, whole),
        ):
            with (
                self.subTest(entries=[e["strategy"] for e in entries]),
                self.assertRaisesRegex(ManifestError, "is listed in"),
            ):
                _manifest(STUB, *entries)

    def test_retire_rules(self):
        from standards_core.scaffold.manifest import ManifestError

        rule = {
            "paths": ["a.{x,y}"],
            "rule": "r",
            "module": "ci",
            "except": ["b/{c,d}"],
        }
        (r,) = _manifest(STUB, retire=[rule]).retire
        self.assertEqual((r.paths, r.unless), (("a.x", "a.y"), ("b/c", "b/d")))
        for bad, why in (
            ({**rule, "only_section": "x", "only_sections": ["x"]}, "exclusive"),
            ({**rule, "nope": True}, "unknown key"),
            ({**rule, "paths": ["a.{x"]}, "never closed"),
            ({"paths": ["a"], "rule": "r"}, "needs paths, rule and module"),
        ):
            with self.subTest(why=why), self.assertRaisesRegex(ManifestError, why):
                _manifest(STUB, retire=[bad])
        with self.assertRaisesRegex(ManifestError, "set by two fragments"):
            from standards_core.scaffold import manifest

            manifest.parse(
                STUB,
                {
                    "a.json": {"floors": {"uv": {"x": "1"}}},
                    "b.json": {"floors": {"uv": {"x": "2"}}},
                },
            )

    def test_profile_rules_and_extra_files(self):
        from standards_core.scaffold import manifest

        cfg = {
            "retire": [{"paths": ["x.{a,b}"], "module": "ci", "contains": ["old"]}],
            "extra-files": [
                {
                    "path": "S.md",
                    "template": "S.md.j2",
                    "strategy": "whole",
                    "module": "hygiene",
                },
                {
                    "path": "T",
                    "template": "T.j2",
                    "strategy": "seed",
                    "module": "ci",
                    "header": "yaml",
                },
            ],
        }
        (r,) = manifest.profile_retire(cfg)
        self.assertEqual(
            (r.paths, r.fragment, r.contains), (("x.a", "x.b"), "profile", ("old",))
        )
        extra = manifest.extra_entries(cfg)
        self.assertEqual(
            [(e.header, e.profile_template) for e in extra],
            [("none", True), ("none", True)],
        )
        with self.assertRaisesRegex(ConfigError, "not a regular expression"):
            manifest.profile_retire(
                {"retire": [{"paths": ["a"], "module": "ci", "contains": ["("]}]}
            )
        with self.assertRaisesRegex(ConfigError, "never closed"):
            manifest.profile_retire({"retire": [{"paths": ["a{"], "module": "ci"}]})


class TestRender(unittest.TestCase):
    def test_header(self):
        from standards_core.scaffold import render
        from standards_core.scaffold.globs import ManifestError

        self.assertEqual(
            render.header(STUB, "nix", True),
            "# stub-nix:managed — generated by stub-nix (`stub-init --sync`); edit only"
            " inside the stub-nix:local region.\n",
        )
        self.assertEqual(
            render.header(KIT, "jsonc", False, "see docs"),
            "// kit-nix:managed — generated by kit-nix (`kit-init --sync`); do not edit."
            " see docs\n",
        )
        self.assertEqual(render.header(STUB, "none", False), "")
        md = dataclasses.replace(STUB, header_kinds={"md": HeaderKind("<!--", "-->")})
        self.assertEqual(
            render.header(md, "md", False),
            "<!-- stub-nix:managed — generated by stub-nix (`stub-init --sync`); do not"
            " edit. -->\n",
        )
        with self.assertRaises(ManifestError):
            render.header(STUB, "md", False)

    def test_filters(self):
        from standards_core.scaffold import render

        def r(product, source, **ctx):
            return render.render_string(product, source, ctx)

        self.assertEqual(r(STUB, "{{ v | json }}", v={"a": 1}), '{\n\t"a": 1\n}\n')
        self.assertEqual(r(KIT, "{{ v | json }}", v={"a": 1}), '{\n  "a": 1\n}\n')
        self.assertEqual(r(STUB, "{{ v | nix_string }}", v='a"${b}'), '"a\\"\\${b}"')
        self.assertEqual(r(STUB, "{{ v | nix_attr }}", v="my.unit"), '"my.unit"')
        self.assertEqual(r(STUB, "{{ v | nix_attr }}", v="unit"), "unit")
        self.assertEqual(r(STUB, "{{ v | yaml_json }}", v=["a'b"]), "'[\"a''b\"]'")
        self.assertEqual(
            r(STUB, "{{ v | glob_to_regex }}", v="a/*.ts"), "a/[^/]*\\.ts$"
        )
        self.assertEqual(
            r(STUB, "{{ 'https://x/{repo}/' | org_url }}", repo="o/r"), "https://x/o/r/"
        )

    def test_url_placeholders_apply_in_order(self):
        from standards_core.scaffold import render

        hooks = dataclasses.replace(
            STUB.hooks,
            url_placeholders=lambda ctx: (
                ("name_hyphen", ctx["name_hyphen"]),
                ("name", ctx["name"]),
            ),
        )
        product = dataclasses.replace(STUB, hooks=hooks)
        ctx = {"name": "a_b", "name_hyphen": "a-b"}
        self.assertEqual(
            render.url_template(product, "{name_hyphen}.{name}/{repo}", ctx),
            "a-b.a_b/{repo}",
        )

    def test_product_filters_and_helpers(self):
        from standards_core.scaffold import render

        product = dataclasses.replace(
            STUB,
            jinja_filters={"shout": lambda v: v.upper()},
            hooks=dataclasses.replace(
                STUB.hooks,
                render_helpers=lambda ctx, current: {"gen": lambda: current or "-"},
            ),
        )
        self.assertEqual(render.render_string(product, "{{ 'a' | shout }}", {}), "A")
        self.assertEqual(
            render.render(product, "envrc.j2", {}, None),
            "use flake . --no-pure-eval\n# stub-nix:local-begin envrc\n# stub-nix:local-end envrc\n",
        )
        helpers = render.helpers(product, {}, "now")
        self.assertEqual(
            (sorted(helpers), helpers["gen"]()), (["gen", "pin", "rev"], "now")
        )
        shadow = dataclasses.replace(STUB, jinja_filters={"json": str})
        with self.assertRaisesRegex(RuntimeError, "shadows the core filter"):
            render.environment(shadow)
        for extra in ({"rev": 1}, {"name": 1}):
            bad = dataclasses.replace(
                STUB,
                hooks=dataclasses.replace(
                    STUB.hooks, render_helpers=lambda c, cur, e=extra: e
                ),
            )
            with (
                self.subTest(extra=extra),
                self.assertRaisesRegex(RuntimeError, "render_helpers shadows"),
            ):
                render.helpers(bad, {"name": "x"}, None)

    def test_templates_come_from_the_roots_product_first(self):
        from support import product_copy

        from standards_core.scaffold import render

        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(STUB, Path(tmp))
            (product.data_root / "templates" / "envrc.j2").write_text("own\n")
            self.assertEqual(render.render(product, "envrc.j2", {}, None), "own\n")
            # A product template's error is the product's bug, raised as is.
            (product.data_root / "templates" / "bad.j2").write_text("{{ nope }}")
            with self.assertRaises(Exception) as caught:
                render.render(product, "bad.j2", {}, None)
            self.assertNotIsInstance(caught.exception, ConfigError)

    def test_profile_templates(self):
        from standards_core.scaffold import render

        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            templates.mkdir()
            (templates / "S.md.j2").write_text("{{ name }}\n")
            (templates / "bad.j2").write_text("{{ nope }}\n")
            self.assertEqual(
                render.render(
                    STUB, "S.md.j2", {"name": "u"}, None, directory=templates
                ),
                "u\n",
            )
            with self.assertRaisesRegex(
                ConfigError, "the org profile's templates/bad.j2 does not render"
            ):
                render.render(STUB, "bad.j2", {}, None, directory=templates)
            (templates / "link.j2").symlink_to(templates / "S.md.j2")
            with self.assertRaisesRegex(ConfigError, "templates/link.j2 is a symlink"):
                render.template_files(templates)
            with self.assertRaisesRegex(ConfigError, "templates/link.j2 is a symlink"):
                render.profile_environment(STUB, str(templates)).get_template("link.j2")
            with self.assertRaisesRegex(ConfigError, "sandbox refuses"):
                render.render_string(STUB, "{{ cycler.__init__.__globals__ }}", {})

    def test_evaluate(self):
        from standards_core.scaffold.render import evaluate

        ctx = {
            "modules": {"ci": True, "tests": False},
            "discover": {"units": ["a", "b"]},
            "n": 2,
        }
        for expr, want in (
            ("True", True),
            ("modules.ci and not modules.tests", True),
            ("modules['tests'] or n > 1", True),
            ("len(discover.units) == 2", True),
            ("'c' in discover.units", False),
            ("any([modules.tests, modules.ci])", True),
            ("-n < 0 <= n", True),
            ("discover.units[0] is not None", True),
        ):
            with self.subTest(expr=expr):
                self.assertIs(evaluate(expr, ctx), want)
        for expr, why in (
            ("nope", "not in the context"),
            ("modules.__class__", "names starting with _"),
            ("n.x", "not a table"),
            ("open('x')", "only len, any, all"),
            ("n + 1", "BinOp is not allowed"),
            ("modules.nope", "no 'nope' there"),
            ("(", "is not an expression"),
        ):
            with self.subTest(expr=expr), self.assertRaisesRegex(ConfigError, why):
                evaluate(expr, ctx)


class TestContext(unittest.TestCase):
    def setUp(self):
        from support import git

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        git(self.root, "init", "-q")
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "demo_unit"\n\n[tool.stub-nix]\nschema = 1\nmajor = 3\n'
            'profile = "recommended"\n'
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "first")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, product=STUB, **kwargs):
        from standards_core.common import config, pyproject
        from standards_core.scaffold import context

        checkout = product.hooks.load_checkout(self.root)
        resolved = config.resolve(
            product, pyproject.config_path(product, self.root), checkout=checkout
        )
        return context.build(product, checkout, resolved, floors={"uv": {}}, **kwargs)

    def test_core_keys(self):
        from standards_core.scaffold import context

        ctx = self.build(
            lock={"rev": "a" * 40, "owner": "fork", "repo": "stub-nix"},
            options={"x": 1},
        )
        core = set(context.CORE_KEYS) - {"previous", "history"}
        self.assertEqual(set(ctx), core | {"stub_nix", "major", "site"})
        self.assertEqual((ctx.name, ctx.name_hyphen), ("demo_unit", "demo-unit"))
        self.assertEqual(
            ctx.branches, {"integration": "develop", "release": "version-3"}
        )
        self.assertEqual(ctx.gates, ["pr-policy", "lint", "test"])
        self.assertEqual(
            ctx.discover,
            {
                "gitmodules": False,
                "github_workflows": False,
                "tests_dir": False,
                "requirements": False,
            },
        )
        self.assertEqual(
            ctx.engine,
            {
                "rev": "a" * 40,
                "version": "1.0.0",
                "major": 1,
                "owner": "fork",
                "name": "stub-nix",
                "url": "github:example/stub-nix/release-1",
            },
        )
        self.assertIs(ctx.stub_nix, ctx.engine)
        self.assertEqual(ctx.options, {"force": False, "x": 1})
        self.assertEqual(ctx.profile_input, "")
        self.assertEqual((ctx.major, ctx.site), (3, "demo-unit.localhost"))
        self.assertIsInstance(ctx.first_commit_year, int)
        self.assertEqual(ctx.unmanaged_dirs, [])
        # A table may name another engine URL.
        (self.root / "pyproject.toml").write_text(
            (self.root / "pyproject.toml").read_text()
            + '\n[tool.stub-nix.dev-shell]\nstub-nix-url = "github:me/stub-nix"\n'
        )
        self.assertEqual(self.build().engine["url"], "github:me/stub-nix")

    def test_the_release_branch_follows_the_facts(self):
        """Under a series branching the release branch is the series the checkout's files name."""
        (self.root / ".kit-series").write_text("18.0\n")
        (self.root / "pyproject.toml").write_text(
            '[tool.kit-nix]\nschema = 1\nprofile = "recommended"\n'
        )
        from support import git

        git(self.root, "add", "-A")
        ctx = self.build(KIT)
        self.assertEqual(ctx.branches, {"integration": "develop", "release": "18.0"})
        self.assertEqual((ctx.shape, ctx.series), ("", "18.0"))
        self.assertIs(ctx.kit_nix, ctx.engine)

    def test_shadowing_is_a_bug(self):
        bad = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(
                STUB.hooks, template_globals=lambda c, r, ctx: {"gates": []}
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "template_globals shadows"):
            self.build(bad)
        bad = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(
                STUB.hooks, discover=lambda c, cfg, m: {"gitmodules": 1}
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "hooks.discover shadows"):
            self.build(bad)

    def test_repoconfig(self):
        from standards_core.common import repoconfig

        rc = repoconfig.load(STUB, self.root)
        self.assertEqual(
            (rc.name, rc.integration, rc.release_branch),
            ("demo_unit", "develop", "version-3"),
        )
        self.assertEqual(rc.gates, ["pr-policy", "lint", "test"])
        self.assertEqual(rc.facts["tests_dir"], False)
        hooked = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(
                STUB.hooks, release_branch=lambda cfg, modules, facts: "stable"
            ),
        )
        self.assertEqual(repoconfig.load(hooked, self.root).release_branch, "stable")
        self.assertEqual(
            repoconfig.release_branch(STUB, rc.cfg, {"releases": False}, rc.facts), ""
        )
