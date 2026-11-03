import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB

from standards_core.common import pyproject
from standards_core.common.report import ConfigError, EnvError


class TestPyproject(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pyproject.toml"
            with self.assertRaises(EnvError):
                pyproject.load(STUB, root)
            path.write_text("[tool\n")
            with self.assertRaises(ConfigError):
                pyproject.load(STUB, root)
            path.write_text("[tool.stub-nix]\nschema = 1\n")
            self.assertEqual(
                pyproject.load(STUB, root)["tool"]["stub-nix"]["schema"], 1
            )

    def test_optional_config_file_reads_as_empty(self):
        # The kit has config_required = False: a checkout without the file is not opted
        # in (exit 2 with the hint later), never an environment error.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(pyproject.load(KIT, root), {})
            self.assertEqual(pyproject.config_text(KIT, root), "")
            self.assertFalse(pyproject.opted_in(KIT, pyproject.load(KIT, root)))
            (root / "pyproject.toml").write_text("[tool.kit-nix]\nschema = 1\n")
            self.assertTrue(pyproject.opted_in(KIT, pyproject.load(KIT, root)))

    def test_lone_carriage_return_reaches_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_bytes(b"[tool.stub-nix]\rschema = 1\n")
            self.assertIn("\r", pyproject.read(path))
            with self.assertRaises(ConfigError):
                pyproject.load(STUB, Path(tmp))

    def test_table_and_opt_in(self):
        self.assertIsNone(pyproject.tool_table(STUB, {}))
        self.assertFalse(pyproject.opted_in(STUB, {"tool": {"ruff": {}}}))
        self.assertTrue(pyproject.opted_in(STUB, {"tool": {"stub-nix": {}}}))
        self.assertFalse(pyproject.opted_in(KIT, {"tool": {"stub-nix": {}}}))
        with self.assertRaisesRegex(ConfigError, r"\[tool.stub-nix\] must be a table"):
            pyproject.tool_table(STUB, {"tool": {"stub-nix": 1}})

    def test_opt_in_line(self):
        for line, seen in (
            ("[tool.stub-nix]", True),
            ("  [tool.stub-nix.commits]", True),
            ("[[tool.stub-nix.extra-files]]", True),
            ("[tool.stub-nixer]", False),
            ('[tool."stub-nix"]', False),
            ("[ tool.stub-nix ]", False),
            ("# [tool.stub-nix]", False),
        ):
            with self.subTest(line=line):
                self.assertEqual(pyproject.opt_in_line(STUB, f"x = 1\n{line}\n"), seen)
        self.assertFalse(pyproject.opt_in_line(KIT, "[tool.stub-nix]\n"))

    def test_spelling_the_shell_and_the_tools_disagree_on(self):
        text = '[tool."stub-nix"]\nschema = 1\n'
        doc = pyproject.parse(text, Path("pyproject.toml"))
        with self.assertRaisesRegex(ConfigError, "without stub-nix's tools"):
            pyproject.check_opt_in_spelling(STUB, text, doc)
        text = 'x = """\n[tool.stub-nix]\n"""\n'
        doc = pyproject.parse(text, Path("pyproject.toml"))
        with self.assertRaisesRegex(ConfigError, "TOML sees no such table"):
            pyproject.check_opt_in_spelling(STUB, text, doc)
        text = "[tool.stub-nix]\nschema = 1\n"
        pyproject.check_opt_in_spelling(STUB, text, pyproject.parse(text, Path("p")))

    def test_project_name_and_hint(self):
        self.assertEqual(
            pyproject.project_name({"project": {"name": "demo_unit"}}), "demo_unit"
        )
        self.assertIsNone(pyproject.project_name({}))
        self.assertIn("stub-init --sync --standards", pyproject.opt_in_hint(STUB))


if __name__ == "__main__":
    unittest.main()
