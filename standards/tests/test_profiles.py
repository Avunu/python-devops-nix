"""The built-in profiles (DESIGN §4.4), the changed-defaults warning, and ``profile
show|list``."""

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

from packaging.version import Version
from support import KIT, STUB, git, product_copy, run_cli

from standards_core.common import config, schema
from standards_core.common.data import data_path
from standards_core.scaffold import defaults


def load(product, stem):
    return tomllib.loads(data_path(product, f"profiles/{stem}.toml").read_text())


class TestBuiltinProfiles(unittest.TestCase):
    def test_names(self):
        for product in (STUB, KIT):
            with self.subTest(product=product.identity.name):
                names = config.builtin_names(product)
                self.assertEqual(names[0], "minimal")
                self.assertIn("recommended@1.0", names)
                self.assertEqual(config.builtin_name(product, "recommended"), names[-1])
        with self.assertRaises(config.ConfigError) as caught:
            config.builtin_name(STUB, "recommended@9.9")
        self.assertIn(
            "built-ins: recommended, minimal, recommended@1.0", str(caught.exception)
        )

    def test_validate_against_the_profile_schema(self):
        for product in (STUB, KIT):
            for stem in config.builtin_names(product):
                with self.subTest(product=product.identity.name, stem=stem):
                    doc = load(product, stem)
                    validator = schema.Validator(
                        product, "profile.schema.json", stem, app_only_allowed=False
                    )
                    self.assertEqual(validator.errors(doc), [])
                    config.validate_profile(product, doc, stem, org=False)

    def test_every_module_has_a_table(self):
        for product in (STUB, KIT):
            for stem in config.builtin_names(product):
                doc = load(product, stem)
                with self.subTest(product=product.identity.name, stem=stem):
                    names = product.modules.names
                    self.assertEqual([m for m in names if m not in doc], [])
                    self.assertTrue(all("enable" in doc[m] for m in names))

    def test_the_schema_defines_exactly_the_modules(self):
        for product in (STUB, KIT):
            modules = schema.load(product, "modules.schema.json")["$defs"]["modules"]
            self.assertEqual(list(modules), list(product.modules.names))
            composed = schema.load(product, "profile.schema.json")["$defs"]["modules"]
            self.assertEqual(list(composed), list(product.modules.names))

    def test_minimal_enables_only_the_dev_shell(self):
        for product in (STUB, KIT):
            doc = load(product, "minimal")
            names = product.modules.names
            self.assertEqual([m for m in names if doc[m]["enable"]], ["dev-shell"])
            self.assertEqual({k for m in names for k in doc[m]}, {"enable"})

    def test_recommended_sets_no_organisation_value(self):
        org = load(STUB, "recommended@1.0")["org"]
        self.assertEqual(
            {k for k, v in org.items() if v and not isinstance(v, dict)},
            {"dev-docs-url", "repo-url"},
        )

    def test_schema_defaults_are_the_recommended_values(self):
        # minimal sets no parameters: a module turned on over it gets the schema
        # defaults, which must be the recommended@1.0 values.
        defaults_ = schema.defaults(STUB, "profile.schema.json")
        recommended = load(STUB, "recommended@1.0")

        def subset(default, value):
            if isinstance(value, dict) and value:
                return {k: subset(default[k], v) for k, v in value.items()}
            return default

        for m in STUB.modules.names:
            params = {k: v for k, v in recommended[m].items() if k != "enable"}
            with self.subTest(module=m):
                self.assertEqual(
                    {k: subset(defaults_[m][k], v) for k, v in params.items()}, params
                )
        self.assertEqual(
            subset(defaults_["org"], recommended["org"]), recommended["org"]
        )


SNAPSHOT_1_1 = """\
schema = 1
name = "recommended"
description = "The second snapshot."

[dev-shell]
enable = true

[editorconfig]
enable = true
line-length = 100
"""


class TestChangedDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.product = product_copy(STUB, Path(self.tmp.name))
        profiles = self.product.data_root / "profiles"
        (profiles / "recommended@1.1.toml").write_text(SNAPSHOT_1_1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshots(self):
        product = self.product
        self.assertEqual(
            config.builtin_names(product),
            ["minimal", "recommended@1.0", "recommended@1.1"],
        )
        self.assertEqual(config.builtin_name(product, "recommended"), "recommended@1.1")
        self.assertEqual(
            defaults.snapshot_at(product, Version("1.0.7")), "recommended@1.0"
        )
        self.assertEqual(
            defaults.snapshot_at(product, Version("1.4.0")), "recommended@1.1"
        )
        self.assertIsNone(defaults.snapshot_at(product, Version("0.9")))
        changes = defaults.changes(product, "recommended@1.0", "recommended@1.1")
        self.assertIn("editorconfig.line-length: 88 → 100", changes)
        self.assertIn("editorconfig.indent: 'space' → None", changes)
        self.assertNotIn("description", " ".join(changes))

    def test_warning_names_the_snapshot_head_ran(self):
        root = Path(self.tmp.name) / "unit"
        (root / ".github" / "workflows").mkdir(parents=True)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        plan = SimpleNamespace(cfg={"profile": "recommended"})
        # No caller workflow at HEAD: nothing to compare.
        self.assertEqual(defaults.warn_changed(self.product, root, plan), [])
        sha = "a" * 40
        (root / ".github" / "workflows" / "ci.yml").write_text(
            f"jobs:\n  lint:\n    uses: example/stub-nix/.github/workflows/stub-lint.yml@{sha} # v1.0.3\n"
        )
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "ci")
        lines = defaults.warn_changed(self.product, root, plan)
        self.assertEqual(
            lines[0],
            'profile = "recommended" now resolves to recommended@1.1 (it was'
            " recommended@1.0 at HEAD); changed defaults:",
        )
        self.assertEqual(
            lines[-1],
            'pin profile = "recommended@1.0" in [tool.stub-nix] to keep the old defaults',
        )
        # Another engine's callers and a pinned profile say nothing.
        self.assertEqual(defaults.head_version(KIT, root), None)
        pinned = SimpleNamespace(cfg={"profile": "recommended@1.0"})
        self.assertEqual(defaults.warn_changed(self.product, root, pinned), [])


ORG_PROFILE = """\
schema = 1
name = "example-org"
extends = "recommended@1.0"

[org]
publisher = "Example Org"

[repo-policy]
enable = true
"""


class TestProfileCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".standards-profile").mkdir()
        (self.root / ".standards-profile" / "profile.toml").write_text(ORG_PROFILE)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "demo_unit"\n\n[tool.stub-nix]\nschema = 1\nmajor = 1\n'
            'profile = "./.standards-profile"\n\n[[tool.stub-nix.untested]]\n'
            'target = "demo_unit.api"\nreason = "kept"\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        return run_cli(STUB, *argv, cwd=self.root)

    def test_profile_list_describes_each(self):
        code, out, _ = self.run_cli("profile", "list")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("minimal "), out)
        self.assertIn("The stub-nix dev shell only", lines[0])
        self.assertTrue(
            lines[1].startswith("recommended@1.0  (recommended)  Lint"), out
        )

    def test_profile_show(self):
        code, out, err = self.run_cli("profile", "show")
        self.assertEqual((code, err), (0, ""))
        doc = tomllib.loads(out)
        self.assertEqual(
            doc["profile"],
            {"name": "example-org", "source": "./.standards-profile", "rev": ""},
        )
        self.assertTrue(doc["modules"]["repo-policy"])
        self.assertEqual(doc["config"]["org"]["publisher"], "Example Org")
        code, out, _ = self.run_cli("profile", "show", "--format", "json")
        doc = json.loads(out)
        self.assertEqual(list(doc), ["profile", "modules", "config"])
        self.assertEqual(list(doc["modules"]), list(STUB.modules.names))

    def test_profile_show_explains(self):
        code, out, err = self.run_cli("profile", "show", "--explain")
        self.assertEqual(code, 0, err)
        self.assertIn('org.publisher = "Example Org"  # org:./.standards-profile', out)
        self.assertIn("repo-policy.enable = true  # org:./.standards-profile", out)
        self.assertIn('python-lint.tool = "ruff"  # builtin:recommended@1.0', out)
        self.assertIn("major = 1  # app", out)
        self.assertIn("tests.setup = []  # default", out)
        # Valid TOML, tables and all (an [[untested]] entry is an inline table).
        doc = tomllib.loads(out)
        self.assertEqual(doc["config"]["org"]["publisher"], "Example Org")
        self.assertTrue(doc["modules"]["repo-policy"])
        self.assertEqual(
            doc["config"]["untested"], [{"target": "demo_unit.api", "reason": "kept"}]
        )
        code, out, _ = self.run_cli("profile", "show", "--explain", "--format", "json")
        doc = json.loads(out)
        self.assertEqual(doc["sources"]["org.publisher"], "org:./.standards-profile")
        self.assertEqual(doc["config"]["org"]["publisher"], "Example Org")
        self.assertTrue(doc["modules"]["repo-policy"])

    def test_profile_needs_a_subcommand(self):
        code, _, err = self.run_cli("profile")
        self.assertEqual(code, 2)
        self.assertIn("<show|validate|list>", err)

    def test_profile_show_not_opted_in(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "demo_unit"\n')
        code, out, err = self.run_cli("profile", "show")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"stub-nix profile: {STUB.identity.opt_in_hint}\n")


