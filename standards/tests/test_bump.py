"""``<cli> ci bump``: the post-merge bump of the develop+series flow (DESIGN §8.2), on
kit checkouts with a bare ``origin``."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import KIT, git, run_cli

from standards_core.ci import bump
from standards_core.common.report import ConfigError

TOOL = KIT.release_tool
SCHEME = KIT.version_schemes["kit-series"]
PYPROJECT = '[tool.kit-nix]\nschema = 1\nprofile = "recommended"\n'


def unit_toml(version):
    return f'name = "x"\nversion = "{version}"\n'


class BumpCase(unittest.TestCase):
    """``self.root``: a kit bundle on ``develop`` with units alpha and beta, whose
    ``origin`` (a bare repository) has ``develop`` and the release branch ``18.0`` at
    the first commit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        env = mock.patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "KIT_NIX_OFFLINE": "1",
                "GIT_AUTHOR_NAME": "bump",
                "GIT_AUTHOR_EMAIL": "bump@example.org",
                "GIT_COMMITTER_NAME": "bump",
                "GIT_COMMITTER_EMAIL": "bump@example.org",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        for key in ("GITHUB_OUTPUT", "KIT_NIX_ALLOW_SKEW"):
            os.environ.pop(key, None)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        self.root = self.tmp / "bundle"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "develop")
        git(self.root, "remote", "add", "origin", str(self.origin))
        self.write("pyproject.toml", PYPROJECT)
        self.write(".kit-series", "18.0\n")
        self.write("alpha/__unit__.toml", unit_toml("18.0.1.2.3"))
        self.write("alpha/__init__.py", "")
        self.write("alpha/README.md", "# alpha\n")
        self.write("beta/__unit__.toml", unit_toml("18.0.2.0.0"))
        self.write("beta/__init__.py", "")
        self.commit("chore: start")
        git(self.root, "branch", "18.0")
        git(self.root, "push", "-q", "origin", "develop", "18.0")
        git(self.root, "fetch", "-q", "origin")
        self.base = "origin/18.0"

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message, *files):
        for rel in files:
            self.write(rel, f"# {message}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.root, "rev-parse", "HEAD").strip()

    def plan(self, ctx=None):
        return bump.plan_bumps(
            self.root, self.base, TOOL, SCHEME, ctx or {"discover": {"series": "18.0"}}
        )

    def versions(self, bumps):
        return {b.unit.name: (b.old, b.new, b.level) for b in bumps}

    def cli(self, *argv):
        return run_cli(KIT, "ci", "bump", "--base", self.base, *argv, cwd=self.root)

    def version_at(self, rev, unit):
        return TOOL.read_version(
            git(self.root, "show", f"{rev}:{unit}/__unit__.toml"), None
        )


class TestPlan(BumpCase):
    def test_levels_per_unit(self):
        feat = self.commit("feat(alpha): x", "alpha/x.py")
        self.commit("fix: y", "beta/y.py")
        bumps = self.plan()
        self.assertEqual(
            self.versions(bumps),
            {
                "alpha": ("18.0.1.2.3", "18.0.1.3.0", "minor"),
                "beta": ("18.0.2.0.0", "18.0.2.0.1", "patch"),
            },
        )
        self.assertEqual(bumps[0].commits, (feat[:12],))

    def test_the_highest_level_and_the_commits_that_set_it(self):
        self.commit("fix: a", "alpha/a.py")
        breaking = self.commit("refactor(alpha)!: b", "alpha/b.py")
        self.commit("feat: c", "alpha/c.py")
        (only,) = self.plan()
        self.assertEqual((only.new, only.level), ("18.0.2.0.0", "major"))
        self.assertEqual(only.commits, (breaking[:12],))

    def test_no_level_no_bump(self):
        self.commit("chore: tidy", "alpha/x.py")
        self.commit("docs: words", "beta/x.py")
        self.commit("not conventional", "alpha/y.py")
        self.commit("feat: outside every unit", "tools/x.py")
        self.assertEqual(self.plan(), [])

    def test_exempt_only_commits_do_not_count(self):
        self.commit("fix: tests", "alpha/tests/test_x.py")
        self.commit("fix: docs", "alpha/README.md")
        self.commit("feat: i18n", "alpha/i18n/de.po")
        self.assertEqual(self.plan(), [])
        self.commit("fix: mixed", "alpha/tests/test_y.py", "alpha/y.py")
        self.assertEqual(
            self.versions(self.plan()), {"alpha": ("18.0.1.2.3", "18.0.1.2.4", "patch")}
        )

    def test_merge_commits_do_not_count(self):
        git(self.root, "checkout", "-q", "-b", "side")
        self.commit("chore: side", "alpha/side.py")
        git(self.root, "checkout", "-q", "develop")
        git(self.root, "merge", "-q", "--no-ff", "-m", "feat: merge side", "side")
        self.assertEqual(self.plan(), [])

    def test_since_after_an_unforwarded_bump(self):
        self.commit("feat(alpha): x", "alpha/x.py")
        first = bump.apply_bumps(self.root, self.plan(), TOOL)
        self.assertEqual(
            git(self.root, "log", "-1", "--format=%s").strip(),
            "chore(release): bump alpha",
        )
        unit = TOOL.units(self.root, {})[0]
        self.assertEqual(bump.since(self.root, self.base, unit, TOOL), first)
        self.commit("fix(alpha): y", "alpha/y.py")
        self.assertEqual(
            self.versions(self.plan()), {"alpha": ("18.0.1.3.0", "18.0.1.3.1", "patch")}
        )

    def test_a_bump_of_another_unit_is_not_since(self):
        self.commit("fix: b", "beta/b.py")
        bump.apply_bumps(self.root, self.plan(), TOOL)
        alpha = TOOL.units(self.root, {})[0]
        self.assertEqual(bump.since(self.root, self.base, alpha, TOOL), self.base)

    def test_a_feature_commit_that_edits_the_version_is_counted_not_since(self):
        self.write("alpha/__unit__.toml", unit_toml("18.0.1.9.0"))
        sha = self.commit("feat(alpha): raise it myself", "alpha/x.py")
        alpha = TOOL.units(self.root, {})[0]
        self.assertEqual(bump.since(self.root, self.base, alpha, TOOL), self.base)
        self.assertEqual(
            [s for s, _ in bump.counted(self.root, self.base, alpha, TOOL)], [sha]
        )
        # ... and since its version differs from base's, the unit is a hand edit.
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.plan(), [])
        self.assertIn(
            "alpha: version edited by hand since origin/18.0 (18.0.1.2.3 →"
            " 18.0.1.9.0); not bumped",
            " ".join(str(a) for c in p.call_args_list for a in c.args),
        )

    def test_a_hand_edit_since_the_last_bump_skips_the_unit(self):
        self.commit("feat(alpha): x", "alpha/x.py")
        first = bump.apply_bumps(self.root, self.plan(), TOOL)
        self.write("alpha/__unit__.toml", unit_toml("18.0.5.0.0"))
        self.commit("chore: set the version")
        self.commit("fix(alpha): y", "alpha/y.py")
        self.commit("fix(beta): z", "beta/z.py")
        with mock.patch("builtins.print") as p:
            bumps = self.plan()
        self.assertEqual(
            self.versions(bumps), {"beta": ("18.0.2.0.0", "18.0.2.0.1", "patch")}
        )
        self.assertIn(
            f"::notice::alpha: version edited by hand since {first[:12]} (18.0.1.3.0 →"
            " 18.0.5.0.0); not bumped",
            [str(a) for c in p.call_args_list for a in c.args],
        )

    def test_a_new_unit_is_not_bumped(self):
        self.write("gamma/__unit__.toml", unit_toml("18.0.1.0.0"))
        self.commit("feat(gamma): new", "gamma/x.py")
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.plan(), [])
        self.assertIn(
            "gamma: no version at origin/18.0; not bumped",
            " ".join(str(a) for c in p.call_args_list for a in c.args),
        )

    def test_the_new_version_must_be_valid(self):
        self.commit("fix(alpha): x", "alpha/x.py")
        with self.assertRaisesRegex(
            ConfigError, "alpha: 18.0.1.2.3 raised to 18.0.1.2.4: .* not on series 19.0"
        ):
            self.plan({"discover": {"series": "19.0"}})

    def test_loop_safety(self):
        """The bump commit is a chore: the run its push triggers bumps nothing."""
        self.commit("feat(alpha): x", "alpha/x.py")
        bump.apply_bumps(self.root, self.plan(), TOOL)
        self.assertEqual(self.plan(), [])

    def test_subject_recognition(self):
        self.assertTrue(bump.is_bump(TOOL, "chore(release): bump alpha, beta", "beta"))
        self.assertTrue(bump.is_bump(TOOL, "chore(release): bump alpha"))
        self.assertFalse(bump.is_bump(TOOL, "chore(release): bump alpha", "beta"))
        self.assertFalse(bump.is_bump(TOOL, "chore(release): bump alphabet", "alpha"))
        self.assertFalse(bump.is_bump(TOOL, "fix: chore(release): bump alpha"))


