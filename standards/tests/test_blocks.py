"""Anchored sentinel blocks (DESIGN §5.6): insertion at each anchor, replacement in place,
the inferred indentation, retraction, malformed owned blocks, foreign blocks kept byte
for byte, an anchor inside a foreign region, ``absorb`` (with the comment paragraph it
empties), two blocks in one file, and a missing file with and without a seed."""

import tomllib
import unittest
from types import SimpleNamespace

from support import KIT
from support.checkouts import CheckoutCase

from standards_core.common.report import CLEAN, DRIFT, ConfigError
from standards_core.scaffold import blocks, context, manifest
from standards_core.testing.fakes import git

BEGIN = "# >>> kit-nix: {} (managed) >>>"
END = "# <<< kit-nix: {} (managed) <<<"


def block(name: str, *lines: str, indent: str = "") -> str:
    return "".join(
        indent + line + "\n" if line else "\n"
        for line in (BEGIN.format(name), *lines, END.format(name))
    )


def entry(**raw):
    raw = {
        "path": "f",
        "strategy": "sentinel",
        "module": "dev-shell",
        "template": "t",
        "block": "b",
        "anchor": "eof",
        **raw,
    }
    return manifest.parse(KIT, {"t.json": {"entries": [raw]}}).entries[0]


class Handler(blocks.SentinelHandler):
    """The sentinel handler with its body, seed and owned names given."""

    def __init__(self, body: str, *, seed: str | None = None, owned=("b",)) -> None:
        self._body = body
        self._seed = seed
        self._owned = tuple(owned)

    def body(self, plan, entry, templates):
        return self._body.rstrip("\n")

    def seed(self, plan, entry, templates):
        return self._seed

    def owned(self, plan, entry):
        return self._owned


def plan(force: bool = False):
    return SimpleNamespace(
        product=KIT,
        ctx=context.ns({"options": {"force": force}, "modules": {}}),
        root=None,
        templates=None,
        notices=[],
    )


def run(text, body, *, seed=None, owned=("b",), **raw):
    """``(wanted, problem, code)`` of one entry on ``text``."""
    (it,) = Handler(body, seed=seed, owned=owned).render(
        plan(), entry(**raw), raw.get("path", "f"), text, None
    )
    return it.wanted, it.problem, it.code


class TestScan(unittest.TestCase):
    def scan(self, text, owned=("b",), comment="#"):
        return blocks.scan(text, KIT, comment, path="f", owned=owned)

    def test_owned_and_foreign(self):
        text = (
            "x\n"
            + block("b", "y")
            + "# >>> kit-nix: install set (managed from units.txt) >>>\n"
            "z\n"
            "# <<< kit-nix: install set <<<\n" + block("other", "w")
        )
        found = self.scan(text)
        self.assertEqual(
            [(f.name, f.begin, f.end) for f in found],
            [("b", 1, 3), (None, 4, 6), (None, 7, 9)],
        )

    def test_malformed_owned_blocks_are_exit_2_with_their_line(self):
        cases = (
            (BEGIN.format("b") + "\nx\n", r"^f:1: the b block never ends"),
            ("x\n" + END.format("b") + "\n", r"^f:2: the b block ends, never begun"),
            (block("b") + block("b"), r"^f:3: the b block is there twice"),
            (
                BEGIN.format("b") + "\n" + block("c") + END.format("b") + "\n",
                r"^f:2: the c block opens inside the b block",
            ),
            (
                "# >>> gen: x >>>\n" + block("b") + "# <<< gen: x <<<\n",
                (
                    r"^f:2: the b block marker is inside the foreign block that starts"
                    r" at line 1"
                ),
            ),
        )
        for text, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaises(ConfigError) as caught,
            ):
                self.scan(text, owned=("b", "c"))
            self.assertRegex(str(caught.exception), message)

    def test_names_with_spaces_never_match(self):
        text = "# >>> kit-nix: install set (managed) >>>\nx\n"
        # A foreign begin with no end runs to the end of the file.
        found = self.scan(text)
        self.assertEqual([(f.name, f.closed) for f in found], [(None, False)])

    def test_other_comment_kinds(self):
        text = "// >>> kit-nix: b (managed) >>>\nx\n// <<< kit-nix: b (managed) <<<\n"
        self.assertEqual([f.name for f in self.scan(text, comment="//")], ["b"])
        self.assertEqual(self.scan(text), [])
        html = (
            "<!-- kit-nix:begin b -->\nx\n<!-- kit-nix:end b -->\n<!-- >>> a >>> -->\n"
        )
        found = self.scan(html, comment="html")
        self.assertEqual(
            [(f.name, f.closed) for f in found], [("b", True), (None, False)]
        )


