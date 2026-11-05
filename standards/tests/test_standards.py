"""The ported acceptance cases of opting in and out (DESIGN §11.2, stage 4): opt-out,
``minimal``, module toggles and their round trip, retraction after a commit, a move off
GitHub, in-repo and locked org profiles, retirement by function and the first opt-in.

Every case runs ``stub-nix`` offline on a throwaway stub checkout. What a framework
product's JavaScript modules did in the original cases, the stub's ``pyproject.toml``
key groups (``python-lint``, ``tests``) and whole files (``committed.toml``,
``.editorconfig``, ``CONTRIBUTING.md``, the GitHub files) do here.
"""

import dataclasses
import json
import os
import shutil
import tarfile
import tomllib
from pathlib import Path
from unittest import mock

from support import STUB, git, product_copy, run_cli
from support.checkouts import EXAMPLE_ORG, CheckoutCase, write_profile

from standards_core.common import nar
from standards_core.product import Paths
from standards_core.scaffold import engine
from standards_core.testing.fakes import flake_lock

# The stub with what ``--standards`` adds besides schema and profile: its required
# ``major`` (a framework product takes it from a flag such as its version).
OPTING = dataclasses.replace(
    STUB,
    hooks=dataclasses.replace(
        STUB.hooks, new_target_table=lambda root, checkout, args, profile: {"major": 16}
    ),
)

BARE = """[project]
name = "demo_unit"
description = "A demo unit"
requires-python = ">=3.11"
dynamic = ["version"]
dependencies = []

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.ruff]
line-length = 110
target-version = "py311"

[tool.ruff.format]
quote-style = "double"
docstring-code-format = true

[tool.coverage.report]
fail_under = 10
show_missing = true
"""


class OptOutCase(CheckoutCase):
    """A checkout with no ``[tool.stub-nix]``: what a new unit's generator writes."""

    product = OPTING

    def setUp(self) -> None:
        super().setUp()
        self.write("pyproject.toml", BARE)
        self.commit()

    def changed(self) -> list[str]:
        return sorted(
            line[3:] for line in git(self.root, "status", "--porcelain").splitlines()
        )


class TestDefaultModeUnchanged(OptOutCase):
    """Without opting in, sync and check refuse with the hint and write nothing."""

    def test_check_and_sync_refuse(self):
        for argv in (("sync", "--check"), ("sync", "--write")):
            with self.subTest(argv=argv):
                code, out, err = self.fn(*argv)
                self.assertEqual(code, 2, out + err)
                self.assertIn("has not opted in", err)
                self.assertIn("--standards minimal|recommended", err)
                self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_json_report_says_invalid(self):
        code, out, _ = self.fn("sync", "--check", "--format", "json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "invalid")


class TestMinimalRendersNothingExtra(OptOutCase):
    def test_minimal(self):
        before = self.read("pyproject.toml")
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.changed(), [".envrc", "flake.nix", "pyproject.toml"])
        after = self.read("pyproject.toml")
        self.assertTrue(after.startswith(before), "a key outside the table changed")
        table = tomllib.loads(after)["tool"]["stub-nix"]
        self.assertEqual(table, {"schema": 1, "profile": "minimal", "major": 16})
        self.fake_locks()
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        plan = engine.build(self.product, self.root)
        live = [i.path for i in plan.items if i.current is not None]
        self.assertEqual(sorted(live), [".envrc", "flake.nix", "pyproject.toml"])
        self.assertEqual(
            next(i for i in plan.items if i.path == "pyproject.toml").code, 0
        )


class TestSeedNoticeOnlyForATurnOff(OptOutCase):
    def test_a_checkouts_own_seed_name_is_not_reported(self):
        self.write(".git-blame-ignore-revs", "# mine\n")
        self.commit()
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.assertNotIn(".git-blame-ignore-revs", err)
        self.commit()
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertNotIn(".git-blame-ignore-revs", err)


