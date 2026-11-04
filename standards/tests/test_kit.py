"""The kit stub end to end (DESIGN §11.1, §11.3): shape-dependent discovery; an offline
sync then ``--check`` on both shapes (a bundle without a config file opts in with
``--standards``); ``minimal`` retracting blocks and jsonc leaves; the series-dependent
release branch and derived cfg; ``hooks.checks`` failing ``--write`` and ``--check``;
the multi-section ``setup.cfg`` retirement; per-branch Dependabot entries and a
local-region entry for another branch; the ``.envrc`` first opt-in; the key groups of
``package.json`` and ``tools/pyproject.toml``; exit codes 1, 2 and 3."""

import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from kit_nix_tools import product as kit
from support import KIT
from support.checkouts import CheckoutCase, write_profile

from standards_core.commands.repo import rollout
from standards_core.common import config, nar, pyproject, repoconfig
from standards_core.scaffold import context, discover, engine, fixtures
from standards_core.testing import conformance
from standards_core.testing.fakes import flake_lock, git, run_cli


class KitCase(CheckoutCase):
    """A ``bundle`` checkout (one unit, ``alpha``) opted in with ``profile``: no root
    ``pyproject.toml``, the table in the generated ``kit/pyproject.toml``
    (``config``)."""

    product = KIT
    profile = "recommended"
    config = kit.KIT_CONFIG

    def setUp(self) -> None:
        super().setUp()
        git(self.root, "rm", "-q", "--cached", "-r", "demo_unit", "pyproject.toml")
        (self.root / "demo_unit" / "__init__.py").unlink()
        (self.root / "pyproject.toml").unlink()
        self.write(
            self.config,
            kit.BUNDLE_PYPROJECT
            + f'[tool.kit-nix]\nschema = 1\nprofile = "{self.profile}"\n',
        )
        self.write("alpha/__unit__.toml", 'name = "alpha"\nversion = "18.0.1.0.0"\n')
        self.write("alpha/__init__.py", "")
        self.write("alpha/README.md", "# alpha\n")
        self.commit("bundle")

    def table(self, extra: str) -> None:
        self.write(
            self.config,
            self.read(self.config).replace(
                "[tool.kit-nix]\n", f"[tool.kit-nix]\n{extra}", 1
            ),
        )

    def plan(self) -> engine.Plan:
        return engine.build(KIT, self.root)


class TestDiscovery(KitCase):
    def facts(self) -> dict:
        checkout = KIT.hooks.load_checkout(self.root)
        resolved = config.resolve_doc(KIT, checkout.config, checkout=checkout)
        return discover.facts(KIT, checkout, resolved.cfg, resolved.modules)

    def test_a_bundle(self):
        facts = self.facts()
        self.assertEqual(
            (facts["shape"], facts["series"], facts["units"]),
            ("bundle", "18.0", ["alpha"]),
        )
        self.assertFalse(facts["gitmodules"])

    def test_a_workspace_from_units_txt_or_a_core_gitlink(self):
        git(self.root, "rm", "-q", "-r", "alpha")
        self.write("units.txt", "alpha\n")
        self.write(".kit-series", "17.0\n")
        self.commit("workspace")
        self.assertEqual(
            (self.facts()["shape"], self.facts()["series"]), ("workspace", "17.0")
        )
        git(self.root, "rm", "-q", "units.txt")
        git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'a' * 40},core",
        )
        # Committed as staged: `add -A` would drop a gitlink with no checkout.
        git(self.root, "commit", "-q", "-m", "gitlink")
        self.assertEqual(self.facts()["shape"], "workspace")

    def test_the_table_wins_and_the_branch_is_the_last_resort(self):
        git(self.root, "rm", "-q", "-r", "alpha")
        self.table('shape = "workspace"\n')
        self.commit("explicit")
        git(self.root, "checkout", "-q", "-b", "16.0")
        facts = self.facts()
        self.assertEqual((facts["shape"], facts["series"]), ("workspace", "16.0"))