if __name__ == "__main__":
    unittest.main()


class TestProfileValidate(unittest.TestCase):
    """``profile validate``: an org profile checked against every fixture checkout."""

    def setUp(self):
        from support.checkouts import write_profile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = write_profile(Path(tmp.name), "org")
        self.file = self.dir / "profile.toml"
        os_env = {"GIT_CONFIG_GLOBAL": "/dev/null"}
        import os
        from unittest import mock

        patch = mock.patch.dict(os.environ, os_env)
        patch.start()
        self.addCleanup(patch.stop)

    def validate(self, *extra):
        return run_cli(STUB, "profile", "validate", str(self.file), *extra)

    def test_a_valid_profile(self):
        code, out, err = self.validate()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, f"{self.file}: valid (extends recommended@1.0)\n")

    def test_a_template_that_does_not_render(self):
        (self.dir / "templates" / "SECURITY.md.j2").write_text("{{ nope }}\n")
        code, _, err = self.validate()
        self.assertEqual(code, 2, err)
        self.assertIn(
            "the plain checkout: the org profile's templates/SECURITY.md.j2 does not"
            " render: UndefinedError",
            err,
        )
        # Rendered by the validation itself where no live entry renders it.
        self.file.write_text(self.file.read_text() + "\n[hygiene]\nenable = false\n")
        code, _, err = self.validate()
        self.assertEqual(code, 2, err)
        self.assertIn(
            "templates/SECURITY.md.j2 does not render for the plain checkout", err
        )

    def test_a_template_that_overrides_nothing(self):
        (self.dir / "templates" / "flake.nix.j2").write_text("{}\n")
        code, _, err = self.validate()
        self.assertEqual(code, 2, err)
        self.assertIn("the plain checkout: the profile's templates/flake.nix.j2", err)

    def test_an_override_renders(self):
        (self.dir / "templates" / "CONTRIBUTING.md.j2").write_text("# {{ name }}\n")
        self.assertEqual(self.validate()[0], 0)

    def test_a_fixture_needing_modules_the_profile_leaves_off_is_skipped(self):
        text = self.file.read_text().replace(
            'extends = "recommended@1.0"', 'extends = "minimal"'
        )
        text = text.split("[[retire]]")[0]
        self.file.write_text(text)
        (self.dir / "templates" / "SECURITY.md.j2").unlink()
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_schema_and_file_errors(self):
        self.file.write_text(self.file.read_text() + "\n[nope]\nx = 1\n")
        self.assertEqual(self.validate()[0], 2)
        self.file.write_text("x = ")
        self.assertEqual(self.validate()[0], 2)
        self.file.unlink()
        self.assertEqual(self.validate()[0], 3)

    def test_the_fixture_syncs_are_throwaway(self):
        import dataclasses

        seen = []
        product = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(
                STUB.hooks,
                notices=lambda plan: seen.append(plan.ctx.options["throwaway"]) or [],
            ),
        )
        code, _, err = run_cli(product, "profile", "validate", str(self.file))
        self.assertEqual(code, 0, err)
        self.assertEqual(seen, [True, True])

    def test_templates_elsewhere(self):
        other = self.dir.parent / "elsewhere"
        other.mkdir()
        (other / "SECURITY.md.j2").write_text("# s\n")
        code, _, err = self.validate("--templates", str(other))
        self.assertEqual(code, 0, err)
