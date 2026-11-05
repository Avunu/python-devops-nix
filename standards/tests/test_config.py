"""Resolution (DESIGN §4.4): layers, merge order, defaults, module rules, the hooks
that take part (``derive``, ``integration_default``, ``host_rules``), and the
``config`` command."""

import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import KIT, STUB, run_cli
from support.checkouts import NestedConfigCase, write_profile

from standards_core.common import config
from standards_core.common.report import ConfigError, EnvError
from standards_core.scaffold.context import Checkout

PYPROJECT = """\
[project]
name = "demo_unit"

[tool.stub-nix]
schema = 1
major = 1
profile = "recommended"
nightly-suites = ["node scripts/test-tables.ts", "echo two"]

[tool.stub-nix.repo-policy]
enable = true

[tool.stub-nix.hygiene]
enable = false

[[tool.stub-nix.untested]]
target = "demo_unit.api.legacy"
reason = "Kept for old kiosk bundles"
"""

# An org profile layer for the merge-order tests: fictitious values only.
ORG_PROFILE = """\
schema = 1
name = "example-org"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
license = "MIT"

[python-lint]
line-length = 100
ignore = ["E501", "E731"]

[tests.coverage]
target = 90

[[retire]]
paths = [".github/workflows/check.yml"]
module = "ci"
"""


def app(**table):
    return {
        "project": {"name": "demo_unit"},
        "tool": {"stub-nix": {"schema": 1, "major": 1, **table}},
    }


def code_of(fn):
    try:
        fn()
    except (ConfigError, EnvError) as e:
        return e.code, str(e)
    return 0, ""