class TestFirstOptIn(OptOutCase):
    OWN_FLAKE = (
        '{\n  inputs.stub-nix.url = "github:example/stub-nix";\n'
        "  outputs = _: { };\n  # mine\n}\n"
    )

    def test_an_own_flake_needs_force(self):
        self.write("flake.nix", self.OWN_FLAKE)
        self.commit()
        for argv in (
            ("sync", "--write", "--standards", "recommended"),
            ("sync", "--check", "--standards", "recommended"),
        ):
            with self.subTest(argv=argv):
                code, out, err = self.fn(*argv)
                self.assertEqual(code, 2, out + err)
                self.assertIn(
                    "flake.nix is the checkout's own and differs from what stub-nix"
                    " writes: sync would replace it with the managed file. Keep custom"
                    " inputs in the flake.nix `inputs` local region. then pass --force"
                    " to replace it. The replacement:\n--- current\n+++ rendered\n",
                    out + err,
                )
                self.assertIn("+# stub-nix:managed", out + err)
                self.assertIn("-  # mine", out + err)
                self.assertEqual(git(self.root, "status", "--porcelain"), "")
        code, _, err = self.fn(
            "sync", "--write", "--standards", "recommended", "--force"
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(self.read("flake.nix").startswith("# stub-nix:managed"))

    def test_an_own_envrc_needs_force_too(self):
        self.write(".envrc", "dotenv\nuse flake\n")
        self.commit()
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 2, err)
        self.assertIn(".envrc is the checkout's own", err)
        self.assertIn("Keep your own direnv lines in the .envrc `envrc` local", err)
        self.assertEqual(self.read(".envrc"), "dotenv\nuse flake\n")

    def test_what_the_product_wrote_before_is_replaced_silently(self):
        from stub_nix_tools.product import LEGACY_ENVRC, LEGACY_FLAKE

        self.write("flake.nix", LEGACY_FLAKE)
        self.write(".envrc", LEGACY_ENVRC)
        self.commit()
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.read("flake.nix").startswith("# stub-nix:managed"))
        self.assertTrue(self.read(".envrc").startswith("# stub-nix:managed"))

    def test_once_managed_an_edit_is_ordinary_drift(self):
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.fake_locks()
        self.commit()
        flake = self.read("flake.nix")
        # The header goes: the file no longer says it is sync's, but HEAD's does.
        self.write("flake.nix", flake.split("\n", 1)[1] + "# mine\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertNotIn("--force", out)
        self.assertEqual(self.fn("sync", "--write")[0], 0)
        self.assertEqual(self.read("flake.nix"), flake)

    def test_the_products_message_template(self):
        spec = dataclasses.replace(
            STUB.first_opt_in[0],
            message="{path}: {name} would replace it ({hint}).\n{diff}",
        )
        self.product = dataclasses.replace(
            OPTING, first_opt_in=(spec, *STUB.first_opt_in[1:])
        )
        self.write("flake.nix", self.OWN_FLAKE)
        self.commit()
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 2, err)
        self.assertIn(
            "flake.nix: stub-nix would replace it (Keep custom inputs in the flake.nix"
            " `inputs` local region.).\n--- current\n+++ rendered\n",
            err,
        )


class ToggleCase(CheckoutCase):
    """A checkout synced with ``recommended`` and committed."""

    def setUp(self) -> None:
        super().setUp()
        self.synced()

    def resync(self) -> tuple[int, str]:
        code, out, err = self.fn("sync", "--write")
        return code, out + err

    def ruff(self) -> dict:
        return tomllib.loads(self.read("pyproject.toml"))["tool"]["ruff"]