class TestSeries(KitCase):
    def test_release_branch_and_derived_cfg(self):
        plan = self.plan()
        self.assertEqual(plan.cfg["series"], "18.0")
        self.assertEqual(plan.cfg["integration-branch"], "develop")
        self.assertEqual(
            plan.ctx.branches, {"integration": "develop", "release": "18.0"}
        )
        loaded = repoconfig.load(KIT, self.root)
        self.assertEqual(loaded.release_branch, "18.0")
        # The table's series wins over the units'.
        self.table('series = "19.0"\n')
        self.commit("series")
        self.assertEqual(self.plan().ctx.branches["release"], "19.0")

    def test_the_series_scheme(self):
        scheme = KIT.version_schemes["kit-series"]
        facts = {"series": "18.0"}
        self.assertEqual(scheme.bump("18.0.1.2.3", "minor", {}, facts), "18.0.1.3.0")
        self.assertEqual(scheme.bump("18.0.1.2.3", "major", {}, facts), "18.0.2.0.0")
        self.assertEqual(scheme.bump("18.0.1.2.3", "patch", {}, facts), "18.0.1.2.4")
        self.assertIsNone(scheme.valid("18.0.1.2.3", {}, facts))
        self.assertIn("not on series 18.0", scheme.valid("17.0.1.2.3", {}, facts))

    def test_the_release_tool(self):
        tool = KIT.release_tool
        (unit,) = tool.units(self.root, None)
        self.assertEqual(
            (unit.name, unit.path, unit.version_file),
            ("alpha", "alpha/", "alpha/__unit__.toml"),
        )
        text = self.read(unit.version_file)
        self.assertEqual(tool.read_version(text, unit), "18.0.1.0.0")
        self.assertEqual(
            tool.write_version(text, unit, "18.0.1.1.0"),
            'name = "alpha"\nversion = "18.0.1.1.0"\n',
        )
        self.assertTrue(tool.exempt(unit, "alpha/tests/test_x.py"))
        self.assertTrue(tool.exempt(unit, "alpha/README.md"))
        self.assertFalse(tool.exempt(unit, "alpha/models.py"))


class TestBothShapes(unittest.TestCase):
    """Each fixture, offline sync then ``--check``, under each built-in profile."""

    def test_offline_sync_then_check(self):
        with tempfile.TemporaryDirectory() as tmp, conformance._isolated(KIT):
            work = Path(tmp)
            for profile in ("minimal", "recommended"):
                for name, fixture in KIT.fixtures.items():
                    with self.subTest(fixture=name, profile=profile):
                        root = fixtures.throwaway(
                            KIT, work, None, name, fixture, profile=profile
                        )
                        code, _, err = run_cli(
                            KIT, "sync", "--write", "--offline", cwd=root
                        )
                        self.assertEqual(code, 0, err)
                        text = (root / "flake.nix").read_text()
                        self.assertIn("self.submodules = true;", text)
                        self.assertIn("# >>> kit-nix: inputs (managed) >>>", text)
                        (root / "flake.lock").write_text(json.dumps(flake_lock(text)))
                        fixtures.commit(root, "synced")
                        code, out, err = run_cli(KIT, "sync", "--check", cwd=root)
                        self.assertEqual(code, 0, out + err)
                        if name == "workspace":
                            self.assertIn("port=\"''${PORT:-8069}\"", text)
                            deps = tomllib.loads((root / "pyproject.toml").read_text())
                            self.assertEqual(
                                deps["project"]["dependencies"],
                                ["kit-unit-alpha", "kit-runtime~=18.0.0"],
                            )
                            # The foreign install set stays byte for byte.
                            self.assertIn(
                                "    # >>> kit-nix: install set (managed from units.txt) >>>\n"
                                '    "kit-unit-alpha",\n'
                                "    # <<< kit-nix: install set <<<\n",
                                (root / "pyproject.toml").read_text(),
                            )

    def test_the_kit_conforms(self):
        self.assertEqual(conformance.check(KIT), [])


