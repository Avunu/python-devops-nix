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
from support.checkouts import CheckoutCase

from standards_core.common import config, repoconfig
from standards_core.scaffold import context, discover, engine, fixtures
from standards_core.testing import conformance
from standards_core.testing.fakes import flake_lock, git, run_cli


class KitCase(CheckoutCase):
    """A ``bundle`` checkout (one unit, ``alpha``) opted in with ``profile``."""

    product = KIT
    profile = "recommended"

    def setUp(self) -> None:
        super().setUp()
        git(self.root, "rm", "-q", "--cached", "-r", "demo_unit")
        (self.root / "demo_unit" / "__init__.py").unlink()
        self.write(
            "pyproject.toml",
            f'[tool.kit-nix]\nschema = 1\nprofile = "{self.profile}"\n',
        )
        self.write("alpha/__unit__.toml", 'name = "alpha"\nversion = "18.0.1.0.0"\n')
        self.write("alpha/__init__.py", "")
        self.write("alpha/README.md", "# alpha\n")
        self.commit("bundle")

    def table(self, extra: str) -> None:
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml").replace(
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
    def test_a_bundle_with_no_config_file(self):
        git(self.root, "rm", "-q", "pyproject.toml")
        self.commit("no config")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("has not opted in to kit-nix standards", out)
        code, _, err = self.fn("sync", "--write", "--standards", "minimal")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            tomllib.loads(self.read("pyproject.toml")),
            {"tool": {"kit-nix": {"schema": 1, "profile": "minimal"}}},
        )
        # The seed made the flake: the bundle had none.
        self.assertIn("kit-nix.lib.mkFlake", self.read("flake.nix"))
        self.fake_locks()
        self.commit("opted in")
        self.assertEqual(self.check()[0], 0)


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
            "pyproject.toml",
            self.read("pyproject.toml").replace("recommended", "minimal"),
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