def with_hooks(product, **hooks):
    return dataclasses.replace(
        product, hooks=dataclasses.replace(product.hooks, **hooks)
    )


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        profile = self.root / ".standards-profile"
        profile.mkdir()
        (profile / "profile.toml").write_text(ORG_PROFILE)

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, product=STUB, **table):
        return config.resolve_doc(product, app(**table), root=self.root)

    def checkout(self, doc):
        return Checkout(root=self.root, name="demo_unit", config=doc, tracked=[])

    def test_not_opted_in(self):
        with self.assertRaises(config.NotOptedIn) as ctx:
            config.resolve_doc(STUB, {"project": {"name": "demo_unit"}}, root=self.root)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--standards", str(ctx.exception))
        self.assertEqual(str(ctx.exception), STUB.identity.opt_in_hint)

    def test_absent_profile_is_minimal(self):
        r = self.resolve()
        self.assertEqual(r.profile, {"name": "minimal", "source": "builtin", "rev": ""})
        self.assertEqual([m for m, on in r.modules.items() if on], ["dev-shell"])
        # A module turned on over minimal gets the schema defaults: the recommended values.
        r = self.resolve(**{"python-lint": {"enable": True}})
        self.assertTrue(r.modules["python-lint"])
        self.assertEqual(r.cfg["python-lint"]["select"][:3], ["F", "E", "W"])

    def test_recommended_is_the_newest_snapshot(self):
        plain = self.resolve(profile="recommended")
        pinned = self.resolve(profile="recommended@1.0")
        self.assertEqual(plain.cfg | {"profile": None}, pinned.cfg | {"profile": None})
        self.assertEqual(plain.modules, pinned.modules)
        self.assertEqual(plain.profile, pinned.profile)
        self.assertEqual(plain.profile["name"], "recommended@1.0")

    def test_unknown_snapshot_is_exit_2(self):
        code, message = code_of(lambda: self.resolve(profile="recommended@9.9"))
        self.assertEqual(code, 2)
        self.assertIn("recommended@9.9", message)

    def test_merge_order(self):
        r = self.resolve(
            profile="./.standards-profile",
            **{
                "python-lint": {"line-length": 120, "select": ["E"]},
                "tests": {"coverage": {"omit": ["demo_unit/vendor/*"]}},
            },
        )
        # A value set in all three layers resolves to the checkout's.
        self.assertEqual(r.cfg["python-lint"]["line-length"], 120)
        self.assertEqual(r.source("python-lint.line-length"), "app")
        # A table merges key by key: the org's target, the checkout's omit, the
        # built-in's floor.
        self.assertEqual(
            r.cfg["tests"]["coverage"],
            {
                "enable": True,
                "target": 90,
                "initial-floor": 0,
                "omit": ["demo_unit/vendor/*"],
            },
        )
        self.assertEqual(r.source("tests.coverage.target"), "org:./.standards-profile")
        self.assertEqual(
            r.source("tests.coverage.initial-floor"), "builtin:recommended@1.0"
        )
        # An array replaces: the checkout's select, the org's ignore.
        self.assertEqual(r.cfg["python-lint"]["select"], ["E"])
        self.assertEqual(r.cfg["python-lint"]["ignore"], ["E501", "E731"])
        # Org values, the copyright fallback, and profile-only lists from the org profile.
        self.assertEqual(
            (r.cfg["org"]["publisher"], r.cfg["org"]["copyright-holder"]),
            ("Example Org", "Example Org"),
        )
        self.assertEqual(
            r.cfg["retire"],
            [{"paths": [".github/workflows/check.yml"], "module": "ci"}],
        )
        self.assertEqual(r.cfg["replace-units"], [])
        self.assertEqual(
            r.profile,
            {"name": "example-org", "source": "./.standards-profile", "rev": ""},
        )

    def test_defaults_are_marked(self):
        r = self.resolve(profile="recommended")
        self.assertEqual(r.source("tests.setup"), "default")
        self.assertEqual(r.cfg["tests"]["setup"], [])
        self.assertEqual(r.cfg["integration-branch"], "develop")
        r = self.resolve(profile="recommended", releases={"branching": "main+tags"})
        self.assertEqual(r.cfg["integration-branch"], "main")
        r = self.resolve(profile="recommended", **{"integration-branch": "trunk"})
        self.assertEqual(r.cfg["integration-branch"], "trunk")

    def test_derive_runs_between_the_integration_branch_and_repo(self):
        """The product's derived defaults land where the framework product's `site`
        does: after integration-branch, before repo (so cfg's key order is the same)."""
        doc = app(profile="recommended", org={"github-owner": "example"})
        r = config.resolve_doc(STUB, doc, checkout=self.checkout(doc))
        self.assertEqual(r.cfg["site"], "demo-unit.localhost")
        keys = list(r.cfg)
        self.assertLess(keys.index("integration-branch"), keys.index("site"))
        self.assertLess(keys.index("site"), keys.index("repo"))
        self.assertEqual(r.cfg["repo"], "example/demo_unit")
        # The checkout's own value is kept; with no checkout there is nothing to derive.
        doc = app(site="x.localhost")
        self.assertEqual(
            config.resolve_doc(STUB, doc, checkout=self.checkout(doc)).cfg["site"],
            "x.localhost",
        )
        self.assertNotIn("site", self.resolve().cfg)
        seen = []
        product = with_hooks(STUB, derive=lambda cfg, checkout: seen.append(checkout))
        config.resolve_doc(product, app(), root=self.root)
        self.assertEqual(seen, [None])

    def test_integration_default_hook(self):
        product = with_hooks(
            STUB, integration_default=lambda cfg: f"v{cfg['major']}-dev"
        )
        self.assertEqual(self.resolve(product).cfg["integration-branch"], "v1-dev")
        r = self.resolve(product, **{"integration-branch": "trunk"})
        self.assertEqual(r.cfg["integration-branch"], "trunk")

    def test_app_only_key_in_a_profile_is_exit_2(self):
        for text in (
            "major = 1\n" + ORG_PROFILE,
            ORG_PROFILE + "[extras]\nbrowser = false\n",
            ORG_PROFILE + '[dev-shell]\nsystems = ["x86_64-linux"]\n',
        ):
            with self.subTest(text=text[-40:]):
                (self.root / ".standards-profile" / "profile.toml").write_text(text)
                code, message = code_of(
                    lambda: self.resolve(profile="./.standards-profile")
                )
                self.assertEqual(code, 2)
                self.assertIn("app-only key", message)

    def test_profile_only_key_in_the_app_is_exit_2(self):
        for table in (
            {"extends": "recommended"},
            {"retire": []},
            {"name": "x"},
            {"replace-units": []},
            {"requires-stub-nix": ">=1"},
        ):
            with self.subTest(table=table):
                code, message = code_of(lambda t=table: self.resolve(**t))
                self.assertEqual(code, 2)
                self.assertIn("profile-only key", message)

    def test_unmet_module_dependency_names_both(self):
        code, message = code_of(
            lambda: self.resolve(profile="recommended", commits={"enable": False})
        )
        self.assertEqual(code, 2)
        self.assertIn("releases", message)
        self.assertIn("commits", message)
        code, message = code_of(lambda: self.resolve(extras={"enable": True}))
        self.assertEqual(code, 2)
        self.assertIn("extras needs module commits", message)
        # tool = "none" is the same as enable = false, needs included.
        code, message = code_of(
            lambda: self.resolve(
                profile="recommended",
                commits={"tool": "none"},
                releases={"enable": False},
                extras={"enable": False},
            )
        )
        self.assertEqual(code, 0, message)
        r = self.resolve(profile="recommended", **{"python-lint": {"tool": "none"}})
        self.assertFalse(r.modules["python-lint"])

    def test_provisional_leaves_needs_to_the_profile(self):
        """Phase A over minimal, before the org profile is locked: a need minimal leaves
        off may be one the profile turns on, so it is checked by the full resolution."""
        extras = {"extras": {"enable": True}}
        code, message = code_of(lambda: self.resolve(**extras))
        self.assertEqual(code, 2)
        self.assertIn("extras needs module commits", message)
        r = config.resolve_doc(STUB, app(**extras), root=self.root, provisional=True)
        self.assertTrue(r.modules["extras"])
        self.assertFalse(r.modules["commits"])
        # A need the checkout's own table turns off is the checkout's.
        code, message = code_of(
            lambda: config.resolve_doc(
                STUB,
                app(**extras, commits={"enable": False}),
                root=self.root,
                provisional=True,
            )
        )
        self.assertEqual(code, 2)
        self.assertIn("extras needs module commits", message)
        # tool = "none" turns it off the same way.
        releases = {"releases": {"enable": True}, "commits": {"tool": "none"}}
        code, message = code_of(
            lambda: config.resolve_doc(
                STUB, app(**releases), root=self.root, provisional=True
            )
        )
        self.assertEqual(code, 2)
        self.assertIn("releases needs module commits", message)

    def test_required_module(self):
        code, message = code_of(
            lambda: self.resolve(**{"dev-shell": {"enable": False}})
        )
        self.assertEqual(code, 2)
        self.assertIn("module dev-shell is required", message)
        product = dataclasses.replace(
            STUB, modules=dataclasses.replace(STUB.modules, required="hygiene")
        )
        code, message = code_of(lambda: self.resolve(product))
        self.assertIn("module hygiene is required", message)
        self.assertTrue(
            self.resolve(product, hygiene={"enable": True}).modules["hygiene"]
        )

    def test_schema_errors_are_exit_2(self):
        for table in (
            {"nightly-suites": [1]},
            {"major": "1"},
            {"python-lint": {"tool": "black"}},
            {"releases": {"branching": "develop+series"}},
            {"nope": True},
        ):
            with self.subTest(table=table):
                self.assertEqual(code_of(lambda t=table: self.resolve(**t))[0], 2)
        no_major = app()
        del no_major["tool"]["stub-nix"]["major"]
        self.assertEqual(
            code_of(lambda: config.resolve_doc(STUB, no_major, root=self.root))[0], 2
        )

    def test_github_only_modules_off_on_other_hosts(self):
        r = config.resolve_doc(
            STUB,
            app(profile="recommended", repo="gitlab.example.org/group/demo_unit"),
            root=self.root,
        )
        self.assertEqual(r.repo_host, "gitlab.example.org")
        self.assertEqual(r.cfg["repo"], "group/demo_unit")
        for m in STUB.modules.github_only:
            self.assertFalse(r.modules[m], m)
        self.assertTrue(r.modules["commits"] and r.modules["python-lint"])
        self.assertEqual(
            r.notices,
            [
                (
                    "repository is not on GitHub: GitHub-only modules off: ci,"
                    " dependabot, extras, releases"
                )
            ],
        )
        code, message = code_of(
            lambda: config.resolve_doc(
                STUB,
                app(
                    profile="recommended",
                    repo="gitlab.example.org/g/a",
                    ci={"enable": True},
                ),
                root=self.root,
            )
        )
        self.assertEqual(code, 2)
        self.assertIn("GitHub-only", message)

    def test_host_rules_hook(self):
        """The product's off-GitHub rules: they see the host answer, may refuse, and may
        turn a parameter off."""
        calls = []

        def rules(cfg, table, on_github, notices):
            calls.append(on_github)
            if not on_github and table.get("extras", {}).get("browser") is True:
                raise ConfigError("extras.browser is GitHub-only")
            if not on_github:
                cfg["extras"]["browser"] = False
                notices.append("extras.browser off")

        product = with_hooks(STUB, host_rules=rules)
        r = self.resolve(product, profile="recommended")
        self.assertTrue(r.cfg["extras"]["browser"])
        r = self.resolve(product, profile="recommended", repo="gitlab.com/g/demo_unit")
        self.assertFalse(r.cfg["extras"]["browser"])
        self.assertEqual(r.notices[0], "extras.browser off")
        self.assertEqual(calls, [True, False])
        code, message = code_of(
            lambda: self.resolve(
                product, repo="gitlab.com/g/u", extras={"browser": True}
            )
        )
        self.assertEqual((code, message), (2, "extras.browser is GitHub-only"))

    def test_repo_from_origin_and_owner(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.assertNotIn("repo", self.resolve().cfg)
        self.assertEqual(
            self.resolve(org={"github-owner": "example"}).cfg["repo"],
            "example/demo_unit",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "remote",
                "add",
                "origin",
                "git@gitlab.com:group/sub/demo_unit.git",
            ],
            check=True,
        )
        r = self.resolve(profile="recommended")
        self.assertEqual(
            (r.repo_host, r.cfg["repo"]), ("gitlab.com", "group/sub/demo_unit")
        )
        self.assertFalse(r.modules["ci"])

    def test_requires(self):
        profile = self.root / ".standards-profile" / "profile.toml"
        # One major past this product, so the range is never met.
        unmet = f">={int(STUB.identity.version.split('.')[0]) + 1}"
        line = 'extends = "recommended@1.0"\n'
        profile.write_text(
            ORG_PROFILE.replace(line, f'{line}requires-stub-nix = "{unmet}"\n')
        )
        code, message = code_of(lambda: self.resolve(profile="./.standards-profile"))
        self.assertEqual(code, 3)
        self.assertIn("requires stub-nix", message)
        self.assertIn("stub-nix-tools 1.0.0", message)
        profile.write_text(
            ORG_PROFILE.replace(line, f'{line}requires-stub-nix = ">=0"\n')
        )
        self.assertEqual(
            code_of(lambda: self.resolve(profile="./.standards-profile"))[0], 0
        )
        profile.write_text(
            ORG_PROFILE.replace(line, f'{line}requires-stub-nix = "~~1"\n')
        )
        code, message = code_of(lambda: self.resolve(profile="./.standards-profile"))
        self.assertEqual(code, 2)
        self.assertIn("is not a version specifier", message)

    def test_org_profile_rules(self):
        profile = self.root / ".standards-profile" / "profile.toml"
        profile.write_text(ORG_PROFILE.replace('extends = "recommended@1.0"\n', ""))
        code, message = code_of(lambda: self.resolve(profile="./.standards-profile"))
        self.assertIn("extends is required", message)
        self.assertEqual(code_of(lambda: self.resolve(profile="./missing"))[0], 2)
        self.assertEqual(code_of(lambda: self.resolve(profile="./../outside"))[0], 2)
        # A flake URL is read from the locked standards-profile input, which must exist.
        (self.root / "flake.lock").write_text(
            json.dumps({"version": 7, "root": "root", "nodes": {"root": {}}})
        )
        code, message = code_of(
            lambda: self.resolve(profile="github:example/profile/v1")
        )
        self.assertEqual(code, 2)
        self.assertIn("not locked", message)
        self.assertIn("run stub-init --sync", message)

    def test_an_in_repo_profile_is_never_read_through_a_link(self):
        (self.root / "elsewhere").mkdir()
        (self.root / "elsewhere" / "profile.toml").write_text(ORG_PROFILE)
        (self.root / ".lnk").symlink_to(self.root / "elsewhere")
        code, message = code_of(lambda: self.resolve(profile="./.lnk"))
        self.assertEqual(code, 2)
        self.assertIn(".lnk is a symlink", message)

    def test_profile_dir_override(self):
        r = config.resolve_doc(
            STUB,
            app(profile="github:example/profile/v1"),
            root=self.root,
            profile_dir=self.root / ".standards-profile",
        )
        self.assertEqual(r.profile["source"], "github:example/profile/v1")
        self.assertEqual(r.cfg["org"]["publisher"], "Example Org")

    def test_get(self):
        r = self.resolve(profile="recommended")
        self.assertIs(r.get("modules.repo-policy"), False)
        self.assertEqual(r.get("tests.coverage.target"), 80)
        self.assertEqual(r.get("profile.name"), "recommended@1.0")
        self.assertIsNone(r.get("releases.bootstrap-sha"))
        self.assertEqual(r.get("replace-units"), [])
        for key in (
            "nonsense",
            "tests.nonsense",
            "modules.nonsense",
            "profile.nonsense",
            "replace-units.from",
        ):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                r.get(key)

    def test_kit_derives_the_series(self):
        doc = {"tool": {"kit-nix": {"schema": 1, "profile": "recommended"}}}
        (self.root / ".kit-series").write_text("18.0\n")
        checkout = Checkout(self.root, "bundle", doc, [".kit-series"])
        r = config.resolve_doc(KIT, doc, checkout=checkout)
        self.assertEqual(r.cfg["series"], "18.0")
        self.assertEqual(r.cfg["releases"]["branching"], "develop+series")
        self.assertEqual(r.cfg["integration-branch"], "develop")
        doc["tool"]["kit-nix"]["series"] = "17.0"
        self.assertEqual(
            config.resolve_doc(KIT, doc, checkout=checkout).cfg["series"], "17.0"
        )


class TestConfigCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pyproject.toml"
        self.path.write_text(PYPROJECT)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, *args):
        return run_cli(STUB, "config", *args, "--pyproject", str(self.path))

    def test_modules_print_the_resolved_boolean(self):
        self.assertEqual(self.config("modules.repo-policy"), (0, "true\n", ""))
        self.assertEqual(self.config("modules.hygiene"), (0, "false\n", ""))

    def test_list_one_item_per_line(self):
        self.assertEqual(
            self.config("nightly-suites"),
            (0, "node scripts/test-tables.ts\necho two\n", ""),
        )

    def test_scalars_defaults_and_unset(self):
        self.assertEqual(self.config("major")[1], "1\n")
        self.assertEqual(self.config("tests.coverage.target")[1], "80\n")
        self.assertEqual(self.config("shell-checks"), (0, "", ""))
        self.assertEqual(self.config("releases.bootstrap-sha"), (0, "", ""))
        self.assertEqual(self.config("extras.browser")[1], "true\n")

    def test_tables_print_as_json(self):
        code, out, _ = self.config("untested")
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out),
            {"target": "demo_unit.api.legacy", "reason": "Kept for old kiosk bundles"},
        )
        code, out, _ = self.config("python-lint.ignore", "--json")
        self.assertEqual(json.loads(out), ["E501"])

    def test_github_output(self):
        out = Path(self.tmp.name) / "out"
        out.write_text("before=1\n")
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
            self.assertEqual(self.config("--github-output", "modules")[0], 0)
        lines = out.read_text().splitlines()
        self.assertEqual(lines[0], "before=1")
        self.assertEqual(len(lines), 1 + len(STUB.modules.names))
        self.assertIn("repo-policy=true", lines)
        self.assertIn("hygiene=false", lines)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_OUTPUT", None)
            self.assertEqual(self.config("--github-output", "modules")[0], 3)
        self.assertEqual(self.config("--github-output", "cfg")[0], 2)

    def test_unknown_key_exits_2(self):
        code, _, err = self.config("nope")
        self.assertEqual(code, 2)
        self.assertIn("no key 'nope'", err)
        self.assertIn("stub-nix config:", err)
        self.assertEqual(self.config()[0], 2)

    def test_not_opted_in_exits_2(self):
        self.path.write_text('[project]\nname = "demo_unit"\n')
        code, _, err = self.config("modules.ci")
        self.assertEqual(code, 2)
        self.assertIn("has not opted in", err)

    def test_missing_config_file(self):
        self.path.unlink()
        self.assertEqual(self.config("site")[0], 3)
        # A product whose config file is optional reads it as not opted in.
        code, _, err = run_cli(KIT, "config", "series", "--pyproject", str(self.path))
        self.assertEqual(code, 2)
        self.assertIn(KIT.identity.opt_in_hint, err)

    def test_default_is_the_nearest_config_file_in_the_work_tree(self):
        outer = Path(self.tmp.name)
        subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
        unit = outer / "tests" / "fixtures" / "unit"
        (unit / "pkg").mkdir(parents=True)
        self.path.rename(unit / "pyproject.toml")
        for cwd in (unit, unit / "pkg"):
            self.assertEqual(run_cli(STUB, "config", "major", cwd=cwd), (0, "1\n", ""))
        # Nothing at or above tests/ up to the root: the root's, which does not exist.
        self.assertEqual(run_cli(STUB, "config", "major", cwd=outer / "tests")[0], 3)

    def test_the_checkout_feeds_derive(self):
        """In a git checkout, the command resolves with it: the derived site appears."""
        root = Path(self.tmp.name)
        self.assertEqual(self.config("site"), (0, "", ""))
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        self.assertEqual(self.config("site"), (0, "demo-unit.localhost\n", ""))

    def test_unreadable_config_file_exits_3(self):
        self.assertEqual(
            run_cli(STUB, "config", "site", "--pyproject", self.tmp.name)[0], 3
        )

    def test_profile_lists_print(self):
        # retire, extra-files and the product's replace-units are the org profile's
        # alone, so the app schema lacks them, but they are resolved values all the same.
        profile = Path(self.tmp.name) / "prof"
        profile.mkdir()
        (profile / "profile.toml").write_text(
            ORG_PROFILE + '\n[[replace-units]]\nfrom = "old_unit"\nto = "new_unit"\n'
        )
        self.path.write_text(
            PYPROJECT.replace('profile = "recommended"', 'profile = "./prof"')
        )
        code, out, err = self.config("replace-units", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), [{"from": "old_unit", "to": "new_unit"}])
        code, out, _ = self.config("retire", "--json")
        self.assertEqual(
            json.loads(out),
            [{"paths": [".github/workflows/check.yml"], "module": "ci"}],
        )
        self.assertEqual(self.config("extra-files", "--json"), (0, "[]\n", ""))
        self.assertEqual(self.config("replace-units.from")[0], 2)
        # A built-in profile has none of them.
        self.path.write_text(PYPROJECT)
        self.assertEqual(self.config("replace-units", "--json"), (0, "[]\n", ""))

    def test_notices_go_to_stderr(self):
        text = PYPROJECT.replace("[tool.stub-nix.repo-policy]\nenable = true\n", "")
        self.path.write_text(
            text.replace("major = 1\n", 'major = 1\nrepo = "gitlab.com/g/demo_unit"\n')
        )
        code, out, err = self.config("modules.ci")
        self.assertEqual((code, out), (0, "false\n"))
        self.assertIn("notice: repository is not on GitHub", err)