class TestOptIn(KitCase):
    def test_a_bundle_whose_kit_project_has_no_table(self):
        self.write(self.config, kit.BUNDLE_PYPROJECT)
        self.commit("no table")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("has not opted in to kit-nix standards", out)
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        # The table goes into kit/pyproject.toml, after what kit-init generated there.
        text = self.read(self.config)
        self.assertTrue(text.startswith(kit.BUNDLE_PYPROJECT), text)
        self.assertEqual(
            tomllib.loads(text)["tool"]["kit-nix"], {"schema": 1, "profile": "minimal"}
        )
        self.assertFalse((self.root / "pyproject.toml").exists())
        # The seed made the flake: the bundle had none.
        self.assertIn("kit-nix.lib.mkFlake", self.read("flake.nix"))
        self.fake_locks()
        self.commit("opted in")
        self.assertEqual(self.check()[0], 0)

    def test_a_bundle_whose_kit_directory_has_only_other_files(self):
        """The file is created where its directory exists (§4.4)."""
        git(self.root, "rm", "-q", self.config)
        self.write("kit/README.md", "# kit\n")
        self.commit("no kit project")
        self.assertEqual(KIT.hooks.config_file(self.root), self.config)
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            tomllib.loads(self.read(self.config)),
            {"tool": {"kit-nix": {"schema": 1, "profile": "minimal"}}},
        )

    def test_the_preflight_refuses_a_bundle_without_kit(self):
        git(self.root, "rm", "-q", "-r", "kit")
        self.commit("no kit")
        self.assertEqual(KIT.hooks.config_file(self.root), "pyproject.toml")
        before = self.snapshot()
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 2, err)
        self.assertIn(
            "a bundle keeps [tool.kit-nix] in kit/pyproject.toml, and this checkout"
            " has no kit/ directory",
            err,
        )
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / "pyproject.toml").exists())


class TestSyncFromKit(KitCase):
    """kit/ holds the checkout's config file, so it is no checkout of its own: sync from
    there is refused (exit 2) and writes nothing under kit/ (§6.2)."""

    def test_check_and_write_from_kit_are_refused(self):
        self.synced()
        kit_dir = self.root / "kit"
        before = self.snapshot()
        message = (
            f"kit/pyproject.toml belongs to the checkout at {self.root.resolve()}: run"
            " kit-init --sync there"
        )
        code, out, err = self.fn("sync", "--check", cwd=kit_dir)
        self.assertEqual(code, 2, out + err)
        self.assertIn(message, err)
        code, out, err = self.fn("sync", "--check", "--format", "json", cwd=kit_dir)
        self.assertEqual(code, 2, out + err)
        self.assertIn(message, out)
        code, _, err = self.fn("sync", "--write", cwd=kit_dir)
        self.assertEqual(code, 2, err)
        self.assertIn(message, err)
        code, _, err = self.fn("sync", "--write", "--standards", "minimal", cwd=kit_dir)
        self.assertEqual(code, 2, err)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.name for p in kit_dir.iterdir()), ["pyproject.toml"])
        # The root still syncs clean.
        self.assertEqual(self.check()[0], 0)

    def test_a_unit_directory_with_its_own_pyproject_is_not_refused(self):
        """A unit's own pyproject.toml is not the checkout's config file: sync there
        reads it, and it has not opted in (the opt-in hint, not the refusal)."""
        self.write("alpha/pyproject.toml", '[project]\nname = "alpha"\n')
        self.commit("unit project")
        self.synced()
        code, out, err = self.fn("sync", "--check", cwd=self.root / "alpha")
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("belongs to the checkout", out + err)


# A kit org profile, pinned in the root's flake.lock.
KIT_ORG = """schema = 1
name = "example-org"
description = "Example Org's kit standards (a test fixture)"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
"""


