"""``<cli> repo audit`` against a fake GitHub (DESIGN §7.4): the core rows, the release
rows under each flow, the product's rows, the issue."""

import dataclasses
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from support import KIT, STUB, run_cli
from support.repos import K, R, RepoCase, kit_bundle, write_unit

from standards_core.commands.repo import audit
from standards_core.testing.fakes import flake_lock

SHA = "c" * 40
CORE_IDS = ["A1", "A2", "A3", "A4", "A5", "A9", "A10", "A11"]


class AuditCase(RepoCase):
    """``self.report`` is what the faked ``sync --check`` prints."""

    def _tool(self, product, root, argv, spec):
        self.tool_calls.append((argv, spec))
        if argv[:2] == ["sync", "--check"]:
            return 0, json.dumps(self.report)
        return 0, ""

    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(audit, "run_tool", side_effect=self._tool)
        p.start()
        self.addCleanup(p.stop)
        self.tool_calls: list = []
        self.report: dict | None = {"status": "clean", "files": []}

    def rows(self, product=None, repo=R, *extra_argv) -> tuple[int, dict]:
        code, out, err = run_cli(
            product or self.product,
            "repo",
            "audit",
            "--repo",
            repo,
            "--format",
            "json",
            *extra_argv,
        )
        self.assertIn(code, (0, 1), err)
        doc = json.loads(out)
        return code, {row["id"]: row for row in doc["repos"][0]["rows"]}