class TestNestedConfigCommand(NestedConfigCase):
    """``identity.config_file = "sub/pyproject.toml"``: ``config`` and ``profile show``
    resolve the checkout at the work tree's root, with its ``flake.lock``, from the root
    and from ``sub/`` (§15.CR1)."""

    def test_from_the_root_and_from_below_it(self):
        root = self.root.resolve()
        for cwd in (self.root, self.root / "sub"):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.fn("config", "major", cwd=cwd), (0, "16\n", ""))
                code, out, err = self.fn("profile", "show", "--format", "json", cwd=cwd)
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["config"]["major"], 16)
        # --pyproject names the file; the root is the directory whose config file it is.
        code, out, err = self.fn(
            "config", "repo", "--pyproject", str(root / "sub/pyproject.toml"), cwd=root
        )
        self.assertEqual(code, 0, err)
        # An org profile is read from the root's flake.lock, never one beside the file.
        self.write(
            "sub/pyproject.toml",
            self.read("sub/pyproject.toml").replace(
                'profile = "recommended"', 'profile = "github:example/profile"'
            ),
        )
        self.write(
            "sub/flake.lock", '{"version": 7, "root": "root", "nodes": {"root": {}}}'
        )
        for cwd in (self.root, self.root / "sub"):
            with self.subTest(cwd=cwd):
                code, _, err = self.fn("config", "major", cwd=cwd)
                self.assertEqual(code, 3, err)
                self.assertIn(f"{root / 'flake.lock'} does not exist", err)
                code, _, err = self.fn(
                    "config",
                    "major",
                    "--pyproject",
                    str(root / "sub/pyproject.toml"),
                    cwd=cwd,
                )
                self.assertIn(f"{root / 'flake.lock'} does not exist", err)

    def test_the_help_names_the_existing_file_rule(self):
        """One config file with a directory part and no hook: the root is the nearest
        directory where that file exists, opted in or not."""
        _, out, _ = self.fn("config", "--help")
        out = " ".join(out.split())
        self.assertIn("one where sub/pyproject.toml exists", out)
        self.assertNotIn("opens the table", out)

    def test_resolve_takes_the_root(self):
        path = self.root / "sub/pyproject.toml"
        resolved = config.resolve(self.product, path, root=self.root)
        self.assertEqual(resolved.cfg["major"], 16)
        # The default root is the file's directory (a one-file product's root).
        self.write("sub/.standards-profile/profile.toml", "")
        text = self.read("sub/pyproject.toml").replace(
            'profile = "recommended"', 'profile = "./.standards-profile"'
        )
        self.write("sub/pyproject.toml", text)
        with self.assertRaises(ConfigError):
            config.resolve(self.product, path)  # sub/.standards-profile: empty, invalid
        with self.assertRaises(ConfigError):
            config.resolve(
                self.product, path, root=self.root
            )  # ./.standards-profile missing
        write_profile(self.root)
        self.assertEqual(
            config.resolve(self.product, path, root=self.root).profile["name"],
            "example-org",
        )


