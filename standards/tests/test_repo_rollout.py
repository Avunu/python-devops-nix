"""``<cli> repo rollout``: arguments, the dry-run plan, the product's rollout steps and a
full run with the commands faked (DESIGN §7.2)."""

import dataclasses
import json
from pathlib import Path
from unittest import mock

from support import STUB, run_cli
from support.repos import R, RepoCase, write_unit

from standards_core.commands.repo import rollout, targets
from standards_core.common.github import Result
from standards_core.testing.fakes import flake_lock, git

REV = "a" * 40
FLAKE = '{\n  inputs = {\n    stub-nix.url = "github:example/stub-nix";\n  };\n}\n'


class RolloutCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = write_unit(self.tmp / "unit")
        self.clones[(R, None)] = self.root
        p = mock.patch.object(targets, "clone_branch", return_value="develop")
        p.start()
        self.addCleanup(p.stop)


class TestDryRun(RolloutCase):
    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(
            rollout, "sh", side_effect=AssertionError("dry-run ran a command")
        )
        p.start()
        self.addCleanup(p.stop)

    def test_needs_a_target_release(self):
        code, _, err = run_cli(STUB, "repo", "rollout", "--repo", R)
        self.assertEqual(code, 2)
        self.assertIn("--to", err)

    def test_to_must_be_a_release_tag(self):
        code, _, _ = run_cli(STUB, "repo", "rollout", "--repo", R, "--to", "main")
        self.assertEqual(code, 2)

    def test_all_needs_fleet(self):
        code, _, _ = run_cli(STUB, "repo", "rollout", "--all", "--to", "v1.2.0")
        self.assertEqual(code, 2)

    def test_dry_run_prints_the_plan(self):
        code, out, err = run_cli(
            STUB, "repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.splitlines(),
            [
                f"{R} (develop):",
                "  nix flake update stub-nix",
                "  nix run --no-pure-eval .#stub-init -- --sync",
                "  commit 'chore(deps): stub-nix v1.2.0' on standards/stub-nix-v1.2.0",
                "  gh pr create --base develop",
                "  gh pr merge --auto --squash",
            ],
        )
        self.assertEqual(self.gh.calls, [])
        code, out, _ = run_cli(
            STUB,
            "repo",
            "rollout",
            "--repo",
            R,
            "--to",
            "v1.2.0",
            "--dry-run",
            "--no-auto-merge",
        )
        self.assertNotIn("gh pr merge", out)

    def test_the_product_s_rollout_steps_follow_the_update(self):
        product = dataclasses.replace(
            STUB,
            sync=dataclasses.replace(
                STUB.sync, rollout_steps=(("nix", "run", "--no-pure-eval", ".#relock"),)
            ),
        )
        code, out, err = run_cli(
            product, "repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run"
        )
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(
            lines[1:4],
            [
                "  nix flake update stub-nix",
                "  nix run --no-pure-eval .#relock",
                "  nix run --no-pure-eval .#stub-init -- --sync",
            ],
        )

    def test_profile_to_skips_a_builtin_profile(self):
        code, out, err = run_cli(
            STUB, "repo", "rollout", "--repo", R, "--profile-to", "latest", "--dry-run"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.strip(), f"{R}: skipped, its profile is the built-in 'recommended'"
        )

    def test_a_repository_without_the_table_is_skipped(self):
        self.clones[(R, None)] = write_unit(self.tmp / "bare", opted_in=False)
        code, out, err = run_cli(
            STUB, "repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), f"{R}: skipped, no [tool.stub-nix]")


class TestSetProfileRef(RolloutCase):
    def test_only_the_table_s_profile_line_changes(self):
        path = self.tmp / "pyproject.toml"
        text = (
            '[tool.other]\nprofile = "keep"\n\n'
            '[tool.stub-nix]\nschema = 1\nprofile = "github:example/p/v1"\n'
        )
        path.write_text(text)
        self.assertTrue(rollout.set_profile_ref(STUB, path, "github:example/p/v2"))
        self.assertEqual(path.read_text(), text.replace("p/v1", "p/v2"))
        self.assertFalse(rollout.set_profile_ref(STUB, path, "latest"))
        self.assertFalse(rollout.set_profile_ref(STUB, path, "github:example/p/v2"))


class TestRun(RolloutCase):
    """A run with every command faked: ``nix flake update`` writes the lock it would."""

    def setUp(self) -> None:
        super().setUp()
        self.commands: list[list[str]] = []
        p = mock.patch.object(rollout, "sh", side_effect=self.sh)
        p.start()
        self.addCleanup(p.stop)
        self.gh.cli_results[("pr", "create")] = Result(
            0, "https://github.com/example/demo_unit/pull/4\n"
        )
        self.gh.routes[("GET", "repos/example/stub-nix/commits/v1.2.0")] = {"sha": REV}

    def sh(self, cmd: list[str], cwd: Path) -> str:
        self.commands.append(cmd)
        if cmd[:3] == ["nix", "flake", "update"]:
            (cwd / "flake.lock").write_text(json.dumps(flake_lock(FLAKE, rev=REV)))
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return git(cwd, "status", "--porcelain")
        return ""

    def test_a_rollout_opens_a_pull_request(self):
        code, out, err = run_cli(STUB, "repo", "rollout", "--repo", R, "--to", "v1.2.0")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.strip(), f"{R}: https://github.com/example/demo_unit/pull/4"
        )
        self.assertIn(["nix", "flake", "update", "stub-nix"], self.commands)
        self.assertIn(
            ["git", "commit", "-q", "-m", "chore(deps): stub-nix v1.2.0"], self.commands
        )
        self.assertIn(
            [
                "git",
                "push",
                "-q",
                "--force",
                "origin",
                "HEAD:refs/heads/standards/stub-nix-v1.2.0",
            ],
            self.commands,
        )
        create = self.gh.cli_calls[0]
        self.assertEqual(create[:2], ("pr", "create"))
        self.assertIn(
            "Rolled out with `stub-nix repo rollout`.\n\nstub-nix v1.2.0", create
        )
        self.assertEqual(
            self.gh.cli_calls[1],
            (
                "pr",
                "merge",
                "-R",
                R,
                "--auto",
                "--squash",
                "https://github.com/example/demo_unit/pull/4",
            ),
        )

    def test_a_lock_short_of_the_release_fails(self):
        self.gh.routes[("GET", "repos/example/stub-nix/commits/v1.2.0")] = {
            "sha": "b" * 40
        }
        code, _, err = run_cli(STUB, "repo", "rollout", "--repo", R, "--to", "v1.2.0")
        self.assertEqual(code, 1)
        self.assertIn("its release branch has not reached that release", err)
        self.assertNotIn("push", [c[1] for c in self.commands if c[0] == "git"])

    def test_nothing_changed_is_already_current(self):
        (self.root / "flake.lock").write_text(json.dumps(flake_lock(FLAKE, rev=REV)))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "lock")
        code, out, err = run_cli(STUB, "repo", "rollout", "--repo", R, "--to", "v1.2.0")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), f"{R}: already current")
        self.assertEqual(self.gh.cli_calls, [])