class TestConfigInKit(KitCase):
    """§15.CR1: the table in ``kit/pyproject.toml``, the checkout's root the work
    tree's, from the root and from ``kit/``."""

    profile = "github:example/profile"
    rev = "c" * 40

    def setUp(self) -> None:
        super().setUp()
        # The locked profile tree, where pin-path finds it without a fetch: under the
        # root's pins directory, beside the root's flake.lock.
        tree = write_profile(
            self.root, rel=f".dev-dist/pins/profile-{self.rev}", text=KIT_ORG
        )
        lock = {"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}}
        lock["nodes"]["standards-profile"] = {
            "flake": False,
            "locked": {
                "type": "github",
                "owner": "example",
                "repo": "profile",
                "rev": self.rev,
                "narHash": nar.nar_hash(tree),
            },
            "original": {"type": "github", "owner": "example", "repo": "profile"},
        }
        lock["nodes"]["root"]["inputs"]["standards-profile"] = "standards-profile"
        self.write("flake.lock", json.dumps(lock, indent=2))
        self.commit("locked profile")

    def test_the_hook_and_the_root(self):
        self.assertEqual(KIT.hooks.config_file(self.root), "kit/pyproject.toml")
        self.assertEqual(
            pyproject.config_path(KIT, self.root), self.root / "kit/pyproject.toml"
        )
        kit_dir = self.root.resolve() / "kit"
        for start in (self.root, kit_dir):
            with self.subTest(start=start):
                self.assertEqual(
                    pyproject.checkout_root(KIT, start), self.root.resolve()
                )
        self.assertEqual(
            pyproject.root_for(KIT, kit_dir / "pyproject.toml"), self.root.resolve()
        )
        # Without a kit/ directory the root pyproject.toml is the config file.
        git(self.root, "rm", "-q", "-r", "--cached", "kit")
        self.assertEqual(KIT.hooks.config_file(self.root), "pyproject.toml")

    def test_config_and_profile_show_from_the_root_and_from_kit(self):
        for cwd in (self.root, self.root / "kit"):
            with self.subTest(cwd=cwd):
                code, out, err = self.fn("config", "org.publisher", cwd=cwd)
                self.assertEqual((code, out, err), (0, "Example Org\n", ""))
                code, out, err = self.fn("config", "profile.rev", cwd=cwd)
                self.assertEqual((code, out), (0, self.rev + "\n"), err)
                # The root's series discovery: the units sit at the root, not in kit/.
                self.assertEqual(
                    self.fn("config", "series", cwd=cwd)[:2], (0, "18.0\n")
                )
                code, out, err = self.fn("profile", "show", "--format", "json", cwd=cwd)
                self.assertEqual(code, 0, err)
                doc = json.loads(out)
                self.assertEqual(
                    (doc["profile"]["source"], doc["profile"]["rev"]),
                    ("github:example/profile", self.rev),
                )
                self.assertEqual(doc["config"]["org"]["publisher"], "Example Org")
        # --pyproject names the file; its root is still the checkout's.
        code, out, err = self.fn(
            "config", "profile.rev", "--pyproject", "kit/pyproject.toml"
        )
        self.assertEqual((code, out), (0, self.rev + "\n"), err)
        # The lock is the root's: one beside kit/pyproject.toml is not read.
        self.write(
            "kit/flake.lock", '{"version": 7, "root": "root", "nodes": {"root": {}}}'
        )
        self.assertEqual(
            self.fn("config", "profile.rev", cwd=self.root / "kit")[:2],
            (0, self.rev + "\n"),
        )

    def test_from_a_unit_with_a_pyproject_of_its_own(self):
        """§14 Stage 10: a unit directory's own pyproject.toml, which does not open the
        table, does not shadow the checkout's kit/pyproject.toml."""
        self.write("alpha/pyproject.toml", '[project]\nname = "kit-unit-alpha"\n')
        self.commit("unit project")
        unit = self.root / "alpha"
        self.assertEqual(KIT.hooks.config_file(unit), "pyproject.toml")
        self.assertEqual(pyproject.checkout_root(KIT, unit), self.root.resolve())
        code, out, err = self.fn("config", "org.publisher", cwd=unit)
        self.assertEqual((code, out, err), (0, "Example Org\n", ""))
        code, out, err = self.fn("profile", "show", "--format", "json", cwd=unit)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["profile"]["rev"], self.rev)

    def test_the_name_is_not_the_kit_project_s(self):
        """kit/pyproject.toml's [project].name names the dev-shell project, not the
        repository: the stub's load_checkout takes the name from the table's repo, else
        the origin, else the directory (the core default would take [project].name)."""
        self.write(
            self.config,
            '[project]\nname = "kit-dev-shell"\n\n' + self.read(self.config),
        )
        self.commit("kit project name")
        self.assertEqual(
            context.default_load_checkout(KIT, self.root).name, "kit-dev-shell"
        )
        self.assertEqual(KIT.hooks.load_checkout(self.root).name, "demo")
        self.table('repo = "example/kit-bundle"\n')
        self.assertEqual(KIT.hooks.load_checkout(self.root).name, "kit-bundle")

    def test_the_help_names_both_files(self):
        _, out, _ = self.fn("config", "--help")
        self.assertIn("pyproject.toml or kit/pyproject.toml", " ".join(out.split()))
        self.assertIn(
            "the checkout root's, beside its flake.nix", " ".join(out.split())
        )

    def test_rollout_sets_the_profile_ref_in_kit(self):
        path = pyproject.config_path(KIT, self.root)
        self.assertTrue(rollout.set_profile_ref(KIT, path, "github:example/profile/v2"))
        self.assertIn('profile = "github:example/profile/v2"\n', self.read(self.config))
        self.assertTrue(self.read(self.config).startswith(kit.BUNDLE_PYPROJECT))
        self.assertFalse((self.root / "pyproject.toml").exists())