class TestRepoAudit(AuditCase):
    def setUp(self) -> None:
        super().setUp()
        self.gh.routes.update(
            {
                ("GET", f"repos/{R}"): {"default_branch": "develop", "private": False},
                ("GET", f"repos/{R}/tags"): [
                    {"name": "v1.2.0", "commit": {"sha": "d" * 40}},
                    {"name": "v1.3.0", "commit": {"sha": SHA}},
                    {"name": "assets-version-16", "commit": {"sha": "e" * 40}},
                ],
                ("GET", f"repos/{R}/git/ref/heads/version-16"): {
                    "object": {"sha": SHA}
                },
                ("GET", f"repos/{R}/releases/tags/v1.3.0"): {
                    "author": {"login": "github-actions[bot]"}
                },
            }
        )

    def unit(self, extra: str = "") -> None:
        self.clones[(R, None)] = write_unit(self.tmp / f"unit{len(self.cloned)}", extra)

    def test_the_core_rows_and_n_a_for_modules_that_are_off(self):
        self.unit()
        _, rows = self.rows()
        self.assertEqual(list(rows), CORE_IDS)
        for rid in ("A1", "A3"):
            self.assertEqual(rows[rid]["status"], "n/a", rid)
            self.assertEqual(rows[rid]["detail"], "repo-policy is off")
        self.assertEqual(rows["A4"]["status"], "green")
        self.assertEqual(rows["A9"]["status"], "green")
        self.assertEqual(self.tool_calls[0][0], ["sync", "--check", "--format", "json"])
        # One sync --check for A4 and A9.
        self.assertEqual(len(self.tool_calls), 1)

    def test_semver_release_rows_are_green_with_v1_tags(self):
        self.unit()
        _, rows = self.rows()
        self.assertEqual(rows["A2"]["status"], "green", rows["A2"])
        self.assertEqual(rows["A2"]["detail"], "version-16 = v1.3.0")
        self.assertEqual(rows["A11"]["status"], "green", rows["A11"])
        self.gh.routes[("GET", f"repos/{R}/git/ref/heads/version-16")] = {
            "object": {"sha": "f" * 40}
        }
        self.unit()
        code, rows = self.rows()
        self.assertEqual(rows["A2"]["status"], "red")
        self.assertEqual(code, 1)

    def test_a11_takes_a_release_by_an_app(self):
        route = ("GET", f"repos/{R}/releases/tags/v1.3.0")
        self.gh.routes[route] = {"author": {"login": "example-ci[bot]"}}
        self.unit()
        _, rows = self.rows()
        self.assertEqual(rows["A11"]["status"], "green", rows["A11"])
        self.gh.routes[route] = {"author": {"login": "someone"}}
        _, rows = self.rows()
        self.assertEqual(rows["A11"]["status"], "amber", rows["A11"])
        self.assertIn("released by someone", rows["A11"]["detail"])

    def test_the_version_scheme_filters_the_tags(self):
        """``stub-major`` takes only tags on the checkout's major: v1.x are not
        releases of major 16."""
        self.unit('\n[tool.stub-nix.releases]\nversion-scheme = "stub-major"\n')
        _, rows = self.rows()
        self.assertEqual(rows["A11"]["status"], "red")
        self.assertEqual(rows["A2"]["status"], "amber")

    def test_main_tags_has_no_release_branch(self):
        self.unit('\n[tool.stub-nix.releases]\nbranching = "main+tags"\n')
        self.gh.routes[("GET", f"repos/{R}")] = {"default_branch": "main"}
        _, rows = self.rows()
        self.assertEqual(rows["A2"]["status"], "n/a")
        self.assertEqual(rows["A11"]["status"], "green")

    def test_a_report_that_is_not_json_is_unknown(self):
        self.report = None
        self.unit()
        with mock.patch.object(audit, "run_tool", return_value=(3, "oops")):
            _, rows = self.rows()
        self.assertEqual(rows["A4"]["status"], "unknown")
        self.assertEqual(rows["A9"]["status"], "unknown")

    def test_drift_and_legacy_files(self):
        self.report = {
            "status": "drift",
            "files": [
                {"path": ".flake8", "problem": "legacy file (delete)"},
                {"path": "flake.nix", "problem": "differs"},
            ],
        }
        self.unit()
        _, rows = self.rows()
        self.assertEqual(rows["A4"]["status"], "red")
        self.assertEqual(rows["A4"]["detail"], "drift: .flake8, flake.nix")
        self.assertEqual(rows["A9"]["status"], "red")
        self.assertEqual(rows["A9"]["detail"], "legacy files: .flake8")

    def test_a3_compares_with_the_rendered_policy(self):
        self.unit("\n[tool.stub-nix.repo-policy]\nenable = true\n")
        _, rows = self.rows()
        self.assertEqual(rows["A1"]["status"], "green")
        # Nothing is set up: the policy's writes are the detail.
        self.assertEqual(rows["A3"]["status"], "unknown")  # workflow permissions 404
        self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")] = {}
        self.gh.routes[("GET", f"repos/{R}/labels")] = []
        self.gh.routes[("GET", f"repos/{R}/rulesets")] = []
        _, rows = self.rows()
        self.assertEqual(rows["A3"]["status"], "red")
        self.assertIn(f"POST repos/{R}/rulesets", rows["A3"]["detail"])
        self.assertEqual(self.gh.writes(), [])
        # A private repository: rulesets n/a.
        self.gh.routes[("GET", f"repos/{R}")]["private"] = True
        self.gh.routes[("GET", f"repos/{R}/rulesets")] = 403
        _, rows = self.rows()
        self.assertEqual(rows["A3"]["status"], "red")
        self.assertNotIn(f"POST repos/{R}/rulesets", rows["A3"]["detail"])

    def test_a5_reads_the_gate_jobs(self):
        run = {"id": 9, "conclusion": "success"}
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/actions/workflows/ci.yml/runs?status=completed&per_page=1&branch=develop",
            )
        ] = {"workflow_runs": [run]}
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/actions/workflows/pr-policy.yml/runs?status=completed&per_page=1",
            )
        ] = {"workflow_runs": [run]}
        self.gh.routes[("GET", f"repos/{R}/actions/runs/9/jobs?per_page=100")] = {
            "jobs": [
                {"name": "ci / lint", "conclusion": "success"},
                {"name": "ci / test", "conclusion": "success"},
            ]
        }
        self.unit()
        _, rows = self.rows()
        self.assertEqual(rows["A5"]["status"], "green", rows["A5"])
        # A product with a nightly workflow: it never ran.
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/actions/workflows/nightly.yml/runs?status=completed&per_page=1",
            )
        ] = {"workflow_runs": []}
        product = dataclasses.replace(
            STUB, ci=dataclasses.replace(STUB.ci, nightly_workflow="nightly.yml")
        )
        _, rows = self.rows(product)
        self.assertEqual(rows["A5"]["status"], "red")
        self.assertEqual(rows["A5"]["detail"], "nightly never ran")
        self.gh.routes[("GET", f"repos/{R}/actions/runs/9/jobs?per_page=100")] = {
            "jobs": [{"name": "ci / lint", "conclusion": "failure"}]
        }
        _, rows = self.rows()
        self.assertEqual(rows["A5"]["status"], "red")
        self.assertEqual(rows["A5"]["detail"], "ci / lint failure; ci / test absent")

    def test_a10_reads_the_last_nix_bump(self):
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/pulls?state=closed&per_page=100&sort=updated&direction=desc",
            )
        ] = [
            {
                "number": 5,
                "merged_at": "2026-01-01T00:00:00Z",
                "head": {"ref": "dependabot/nix/flake-lock"},
                "merged_by": {"login": "someone"},
            }
        ]
        self.gh.routes[("GET", f"repos/{R}/pulls/5/commits")] = [
            {"commit": {"message": "build(deps): bump"}},
            {"commit": {"message": "chore(deps): relock and sync"}},
        ]
        self.unit()
        _, rows = self.rows()
        self.assertEqual(rows["A10"]["status"], "amber")
        self.assertEqual(rows["A10"]["detail"], "#5 merged by someone")
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/pulls?state=closed&per_page=100&sort=updated&direction=desc",
            )
        ][0]["merged_by"] = {"login": "app[bot]"}
        _, rows = self.rows()
        self.assertEqual(rows["A10"]["status"], "green", rows["A10"])

    def test_a10_names_the_rollout_for_a_stale_engine(self):
        root = write_unit(self.tmp / "locked")
        lock = flake_lock(
            '{\n  inputs = {\n    stub-nix.url = "github:example/stub-nix";\n  };\n}\n'
        )
        (root / "flake.lock").write_text(json.dumps(lock))
        self.clones[(R, None)] = root
        self.gh.routes[("GET", "repos/example/stub-nix/releases/latest")] = {
            "tag_name": "v2.0.0",
            "published_at": "2020-01-01T00:00:00Z",
        }
        self.gh.routes[("GET", "repos/example/stub-nix/commits/v2.0.0")] = {
            "sha": "b" * 40
        }
        self.gh.routes[
            (
                "GET",
                f"repos/{R}/pulls?state=closed&per_page=100&sort=updated&direction=desc",
            )
        ] = []
        _, rows = self.rows()
        self.assertEqual(rows["A10"]["status"], "amber")
        self.assertEqual(
            rows["A10"]["detail"],
            f"stub-nix v2.0.0 available: stub-nix repo rollout --to v2.0.0 --repo {R}",
        )
        # The pinned tool is the locked engine's.
        spec = self.tool_calls[0][1]
        self.assertEqual(
            spec,
            "stub-nix-tools @ git+https://github.com/example/stub-nix@"
            + "a" * 40
            + "#subdirectory=py/stub_nix_tools",
        )
        # --against latest: the latest release's.
        self.tool_calls.clear()
        self.rows(None, R, "--against", "latest")
        self.assertIn("@v2.0.0#", self.tool_calls[0][1])

    def test_the_tool_spec_hook(self):
        self.unit()
        product = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(STUB.hooks, tool_spec=lambda a: f"tools=={a.r}"),
        )
        self.rows(product)
        self.assertEqual(self.tool_calls[0][1], f"tools=={R}")

    def test_product_rows_add_and_replace(self):
        self.unit()
        product = dataclasses.replace(
            STUB,
            audit_rows=MappingProxyType(
                {
                    "A9": audit.RowSpec(
                        "no legacy files", None, lambda a: ("amber", "x")
                    ),
                    "A12": audit.RowSpec("extras", "extras", lambda a: ("green", "y")),
                    "A13": audit.RowSpec("off", "repo-policy", lambda a: ("red", "")),
                }
            ),
        )
        _, rows = self.rows(product)
        self.assertEqual(list(rows), [*CORE_IDS, "A12", "A13"])
        self.assertEqual((rows["A9"]["status"], rows["A9"]["detail"]), ("amber", "x"))
        self.assertEqual(rows["A12"]["status"], "green")
        self.assertEqual(rows["A13"]["status"], "n/a")

    def test_a_not_opted_in_target(self):
        self.clones[(R, None)] = write_unit(self.tmp / "bare", opted_in=False)
        _, rows = self.rows()
        self.assertEqual(rows["A4"]["status"], "red")
        self.assertEqual(
            rows["A4"]["detail"], "no [tool.stub-nix] on the integration branch"
        )
        self.assertEqual({rows[r]["status"] for r in rows if r != "A4"}, {"unknown"})
        fleet = self.tmp / "fleet.json"
        fleet.write_text(
            json.dumps({"schema": 1, "profile": "recommended", "repos": [{"repo": R}]})
        )
        code, out, _ = run_cli(STUB, "repo", "audit", "--all", "--fleet", str(fleet))
        self.assertEqual(code, 1)
        self.assertIn(f"## {R}", out)
        self.assertIn("| A1 | default branch is the integration branch | n/a |", out)

    def test_markdown_to_a_file(self):
        self.unit()
        out_file = self.tmp / "scorecard.md"
        code, out, err = run_cli(
            STUB, "repo", "audit", "--repo", R, "--out", str(out_file)
        )
        self.assertIn(code, (0, 1), err)
        self.assertEqual(out, "")
        text = out_file.read_text()
        self.assertTrue(text.startswith(f"## {R}\n\n| Row | Item | Status | Detail |"))

    def test_issue_is_opened_then_updated(self):
        self.unit()
        issues = "repos/example/profile/issues?state=open&per_page=100"
        self.gh.routes[("GET", issues)] = []
        seen = []
        self.gh.routes[("POST", "repos/example/profile/issues")] = lambda body: (
            seen.append(os.environ.get("GH_TOKEN")) or {}
        )
        with mock.patch.dict(os.environ, {"STUB_NIX_ISSUE_TOKEN": "issue-token"}):
            code, _, err = run_cli(
                STUB,
                "repo",
                "audit",
                "--repo",
                R,
                "--issue",
                "--issue-repo",
                "example/profile",
            )
        self.assertIn(code, (0, 1), err)
        self.assertEqual(seen, ["issue-token"])
        self.assertNotIn("GH_TOKEN", os.environ)
        posts = [w for w in self.gh.writes() if w[1] == "repos/example/profile/issues"]
        self.assertEqual(posts[0][2]["title"], f"scorecard: {R}")
        self.gh.routes[("GET", issues)] = [{"number": 3, "title": f"scorecard: {R}"}]
        run_cli(
            STUB,
            "repo",
            "audit",
            "--repo",
            R,
            "--issue",
            "--issue-repo",
            "example/profile",
        )
        self.assertIn(
            ("PATCH", "repos/example/profile/issues/3"),
            [w[:2] for w in self.gh.writes()],
        )

    def test_issue_needs_issue_repo(self):
        code, _, _ = run_cli(STUB, "repo", "audit", "--repo", R, "--issue")
        self.assertEqual(code, 2)


