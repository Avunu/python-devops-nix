import subprocess
import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB
from support.checkouts import nested_config_product

from standards_core.common import repo
from standards_core.common.report import ConfigError, EnvError
from standards_core.scaffold.context import default_load_checkout


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class TestRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        git(self.root, "init", "-q")
        (self.root / "pyproject.toml").write_text('[project]\nname = "demo_unit"\n')
        (self.root / "demo_unit").mkdir()
        (self.root / "demo_unit" / "main.py").write_text("")
        (self.root / "untracked.txt").write_text("")
        git(self.root, "add", "pyproject.toml", "demo_unit/main.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_toplevel(self):
        self.assertEqual(repo.toplevel(self.root / "demo_unit"), self.root)
        with tempfile.TemporaryDirectory() as other, self.assertRaises(EnvError):
            repo.toplevel(Path(other))

    def test_find_up(self):
        sub = self.root / "demo_unit"
        self.assertEqual(
            repo.find_up("pyproject.toml", sub), self.root / "pyproject.toml"
        )
        (sub / "pyproject.toml").write_text("")
        self.assertEqual(repo.find_up("pyproject.toml", sub), sub / "pyproject.toml")
        # Nothing up to the work tree's root: the root's, which reading reports missing.
        self.assertEqual(repo.find_up("flake.lock", sub), self.root / "flake.lock")
        with tempfile.TemporaryDirectory() as other:
            here = Path(other).resolve()
            self.assertEqual(repo.find_up("flake.lock", here), here / "flake.lock")

    def test_ls_files(self):
        self.assertEqual(
            repo.ls_files(self.root), ["demo_unit/main.py", "pyproject.toml"]
        )
        self.assertEqual(repo.ls_files(self.root, "*.py"), ["demo_unit/main.py"])

    def test_git_failure_is_env_error(self):
        with self.assertRaisesRegex(EnvError, "git rev-parse"):
            repo.git(self.root, "rev-parse", "--verify", "no-such-ref")

    def test_origin_repo(self):
        self.assertIsNone(repo.origin_repo(self.root))
        git(
            self.root,
            "remote",
            "add",
            "origin",
            "https://github.com/example/my_unit.git",
        )
        self.assertEqual(repo.origin_repo(self.root), ("github.com", "example/my_unit"))

    def test_parse_remote(self):
        for url, expected in (
            (
                "https://github.com/example/my_unit.git",
                ("github.com", "example/my_unit"),
            ),
            ("https://github.com/example/my_unit", ("github.com", "example/my_unit")),
            ("git@github.com:someone/my_unit.git", ("github.com", "someone/my_unit")),
            (
                "ssh://git@gitlab.example.org:2222/group/sub/my_unit.git",
                ("gitlab.example.org", "group/sub/my_unit"),
            ),
            (
                "https://token@GitLab.com/group/my_unit/",
                ("gitlab.com", "group/my_unit"),
            ),
            ("/srv/git/my_unit.git", None),
            ("https://github.com/my_unit", None),
            ("https://github.com/a/../b", None),
        ):
            with self.subTest(url=url):
                self.assertEqual(repo.parse_remote(url), expected)


class TestLoadCheckout(unittest.TestCase):
    """The core's default checkout loader (DESIGN §3.3), which products wrap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "checkout-dir"
        self.root.mkdir()
        git(self.root, "init", "-q")
        (self.root / "pyproject.toml").write_text('[project]\nname = "demo_unit"\n')
        git(self.root, "add", "pyproject.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def name(self, product=STUB):
        return product.hooks.load_checkout(self.root).name

    def test_fields(self):
        checkout = STUB.hooks.load_checkout(self.root / ".")
        self.assertEqual(checkout.root, self.root)
        self.assertEqual(checkout.config, {"project": {"name": "demo_unit"}})
        self.assertEqual(checkout.tracked, ["pyproject.toml"])
        self.assertEqual(dict(checkout.extra), {})
        self.assertEqual((checkout.name, checkout.hyphen), ("demo_unit", "demo-unit"))
        self.assertEqual(default_load_checkout(STUB, self.root), checkout)

    def test_name_order(self):
        # [project].name first.
        self.assertEqual(self.name(), "demo_unit")
        # Then the name part of the table's repo, then origin's, never the directory
        # while there is a remote.
        git(
            self.root,
            "remote",
            "add",
            "origin",
            "git@github.com:example/from_origin.git",
        )
        (self.root / "pyproject.toml").write_text(
            '[tool.stub-nix]\nschema = 1\nrepo = "example/from_table"\n'
        )
        self.assertEqual(self.name(), "from_table")
        (self.root / "pyproject.toml").write_text("[tool.stub-nix]\nschema = 1\n")
        self.assertEqual(self.name(), "from_origin")
        # Only a checkout with no origin remote is named after its directory.
        git(self.root, "remote", "remove", "origin")
        self.assertEqual(self.name(), "checkout-dir")

    def test_the_name_from_a_config_file_in_a_subdirectory(self):
        """``[project].name`` of whichever file ``config_path`` names (§3.3), even one
        below the root (§15.CR1): a product that wants otherwise replaces the name in
        its own ``load_checkout`` (the kit stub does)."""
        nested = nested_config_product(STUB)
        (self.root / "sub").mkdir()
        (self.root / "sub/pyproject.toml").write_text(
            '[project]\nname = "from_sub"\n\n[tool.stub-nix]\nschema = 1\n'
        )
        git(self.root, "add", "sub/pyproject.toml")
        self.assertEqual(self.name(nested), "from_sub")
        (self.root / "sub/pyproject.toml").write_text("[tool.stub-nix]\nschema = 1\n")
        self.assertEqual(self.name(nested), "checkout-dir")

    def test_an_optional_config_file(self):
        (self.root / "pyproject.toml").unlink()
        git(self.root, "rm", "-q", "--cached", "pyproject.toml")
        checkout = KIT.hooks.load_checkout(self.root)
        self.assertEqual((checkout.config, checkout.name), ({}, "checkout-dir"))
        with self.assertRaises(EnvError):
            STUB.hooks.load_checkout(self.root)

    def test_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as other, self.assertRaises(EnvError):
            STUB.hooks.load_checkout(Path(other))
        with self.assertRaises(EnvError):
            STUB.hooks.load_checkout(self.root / "missing")
        (self.root / "pyproject.toml").write_text("[project\n")
        with self.assertRaises(ConfigError):
            STUB.hooks.load_checkout(self.root)


if __name__ == "__main__":
    unittest.main()