class TestHistoryInKit(KitCase):
    """§15.CR1: history reads the table from ``kit/pyproject.toml``, and from the file
    each commit kept it in."""

    def synced(self) -> None:
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.fake_locks()
        self.commit("synced")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def assertRetracts(self) -> None:
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(
            ".gitignore (sentinel): the lint block goes with python-lint", out
        )
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertNotIn("lint (managed)", self.read(".gitignore"))
        self.commit("retracted")
        self.assertEqual(self.check()[0], 0)

    def test_a_module_turned_off_in_a_commit_is_retracted(self):
        self.synced()
        self.write(
            self.config, self.read(self.config).replace("recommended", "minimal")
        )
        self.commit("minimal")
        states = engine.history(KIT, self.root)
        self.assertTrue(context.ever(states, lambda h: h.modules["python-lint"]))
        self.assertRetracts()

    def test_a_table_that_moved_into_kit(self):
        # Opted in with a root pyproject.toml before kit/ existed.
        git(self.root, "rm", "-q", "-r", "kit")
        self.write(
            "pyproject.toml", '[tool.kit-nix]\nschema = 1\nprofile = "recommended"\n'
        )
        self.commit("root table")
        self.assertEqual(engine.config_file(KIT, self.root), "pyproject.toml")
        self.synced()
        # The table moves into kit/pyproject.toml, under minimal.
        git(self.root, "rm", "-q", "pyproject.toml")
        self.write(
            self.config,
            kit.BUNDLE_PYPROJECT + '[tool.kit-nix]\nschema = 1\nprofile = "minimal"\n',
        )
        self.commit("moved")
        self.assertEqual(engine.config_file(KIT, self.root), self.config)
        self.assertEqual(
            engine.history_paths(KIT, self.root), ["pyproject.toml", self.config]
        )
        self.assertRetracts()


