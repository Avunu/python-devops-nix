"""``<cli> repo doctor`` against a fake GitHub (DESIGN §7.2)."""

import dataclasses
import json
from types import SimpleNamespace

from support import STUB, run_cli
from support.repos import R, RepoCase, write_unit

from standards_core.commands.repo import doctor

GATES = ["ci / pr-policy", "ci / lint", "ci / test"]
APP = 987654


class TestRepoDoctor(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = write_unit(self.tmp / "unit")
        self.gh.routes.update(
            {
                ("GET", f"repos/{R}"): {"allow_auto_merge": True},
                ("GET", f"repos/{R}/actions/permissions/workflow"): {
                    "default_workflow_permissions": "read",
                    "can_approve_pull_request_reviews": True,
                },
                ("GET", f"repos/{R}/rules/branches/version-16"): [],
                ("GET", f"repos/{R}/rules/branches/develop"): [
                    {
                        "type": "required_status_checks",
                        "ruleset_id": 1,
                        "parameters": {
                            "required_status_checks": [{"context": c} for c in GATES]
                        },
                    }
                ],
                ("GET", f"repos/{R}/branches/develop"): {
                    "name": "develop",
                    "protected": False,
                },
                ("GET", f"repos/{R}/labels"): [
                    {"name": "dependencies"},
                    {"name": "needs-review"},
                    {"name": "Extras"},
                ],
            }
        )

    def doctor(self, product=STUB) -> tuple[int, dict]:
        code, out, err = run_cli(
            product, "repo", "doctor", "--root", str(self.root), "--format", "json"
        )
        self.assertIn(code, (0, 1), err)
        return code, {r["setting"]: r["status"] for r in json.loads(out)["rows"]}

    def test_everything_ok(self):
        code, rows = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(set(rows.values()), {"ok"})
        self.assertEqual(
            list(rows),
            [
                "Actions may create pull requests",
                "GitHub Actions may fast-forward version-16",
                "auto-merge is allowed",
                "develop requires ci / lint, ci / pr-policy, ci / test",
                "label dependencies",
                "label needs-review",
                "label extras",
            ],
        )

    def test_text_output(self):
        code, out, err = run_cli(STUB, "repo", "doctor", "--root", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertIn("ok       releases    Actions may create pull requests", out)

    def test_from_a_subdirectory_of_the_checkout(self):
        code, out, err = run_cli(
            STUB, "repo", "doctor", "--format", "json", cwd=self.root / "demo_unit"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["repo"], R)

    def test_disabled_auto_merge_is_missing(self):
        self.gh.routes[("GET", f"repos/{R}")] = {"allow_auto_merge": False}
        code, rows = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(rows["auto-merge is allowed"], "missing")

    def test_disallowed_pull_request_creation_is_missing(self):
        self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")][
            "can_approve_pull_request_reviews"
        ] = False
        code, rows = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(rows["Actions may create pull requests"], "missing")

    def test_settings_needing_admin_are_unknown(self):
        self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")] = 403
        self.gh.routes[("GET", f"repos/{R}")] = {}
        code, rows = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(rows["Actions may create pull requests"], "unknown")
        self.assertEqual(rows["auto-merge is allowed"], "unknown")

    def test_unprotected_integration_branch_is_missing(self):
        self.gh.routes[("GET", f"repos/{R}/rules/branches/develop")] = []
        code, rows = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(
            rows["develop requires ci / lint, ci / pr-policy, ci / test"], "missing"
        )

    def test_a_missing_label_of_an_enabled_module(self):
        self.gh.routes[("GET", f"repos/{R}/labels")] = [{"name": "dependencies"}]
        code, rows = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(rows["label needs-review"], "missing")
        self.assertEqual(rows["label extras"], "missing")

    def test_release_branch_ruleset_without_bypass_is_missing(self):
        self.gh.routes[("GET", f"repos/{R}/rules/branches/version-16")] = [
            {"type": "update", "ruleset_id": 7}
        ]
        self.gh.routes[("GET", f"repos/{R}/rulesets/7")] = {
            "id": 7,
            "bypass_actors": [],
        }
        _, rows = self.doctor()
        self.assertEqual(rows["GitHub Actions may fast-forward version-16"], "missing")
        self.gh.routes[("GET", f"repos/{R}/rulesets/7")] = {
            "id": 7,
            "bypass_actors": [
                {
                    "actor_type": "Integration",
                    "actor_id": 15368,
                    "bypass_mode": "always",
                }
            ],
        }
        _, rows = self.doctor()
        self.assertEqual(rows["GitHub Actions may fast-forward version-16"], "ok")

    def test_an_app_as_the_release_bypass(self):
        self.root = write_unit(
            self.tmp / "app",
            f'\n[tool.stub-nix.repo-policy]\nrelease-bypass = "integration:{APP}"\n',
        )
        self.gh.routes[("GET", f"repos/{R}/rules/branches/version-16")] = [
            {"type": "update", "ruleset_id": 7}
        ]
        self.gh.routes[("GET", f"repos/{R}/rulesets/7")] = {
            "id": 7,
            "bypass_actors": [
                {
                    "actor_type": "Integration",
                    "actor_id": 15368,
                    "bypass_mode": "always",
                }
            ],
        }
        _, rows = self.doctor()
        self.assertEqual(rows[f"App {APP} may fast-forward version-16"], "missing")
        self.gh.routes[("GET", f"repos/{R}/rulesets/7")]["bypass_actors"].append(
            {"actor_type": "Integration", "actor_id": APP, "bypass_mode": "always"}
        )
        _, rows = self.doctor()
        self.assertEqual(rows[f"App {APP} may fast-forward version-16"], "ok")

    def test_modules_off_check_nothing(self):
        self.root = write_unit(
            self.tmp / "off",
            "\n[tool.stub-nix.releases]\nenable = false\n"
            "\n[tool.stub-nix.dependabot]\nenable = false\n"
            "\n[tool.stub-nix.extras]\nenable = false\n",
        )
        code, out, err = run_cli(STUB, "repo", "doctor", "--root", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out, f"{R}: no enabled module relies on a repository setting\n"
        )

    def test_product_rows_and_notices(self):
        seen = []

        def rows(repo, rc):
            seen.append((repo, rc.name))
            return [doctor.Row("extras", "secret EXTRAS_TOKEN", "missing", "x")], [
                "a product notice"
            ]

        product = dataclasses.replace(
            STUB, hooks=dataclasses.replace(STUB.hooks, doctor_rows=rows)
        )
        code, out, err = run_cli(
            product, "repo", "doctor", "--root", str(self.root), "--format", "json"
        )
        self.assertEqual(code, 1, err)
        doc = json.loads(out)
        self.assertEqual(doc["rows"][-1]["setting"], "secret EXTRAS_TOKEN")
        self.assertEqual(doc["notices"], ["a product notice"])
        self.assertEqual(seen, [(R, "demo_unit")])

    def test_a_cloned_repository(self):
        self.clones[(R, None)] = self.root
        code, out, err = run_cli(STUB, "repo", "doctor", R, "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["repo"], R)
        self.clones[(R, None)] = write_unit(self.tmp / "bare", opted_in=False)
        code, _, err = run_cli(STUB, "repo", "doctor", R)
        self.assertEqual(code, 2)
        self.assertIn("has no [tool.stub-nix]", err)


class TestProfileToken(RepoCase):
    def rc(self, source: str, dependabot: bool = True):
        return SimpleNamespace(
            resolved=SimpleNamespace(profile={"source": source}),
            modules={"dependabot": dependabot},
        )

    def test_a_private_profile_needs_the_fetch_token(self):
        settings = f"https://github.com/{R}/settings"
        self.gh.routes[("GET", "repos/example/profile")] = {"private": True}
        self.gh.routes[("GET", f"repos/{R}/actions/secrets/STUB_NIX_FETCH_TOKEN")] = {}
        rows = doctor._profile_rows(
            STUB, R, self.rc("github:example/profile/v1"), settings
        )
        self.assertEqual(
            [(r.setting, r.status) for r in rows],
            [
                ("secret STUB_NIX_FETCH_TOKEN (private profile)", "ok"),
                ("Dependabot secret STUB_NIX_FETCH_TOKEN", "missing"),
            ],
        )
        # A public profile, or a built-in, needs none.
        self.gh.routes[("GET", "repos/example/profile")] = {"private": False}
        self.assertEqual(
            doctor._profile_rows(STUB, R, self.rc("github:example/profile"), settings),
            [],
        )
        self.assertEqual(
            doctor._profile_rows(STUB, R, self.rc("builtin"), settings), []
        )