class TestAnchors(unittest.TestCase):
    def test_eof(self):
        self.assertEqual(run("a\n", "x")[0], "a\n" + block("b", "x"))
        # A file without a final newline gets one first.
        self.assertEqual(run("a", "x")[0], "a\n" + block("b", "x"))
        self.assertEqual(run("", "x")[0], block("b", "x"))

    def test_nix_attrset_and_its_indentation(self):
        for unit in ("  ", "    ", "\t"):
            with self.subTest(unit=repr(unit)):
                text = (
                    "{\n"
                    + unit
                    + "inputs = {\n"
                    + unit * 2
                    + 'a.url = "x";\n'
                    + unit
                    + "};\n}\n"
                )
                wanted, _, _ = run(
                    text,
                    'k.url = "y";\nz = {\n  flake = false;\n};',
                    anchor="nix-attrset inputs",
                )
                inner = unit * 2
                self.assertEqual(
                    wanted,
                    "{\n"
                    + unit
                    + "inputs = {\n"
                    + inner
                    + 'a.url = "x";\n'
                    + block(
                        "b",
                        'k.url = "y";',
                        "z = {",
                        "  flake = false;",
                        "};",
                        indent=inner,
                    )
                    + unit
                    + "};\n}\n",
                )
        # An empty set: one unit deeper than its opener.
        wanted, _, _ = run(
            "{\n  inputs = {\n  };\n}\n", "a = 1;", anchor="nix-attrset inputs"
        )
        self.assertIn("    # >>> kit-nix: b (managed) >>>\n    a = 1;\n", wanted)
        # A path of sets.
        text = "{\n  a = {\n    b = {\n      x = 1;\n    };\n  };\n}\n"
        wanted, _, _ = run(text, "y = 2;", anchor="nix-attrset a.b")
        self.assertIn(
            "      x = 1;\n" + block("b", "y = 2;", indent="      ") + "    };", wanted
        )

    def test_nix_attrset_errors(self):
        for text, message in (
            (
                '{\n  inputs = { a.url = "x"; };\n}\n',
                "with its attributes on lines of their own",
            ),
            ("{\n  outputs = { };\n}\n", "anchor nix-attrset inputs is missing"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConfigError, message),
            ):
                run(text, "x = 1;", anchor="nix-attrset inputs")

    def test_toml_array(self):
        text = (
            '[project]\nname = "x"\ndependencies = [\n    "a",\n]\n\n[tool.x]\ny = 1\n'
        )
        wanted, problem, code = run(
            text, '"k",', anchor="toml-array project.dependencies"
        )
        self.assertEqual(
            wanted,
            '[project]\nname = "x"\ndependencies = [\n    "a",\n'
            + block("b", '"k",', indent="    ")
            + "]\n\n[tool.x]\ny = 1\n",
        )
        self.assertEqual((problem, code), ("missing the b block", DRIFT))
        self.assertEqual(tomllib.loads(wanted)["project"]["dependencies"], ["a", "k"])

    def test_toml_array_shapes(self):
        anchor = "toml-array project.dependencies"
        # An inline empty array is opened up first; the indent is the key's plus four.
        wanted = run("[project]\ndependencies = []\n", '"k",', anchor=anchor)[0]
        self.assertEqual(
            wanted,
            "[project]\ndependencies = [\n" + block("b", '"k",', indent="    ") + "]\n",
        )
        # The last item gets the comma it lacks; a comment after it stays a comment.
        wanted = run(
            '[project]\ndependencies = [\n  "a" # x, y\n]\n', '"k",', anchor=anchor
        )[0]
        self.assertIn('  "a", # x, y\n  # >>> kit-nix: b', wanted)
        self.assertEqual(tomllib.loads(wanted)["project"]["dependencies"], ["a", "k"])
        # Dotted at the root; and the root table itself (".key").
        wanted = run("project.dependencies = [\n]\n", '"k",', anchor=anchor)[0]
        self.assertEqual(tomllib.loads(wanted)["project"]["dependencies"], ["k"])
        wanted = run("deps = [\n]\n[t]\ndeps = 1\n", '"k",', anchor="toml-array .deps")[
            0
        ]
        self.assertEqual(tomllib.loads(wanted)["deps"], ["k"])
        for text, message in (
            (
                '[project]\ndependencies = ["a"]\n',
                "with its items on lines of their own",
            ),
            (
                "[project]\nname = 'x'\n",
                "anchor toml-array project.dependencies is missing",
            ),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConfigError, message),
            ):
                run(text, '"k",', anchor=anchor)
        with self.assertRaisesRegex(ConfigError, "does not leave valid TOML"):
            run("[project]\ndependencies = [\n]\n", "not an item", anchor=anchor)

    def test_anchor_found(self):
        self.assertTrue(
            blocks.anchor_found("nix-attrset inputs", "{\n  inputs = {\n  };\n}\n")
        )
        self.assertFalse(blocks.anchor_found("nix-attrset inputs", '{ a = "x; }'))
        self.assertTrue(blocks.anchor_found("toml-array a.b", "[a]\nb = [\n]\n"))