# The spellings of the table the dev shell's line match and TOML disagree on, beside the
# ones they agree on. None: opted in; otherwise the error the config command prints.
TABLE = "schema = 1\nmajor = 1\n"
SPELLINGS = {
    "header": ("[tool.stub-nix]\n" + TABLE, None),
    "indented": ("  [tool.stub-nix]\n" + TABLE, None),
    "subtable only": (
        '[[tool.stub-nix.untested]]\ntarget = "a.b"\nreason = "r"\n',
        "schema",
    ),
    "quoted key": (
        '[tool."stub-nix"]\n' + TABLE,
        "write the table as a [tool.stub-nix] header",
    ),
    "spaces in brackets": (
        "[ tool.stub-nix ]\n" + TABLE,
        "write the table as a [tool.stub-nix] header",
    ),
    "dotted keys": (
        "[tool]\nstub-nix.schema = 1\nstub-nix.major = 1\n",
        "write the table as a [tool.stub-nix] header",
    ),
    "inline table": (
        "[tool]\nstub-nix = { schema = 1, major = 1 }\n",
        "write the table as a [tool.stub-nix] header",
    ),
    "in a string": (
        '[tool.notes]\ntext = """\n[tool.stub-nix]\n"""\n',
        "inside a multi-line string",
    ),
    "commented": ("# [tool.stub-nix]\n", "has not opted in"),
}


