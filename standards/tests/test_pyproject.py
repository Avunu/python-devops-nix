import dataclasses
import subprocess
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

    def test_config_paths_and_the_hook(self):
        self.assertEqual(pyproject.config_paths(STUB), ("pyproject.toml",))
        self.assertEqual(
            pyproject.config_paths(KIT), ("pyproject.toml", "kit/pyproject.toml")
        )
        root = Path("/x")
        self.assertEqual(pyproject.config_path(STUB, root), root / "pyproject.toml")
        hooked = dataclasses.replace(
            KIT,
            hooks=dataclasses.replace(
                KIT.hooks, config_file=lambda r: "kit/pyproject.toml"
            ),
        )
        self.assertEqual(pyproject.config_rel(hooked, root), "kit/pyproject.toml")
        self.assertEqual(
            pyproject.config_path(hooked, root), root / "kit/pyproject.toml"
        )
        stray = dataclasses.replace(
            KIT, hooks=dataclasses.replace(KIT.hooks, config_file=lambda r: "x.toml")
        )
        with self.assertRaisesRegex(ConfigError, "not one of identity.config_paths"):
            pyproject.config_path(stray, root)
        # Without config_paths the hook may name any file.
        free = dataclasses.replace(
            STUB, hooks=dataclasses.replace(STUB.hooks, config_file=lambda r: "x.toml")
        )
        self.assertEqual(pyproject.config_path(free, root), root / "x.toml")

    def test_the_outermost_directory_claims_its_config_file(self):
        """From a subdirectory whose own pyproject.toml is the checkout's config file
        (kit/pyproject.toml), the root is the checkout's, not the subdirectory."""
        hooked = dataclasses.replace(
            STUB,
            identity=dataclasses.replace(
                STUB.identity, config_paths=("pyproject.toml", "sub/pyproject.toml")
            ),
            hooks=dataclasses.replace(
                STUB.hooks,
                config_file=lambda r: (
                    "sub/pyproject.toml" if (r / "sub").is_dir() else "pyproject.toml"
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp).resolve() / "top"
            (top / "sub" / "deeper").mkdir(parents=True)
            subprocess.run(["git", "-C", str(top), "init", "-q"], check=True)
            (top / "sub" / "pyproject.toml").write_text("[tool.stub-nix]\n")
            for start in (top, top / "sub", top / "sub" / "deeper"):
                with self.subTest(start=start):
                    self.assertEqual(pyproject.checkout_root(hooked, start), top)
            self.assertEqual(
                pyproject.root_for(hooked, top / "sub" / "pyproject.toml"), top
            )
            # A nested checkout with a config file of its own is its own root.
            inner = top / "inner"
            inner.mkdir()
            (inner / "pyproject.toml").write_text("[tool.stub-nix]\n")
            self.assertEqual(pyproject.checkout_root(hooked, inner), inner)
            self.assertEqual(
                pyproject.root_for(hooked, inner / "pyproject.toml"), inner
            )
            # Nothing found: the work tree's root; outside git, the start or the parent.
            (top / "sub" / "pyproject.toml").unlink()
            self.assertEqual(pyproject.checkout_root(hooked, top / "sub"), top)
            outside = Path(tmp).resolve() / "outside"
            outside.mkdir()
            self.assertEqual(pyproject.checkout_root(STUB, outside), outside)
            self.assertEqual(
                pyproject.root_for(STUB, outside / "pyproject.toml"), outside
            )

    def test_an_opted_in_file_wins_over_a_nearer_one_that_is_not(self):
        """§14 Stage 10 (amends CR1 point 4): a package directory's own pyproject.toml,
        which does not open the table, does not shadow the checkout above it whose table
        is in a subdirectory file (an addon of a repository keeping it in
        nix/pyproject.toml)."""
        hooked = dataclasses.replace(
            STUB,
            identity=dataclasses.replace(
                STUB.identity, config_paths=("pyproject.toml", "nix/pyproject.toml")
            ),
            hooks=dataclasses.replace(
                STUB.hooks,
                config_file=lambda r: (
                    "nix/pyproject.toml"
                    if (r / "nix" / "pyproject.toml").is_file()
                    else "pyproject.toml"
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp).resolve()
            subprocess.run(["git", "-C", str(top), "init", "-q"], check=True)
            (top / "nix").mkdir()
            (top / "nix" / "pyproject.toml").write_text("[tool.stub-nix]\n")
            addon = top / "addon_a"
            (addon / "models").mkdir(parents=True)
            (addon / "pyproject.toml").write_text('[project]\nname = "addon-a"\n')
            for start in (top, top / "nix", addon, addon / "models"):
                with self.subTest(start=start):
                    self.assertEqual(pyproject.checkout_root(hooked, start), top)
            self.assertEqual(
                pyproject.config_path(hooked, top), top / "nix" / "pyproject.toml"
            )
            # Sync from nix/ is refused: its file is the checkout's; an addon's is not.
            self.assertEqual(pyproject.claimed_by(hooked, top / "nix"), top)
            self.assertIsNone(pyproject.claimed_by(hooked, addon))
            self.assertIsNone(pyproject.claimed_by(hooked, top))
            # An addon that opts in itself is its own checkout.
            (addon / "pyproject.toml").write_text("[tool.stub-nix]\n")
            self.assertEqual(pyproject.checkout_root(hooked, addon / "models"), addon)
            # No file up to the work tree's root opens the table: the nearest one.
            (addon / "pyproject.toml").write_text('[project]\nname = "addon-a"\n')
            (top / "nix" / "pyproject.toml").write_text("[tool.uv]\n")
            self.assertEqual(pyproject.checkout_root(hooked, addon / "models"), addon)
            # A one-file product keeps the nearest file, opted in or not (find_up).
            (top / "pyproject.toml").write_text("[tool.stub-nix]\n")
            self.assertEqual(pyproject.checkout_root(STUB, addon / "models"), addon)
            self.assertIsNone(pyproject.claimed_by(STUB, addon))

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