class TestMinimalRetracts(KitCase):
    def test_blocks_and_jsonc_leaves_go(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.write(
            ".vscode/extensions.json",
            self.read(".vscode/extensions.json").replace(
                '"vendor.ruff"', '"vendor.ruff", "own.ext"'
            ),
        )
        self.fake_locks()
        self.commit("synced")
        self.assertEqual(self.check()[0], 0)
        self.write(
            self.config, self.read(self.config).replace("recommended", "minimal")
        )
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(
            ".gitignore (sentinel): the lint block goes with python-lint", out
        )
        self.assertIn(
            ".vscode/settings.json (jsonc-merge): python.languageServer: goes with editor",
            out,
        )
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertNotIn("lint (managed)", self.read(".gitignore"))
        self.assertIn("dev-shell (managed)", self.read(".gitignore"))
        self.assertEqual(json.loads(self.read(".vscode/settings.json")), {})
        self.assertEqual(
            json.loads(self.read(".vscode/extensions.json")),
            {"recommendations": ["own.ext"]},
        )
        # The flake's inputs block stays: dev-shell is on in minimal.
        self.assertIn("inputs (managed)", self.read("flake.nix"))
        self.assertFalse((self.root / ".github/dependabot.yml").exists())
        self.commit("minimal")
        self.assertEqual(self.check()[0], 0)


class TestChecks(KitCase):
    def test_a_unit_without_a_readme_fails_write_and_check(self):
        git(self.root, "rm", "-q", "alpha/README.md")
        self.commit("no readme")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(
            "alpha/README.md (check): missing: every unit has a README.md", out
        )
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 1, err)
        self.assertIn("alpha/README.md: missing: every unit has a README.md", err)

    def test_a_submodule_off_the_series(self):
        self.write(
            ".gitmodules",
            '[submodule "core"]\n\tpath = core\n\turl = https://example.org/core\n\tbranch = 17.0\n'
            '[submodule "ext"]\n\tpath = ext\n\turl = https://example.org/ext\n\tbranch = 18.0\n',
        )
        self.commit("submodules")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("submodule core has branch = 17.0, not the series 18.0", out)
        self.assertNotIn("submodule ext", out)
        self.table("hygiene.enable = false\n")
        self.commit("off")
        self.assertNotIn("submodule core", self.check()[1])


class TestRetire(KitCase):
    def test_a_multi_section_setup_cfg(self):
        self.write(
            "setup.cfg", "[flake8]\nmax-line-length = 88\n\n[isort]\nprofile = black\n"
        )
        self.commit("setup.cfg")
        code, out = self.check()
        self.assertIn("setup.cfg (retire): legacy file", out)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "setup.cfg").exists())

    def test_another_section_keeps_it(self):
        self.write("setup.cfg", "[flake8]\nx = 1\n\n[metadata]\nname = x\n")
        self.commit("setup.cfg")
        self.assertNotIn("setup.cfg", self.check()[1])


class TestDependabot(KitCase):
    def test_one_entry_per_ecosystem_and_branch(self):
        self.table('dependabot.target-branches = ["18.0", "17.0"]\n')
        self.commit("branches")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        text = self.read(".github/dependabot.yml")
        for branch in ("18.0", "17.0"):
            for directory in ("/", "/tools"):
                self.assertIn(
                    f'    directory: {directory}\n    target-branch: "{branch}"\n', text
                )

    def test_a_local_region_entry_for_another_branch(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.fake_locks()
        self.commit("synced")
        region = "  # kit-nix:local-begin updates\n"
        other = (
            "  - package-ecosystem: github-actions\n    directory: /\n"
            '    target-branch: "16.0"\n    schedule:\n      interval: monthly\n'
        )
        self.write(
            ".github/dependabot.yml",
            self.read(".github/dependabot.yml").replace(region, region + other),
        )
        self.assertEqual(self.check()[0], 0)
        same = other.replace('"16.0"', '"develop"')
        self.write(
            ".github/dependabot.yml",
            self.read(".github/dependabot.yml").replace(other, same),
        )
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("updates entry github-actions / → develop", out)


class TestEnvrcFirstOptIn(KitCase):
    profile = "minimal"

    def test_an_own_envrc_is_refused_and_a_legacy_one_replaced(self):
        self.write(".envrc", "export SECRET=1\nuse flake\n")
        self.commit("own envrc")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn(
            ".envrc is the checkout's own and differs from what kit-nix writes", out
        )
        self.assertIn(
            "Keep your own direnv lines in the .envrc `envrc` local region.", out
        )
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.read(".envrc"), "export SECRET=1\nuse flake\n")
        for legacy in kit.LEGACY_ENVRCS:
            with self.subTest(legacy=legacy):
                self.write(".envrc", legacy)
                self.commit("legacy envrc")
                code, _, err = self.fn("sync", "--write")
                self.assertEqual(code, 0, err)
                self.assertTrue(self.read(".envrc").startswith("# kit-nix:managed"))
                git(self.root, "reset", "-q", "--hard", "HEAD")

    def test_force_replaces_it(self):
        self.write(".envrc", "export SECRET=1\n")
        self.commit("own envrc")
        code, _, err = self.fn("sync", "--write", "--force")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.read(".envrc").startswith("# kit-nix:managed"))


