"""repo-policy/ (DESIGN §7.3): the shipped JSON renders to rulesets GitHub's API takes,
for the stub's tags flow and the kit's post-merge-bump flow."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from support import KIT, STUB, product_copy

from standards_core.common import config, data, repo_policy, schema
from standards_core.common.report import ConfigError

ALL_GATES = ["pr-policy", "lint", "test"]
APP = 987654
ACTIONS = [{"actor_type": "Integration", "actor_id": 15368, "bypass_mode": "always"}]


def cfg(product=STUB, **over) -> tuple[dict, dict]:
    table = {"schema": 1, "profile": "recommended", "repo": "example/demo_unit"}
    table.update({"major": 16} if product is STUB else {"series": "18.0"})
    table.update(over)
    doc = {"tool": {product.identity.table: table}}
    r = config.resolve_doc(product, doc, on_github=True)
    return r.cfg, r.modules


def contexts(policy: repo_policy.Policy) -> list[str]:
    for rs in policy.rulesets:
        for rule in rs["rules"]:
            if rule["type"] == "required_status_checks":
                return [
                    c["context"] for c in rule["parameters"]["required_status_checks"]
                ]
    return []


def core_dir() -> Path:
    return data.product_roots(STUB).roots[1] / "repo-policy"


class TestRepoPolicy(unittest.TestCase):
    def validate(self, product, ruleset: dict) -> None:
        validator = schema.Validator(
            product, "github-ruleset.schema.json", ruleset["name"]
        )
        self.assertEqual(validator.errors(ruleset), [])

    def test_shipped_files_are_json(self):
        directory = core_dir()
        for path in [
            directory / "repo-settings.json",
            *sorted((directory / "rulesets").glob("*.json")),
        ]:
            json.loads(path.read_text())
        self.assertEqual(
            sorted(p.name for p in (directory / "rulesets").glob("*.json")),
            [
                "integration-provisional.json",
                "integration.json",
                "release-branch.json",
                "tags.json",
            ],
        )
        settings = json.loads((directory / "repo-settings.json").read_text())
        self.assertEqual(
            [lb["name"] for lb in settings["labels"]], ["dependencies", "needs-review"]
        )

    def test_self_policy(self):
        """The stub engine repository's own repo-policy/self: concrete, and valid for
        the API."""
        policy = repo_policy.from_dir(repo_policy.self_dir(STUB))
        self.assertEqual(
            [rs["name"] for rs in policy.rulesets],
            ["main", "release-* (release automation only)", "v* tags"],
        )
        for rs in policy.rulesets:
            self.validate(STUB, rs)
        self.assertEqual(contexts(policy), ["lint", "flake", "pr-policy"])
        self.assertEqual(policy.repository["default_branch"], "main")
        self.assertEqual(policy.labels, [])
        main, release, tags = policy.rulesets
        pr = next(r for r in main["rules"] if r["type"] == "pull_request")
        self.assertEqual(pr["parameters"]["required_approving_review_count"], 0)
        self.assertEqual(
            release["bypass_actors"], repo_policy.bypass_actors("github-actions")
        )
        # The same rules as the core defaults they derive from.
        default = core_dir() / "rulesets"
        for mine, theirs in ((release, "release-branch.json"), (tags, "tags.json")):
            self.assertEqual(
                mine["rules"], json.loads((default / theirs).read_text())["rules"]
            )

    def test_no_self_policy_is_exit_2(self):
        with self.assertRaises(ConfigError) as caught:
            repo_policy.self_dir(KIT)
        self.assertIn("kit-nix has no repo-policy/self/", str(caught.exception))

    def test_full_rulesets_validate_against_the_api_schema(self):
        c, m = cfg()
        policy = repo_policy.for_target(
            STUB, c, m, ALL_GATES, phase="full", release_branch="version-16"
        )
        self.assertEqual(
            [rs["name"] for rs in policy.rulesets],
            ["develop", "version-* (release automation only)", "v* tags"],
        )
        self.assertEqual(
            policy.rulesets[1]["conditions"]["ref_name"]["include"],
            ["refs/heads/version-*"],
        )
        for rs in policy.rulesets:
            self.validate(STUB, rs)
        provisional = repo_policy.for_target(
            STUB, c, m, ALL_GATES, phase="provisional", release_branch="version-16"
        )
        self.assertEqual(len(provisional.rulesets), 1)
        self.validate(STUB, provisional.rulesets[0])
        self.assertEqual(contexts(provisional), [])

    def test_tags_flow_renders_the_integration_ruleset_without_bypass(self):
        c, m = cfg(**{"repo-policy": {"release-bypass": f"integration:{APP}"}})
        policy = repo_policy.for_target(
            STUB, c, m, ALL_GATES, phase="full", release_branch="version-16"
        )
        integration = policy.rulesets[0]
        self.assertEqual(integration["bypass_actors"], [])
        rule = next(
            r for r in integration["rules"] if r["type"] == "required_status_checks"
        )
        self.assertIs(rule["parameters"]["strict_required_status_checks_policy"], False)
        # The release branch takes the App, the form the core adds.
        self.assertEqual(
            policy.rulesets[1]["bypass_actors"],
            [{"actor_type": "Integration", "actor_id": APP, "bypass_mode": "always"}],
        )

    def test_post_merge_bump_lets_the_app_past_the_integration_ruleset(self):
        c, m = cfg(KIT, **{"repo-policy": {"release-bypass": f"integration:{APP}"}})
        app = [{"actor_type": "Integration", "actor_id": APP, "bypass_mode": "always"}]
        policy = repo_policy.for_target(
            KIT, c, m, ALL_GATES, phase="full", release_branch="18.0"
        )
        self.assertEqual(
            [rs["name"] for rs in policy.rulesets],
            ["develop", "*.0 (release automation only)"],
        )
        integration, release = policy.rulesets
        self.assertEqual(integration["bypass_actors"], app)
        self.assertEqual(release["bypass_actors"], app)
        self.assertEqual(
            release["conditions"]["ref_name"]["include"], ["refs/heads/*.0"]
        )
        for rs in policy.rulesets:
            self.validate(KIT, rs)
        # Under the default bypass, GitHub Actions.
        c, m = cfg(KIT)
        policy = repo_policy.for_target(
            KIT, c, m, ALL_GATES, phase="full", release_branch="18.0"
        )
        self.assertEqual(policy.rulesets[0]["bypass_actors"], ACTIONS)

    def test_no_integration_bypass_without_a_release_branch(self):
        """§15.CR3: no release branch, no bump push, so nothing bypasses the integration
        ruleset."""
        c, m = cfg(KIT, **{"repo-policy": {"release-bypass": f"integration:{APP}"}})
        policy = repo_policy.for_target(
            KIT, c, m, ALL_GATES, phase="full", release_branch=""
        )
        self.assertEqual([rs["name"] for rs in policy.rulesets], ["develop"])
        self.assertEqual(policy.rulesets[0]["bypass_actors"], [])
        # Nor with releases off.
        c, m = cfg(KIT, releases={"enable": False})
        vals = repo_policy.values(KIT, c, m, ALL_GATES, release_branch="18.0")
        self.assertEqual(vals["integration_bypass"], [])

    def test_bypass_actor_forms(self):
        self.assertEqual(repo_policy.bypass_actors("github-actions"), ACTIONS)
        self.assertEqual(
            repo_policy.bypass_actors("user:42"),
            [{"actor_type": "User", "actor_id": 42, "bypass_mode": "always"}],
        )
        self.assertEqual(
            repo_policy.bypass_actors("integration:7"),
            [{"actor_type": "Integration", "actor_id": 7, "bypass_mode": "always"}],
        )
        with self.assertRaises(ConfigError) as caught:
            repo_policy.bypass_actors("team:3")
        for form in ('"github-actions"', '"user:<id>"', '"integration:<id>"'):
            self.assertIn(form, str(caught.exception))

    def test_release_pattern_values(self):
        c, m = cfg()
        vals = repo_policy.values(STUB, c, m, ALL_GATES)
        self.assertEqual(
            (vals["release_pattern"], vals["release_label"]),
            ("refs/heads/version-*", "version-*"),
        )
        c, m = cfg(KIT)
        vals = repo_policy.values(KIT, c, m, ALL_GATES)
        self.assertEqual(
            (vals["release_pattern"], vals["release_label"]), ("refs/heads/*.0", "*.0")
        )

    def test_required_checks_are_the_enabled_gates_in_order(self):
        c, m = cfg()
        policy = repo_policy.for_target(
            STUB, c, m, list(reversed(ALL_GATES)), phase="full", release_branch=""
        )
        self.assertEqual(contexts(policy), [f"ci / {g}" for g in ALL_GATES])
        rule = next(
            r
            for r in policy.rulesets[0]["rules"]
            if r["type"] == "required_status_checks"
        )
        self.assertTrue(
            all(
                ch["integration_id"] == 15368
                for ch in rule["parameters"]["required_status_checks"]
            )
        )
        two = repo_policy.for_target(
            STUB, c, m, ["lint", "test"], phase="full", release_branch=""
        )
        self.assertEqual(contexts(two), ["ci / lint", "ci / test"])
        none = repo_policy.for_target(STUB, c, m, [], phase="full", release_branch="")
        self.assertNotIn(
            "required_status_checks", [r["type"] for r in none.rulesets[0]["rules"]]
        )

    def test_check_context_comes_from_the_product(self):
        product = dataclasses.replace(
            STUB, ci=dataclasses.replace(STUB.ci, check_context="checks / {gate}")
        )
        c, m = cfg()
        policy = repo_policy.for_target(
            product, c, m, ["lint"], phase="full", release_branch=""
        )
        self.assertEqual(contexts(policy), ["checks / lint"])

    def test_explicit_required_checks_must_be_enabled_gates(self):
        c, m = cfg(**{"repo-policy": {"required-checks": ["lint"]}})
        policy = repo_policy.for_target(
            STUB, c, m, ["lint", "test"], phase="full", release_branch=""
        )
        self.assertEqual(contexts(policy), ["ci / lint"])
        c, m = cfg(**{"repo-policy": {"required-checks": ["test"]}})
        with self.assertRaises(ConfigError):
            repo_policy.for_target(
                STUB, c, m, ["lint"], phase="full", release_branch=""
            )

    def test_values_from_the_profile(self):
        c, m = cfg(
            **{
                "repo-policy": {
                    "required-approvals": 0,
                    "merge-methods": ["rebase", "squash"],
                }
            }
        )
        policy = repo_policy.for_target(
            STUB, c, m, ["lint"], phase="full", release_branch=""
        )
        pr = next(r for r in policy.rulesets[0]["rules"] if r["type"] == "pull_request")
        self.assertEqual(pr["parameters"]["required_approving_review_count"], 0)
        self.assertEqual(
            pr["parameters"]["allowed_merge_methods"], ["rebase", "squash"]
        )
        self.assertTrue(policy.repository["allow_rebase_merge"])
        self.assertFalse(policy.repository["allow_merge_commit"])

    def test_main_tags_has_no_release_branch_ruleset(self):
        c, m = cfg(releases={"branching": "main+tags"})
        self.assertEqual(c["integration-branch"], "main")
        policy = repo_policy.for_target(
            STUB, c, m, ["lint"], phase="full", release_branch=""
        )
        self.assertEqual([rs["name"] for rs in policy.rulesets], ["main", "v* tags"])

    def test_empty_tag_prefix_and_user_bypass(self):
        c, m = cfg(
            releases={"tag-prefix": ""},
            **{"repo-policy": {"release-bypass": "user:42"}},
        )
        policy = repo_policy.for_target(
            STUB, c, m, ["lint"], phase="full", release_branch="version-16"
        )
        tags = policy.rulesets[-1]
        self.assertEqual(
            tags["conditions"]["ref_name"]["include"], ["refs/tags/[0-9]*"]
        )
        self.assertEqual(
            policy.rulesets[1]["bypass_actors"],
            [{"actor_type": "User", "actor_id": 42, "bypass_mode": "always"}],
        )
        for rs in policy.rulesets:
            self.validate(STUB, rs)

    def test_settings_follow_the_parameters_and_modules(self):
        c, m = cfg(
            **{
                "repo-policy": {
                    "manage-default-branch": False,
                    "manage-features": False,
                }
            }
        )
        policy = repo_policy.for_target(
            STUB, c, m, ["lint"], phase="full", release_branch=""
        )
        self.assertNotIn("default_branch", policy.repository)
        self.assertNotIn("has_wiki", policy.repository)
        self.assertNotIn("homepage", policy.repository)
        # The settings file's labels, then the product's (extras), each while its
        # module is on.
        self.assertEqual(
            [lb["name"] for lb in policy.labels],
            ["dependencies", "needs-review", "extras"],
        )
        self.assertEqual(
            policy.labels[2],
            {"name": "extras", "color": "c5def5", "description": "An extras refresh"},
        )
        with_docs = repo_policy.for_target(
            STUB,
            c,
            m,
            ["lint"],
            phase="full",
            release_branch="",
            documentation="https://d.example.org/",
        )
        self.assertEqual(with_docs.repository["homepage"], "https://d.example.org/")
        c, m = cfg(dependabot={"enable": False}, extras={"enable": False})
        self.assertEqual(
            repo_policy.for_target(
                STUB, c, m, [], phase="full", release_branch=""
            ).labels,
            [],
        )

    def test_org_override(self):
        with tempfile.TemporaryDirectory() as d:
            over = Path(d) / "repo-policy" / "rulesets"
            over.mkdir(parents=True)
            doc = json.loads((core_dir() / "rulesets/tags.json").read_text())
            doc["enforcement"] = "evaluate"
            (over / "tags.json").write_text(json.dumps(doc))
            c, m = cfg()
            policy = repo_policy.for_target(
                STUB, c, m, ["lint"], phase="full", release_branch="", overrides=Path(d)
            )
            self.assertEqual(policy.rulesets[-1]["enforcement"], "evaluate")

    def test_a_product_file_replaces_the_core_file(self):
        with tempfile.TemporaryDirectory() as d:
            product = product_copy(STUB, Path(d))
            settings = json.loads((core_dir() / "repo-settings.json").read_text())
            settings["repository"]["has_wiki"] = True
            target = Path(product.data_root) / "repo-policy"
            target.mkdir(exist_ok=True)
            (target / "repo-settings.json").write_text(json.dumps(settings))
            c, m = cfg()
            policy = repo_policy.for_target(
                product, c, m, ["lint"], phase="full", release_branch=""
            )
            self.assertTrue(policy.repository["has_wiki"])

    def test_product_hooks(self):
        seen = []

        def rulesets(c, m, phase, release_branch):
            seen.append((phase, release_branch))
            return ["integration.json"]

        product = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(
                STUB.hooks,
                rulesets=rulesets,
                repo_policy_values=lambda c, m, gates: {
                    "strict_checks": True,
                    "release_label": "series",
                },
            ),
        )
        c, m = cfg()
        policy = repo_policy.for_target(
            product, c, m, ALL_GATES, phase="full", release_branch="version-16"
        )
        self.assertEqual(seen, [("full", "version-16")])
        self.assertEqual([rs["name"] for rs in policy.rulesets], ["develop"])
        rule = next(
            r
            for r in policy.rulesets[0]["rules"]
            if r["type"] == "required_status_checks"
        )
        self.assertIs(rule["parameters"]["strict_required_status_checks_policy"], True)
        vals = repo_policy.values(product, c, m, ALL_GATES)
        self.assertEqual(vals["release_label"], "series")

    def test_a_product_without_branching(self):
        product = dataclasses.replace(STUB, branching=MappingProxyType({}))
        c, m = cfg()
        policy = repo_policy.for_target(
            product, c, m, ["lint"], phase="full", release_branch="version-16"
        )
        self.assertEqual([rs["name"] for rs in policy.rulesets], ["develop"])

    def test_unfilled_placeholder_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            repo_policy.fill({"name": "{nope}"}, {}, "x")


if __name__ == "__main__":
    unittest.main()