class TestCli(BumpCase):
    def output(self):
        out = self.tmp / "out"
        patch = mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)})
        patch.start()
        self.addCleanup(patch.stop)
        return out

    def test_bump_and_push(self):
        out = self.output()
        self.commit("feat(alpha): x", "alpha/x.py")
        self.commit("fix(beta): y", "beta/y.py")
        code, stdout, err = self.cli("--push")
        self.assertEqual(code, 0, stdout + err)
        head = git(self.root, "rev-parse", "HEAD").strip()
        self.assertEqual(
            git(self.root, "log", "-1", "--format=%s").strip(),
            "chore(release): bump alpha, beta",
        )
        self.assertEqual(self.version_at(head, "alpha"), "18.0.1.3.0")
        self.assertEqual(self.version_at(head, "beta"), "18.0.2.0.1")
        self.assertEqual(git(self.origin, "rev-parse", "develop").strip(), head)
        self.assertEqual(out.read_text(), f"bumped=alpha,beta\nsha={head}\n")
        self.assertIn("alpha: 18.0.1.2.3 → 18.0.1.3.0 (minor:", stdout)

    def test_the_series_comes_from_the_checkout(self):
        """``18.0.1.2.3`` + minor is ``18.0.1.3.0``, on the series discovery reads."""
        self.commit("feat(alpha): x", "alpha/x.py")
        code, stdout, err = self.cli("--dry-run")
        self.assertEqual(code, 0, stdout + err)
        self.assertIn("alpha: 18.0.1.2.3 → 18.0.1.3.0", stdout)
        self.assertIn("would commit 'chore(release): bump alpha'", stdout)

    def test_dry_run_writes_nothing(self):
        out = self.output()
        before = self.commit("fix(alpha): x", "alpha/x.py")
        code, _, err = self.cli("--dry-run", "--push")
        self.assertEqual(code, 0, err)
        self.assertEqual(git(self.root, "rev-parse", "HEAD").strip(), before)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(out.read_text(), f"bumped=alpha\nsha={before}\n")

    def test_nothing_to_bump(self):
        out = self.output()
        head = self.commit("chore: x", "alpha/x.py")
        code, stdout, err = self.cli("--push")
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to bump", stdout)
        self.assertEqual(out.read_text(), f"bumped=\nsha={head}\n")

    def test_a_moved_branch_is_not_a_failure(self):
        """The push is rejected because develop moved: the run for its new head bumps
        (DESIGN §15.CR6)."""
        other = self.tmp / "other"
        subprocess.run(
            ["git", "clone", "-q", "-b", "develop", str(self.origin), str(other)],
            check=True,
            capture_output=True,
        )
        (other / "alpha/z.py").write_text("# z\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "fix(alpha): z")
        git(other, "push", "-q", "origin", "develop")
        out = self.output()
        head = self.commit("feat(alpha): x", "alpha/x.py")
        code, stdout, err = self.cli("--push")
        self.assertEqual(code, 0, stdout + err)
        self.assertIn(
            f"::notice::develop moved since {head[:12]}; the run for its new head bumps",
            stdout,
        )
        self.assertEqual(out.read_text(), f"bumped=\nsha={head}\n")

    def test_another_push_failure_is_exit_1(self):
        self.commit("feat(alpha): x", "alpha/x.py")
        git(self.root, "remote", "set-url", "origin", str(self.tmp / "nowhere.git"))
        code, stdout, err = self.cli("--push")
        self.assertEqual(code, 1, stdout + err)
        self.assertIn("::error::could not push the bump to develop", stdout)

    def test_set_overrides_the_scheme(self):
        self.commit("feat(alpha): x", "alpha/x.py")
        code, stdout, err = self.cli(
            "--dry-run", "--set", 'releases.version-scheme="semver"'
        )
        self.assertEqual(code, 2, stdout + err)
        self.assertIn("18.0.1.2.3 is not <major>.<minor>.<patch>", err)

    def test_releases_off_and_no_tool(self):
        self.write(
            "pyproject.toml", PYPROJECT + "\n[tool.kit-nix.releases]\nenable = false\n"
        )
        self.commit("chore: releases off")
        code, _, err = self.cli()
        self.assertEqual(code, 2, err)
        self.assertIn("releases is off", err)
        import dataclasses

        product = dataclasses.replace(KIT, release_tool=None)
        code, _, err = run_cli(
            product, "ci", "bump", "--base", self.base, cwd=self.root
        )
        self.assertEqual(code, 2, err)
        self.assertIn("kit-nix has no release tool", err)


if __name__ == "__main__":
    unittest.main()
