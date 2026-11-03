import subprocess
import tempfile
import unittest
from pathlib import Path

from standards_core.common import repo
from standards_core.common.report import EnvError


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


if __name__ == "__main__":
    unittest.main()
