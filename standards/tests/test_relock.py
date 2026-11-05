"""relock-plan and relock-push's helpers (DESIGN §8): the rollout-input check, the
relock patch, and the workflow-path refusal."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import KIT, STUB, git, run_cli

from standards_core.ci import relock
from standards_core.common.report import ConfigError


def lock(**revs):
    nodes = {"root": {"inputs": {name: name for name in revs}}}
    for name, rev in revs.items():
        nodes[name] = {
            "locked": {"type": "github", "owner": "example", "repo": name, "rev": rev}
        }
    return json.dumps({"root": "root", "version": 7, "nodes": nodes}, indent=2) + "\n"


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "unit"
        self.root.mkdir()
        patch = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "HOME": self.tmp.name}
        )
        patch.start()
        self.addCleanup(patch.stop)
        git(self.root, "init", "-q", "-b", "develop")
        (self.root / "flake.lock").write_text(
            lock(**{"core-lib": "1" * 40, "stub-nix": "2" * 40})
        )
        (self.root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / ".github/workflows/ci.yml").write_text("name: ci\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD").strip()

    def cli(self, *argv, product=STUB):
        return run_cli(product, *argv, cwd=self.root)


class TestRelockInputs(Repo):
    def bump(self, **revs):
        (self.root / "flake.lock").write_text(lock(**revs))
        git(self.root, "commit", "-qam", "bump")

    def inputs(self, product=STUB):
        return self.cli("ci", "relock-inputs", "--base", self.base, product=product)

    def test_other_input_bump_is_ok(self):
        self.bump(**{"core-lib": "3" * 40, "stub-nix": "2" * 40})
        code, out, _ = self.inputs()
        self.assertEqual((code, out.splitlines()[0]), (0, "ok"))

    def test_engine_moved(self):
        self.bump(**{"core-lib": "1" * 40, "stub-nix": "4" * 40})
        _, out, _ = self.inputs()
        self.assertEqual(
            out.splitlines(),
            [
                "rollout-input-moved",
                "moved: stub-nix (these move through stub-nix repo rollout)",
            ],
        )

    def test_the_products_engine_is_the_rollout_input(self):
        """The kit's engine input is kit-nix: a stub-nix move is not one of its."""
        self.bump(**{"core-lib": "1" * 40, "stub-nix": "4" * 40})
        _, out, _ = self.inputs(KIT)
        self.assertEqual(out.splitlines()[0], "ok")

    def test_standards_profile_moved(self):
        self.assertEqual(
            relock.moved_inputs(
                STUB,
                lock(**{"stub-nix": "2" * 40, "standards-profile": "5" * 40}),
                lock(**{"stub-nix": "2" * 40, "standards-profile": "6" * 40}),
            ),
            ["standards-profile"],
        )
        out = Path(self.tmp.name) / "out"
        (self.root / "flake.lock").write_text(
            lock(**{"stub-nix": "2" * 40, "standards-profile": "6" * 40})
        )
        git(self.root, "commit", "-qam", "add profile")
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
            _, stdout, _ = self.inputs()
        self.assertEqual(stdout.splitlines()[0], "rollout-input-moved")
        self.assertIn("state=rollout-input-moved\n", out.read_text())
        self.assertIn("moved=standards-profile\n", out.read_text())

    def test_follows_and_missing_base_lock(self):
        follows = json.dumps(
            {"root": "root", "nodes": {"root": {"inputs": {"stub-nix": ["x", "y"]}}}}
        )
        self.assertEqual(relock.moved_inputs(STUB, "", follows), ["stub-nix"])
        self.assertEqual(relock.moved_inputs(STUB, follows, follows), [])

    def test_not_a_lock(self):
        with self.assertRaisesRegex(ConfigError, "flake.lock: not JSON"):
            relock.moved_inputs(STUB, "", "{")
        with self.assertRaisesRegex(ConfigError, "not a flake.lock"):
            relock.moved_inputs(STUB, "", "[]")

    def test_no_lock_is_exit_2(self):
        (self.root / "flake.lock").unlink()
        code, _, err = self.inputs()
        self.assertEqual(code, 2, err)
        self.assertIn("no flake.lock", err)


class TestRelockPatch(Repo):
    def test_patch_reproduces_with_apply_index(self):
        # sync stages a change and writes a new, untracked lock seed
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'x'\nversion = '1'\n"
        )
        git(self.root, "add", "pyproject.toml")
        seed = self.root / "nix/locks/x/yarn.lock"
        seed.parent.mkdir(parents=True)
        seed.write_bytes(b"# yarn lockfile v1\n\x00binary\n")
        (self.root / "flake.lock").write_text(
            lock(**{"core-lib": "9" * 40, "stub-nix": "2" * 40})
        )
        patch = Path(self.tmp.name) / "relock.patch"
        code, out, err = self.cli("ci", "relock-patch", "--out", str(patch))
        self.assertEqual((code, out.strip()), (0, "empty=false"), err)
        want = {
            p: (self.root / p).read_bytes()
            for p in ("pyproject.toml", "flake.lock", "nix/locks/x/yarn.lock")
        }

        clone = Path(self.tmp.name) / "clone"
        subprocess.run(["git", "clone", "-q", str(self.root), str(clone)], check=True)
        git(clone, "checkout", "-q", self.base)
        git(clone, "apply", "--index", str(patch))
        for p, data in want.items():
            self.assertEqual((clone / p).read_bytes(), data, p)
        self.assertEqual(
            git(clone, "diff", "--cached", "--name-only").split(), sorted(want)
        )

    def test_empty(self):
        patch = Path(self.tmp.name) / "relock.patch"
        code, out, _ = self.cli("ci", "relock-patch", "--out", str(patch))
        self.assertEqual((code, out.strip()), (0, "empty=true"))

    def test_workflow_patch_refused(self):
        (self.root / ".github/workflows/ci.yml").write_text("name: changed\n")
        patch = Path(self.tmp.name) / "relock.patch"
        code, out, _ = self.cli("ci", "relock-patch", "--out", str(patch))
        self.assertEqual(code, 1)
        self.assertIn("::error::relock would change a workflow", out)
        self.assertIn("run stub-nix repo rollout", out)


class TestRefuseWorkflows(Repo):
    def test_range_and_index(self):
        (self.root / "README.md").write_text("x\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "docs")
        self.assertEqual(self.cli("ci", "refuse-workflows", "--base", self.base)[0], 0)
        (self.root / ".github/workflows/new.yml").write_text("name: new\n")
        git(self.root, "add", "-A")
        code, out, _ = self.cli("ci", "refuse-workflows", "--cached")
        self.assertEqual(code, 1)
        self.assertIn(".github/workflows/new.yml", out)
        git(self.root, "commit", "-qm", "wf")
        self.assertEqual(self.cli("ci", "refuse-workflows", "--base", self.base)[0], 1)

    def test_neither_base_nor_cached_is_exit_2(self):
        self.assertEqual(self.cli("ci", "refuse-workflows")[0], 2)

    def test_subdirectory_checkout(self):
        self.assertEqual(
            relock.workflow_paths(
                ["tests/fixtures/unit/.github/workflows/ci.yml", "a.py"]
            ),
            ["tests/fixtures/unit/.github/workflows/ci.yml"],
        )


if __name__ == "__main__":
    unittest.main()
