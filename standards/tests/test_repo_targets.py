"""``repo`` targets (DESIGN §7.1): the fleet list, the clones and each target's
configuration, and ``<cli> repo targets``."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from support import KIT, STUB, run_cli
from support.repos import K, R, RepoCase, kit_bundle, write_unit

from standards_core.commands.repo import targets
from standards_core.common.report import ConfigError, EnvError
from standards_core.testing.fakes import git


def fleet(path: Path, *entries: dict, **top) -> str:
    path.write_text(json.dumps({"schema": 1, **top, "repos": list(entries)}))
    return str(path)


class TestTargets(RepoCase):
    def test_an_opted_in_target(self):
        self.clones[(R, None)] = write_unit(self.tmp / "unit")
        t = targets.load(STUB, R, {}, {})
        self.assertTrue(t.opted_in)
        self.assertEqual(t.root, self.clones[(R, None)])
        self.assertEqual(t.integration, "develop")
        self.assertEqual(t.branches, ["develop"])
        self.assertEqual(t.release_branch, "version-16")
        self.assertEqual(t.gates, ["pr-policy", "lint", "test"])
        self.assertEqual(t.config.name, "demo_unit")
        self.assertIs(t.resolved, t.config.resolved)
        self.assertIsNone(t.profile_dir)
        self.assertEqual(self.cloned, [(R, None)])

    def test_the_integration_branch_clone_when_the_default_is_another(self):
        self.clones[(R, None)] = write_unit(self.tmp / "main", branch="main")
        self.clones[(R, "develop")] = write_unit(self.tmp / "develop")
        t = targets.load(STUB, R, {}, {})
        self.assertEqual(t.root, self.clones[(R, "develop")])
        self.assertEqual(self.cloned, [(R, None), (R, "develop")])
        # Not opted in there yet: the default branch's clone stays.
        self.cloned.clear()
        self.clones[(R, "develop")] = write_unit(self.tmp / "bare", opted_in=False)
        t = targets.load(STUB, R, {}, {})
        self.assertEqual(t.root, self.clones[(R, None)])

    def test_the_entry_names_the_integration_branch(self):
        self.clones[(R, None)] = write_unit(self.tmp / "unit")
        t = targets.load(STUB, R, {"repo": R, "branches": ["stable", "next"]}, {})
        self.assertEqual((t.integration, t.branches), ("stable", ["stable", "next"]))
        self.assertIn((R, "stable"), self.cloned)
        entry = {"repo": R, "integration_branch": "trunk", "branches": ["stable"]}
        t = targets.load(STUB, R, entry, {})
        self.assertEqual((t.integration, t.branches), ("trunk", ["stable"]))

    def test_a_not_opted_in_target(self):
        self.clones[(R, None)] = write_unit(self.tmp / "bare", opted_in=False)
        t = targets.load(STUB, R, {"repo": R, "integration_branch": "main"}, {})
        self.assertFalse(t.opted_in)
        self.assertIsNone(t.resolved)
        self.assertEqual((t.cfg, t.modules, t.facts), ({}, {}, {}))
        self.assertEqual(t.release_branch, "")
        # The profile from the fleet file, with the app layer hooks.fleet_table gives.
        t = targets.load(STUB, R, {"repo": R}, {"profile": "recommended"})
        self.assertFalse(t.opted_in)
        self.assertEqual(t.cfg["major"], 16)
        self.assertEqual(t.cfg["repo"], R)
        self.assertEqual(t.integration, "develop")
        self.assertEqual(t.gates, [])
        self.assertEqual(t.facts, {})
        # The entry's profile wins over the fleet's, --profile over both.
        t = targets.load(STUB, R, {"repo": R, "profile": "minimal"}, {"profile": "x"})
        self.assertEqual(t.cfg["profile"], "minimal")
        t = targets.load(STUB, R, {"repo": R}, {}, profile="minimal")
        self.assertEqual(t.cfg["profile"], "minimal")

    def test_an_uncloneable_target_is_exit_3(self):
        with self.assertRaises(EnvError):
            targets.load(STUB, R, {}, {})


class TestKitTargets(RepoCase):
    product = KIT

    def test_facts_from_the_clone(self):
        self.clones[(K, None)] = kit_bundle(self.tmp / "bundle")
        t = targets.load(KIT, K, {}, {})
        self.assertEqual(t.facts["shape"], "bundle")
        self.assertEqual(t.facts["series"], "18.0")
        self.assertEqual(t.facts["units"], ["alpha"])
        self.assertEqual(t.release_branch, "18.0")
        self.assertEqual(t.branches, ["develop"])

    def test_fleet_table_carries_the_shape(self):
        root = self.tmp / "bare"
        root.mkdir()
        git(root, "init", "-q", "-b", "develop")
        git(root, "commit", "-q", "--allow-empty", "-m", "empty")
        self.clones[(K, None)] = root
        t = targets.load(KIT, K, {"repo": K, "shape": "workspace"}, {}, "recommended")
        self.assertEqual(t.cfg["shape"], "workspace")


class TestFleet(RepoCase):
    def test_entries(self):
        path = fleet(
            self.tmp / "fleet.json",
            {"repo": R, "private": True},
            {"repo": "example/other"},
        )
        pairs, doc = targets.entries(STUB, [], True, path)
        self.assertEqual([r for r, _ in pairs], [R, "example/other"])
        self.assertEqual(doc["schema"], 1)
        pairs, _ = targets.entries(STUB, ["Example/Demo_Unit"], False, path)
        self.assertEqual(pairs, [("Example/Demo_Unit", {"repo": R, "private": True})])
        with self.assertRaises(ConfigError):
            targets.entries(STUB, [R], True, path)
        with self.assertRaises(ConfigError):
            targets.entries(STUB, [], False, None)
        with self.assertRaises(ConfigError):
            targets.entries(STUB, ["no-slash"], False, None)

    def test_fleet_validation(self):
        with self.assertRaises(ConfigError) as caught:
            targets.load_fleet(STUB, None)
        self.assertIn("--fleet", str(caught.exception))
        bad = fleet(self.tmp / "bad.json", {"repo": R, "unknown": 1})
        with self.assertRaises(ConfigError):
            targets.load_fleet(STUB, bad)
        with self.assertRaises(ConfigError):
            targets.load_fleet(STUB, str(self.tmp / "missing.json"))

    def test_the_product_names_the_list_key(self):
        path = self.tmp / "fleet.json"
        path.write_text(json.dumps({"schema": 1, "apps": [{"repo": R}]}))
        with self.assertRaises(ConfigError):
            targets.load_fleet(STUB, str(path))


class TestTargetsCommand(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.clones[(R, None)] = write_unit(self.tmp / "unit")
        self.clones[("example/bare", None)] = write_unit(
            self.tmp / "bare", opted_in=False
        )
        self.fleet = fleet(
            self.tmp / "fleet.json",
            {"repo": R, "branches": ["develop", "version-15"]},
            {"repo": "example/bare", "private": True, "integration_branch": "main"},
            profile="recommended",
        )

    def test_json_rows(self):
        code, out, err = run_cli(
            STUB, "repo", "targets", "--all", "--fleet", self.fleet, "--format", "json"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out),
            [
                {
                    "repo": R,
                    "branches": ["develop", "version-15"],
                    "integration": "develop",
                    "opted_in": True,
                    "profile": "recommended",
                    "private": False,
                },
                {
                    "repo": "example/bare",
                    "branches": ["main"],
                    "integration": "main",
                    "opted_in": False,
                    "profile": "recommended",
                    "private": True,
                },
            ],
        )

    def test_text_rows(self):
        code, out, err = run_cli(
            STUB, "repo", "targets", "--all", "--fleet", self.fleet
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.splitlines(),
            [
                (
                    f"{R}: integration develop, branches develop, version-15, opted in,"
                    " profile recommended"
                ),
                (
                    "example/bare: integration main, branches main, not opted in,"
                    " profile recommended, private"
                ),
            ],
        )

    def test_a_named_repository_without_a_profile(self):
        code, out, err = run_cli(
            STUB, "repo", "targets", "--repo", "example/bare", "--format", "json"
        )
        self.assertEqual(code, 0, err)
        row = json.loads(out)[0]
        self.assertEqual(
            (row["opted_in"], row["profile"], row["integration"]), (False, "", "")
        )
        self.assertEqual(row["branches"], [])

    def test_needs_a_target(self):
        code, _, err = run_cli(STUB, "repo", "targets")
        self.assertEqual(code, 2)
        self.assertIn("--all --fleet", err)


class TestClone(unittest.TestCase):
    """The real ``clone``: a temporary directory per call, the clone at
    ``<tmp>/<repo name>``."""

    def setUp(self) -> None:
        self.addCleanup(targets.cleanup)

    def run_git(self, returncode=0, stderr=""):
        calls = []

        def fake(*args, cwd=None):
            calls.append(args)
            return subprocess.CompletedProcess(args, returncode, "", stderr)

        return calls, mock.patch.object(targets, "_git", side_effect=fake)

    def test_clone_directory_is_named_after_the_repository(self):
        calls, patch = self.run_git()
        with patch:
            root = targets.clone(STUB, "example/demo_unit", "develop")
        self.assertEqual(root.name, "demo_unit")
        self.assertTrue(root.parent.name.startswith("stub-nix-repo-"))
        args = calls[0]
        self.assertEqual(
            args[-2:], ("https://github.com/example/demo_unit.git", str(root))
        )
        self.assertIn("--branch", args)
        self.assertEqual(args[args.index("--depth") + 1], "1")
        parent = root.parent
        targets.cleanup()
        self.assertFalse(parent.exists())

    def test_a_missing_branch_is_none_and_other_failures_exit_3(self):
        _, patch = self.run_git(128, "Remote branch x not found in upstream origin")
        with patch:
            self.assertIsNone(targets.clone(STUB, R, "x"))
        _, patch = self.run_git(128, "fatal: repository not found")
        with patch, self.assertRaises(EnvError):
            targets.clone(STUB, R)

    def test_fetch_profile_reads_the_ref(self):
        calls, patch = self.run_git()
        with patch:
            path = targets.fetch_profile(STUB, "github:example/profile/v1")
        self.assertTrue(path.parent.name.startswith("stub-nix-profile-"))
        self.assertIn("v1", calls[0])
        self.assertIn("https://github.com/example/profile.git", calls[0])
        with self.assertRaises(ConfigError):
            targets.fetch_profile(STUB, "recommended")


if __name__ == "__main__":
    unittest.main()