class TestKeyGroups(KitCase):
    def test_package_json_pins_beside_its_own_scripts(self):
        self.write(
            "package.json",
            '{\n  "name": "kit-ui",\n  "scripts": {\n    "build": "vite build"\n  },\n'
            '  "devDependencies": {\n    "prettier": "^3.4.0"\n  }\n}\n',
        )
        self.commit("package.json")
        # A pin raised by hand is a floor met: no drift.
        self.assertNotIn("package.json", self.check()[1])
        self.write(
            "package.json", self.read("package.json").replace("^3.4.0", "^3.0.0")
        )
        self.commit("lowered")
        code, out = self.check()
        self.assertIn("package.json (json-merge): managed keys differ", out)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        doc = json.loads(self.read("package.json"))
        self.assertEqual(doc["scripts"], {"build": "vite build"})
        self.assertEqual(doc["devDependencies"], {"prettier": "^3.3.0"})
        # The product's JSON style: two spaces, no tabs.
        self.assertIn('\n  "name": "kit-ui",\n', self.read("package.json"))

    def test_tools_pyproject_from_two_modules(self):
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        doc = tomllib.loads(self.read("tools/pyproject.toml"))
        self.assertEqual(
            doc["project"]["dependencies"], ["ruff==0.6.9", "pytest==8.3.0"]
        )
        self.assertEqual(doc["tool"]["coverage"]["report"]["fail_under"], 0)
        self.assertIs(doc["tool"]["uv"]["package"], False)
        # A pin raised by hand stays (a floor); the other is kept as rendered.
        self.write(
            "tools/pyproject.toml",
            self.read("tools/pyproject.toml").replace("ruff==0.6.9", "ruff==0.7.0"),
        )
        self.fake_locks()
        self.commit("raised")
        self.assertNotIn("tools/pyproject.toml", self.check()[1])
        self.table("tests.enable = false\n")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        doc = tomllib.loads(self.read("tools/pyproject.toml"))
        self.assertEqual(doc["project"]["dependencies"], ["ruff==0.7.0"])
        self.assertNotIn("run", doc["tool"].get("coverage", {}))


class TestExitCodes(KitCase):
    def test_one_two_three(self):
        self.assertEqual(self.check()[0], 1)  # drift: nothing synced yet
        self.table('shape = "tower"\n')
        self.commit("bad shape")
        self.assertEqual(self.check()[0], 2)  # the table breaks the schema
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = run_cli(KIT, "sync", "--check", cwd=Path(tmp))
            self.assertEqual(code, 3)  # not a git work tree

    def test_context_options_in_a_throwaway(self):
        with tempfile.TemporaryDirectory() as tmp, conformance._isolated(KIT):
            root = fixtures.throwaway(
                KIT,
                Path(tmp),
                None,
                "bundle",
                KIT.fixtures["bundle"],
                profile="minimal",
            )
            plan = engine.build(KIT, root, options={"throwaway": True})
            self.assertTrue(plan.ctx.options["throwaway"])
            self.assertIsInstance(plan.ctx, context.NS)
        self.assertEqual(os.environ.get("KIT_NIX_OFFLINE"), "1")


if __name__ == "__main__":
    unittest.main()