class TestPostMergeBumpRows(AuditCase):
    """A2 under the kit's post-merge-bump flow: the release branch follows the
    integration head once its checks pass."""

    product = KIT
    HEAD = "1" * 40
    BEHIND = "2" * 40

    def setUp(self) -> None:
        super().setUp()
        self.clones[(K, None)] = kit_bundle(self.tmp / "bundle")
        self.gh.routes.update(
            {
                ("GET", f"repos/{K}"): {"default_branch": "develop"},
                ("GET", f"repos/{K}/git/ref/heads/develop"): {
                    "object": {"sha": self.HEAD}
                },
                ("GET", f"repos/{K}/git/ref/heads/18.0"): {
                    "object": {"sha": self.HEAD}
                },
            }
        )

    def runs(self, sha: str, conclusion: str | None) -> None:
        status = "completed" if conclusion else "in_progress"
        self.gh.routes[("GET", f"repos/{K}/commits/{sha}/check-runs?per_page=100")] = {
            "check_runs": [
                {"name": f"ci / {g}", "status": status, "conclusion": conclusion}
                for g in ("pr-policy", "lint", "test")
            ]
        }

    def test_release_branch_at_the_integration_head(self):
        _, rows = self.rows(KIT, K)
        self.assertEqual(rows["A2"]["status"], "green", rows["A2"])
        self.assertEqual(rows["A2"]["detail"], "18.0 = develop")
        self.assertEqual(rows["A11"]["status"], "n/a")

    def test_behind_only_by_pending_commits(self):
        self.gh.routes[("GET", f"repos/{K}/git/ref/heads/18.0")] = {
            "object": {"sha": self.BEHIND}
        }
        self.gh.routes[("GET", f"repos/{K}/compare/{self.BEHIND}...{self.HEAD}")] = {
            "status": "ahead",
            "commits": [{"sha": self.HEAD}],
        }
        self.runs(self.HEAD, None)
        _, rows = self.rows(KIT, K)
        self.assertEqual(rows["A2"]["status"], "green", rows["A2"])
        self.assertIn("pending", rows["A2"]["detail"])
        self.runs(self.HEAD, "success")
        _, rows = self.rows(KIT, K)
        self.assertEqual(rows["A2"]["status"], "red", rows["A2"])
        self.assertIn("finished its checks", rows["A2"]["detail"])
        self.gh.routes[("GET", f"repos/{K}/compare/{self.BEHIND}...{self.HEAD}")] = {
            "status": "diverged",
            "commits": [],
        }
        _, rows = self.rows(KIT, K)
        self.assertEqual(rows["A2"]["detail"], "18.0 and develop have diverged")