class TestWrite(unittest.TestCase):
    def test_replaced_in_place_wherever_it_is(self):
        text = (
            "{\n  "
            + BEGIN.format("b")
            + "\n  old = 1;\n  "
            + END.format("b")
            + "\n  inputs = {\n  };\n}\n"
        )
        wanted, problem, code = run(text, "new = 2;", anchor="nix-attrset inputs")
        self.assertEqual(wanted, text.replace("old = 1;", "new = 2;"))
        self.assertEqual((problem, code), ("the b block differs", DRIFT))
        # The same body is clean.
        self.assertEqual(
            run(wanted, "new = 2;", anchor="nix-attrset inputs")[1:], ("", CLEAN)
        )

    def test_empty_body_lines_stay_empty(self):
        wanted = run("", "a\n\nb", anchor="eof")[0]
        self.assertEqual(wanted, block("b", "a", "", "b"))
        wanted = run(
            "{\n  x = {\n  };\n}\n", "a = 1;\n\nb = 2;", anchor="nix-attrset x"
        )[0]
        self.assertIn("    a = 1;\n\n    b = 2;\n", wanted)

    def test_retract_on_module_off_and_on_when_false(self):
        text = "a\n" + block("b", "x") + "c\n"
        handler = Handler("x")
        e = entry()
        for method in (handler.retract, handler.absent):
            with self.subTest(method=method.__name__):
                gone = method(plan(), e, "f", text)
                self.assertEqual(gone.wanted, "a\nc\n")
                self.assertEqual(gone.problem, "the b block goes with dev-shell")
        self.assertIsNone(handler.retract(plan(), e, "f", "a\n"))

    def test_foreign_blocks_are_kept_byte_for_byte(self):
        foreign = (
            "    # >>> kit-nix: install set (managed from units.txt) >>>\n"
            '    "kit-unit-alpha",\n'
            '    "k",\n'
            "    # <<< kit-nix: install set <<<\n"
        )
        text = "[project]\ndependencies = [\n" + foreign + "]\n"
        wanted = run(
            text, '"k",', anchor="toml-array project.dependencies", absorb=True
        )[0]
        # Neither read, moved nor absorbed; the block goes after it.
        self.assertEqual(
            wanted,
            "[project]\ndependencies = [\n"
            + foreign
            + block("b", '"k",', indent="    ")
            + "]\n",
        )

    def test_an_anchor_inside_a_foreign_region_is_exit_2(self):
        text = "x\n# >>> gen: tail (managed by gen) >>>\ny\n"
        with self.assertRaisesRegex(
            ConfigError,
            r"^f:4: the b block would go inside the foreign block that starts at line 2;"
            " move one of them$",
        ):
            run(text, "z")
        no_newline = "x\n# >>> gen: tail >>>\ny"
        with self.assertRaisesRegex(ConfigError, "foreign block that starts at line 2"):
            run(no_newline, "z")
        text = "[p]\nd = [\n  # >>> gen: x >>>\n  'a',\n]\n"
        with self.assertRaisesRegex(ConfigError, "would go inside the foreign block"):
            run(text, '"k",', anchor="toml-array p.d")

    def test_absorb(self):
        text = (
            "# build output\n/result\n/.venv\nnode_modules/\n\n"
            "# >>> gen: other (managed by gen) >>>\n/.venv\n# <<< gen: other <<<\n"
        )
        wanted, problem, _ = run(
            text, "/.venv\n# a comment line\n/.direnv/", absorb=True
        )
        self.assertEqual(
            wanted,
            "# build output\n/result\nnode_modules/\n\n"
            "# >>> gen: other (managed by gen) >>>\n/.venv\n# <<< gen: other <<<\n"
            + block("b", "/.venv", "# a comment line", "/.direnv/"),
        )
        self.assertEqual(problem, "line 3 is in the b block; missing the b block")

    def test_absorb_takes_the_comment_paragraph_it_empties(self):
        text = "# the shell's state\n/.venv\n/.direnv/\n\n# mine\nlocal/\n"
        wanted, problem, _ = run(text, "/.venv\n/.direnv/", absorb=True)
        self.assertEqual(wanted, "# mine\nlocal/\n" + block("b", "/.venv", "/.direnv/"))
        self.assertEqual(
            problem,
            "line 1 goes with the lines the b block holds; line 2 is in the b block;"
            " line 3 is in the b block; missing the b block",
        )
        # Comments and blank lines alone are never absorbed.
        same = "# /.venv\n\n"
        self.assertTrue(run(same, "# /.venv", absorb=True)[0].startswith(same))

    def test_missing_file(self):
        wanted, problem, code = run(None, "x")
        self.assertEqual((wanted, problem, code), (block("b", "x"), "missing", DRIFT))
        seed = "{\n  inputs = {\n  };\n}\n"
        wanted, problem, _ = run(None, "a = 1;", seed=seed, anchor="nix-attrset inputs")
        self.assertEqual(
            wanted,
            "{\n  inputs = {\n" + block("b", "a = 1;", indent="    ") + "  };\n}\n",
        )
        self.assertEqual(problem, "missing")
        with self.assertRaisesRegex(
            ConfigError, r"^f does not exist: the b block needs nix-attrset inputs$"
        ):
            run(None, "a = 1;", anchor="nix-attrset inputs")

    def test_two_blocks_in_one_file(self):
        owned = ("b", "c")
        first = Handler("one", owned=owned)
        second = Handler("two", owned=owned)
        (a,) = first.render(plan(), entry(block="b"), "f", "x\n", None)
        (b,) = second.render(plan(), entry(block="c"), "f", a.wanted, None)
        self.assertEqual(b.wanted, "x\n" + block("b", "one") + block("c", "two"))
        # Each sees the other's markers as owned, never as foreign.
        (again,) = first.render(plan(), entry(block="b"), "f", b.wanted, None)
        self.assertEqual(again.code, CLEAN)