class TestToggleOff(ToggleCase):
    def test_python_lint_off_and_on(self):
        before = self.snapshot()
        self.table("python-lint.enable = false\n")
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertNotIn("lint", self.ruff(), "the managed keys go")
        # line-length = 88 is what a new unit's generator writes: the checkout's.
        self.assertEqual(self.ruff()["line-length"], 88)
        self.assertNotIn("ruff", self.read("tools/pyproject.toml"))
        self.assertIn("pytest", self.read("tools/pyproject.toml"))
        self.assertEqual(self.check()[0], 0)
        # Back on: every file byte for byte.
        self.write("pyproject.toml", before["pyproject.toml"].decode())
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.snapshot(), before)

    def test_ci_off_removes_what_sync_rendered_only(self):
        """Through an org profile's ``[[extra-files]]`` entry of module ci: the file
        sync rendered goes, the checkout's own workflow stays."""
        write_profile(
            self.root,
            text=EXAMPLE_ORG
            + '\n[[extra-files]]\npath = ".github/workflows/rendered.yml"\n'
            'template = "rendered.yml.j2"\nstrategy = "whole"\nmodule = "ci"\n'
            'header = "yaml"\n',
        )
        self.write(
            ".standards-profile/templates/rendered.yml.j2",
            "name: rendered\non: [push]\n",
        )
        self.write(".github/workflows/custom.yml", "name: custom\non: [push]\n")
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                'profile = "recommended"', 'profile = "./.standards-profile"'
            ),
        )
        self.commit()
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertTrue(
            self.read(".github/workflows/rendered.yml").startswith("# stub-nix:managed")
        )
        self.fake_locks()
        self.commit()
        self.table("ci.enable = false\n")
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / ".github/workflows/rendered.yml").exists())
        self.assertFalse((self.root / ".github/workflows/ci.yml").exists())
        self.assertTrue((self.root / ".github/workflows/custom.yml").exists())
        self.assertEqual(self.check()[0], 0)

    def test_local_region_content_stops_the_retraction(self):
        text = self.read(".editorconfig").replace(
            "# stub-nix:local-begin editorconfig\n",
            "# stub-nix:local-begin editorconfig\n[*.md]\nindent_size = 2\n",
        )
        self.write(".editorconfig", text)
        self.commit()
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                'profile = "recommended"', 'profile = "minimal"'
            ),
        )
        before = self.snapshot()
        code, out = self.resync()
        self.assertEqual(code, 2, out)
        self.assertIn("local region editorconfig holds content", out)
        self.assertEqual(
            self.snapshot(), before, "a refused retraction deleted something"
        )

    def test_an_edited_managed_key_is_left_with_a_warning(self):
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                "line-length = 88", "line-length = 120"
            ),
        )
        self.commit()
        self.table("python-lint.enable = false\n")
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ruff()["line-length"], 120)
        self.assertNotIn("lint", self.ruff(), "the unedited keys go")
        self.assertIn(
            "pyproject.toml tool.ruff.line-length is the app's now that python-lint is"
            " off (it differs from what sync wrote)",
            out,
        )
        self.assertEqual(self.check()[0], 0)

    def test_recommended_to_minimal(self):
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                'profile = "recommended"', 'profile = "minimal"'
            ),
        )
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        for path in (
            "committed.toml",
            ".editorconfig",
            "CONTRIBUTING.md",
            "tools/pyproject.toml",
            ".github/dependabot.yml",
            ".github/workflows/ci.yml",
        ):
            self.assertFalse((self.root / path).exists(), path)
        # The seeds stay, said once: on the run that turns their module off. A lock
        # a command makes stays without a word.
        for seed in (".git-blame-ignore-revs", "tools/uv.lock"):
            self.assertTrue((self.root / seed).exists(), seed)
        self.assertIn(".git-blame-ignore-revs stays", out)
        self.assertNotIn("tools/uv.lock", out)
        self.commit()
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertNotIn("stays", out)
        self.assertEqual(self.check()[0], 0)


class TestToggleRoundTrip(ToggleCase):
    """Turning a module off (committed, synced, committed) and back on restores every
    file byte for byte, for each module that owns keys or files."""

    def off_and_on(self, line: str) -> None:
        before = self.snapshot()
        self.table(line)
        self.commit()
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.write("pyproject.toml", self.read("pyproject.toml").replace(line, "", 1))
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        after = self.snapshot()
        for path in sorted(set(before) | set(after)):
            if path == "tools/uv.lock":
                continue  # the lock uv makes, faked here
            self.assertEqual(
                after.get(path, b"").decode(), before.get(path, b"").decode(), path
            )

    def test_each_module(self):
        for line in (
            "python-lint.enable = false\n",
            "tests.enable = false\n",
            "tests.coverage.enable = false\n",
            "editorconfig.enable = false\n",
            "hygiene.enable = false\n",
            "dependabot.enable = false\n",
            "ci.enable = false\n",
            "commits.enable = false\nreleases.enable = false\nextras.enable = false\n",
        ):
            with self.subTest(line=line):
                self.off_and_on(line)
                self.commit()


