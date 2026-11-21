"""relock-plan and relock-push's helpers (DESIGN §8): the rollout-input check, the
relock patch, and the workflow-path refusal."""

import dataclasses
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


def with_ci(product, **ci):
    return dataclasses.replace(product, ci=dataclasses.replace(product.ci, **ci))


SITE = with_ci(STUB, relock_patch_paths=("nix/vendor-hash", "nix/locks/**"))


class TestApplyRelockPatch(Repo):
    """relock-push's check of relock-plan's patch (``ci.relock_patch_paths``)."""

    def setUp(self):
        super().setUp()
        (self.root / "nix").mkdir()
        (self.root / "nix/vendor-hash").write_text("sha256-" + "A" * 43 + "=\n")
        (self.root / "nix/tool.sh").write_text("#!/bin/sh\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "vendor hash")
        self.head = git(self.root, "rev-parse", "HEAD").strip()
        self.patch = Path(self.tmp.name) / "relock.patch"

    def make_patch(self, change):
        """``change`` the work tree, write it as relock-plan's patch, and reset."""
        change(self.root)
        git(self.root, "add", "-A")
        self.patch.write_text(git(self.root, "diff", "--cached", "--binary"))
        git(self.root, "reset", "-q", "--hard", self.head)
        git(self.root, "clean", "-qfdx")

    def apply(self, product=SITE):
        return self.cli(
            "ci", "apply-relock-patch", "--patch", str(self.patch), product=product
        )

    def staged(self):
        return git(self.root, "diff", "--cached", "--name-only").split()

    def test_an_allowed_patch_is_applied_to_the_index(self):
        def change(root):
            (root / "nix/vendor-hash").write_text("sha256-" + "B" * 43 + "=\n")
            (root / "nix/locks/x").mkdir(parents=True)
            (root / "nix/locks/x/lock.json").write_text("{}\n")

        self.make_patch(change)
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("2 files changed", out)
        self.assertEqual(self.staged(), ["nix/locks/x/lock.json", "nix/vendor-hash"])

    def test_a_path_not_named_is_refused(self):
        self.make_patch(lambda root: (root / "README.md").write_text("x\n"))
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn(
            "::error::refusing the relock patch: 'README.md': not a path this job"
            " pushes",
            out,
        )

    def test_a_deletion_of_a_named_path_is_allowed(self):
        self.make_patch(lambda root: (root / "nix/vendor-hash").unlink())
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.staged(), ["nix/vendor-hash"])

    def test_workflows_are_refused_whatever_the_globs_say(self):
        product = with_ci(STUB, relock_patch_paths=("**",))
        self.make_patch(
            lambda root: (root / ".github/workflows/ci.yml").write_text("name: x\n")
        )
        code, out, _ = self.apply(product)
        self.assertEqual(code, 1)
        self.assertIn("'.github/workflows/ci.yml': path", out)

    def test_a_mode_change_is_refused_before_anything_is_applied(self):
        self.make_patch(lambda root: (root / "nix/vendor-hash").chmod(0o755))
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("changes a file's mode", out)
        self.assertEqual(self.staged(), [])

    def test_a_rename_is_refused(self):
        def change(root):
            git(root, "mv", "nix/tool.sh", "nix/vendor-hash.sh")

        change(self.root)
        self.patch.write_text(git(self.root, "diff", "--cached", "-M", "--binary"))
        git(self.root, "reset", "-q", "--hard", self.head)
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("renames or copies a file", out)
        self.assertEqual(self.staged(), [])

    def test_a_symlink_is_refused(self):
        def change(root):
            (root / "nix/locks").mkdir()
            (root / "nix/locks/link").symlink_to("../../pyproject.toml")

        self.make_patch(change)
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("a file mode other than 100644", out)
        self.assertEqual(self.staged(), [])

    def test_an_empty_patch_is_refused(self):
        self.patch.write_text("")
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("the patch changes nothing", out)

    def test_a_patch_that_does_not_apply_is_refused(self):
        self.make_patch(lambda root: (root / "nix/vendor-hash").write_text("x\n"))
        (self.root / "nix/vendor-hash").write_text("moved on\n")
        git(self.root, "commit", "-qam", "moved")
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("the patch does not apply", out)

    def test_a_product_naming_no_paths_pushes_nothing(self):
        self.make_patch(lambda root: (root / "nix/vendor-hash").write_text("x\n"))
        code, _, err = self.apply(STUB)
        self.assertEqual(code, 2)
        self.assertIn("names no ci.relock_patch_paths", err)

    def test_the_globs_are_checked_when_the_product_is_built(self):
        for glob in ("", "/nix/x", "../x", "nix/../x", ".github/workflows/*", "a\\b"):
            with (
                self.subTest(glob=glob),
                self.assertRaisesRegex(ValueError, "ci.relock_patch_paths"),
            ):
                with_ci(STUB, relock_patch_paths=(glob,))


class TestVerifyRelock(Repo):
    """``ci verify-relock`` asks the product's ``ci.verify_relock``."""

    def product(self, rejects=(), reviews=()):
        calls = []

        def verify(root, base, head):
            calls.append((root, base, head))
            return relock.Verification(list(rejects), list(reviews))

        return with_ci(STUB, verify_relock=verify), calls

    def verify(self, product, head=None, *extra):
        tree = head or git(self.root, "write-tree").strip()
        return self.cli(
            "ci",
            "verify-relock",
            "--base",
            self.base,
            "--head",
            tree,
            *extra,
            product=product,
        )

    def test_ok(self):
        product, calls = self.product()
        out_file = Path(self.tmp.name) / "out"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out_file)}):
            code, out, err = self.verify(product)
        self.assertEqual((code, out), (0, "ok\n"), err)
        self.assertEqual(out_file.read_text(), "decision=ok\n")
        root, base, head = calls[0]
        self.assertEqual((root, base), (Path("."), self.base))
        self.assertEqual(head, git(self.root, "write-tree").strip())

    def test_needs_review(self):
        product, _ = self.product(reviews=["a new package: example/new"])
        code, out, _ = self.verify(product)
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            ["needs-review", "  needs-review: a new package: example/new"],
        )

    def test_reject_fails_the_step(self):
        product, _ = self.product(
            rejects=["evil.example is not a source"], reviews=["x"]
        )
        out_file = Path(self.tmp.name) / "out"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out_file)}):
            code, out, _ = self.verify(product)
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "reject")
        self.assertIn("::error::refusing the relock: evil.example is not a source", out)
        self.assertFalse(out_file.exists())

    def test_object_ids_and_a_missing_hook_are_exit_2(self):
        product, calls = self.product()
        code, _, err = self.verify(product, "HEAD")
        self.assertEqual(code, 2)
        self.assertIn("--head 'HEAD' is not a full object id", err)
        self.assertEqual(calls, [])
        code, _, err = self.verify(STUB)
        self.assertEqual(code, 2)
        self.assertIn("stub-nix has no ci.verify_relock", err)

    def test_decision(self):
        self.assertEqual(relock.Verification().decision(), relock.OK)
        self.assertEqual(relock.Verification(reviews=["x"]).decision(), relock.REVIEW)
        self.assertEqual(
            relock.Verification(["a"], ["x", "x"]).reasons(),
            ["reject: a", "needs-review: x"],
        )


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