# --- through the engine: the kit's .gitignore holds two blocks ------------------------------


class TestKitGitignore(CheckoutCase):
    product = KIT
    profile = "recommended"

    def setUp(self) -> None:
        super().setUp()
        self.write(
            "pyproject.toml",
            '[tool.kit-nix]\nschema = 1\nprofile = "recommended"\n',
        )
        git(self.root, "rm", "-q", "--cached", "-r", "demo_unit")
        (self.root / "demo_unit" / "__init__.py").unlink()
        self.write("alpha/__unit__.toml", 'version = "18.0.1.0.0"\n')
        self.write("alpha/README.md", "# alpha\n")
        self.write(".gitignore", "# local\n/.venv\nscratch/\n")
        self.commit("bundle")

    def test_two_entries_one_item(self):
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(
            ".gitignore (sentinel): line 2 is in the dev-shell block; missing the"
            " dev-shell block; missing the lint block",
            out,
        )
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.read(".gitignore"),
            "# local\nscratch/\n"
            + block("dev-shell", "/.venv", "/.direnv/", "/result")
            + block("lint", ".ruff_cache/"),
        )

    def test_retracted_with_its_module(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.fake_locks()
        self.commit("synced")
        self.table("python-lint.enable = false\n")
        code, out = self.check()
        self.assertIn("the lint block goes with python-lint", out)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertNotIn("lint (managed)", self.read(".gitignore"))
        self.assertIn("dev-shell (managed)", self.read(".gitignore"))

    def test_a_foreign_marker_of_the_products_shape_is_kept(self):
        text = self.read(".gitignore") + block("retired", "old/")
        self.write(".gitignore", text)
        self.commit("old block")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertIn(block("retired", "old/"), self.read(".gitignore"))

    def table(self, extra: str) -> None:
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                "[tool.kit-nix]\n", f"[tool.kit-nix]\n{extra}", 1
            ),
        )


if __name__ == "__main__":
    unittest.main()