class TestRetractionAfterACommit(ToggleCase):
    """A module turned off in a commit made before syncing (what ``--check`` sees on a
    pull request) is retracted all the same: ``--check`` reports what is left, and
    sync removes it."""

    OFF = (
        "python-lint.enable = false\ncommits.enable = false\nreleases.enable = false\n"
        "extras.enable = false\n"
    )

    def test_config_committed_first(self):
        self.table(self.OFF)
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 1, out)
        for path in ("pyproject.toml", "committed.toml", "tools/pyproject.toml"):
            self.assertIn(f"{path} (", out)
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.commit()
        self.assertEqual(self.check()[0], 0)
        self.assertFalse((self.root / "committed.toml").exists())
        self.assertNotIn("lint", self.ruff())
        self.assertNotIn("ruff", self.read("tools/pyproject.toml"))

    def test_a_settled_module_stays_settled(self):
        """Once retracted and committed, a later sync changes nothing and says
        nothing."""
        self.table(self.OFF)
        self.commit()
        self.assertEqual(self.resync()[0], 0)
        self.commit()
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertNotIn("notice", out)
        self.assertNotIn("warning", out)


PLAIN_PROFILE = """schema = 1
name = "plain"
description = "recommended@1.0 as it is (a test fixture)"
extends = "recommended@1.0"
"""

# What the profile adds to turn python-lint and editorconfig off: nothing in the
# checkout's own pyproject.toml changes.
PROFILE_OFF = "\n[python-lint]\nenable = false\n\n[editorconfig]\nenable = false\n"


class ProfileSideCase(ToggleCase):
    """A module turned off by the org profile, not the checkout's table, is retracted
    the same way: the history reads each committed table against the profile committed
    beside it."""

    def assert_retracted(self, env: dict[str, str] | None = None) -> None:
        with mock.patch.dict(os.environ, env or {}):
            code, out = self.check()
            self.assertEqual(code, 1, out)
            for path in (".editorconfig", "pyproject.toml"):
                self.assertIn(f"{path} (", out)
            code, out = self.resync()
            self.assertEqual(code, 0, out)
            self.commit()
            code, out = self.check()
            self.assertEqual(code, 0, out)
        self.assertFalse((self.root / ".editorconfig").exists())
        self.assertNotIn("lint", self.ruff())


class TestOriginMove(ToggleCase):
    def test_origin_moving_off_github(self):
        """Where the repository is hosted is not committed: a commit with .github/ is
        read as on GitHub too, so what the GitHub-only modules left behind goes when
        origin moves elsewhere."""
        self.write(".github/workflows/custom.yml", "name: custom\non: [push]\n")
        self.commit()
        git(self.root, "remote", "add", "origin", "https://github.com/example/demo.git")
        self.assertEqual(self.check()[0], 0)
        git(
            self.root,
            "remote",
            "set-url",
            "origin",
            "https://gitlab.com/example/demo.git",
        )
        code, out = self.check()
        self.assertEqual(code, 1, out)
        for path in (".github/dependabot.yml", ".github/workflows/ci.yml"):
            self.assertIn(f"{path} (", out)
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.commit()
        self.assertEqual(self.check()[0], 0)
        self.assertFalse((self.root / ".github/dependabot.yml").exists())
        self.assertFalse((self.root / ".github/workflows/ci.yml").exists())
        self.assertTrue((self.root / ".github/workflows/custom.yml").exists())