class TestOptInSpelling(unittest.TestCase):
    def test_the_shell_and_the_tools_agree_or_it_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            for name, (table, error) in SPELLINGS.items():
                with self.subTest(name):
                    path.write_text('[project]\nname = "demo_unit"\n\n' + table)
                    code, out, err = run_cli(
                        STUB, "config", "major", "--pyproject", str(path)
                    )
                    if error is None:
                        self.assertEqual((code, out, err), (0, "1\n", ""))
                    else:
                        self.assertEqual(code, 2)
                        self.assertIn(error, err)

    def test_line_endings_reach_toml_as_written(self):
        # Nix splits the raw text on \n: CRLF lines still match the header, but a file
        # ending its lines with a lone \r is one line the match never sees. TOML refuses
        # a lone \r, so the tools must see it too rather than a translated newline.
        text = '[project]\nname = "demo_unit"\n\n[tool.stub-nix]\n' + TABLE
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_bytes(text.replace("\n", "\r\n").encode())
            self.assertEqual(
                run_cli(STUB, "config", "major", "--pyproject", str(path)),
                (0, "1\n", ""),
            )
            path.write_bytes(text.replace("\n", "\r").encode())
            code, _, err = run_cli(STUB, "config", "major", "--pyproject", str(path))
            self.assertEqual(code, 2)
            self.assertIn("Expected newline or end of document", err)


if __name__ == "__main__":
    unittest.main()