class TestRunTool(unittest.TestCase):
    def test_pinned_tools_run_through_uv_with_skew_allowed(self):
        seen = {}

        def fake(cmd, **kwargs):
            seen["cmd"], seen["env"] = cmd, kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, "out", "")

        with mock.patch.object(subprocess, "run", side_effect=fake):
            code, out = audit.run_tool(
                STUB, Path("."), ["sync", "--check"], "x @ git+y"
            )
        self.assertEqual((code, out), (0, "out"))
        self.assertEqual(
            seen["cmd"],
            ["uv", "tool", "run", "--python", "3.12", "--from", "x @ git+y", "stub-nix"]
            + ["sync", "--check"],
        )
        self.assertEqual(seen["env"]["STUB_NIX_ALLOW_SKEW"], "1")
        with mock.patch.object(subprocess, "run", side_effect=fake):
            audit.run_tool(STUB, Path("."), ["sync"], None)
        self.assertEqual(seen["cmd"], ["stub-nix", "sync"])

    def test_rows_sort_by_number(self):
        product = dataclasses.replace(
            STUB,
            audit_rows=MappingProxyType(
                {"A20": audit.RowSpec("x", None, lambda a: ("green", ""))}
            ),
        )
        ids = [rid for rid, _ in audit.row_specs(product)]
        self.assertEqual(ids, [*CORE_IDS, "A20"])


if __name__ == "__main__":
    unittest.main()