class TestOriginMoveWithoutAGithubDir(CheckoutCase):
    """A checkout that never had ``.github/`` (ci and dependabot off from its opt-in),
    with ``EXTRAS.md`` from its in-repo profile, of GitHub-only module ``extras``:
    origin moves off GitHub."""

    profile = "./.standards-profile"

    def setUp(self) -> None:
        super().setUp()
        write_profile(
            self.root,
            text=EXAMPLE_ORG
            + '\n[[extra-files]]\npath = "EXTRAS.md"\ntemplate = "EXTRAS.md.j2"\n'
            'strategy = "whole"\nmodule = "extras"\nheader = "none"\n',
        )
        self.write(".standards-profile/templates/EXTRAS.md.j2", "# Extras\n")
        self.table("ci.enable = false\ndependabot.enable = false\n")
        self.commit()

    def resync(self) -> tuple[int, str]:
        code, out, err = self.fn("sync", "--write")
        return code, out + err

    def move(self) -> tuple[int, str]:
        self.synced()
        self.assertEqual(git(self.root, "log", "--format=%H", "--", ".github"), "")
        self.assertEqual(self.read("EXTRAS.md"), "# Extras\n")
        git(self.root, "remote", "add", "origin", "https://github.com/example/demo.git")
        self.assertEqual(self.check()[0], 0)
        git(
            self.root,
            "remote",
            "set-url",
            "origin",
            "https://gitlab.com/example/demo.git",
        )
        return self.check()

    def test_a_product_mark_reads_the_state_as_on_github(self):
        """A file only a GitHub-only module renders, listed in ``paths.github_marks``,
        marks the state as on GitHub just as ``.github/`` does."""
        self.product = dataclasses.replace(
            STUB, paths=Paths(github_marks=(".github", "EXTRAS.md"))
        )
        code, out = self.move()
        self.assertEqual(code, 1, out)
        self.assertIn("EXTRAS.md (", out)
        code, out = self.resync()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / "EXTRAS.md").exists())

    def test_without_a_mark_the_state_is_read_where_origin_is(self):
        code, out = self.move()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.resync()[0], 0)
        self.assertTrue((self.root / "EXTRAS.md").exists())


class TestInRepoProfileToggle(ProfileSideCase):
    """The checkout opts in with an in-repo profile, which a later commit edits to turn
    python-lint and editorconfig off: the profile committed before the edit had them
    on."""

    profile = "./.standards-profile"

    def setUp(self) -> None:
        CheckoutCase.setUp(self)
        write_profile(self.root, text=PLAIN_PROFILE)
        (self.root / ".standards-profile/templates/SECURITY.md.j2").unlink()
        self.commit()
        self.synced()
        self.assertTrue((self.root / ".editorconfig").exists())

    def test_profile_edit_retracts(self):
        self.write(".standards-profile/profile.toml", PLAIN_PROFILE + PROFILE_OFF)
        self.commit()
        self.assert_retracted()


class TestLockedProfileBump(ProfileSideCase):
    """``nix flake update standards-profile`` to a profile that turns python-lint and
    editorconfig off, read without Nix (pin-path): the old tree, as the old lock locks
    it, had them on. The checkout opts in with the org profile, so that tree is the
    only state with them on."""

    profile = "github:example/profile"

    def setUp(self) -> None:
        CheckoutCase.setUp(self)
        self.work = Path(self._tmp.name) / "pins"
        self.old, new = "c" * 40, "d" * 40
        old_tree = self.publish(self.old, PLAIN_PROFILE)
        self.publish(new, PLAIN_PROFILE + PROFILE_OFF)
        variable = f"{self.product.identity.env_prefix}_PIN_URL"
        self.env = {variable: f"file://{self.work}/{{rev}}.tar.gz"}
        # The pins directory is the checkout's to ignore.
        self.write(".gitignore", "/.dev-dist/\n")
        self.commit()
        with mock.patch.dict(os.environ, self.env):
            # The first sync renders the profile input; the lock nix would then make.
            code, _, err = self.fn("sync", "--write", "--profile-path", str(old_tree))
            self.assertEqual(code, 0, err)
            self.fake_locks()
            self.write("flake.lock", json.dumps(self.lock(self.old)))
            self.commit()
            code, out = self.check()
            self.assertEqual(code, 0, out)
        self.assertTrue((self.root / ".editorconfig").exists())
        self.write("flake.lock", json.dumps(self.lock(new)))
        self.commit()

    def publish(self, rev: str, text: str) -> Path:
        tree = write_profile(self.work / "src", rel=f"profile-{rev}", text=text)
        (tree / "templates/SECURITY.md.j2").unlink()
        with tarfile.open(self.work / f"{rev}.tar.gz", "w:gz") as tar:
            tar.add(tree, arcname=tree.name)
        self.trees = {**getattr(self, "trees", {}), rev: tree}
        return tree

    def lock(self, rev: str) -> dict:
        lock = flake_lock(self.read("flake.nix"))
        lock["nodes"]["standards-profile"] = {
            "flake": False,
            "locked": {
                "type": "github",
                "owner": "example",
                "repo": "profile",
                "rev": rev,
                "narHash": nar.nar_hash(self.trees[rev]),
            },
            "original": {"type": "github", "owner": "example", "repo": "profile"},
        }
        return lock

    def test_bump_retracts(self):
        self.assert_retracted(self.env)

    def test_an_old_tree_that_cant_be_read_is_exit_3(self):
        """What the old tree turned on is unknown, so ``--check`` can't give a
        verdict."""
        (self.work / f"{self.old}.tar.gz").unlink()
        shutil.rmtree(self.root / ".dev-dist/pins", ignore_errors=True)
        with mock.patch.dict(os.environ, self.env):
            code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn(
            f"commit {git(self.root, 'rev-parse', 'HEAD~1')[:12]} locked", out
        )
        self.assertIn("could not be read", out)


