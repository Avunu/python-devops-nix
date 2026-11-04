"""``uv tool install`` of a product built on standards-core, from a git URL, a path and
an override (DESIGN §10.3, §12 stage 9).

A product declares ``standards-core @ git+https://…@<sha>#subdirectory=standards`` and
CI installs it with ``uv tool install`` from git (``standards-install``'s
``install-spec``); a developer installs a product checkout from its path; a product's
self-test installs its own tree with the core as an editable path source
(``standards-install``'s ``override``). Each install must give a working console
script, and ``standards_core.revision()`` must name the core's commit when the core
came from git (the version-skew row reads it) and nothing when it is a path.

It needs ``uv`` and the package index, so it runs only with
``STANDARDS_CORE_INSTALL_CHECK=1`` (the selftest workflow's ``install`` job), from a
source tree: the git repositories it installs from are local copies, so nothing is
fetched from GitHub.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import standards_core
from standards_core.testing.fakes import git

SOURCE = Path(standards_core.__file__).resolve().parents[1]
STUB = Path(__file__).resolve().parent / "stubs" / "stub"
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
ENABLED = os.environ.get("STANDARDS_CORE_INSTALL_CHECK") == "1"
IGNORE = shutil.ignore_patterns(
    "tests", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".venv", "*.lock"
)


@unittest.skipUnless(ENABLED, "set STANDARDS_CORE_INSTALL_CHECK=1 (needs uv and PyPI)")
class TestUvToolInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("uv") is None:
            raise unittest.SkipTest("uv is not on PATH")
        if not (SOURCE / "pyproject.toml").is_file():
            raise unittest.SkipTest(f"{SOURCE} is not a standards-core source tree")
        cls.tmp = tempfile.TemporaryDirectory(prefix="standards-install-")
        root = Path(cls.tmp.name)
        # The repository that ships the core, with the core at standards/.
        cls.core = root / "core"
        cls.core.mkdir()
        git(cls.core, "init", "-q", "-b", "main")
        shutil.copytree(SOURCE, cls.core / "standards", ignore=IGNORE)
        git(cls.core, "add", "-A")
        git(cls.core, "commit", "-q", "-m", "feat: standards-core")
        cls.core_rev = git(cls.core, "rev-parse", "HEAD").strip()
        # A product that depends on the core by URL, as products do.
        cls.product = root / "product"
        shutil.copytree(STUB, cls.product, ignore=IGNORE)
        url = f"git+{cls.core.as_uri()}@{cls.core_rev}#subdirectory=standards"
        pyproject = cls.product / "pyproject.toml"
        text = pyproject.read_text()
        text = text.replace(
            'dependencies = ["standards-core"]',
            f'dependencies = ["standards-core @ {url}"]',
        )
        text = re.sub(r"\[tool\.uv\.sources\]\n[^\[]*", "", text)
        pyproject.write_text(text)
        git(cls.product, "init", "-q", "-b", "main")
        git(cls.product, "add", "-A")
        git(cls.product, "commit", "-q", "-m", "feat: a product")
        cls.product_rev = git(cls.product, "rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def install(self, spec: str) -> tuple[Path, Path]:
        """``uv tool install spec`` into a tool directory of its own: (bin, python)."""
        root = Path(tempfile.mkdtemp(dir=self.tmp.name, prefix="tools-"))
        env = {
            **os.environ,
            "UV_TOOL_DIR": str(root / "tools"),
            "UV_TOOL_BIN_DIR": str(root / "bin"),
            "UV_NO_CONFIG": "1",
        }
        env.pop("VIRTUAL_ENV", None)
        proc = subprocess.run(
            ["uv", "tool", "install", "--python", PYTHON, spec],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return root / "bin", root / "tools" / "stub-nix-tools" / "bin" / "python"

    def run_tool(self, binary: Path, *argv: str) -> str:
        proc = subprocess.run(
            [str(binary / "stub-nix"), *argv],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.tmp.name,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def revision(self, python: Path) -> str:
        proc = subprocess.run(
            [
                str(python),
                "-c",
                "import standards_core; print(standards_core.revision())",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def works(self, binary: Path) -> None:
        self.assertEqual(self.run_tool(binary, "--version"), "1.0.0")
        schema = Path(self.run_tool(binary, "data-path", "schema/core.schema.json"))
        self.assertTrue(schema.is_file(), schema)
        self.assertIn("minimal", self.run_tool(binary, "profile", "list"))

    def test_from_a_git_url(self):
        binary, python = self.install(
            f"stub-nix-tools @ git+{self.product.as_uri()}@{self.product_rev}"
        )
        self.works(binary)
        self.assertEqual(self.revision(python), self.core_rev)

    def test_from_a_path(self):
        binary, python = self.install(str(self.product))
        self.works(binary)
        self.assertEqual(self.revision(python), self.core_rev)

    def test_from_an_override(self):
        # The stub as it is in the source tree: the core is an editable path source.
        binary, python = self.install(str(STUB))
        self.works(binary)
        self.assertEqual(self.revision(python), "None")


if __name__ == "__main__":
    unittest.main()
