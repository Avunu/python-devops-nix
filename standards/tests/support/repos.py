"""A fake GitHub (``common.github.use``) and throwaway unit clones for the ``repo``
command tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stub_nix_tools.product import PRODUCT as STUB

from standards_core.commands.repo import targets
from standards_core.common import github
from standards_core.testing.fakes import FakeGitHub, git
from support.checkouts import PYPROJECT

R = "example/demo_unit"
K = "example/kit-bundle"
REPO_POLICY_ON = "\n[tool.stub-nix.repo-policy]\nenable = true\n"
ONLY_LINT_TEST = (
    "\n[tool.stub-nix.commits]\nenable = false\n"
    "\n[tool.stub-nix.releases]\nenable = false\n"
    "\n[tool.stub-nix.extras]\nenable = false\n"
)


def write_unit(
    root: Path,
    extra: str = "",
    *,
    opted_in: bool = True,
    branch: str = "develop",
    profile: str = "recommended",
) -> Path:
    """A stub checkout in a git repository at ``root``, opted in with ``repo =
    example/demo_unit`` and ``extra`` table lines (or not opted in at all)."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", branch)
    text = PYPROJECT.replace("{profile}", profile)
    if opted_in:
        text = (
            text.replace("[tool.stub-nix]\n", f'[tool.stub-nix]\nrepo = "{R}"\n', 1)
            + extra
        )
    else:
        text = text[: text.index("[tool.stub-nix]")]
    (root / "pyproject.toml").write_text(text)
    (root / "demo_unit").mkdir(exist_ok=True)
    (root / "demo_unit/__init__.py").write_text('__version__ = "16.0.0"\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "unit")
    return root


def kit_bundle(root: Path, table: str = "") -> Path:
    """A kit ``bundle`` checkout (unit ``alpha`` on series 18.0) at ``root``."""
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "develop")
    (root / "pyproject.toml").write_text(
        f'[tool.kit-nix]\nschema = 1\nprofile = "recommended"\nrepo = "{K}"\n{table}'
    )
    (root / "alpha").mkdir()
    (root / "alpha/__unit__.toml").write_text(
        'name = "alpha"\nversion = "18.0.1.0.0"\n'
    )
    (root / "alpha/README.md").write_text("# alpha\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "bundle")
    return root


class RepoCase(unittest.TestCase):
    """``self.gh`` is the fake GitHub; ``self.clones`` maps ``(repo, branch)`` to a
    directory ``targets.clone`` returns (``branch`` None: the default branch).
    ``self.cloned`` records each call's ``(repo, branch)``."""

    product = STUB

    def _clone(self, product, repo, branch=None, depth=1):
        self.cloned.append((repo, branch))
        return self.clones.get((repo, branch))

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        prefix = self.product.identity.env_prefix
        env = mock.patch.dict(
            os.environ,
            {"GIT_CONFIG_GLOBAL": os.devnull, f"{prefix}_OFFLINE": "1"},
        )
        env.start()
        self.addCleanup(env.stop)
        for key in (f"{prefix}_ISSUE_TOKEN", "GH_TOKEN"):
            os.environ.pop(key, None)
        self.gh = FakeGitHub()
        previous = github.use(self.gh)
        self.addCleanup(github.use, previous)
        self.clones: dict[tuple[str, str | None], Path] = {}
        self.cloned: list[tuple[str, str | None]] = []
        patch = mock.patch.object(targets, "clone", side_effect=self._clone)
        patch.start()
        self.addCleanup(patch.stop)