class TestRetireByFunction(ToggleCase):
    """Rules that look inside a file (``contains``) follow their module."""

    LEGACY = "jobs: { x: { steps: [ { run: stub-legacy-check } ] } }\n"

    def test_rules_follow_their_module(self):
        self.write(".github/workflows/old.yml", self.LEGACY)
        self.write(".github/workflows/check.yml", "name: check\n")
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(".github/workflows/old.yml (retire): legacy file", out)
        self.assertNotIn("check.yml", out, "name-only rules are the profile's")
        self.table("ci.enable = false\n")
        _code, out = self.check()
        self.assertNotIn("(retire)", out)

    def test_retire_keep(self):
        self.write(".github/workflows/old.yml", self.LEGACY)
        self.table('retire-keep = [".github/workflows/old.yml"]\n')
        self.commit()
        self.assertNotIn("(retire)", self.check()[1])

    def test_a_tracked_file_in_an_unmanaged_directory_is_not_retired(self):
        """Sync never reads a ``never_write`` directory: a force-added file there that a
        ``**`` rule matches is not a legacy file, and the rest is still checked."""
        product = product_copy(
            STUB,
            Path(self._tmp.name) / "product",
            paths=Paths(never_write=frozenset({"node_modules", ".venv"})),
        )
        (product.data_root / "manifest.d" / "zz.json").write_text(
            json.dumps(
                {
                    "retire": [
                        {
                            "paths": ["**/legacy.ini"],
                            "rule": "the old tool's config",
                            "module": "python-lint",
                        }
                    ]
                }
            )
        )
        self.product = product
        self.write(".gitignore", "node_modules/\n.venv/\n")
        self.write("node_modules/x/legacy.ini", "[x]\n")
        self.write(".venv/lib/legacy.ini", "[x]\n")
        self.write("sub/legacy.ini", "[x]\n")
        git(self.root, "add", "-f", "node_modules/x/legacy.ini", ".venv/lib/legacy.ini")
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("sub/legacy.ini (retire): legacy file", out)
        self.assertNotIn("node_modules", out)
        self.assertNotIn(".venv", out)
        git(self.root, "rm", "-q", "sub/legacy.ini")
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_the_profiles_rule(self):
        write_profile(self.root)
        self.write(".github/workflows/check.yml", "name: check\n")
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
                'profile = "recommended"', 'profile = "./.standards-profile"'
            ),
        )
        self.commit()
        _code, out = self.check()
        self.assertIn(
            ".github/workflows/check.yml (retire): legacy file (the profile's"
            " [[retire]] rule)",
            out,
        )


class TestRetractionNeedsAnOptIn(CheckoutCase):
    """The history stops at the newest commit without the table: what the checkout had
    before it opted in is its own, so a module on in an older opt-in was never sync's
    for what the checkout wrote since."""

    def test_an_opt_out_ends_the_history(self):
        own = '\n[tool.ruff.lint]\nselect = ["F", "E", "W", "I"]\nignore = ["E501"]\n'
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").split("[tool.stub-nix]")[0] + own,
        )
        self.commit()
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml")
            + '\n[tool.stub-nix]\nschema = 1\nprofile = "minimal"\nmajor = 16\n',
        )
        self.commit()
        plan = engine.build(self.product, self.root)
        self.assertEqual(len(plan.ctx.history), 1)
        self.assertFalse(plan.ctx.history[0].modules["python-lint"])
        code, out, err = run_cli(STUB, "sync", "--write", cwd=self.root)
        self.assertEqual(code, 0, out + err)
        self.assertIn(own, self.read("pyproject.toml"))
