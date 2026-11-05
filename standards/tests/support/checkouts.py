"""A throwaway stub checkout in a git repository, and ``stub-nix`` run against it offline."""

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stub_nix_tools.product import PRODUCT as STUB

from standards_core.scaffold import manifest
from standards_core.scaffold.context import default_load_checkout
from standards_core.testing.fakes import flake_lock, git, run_cli, uv_lock

PYPROJECT = """[project]
name = "demo_unit"
authors = [{ name = "Example Org", email = "units@example.org" }]
description = "A demo unit"
requires-python = ">=3.11"
dynamic = ["version"]
dependencies = []

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.coverage.report]
fail_under = 50

[tool.stub-nix]
schema = 1
profile = "{profile}"
major = 16
"""

# A fictitious org profile: every module on, Example Org's values, a [[retire]] rule and
# an [[extra-files]] entry.
EXAMPLE_ORG = """schema = 1
name = "example-org"
description = "Example Org's standards (a test fixture)"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
email = "units@example.org"
license = "MIT"
docs-url = "https://{name_hyphen}.docs.example.org/"

[[retire]]
paths = [".github/workflows/check.yml"]
module = "ci"

[[extra-files]]
path = "SECURITY.md"
template = "SECURITY.md.j2"
strategy = "whole"
module = "hygiene"
header = "none"
"""


def write_profile(
    root: Path, rel: str = ".standards-profile", text: str = EXAMPLE_ORG
) -> Path:
    """An org profile directory under ``root``: ``profile.toml`` and its
    ``templates/``."""
    directory = root / rel
    (directory / "templates").mkdir(parents=True, exist_ok=True)
    (directory / "profile.toml").write_text(text)
    (directory / "templates" / "SECURITY.md.j2").write_text(
        "# Security\n\nReport vulnerabilities in {{ name }} to {{ org.email }}.\n"
    )
    return directory


def floors() -> dict[str, str]:
    return dict(manifest.load(STUB).floors["uv"])


class CheckoutCase(unittest.TestCase):
    """Each test gets ``self.root``: a stub checkout with one commit, opted in with
    ``profile``, and a ``flake.lock`` (phase A's, which an offline sync can't make).
    ``product`` is the product under test (the stub, or a copy of it)."""

    product = STUB
    profile = "recommended"
    extra_pyproject = ""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "demo"
        self.root.mkdir()
        prefix = self.product.identity.env_prefix
        env = mock.patch.dict(
            os.environ,
            {f"{prefix}_OFFLINE": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        for key in (
            f"{prefix}_ALLOW_SKEW",
            f"{prefix}_URL_OVERRIDE",
            f"{prefix}_DEBUG",
            "GITHUB_HEAD_REF",
            "CI",
        ):
            os.environ.pop(key, None)
        git(self.root, "init", "-q", "-b", "develop")
        self.write(
            "pyproject.toml",
            PYPROJECT.replace("{profile}", self.profile) + self.extra_pyproject,
        )
        self.write("demo_unit/__init__.py", '__version__ = "16.0.0"\n')
        self.commit()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def commit(self, message: str = "test") -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--allow-empty", "-m", message)

    def table(self, extra: str) -> None:
        """Add lines to ``[tool.stub-nix]`` (right after its header)."""
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                "[tool.stub-nix]\n", f"[tool.stub-nix]\n{extra}", 1
            ),
        )

    def fn(self, *argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return run_cli(self.product, *argv, cwd=cwd or self.root)

    def fake_locks(self, versions: dict[str, str] | None = None) -> None:
        """A ``tools/uv.lock`` at the floors (or ``versions``) and a ``flake.lock`` for
        the rendered flake, as uv and nix would leave them."""
        packages = floors()
        packages.update(versions or {})
        if (self.root / "tools/pyproject.toml").exists():
            self.write("tools/uv.lock", uv_lock(packages))
        if (self.root / "flake.nix").exists():
            self.write(
                "flake.lock", json.dumps(flake_lock(self.read("flake.nix")), indent=2)
            )
        git(self.root, "add", "-A")

    def synced(self) -> None:
        """Sync, add the locks an offline sync can't make, and commit: a clean start."""
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.fake_locks()
        self.commit("synced")
        code, out, err = self.fn("sync", "--check")
        self.assertEqual(code, 0, out + err)

    def check(self) -> tuple[int, str]:
        code, out, err = self.fn("sync", "--check")
        return code, out + err

    def snapshot(self) -> dict[str, bytes]:
        files = git(self.root, "ls-files", "-co", "--exclude-standard").split()
        return {
            f: (self.root / f).read_bytes() for f in files if (self.root / f).is_file()
        }


def nested_config_product(
    product, rel: str = "sub/pyproject.toml", *, hooks: dict | None = None, **identity
):
    """``product`` with its config file at ``rel`` below the checkout's root
    (``identity.config_file`` holds a directory part, §15.CR1); ``identity`` and
    ``hooks`` replace further fields."""
    nested = dataclasses.replace(
        product,
        identity=dataclasses.replace(product.identity, config_file=rel, **identity),
        hooks=dataclasses.replace(
            product.hooks,
            load_checkout=lambda root: default_load_checkout(nested, root),
            **(hooks or {}),
        ),
    )
    return nested


class NestedConfigCase(CheckoutCase):
    """A ``CheckoutCase`` whose config file is ``sub/pyproject.toml``: the checkout's
    root is the work tree's root, not the file's directory."""

    product = nested_config_product(STUB)

    def setUp(self) -> None:
        super().setUp()
        (self.root / "sub").mkdir()
        (self.root / "pyproject.toml").rename(self.root / "sub/pyproject.toml")
        self.commit("nested config")
