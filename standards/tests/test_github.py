"""``common/github.py`` directly: the transport seam, pagination, required contexts and
the state of a commit's required checks."""

import json
import subprocess
import unittest
from unittest import mock

from support import KIT, STUB, FakeGitHub

from standards_core.common import github
from standards_core.common.report import EnvError


class GitHubCase(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        previous = github.use(self.gh)
        self.addCleanup(github.use, previous)


class TestTransportSeam(GitHubCase):
    def test_use_swaps_and_restores(self):
        other = FakeGitHub({("GET", "x"): {"ok": True}})
        previous = github.use(other)
        self.assertIs(previous, self.gh)
        self.assertEqual(github.get("x"), {"ok": True})
        self.assertIs(github.use(previous), other)
        self.assertEqual(self.gh.calls, [])

    def test_request_get_and_writes(self):
        self.gh.routes[("GET", "repos/o/r")] = {"name": "r"}
        self.assertEqual(github.get("repos/o/r"), {"name": "r"})
        self.assertEqual(github.request("PATCH", "repos/o/r", {"a": 1}), {})
        self.assertEqual(self.gh.writes(), [("PATCH", "repos/o/r", {"a": 1})])

    def test_errors_keep_their_status(self):
        self.gh.routes[("GET", "forbidden")] = 403
        with self.assertRaises(github.GitHubError) as ctx:
            github.get("forbidden")
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, 3)
        self.assertIsInstance(ctx.exception, EnvError)

    def test_get_or_none(self):
        self.assertIsNone(github.get_or_none("missing"))
        self.gh.routes[("GET", "forbidden")] = 403
        with self.assertRaises(github.GitHubError):
            github.get_or_none("forbidden")

    def test_cli(self):
        self.gh.cli_results[("pr", "merge")] = github.Result(1, "", "nope")
        self.assertEqual(github.cli("pr", "merge", "1", "--auto").code, 1)
        self.assertEqual(github.cli("label", "create", "x").code, 0)
        self.assertEqual(
            self.gh.cli_calls,
            [("pr", "merge", "1", "--auto"), ("label", "create", "x")],
        )


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class TestRealTransport(unittest.TestCase):
    """The ``gh``-backed transport, with ``subprocess.run`` replaced."""

    def run_with(self, result, *args, **kwargs):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            if isinstance(result, BaseException):
                raise result
            return result

        with mock.patch.object(github.subprocess, "run", side_effect=run):
            out = github.Transport().request(*args, **kwargs)
        return out, calls

    def test_pagination_flattens_pages(self):
        pages = [[{"n": 1}, {"n": 2}], [{"n": 3}]]
        (status, data), calls = self.run_with(
            completed(json.dumps(pages)), "GET", "repos/o/r/labels", paginate=True
        )
        self.assertEqual((status, data), (200, [{"n": 1}, {"n": 2}, {"n": 3}]))
        self.assertEqual(
            calls[0][0],
            [
                "gh",
                "api",
                "--method",
                "GET",
                "repos/o/r/labels",
                "--paginate",
                "--slurp",
            ],
        )

    def test_body_goes_to_stdin(self):
        _, calls = self.run_with(completed("{}"), "POST", "x", {"a": 1})
        cmd, kw = calls[0]
        self.assertEqual(cmd[-2:], ["--input", "-"])
        self.assertEqual(json.loads(kw["input"]), {"a": 1})

    def test_empty_response_is_204(self):
        (status, data), _ = self.run_with(completed(""), "DELETE", "x")
        self.assertEqual((status, data), (204, None))

    def test_failure_status_is_parsed(self):
        with self.assertRaises(github.GitHubError) as ctx:
            self.run_with(
                completed("", "gh: Not Found (HTTP 404)", 1), "GET", "repos/o/missing"
            )
        self.assertEqual(ctx.exception.status, 404)

    def test_not_json_and_no_gh(self):
        with self.assertRaisesRegex(github.GitHubError, "not JSON"):
            self.run_with(completed("<html>"), "GET", "x")
        with self.assertRaisesRegex(EnvError, "gh is not on PATH"):
            self.run_with(FileNotFoundError("gh"), "GET", "x")


class TestContexts(GitHubCase):
    def test_gate_contexts_use_the_products_template(self):
        self.assertEqual(
            github.gate_contexts(STUB, ["lint", "test"]), ["ci / lint", "ci / test"]
        )
        self.assertEqual(github.gate_contexts(KIT, ()), [])

    def test_required_contexts_from_rulesets_and_protection(self):
        self.gh.routes[("GET", "repos/o/r/rules/branches/main")] = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "ci / lint"},
                        {"context": ""},
                    ]
                },
            },
            {"type": "deletion"},
        ]
        self.gh.routes[("GET", "repos/o/r/branches/main")] = {
            "protection": {
                "required_status_checks": {
                    "contexts": ["ci / test"],
                    "checks": [{"context": "ci / pr-policy"}],
                }
            }
        }
        self.assertEqual(
            github.required_contexts("o/r", "main"),
            ({"ci / lint", "ci / test", "ci / pr-policy"}, True),
        )

    def test_unknown_when_nothing_is_readable(self):
        self.assertEqual(github.required_contexts("o/r", "main"), (set(), False))

    def test_gates_protected(self):
        self.gh.routes[("GET", "repos/o/r/branches/main")] = {
            "protection": {"required_status_checks": {"contexts": ["ci / lint"]}}
        }
        self.assertTrue(github.gates_protected(STUB, "o/r", "main", ["lint"]))
        self.assertFalse(github.gates_protected(STUB, "o/r", "main", ["lint", "test"]))
        self.assertFalse(github.gates_protected(STUB, "o/r", "main", []))
        self.assertFalse(github.gates_protected(STUB, "o/other", "main", ["lint"]))


def run(
    name,
    status="completed",
    conclusion="success",
    started="2026-01-01T00:00:00Z",
    id_=1,
):
    return {
        "id": id_,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
    }


class TestCheckState(GitHubCase):
    SHA = "c" * 40
    RUNS = f"repos/o/r/commits/{'c' * 40}/check-runs?per_page=100"
    STATUS = f"repos/o/r/commits/{'c' * 40}/status?per_page=100"

    def state(self, runs=(), statuses=(), contexts=("ci / lint",)):
        self.gh.routes[("GET", self.RUNS)] = {
            "total_count": len(runs),
            "check_runs": list(runs),
        }
        self.gh.routes[("GET", self.STATUS)] = {
            "state": "pending",
            "statuses": list(statuses),
        }
        return github.check_state("o/r", self.SHA, contexts)

    def test_green_conclusions(self):
        for conclusion in ("success", "neutral", "skipped"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(
                    self.state([run("ci / lint", conclusion=conclusion)]), "green"
                )

    def test_failed_conclusions(self):
        for conclusion in (
            "failure",
            "cancelled",
            "timed_out",
            "action_required",
            "startup_failure",
            "stale",
        ):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(
                    self.state([run("ci / lint", conclusion=conclusion)]), "failed"
                )

    def test_pending(self):
        self.assertEqual(self.state([]), "pending")  # missing
        self.assertEqual(self.state([run("ci / lint", "in_progress", None)]), "pending")
        self.assertEqual(self.state([run("ci / lint", "queued", None)]), "pending")
        self.assertEqual(
            self.state([run("ci / lint", conclusion="something-new")]), "pending"
        )

    def test_every_context_must_be_green_and_any_failure_fails(self):
        both = ("ci / lint", "ci / test")
        self.assertEqual(self.state([run("ci / lint")], contexts=both), "pending")
        self.assertEqual(
            self.state([run("ci / lint"), run("ci / test", id_=2)], contexts=both),
            "green",
        )
        self.assertEqual(
            self.state(
                [
                    run("ci / lint", conclusion="failure"),
                    run("ci / test", "queued", None),
                ],
                contexts=both,
            ),
            "failed",
        )

    def test_a_rerun_replaces_the_failed_attempt(self):
        failed = run("ci / lint", conclusion="failure", started="2026-01-01T00:00:00Z")
        rerun = run("ci / lint", started="2026-01-01T01:00:00Z", id_=2)
        self.assertEqual(self.state([failed, rerun]), "green")
        self.assertEqual(self.state([rerun, failed]), "green")
        broken = run("ci / lint", conclusion="failure", started="2026-01-02T00:00:00Z")
        self.assertEqual(self.state([rerun, broken]), "failed")
        # the same start: the higher id is the newer
        same = "2026-01-01T00:00:00Z"
        self.assertEqual(
            self.state(
                [
                    run("ci / lint", conclusion="failure", started=same, id_=5),
                    run("ci / lint", started=same, id_=4),
                ]
            ),
            "failed",
        )

    def test_a_queued_rerun_with_no_start_replaces_the_failed_attempt(self):
        failed = run("ci / lint", conclusion="failure", id_=1)
        queued = run("ci / lint", "queued", None, started=None, id_=2)
        self.assertEqual(self.state([failed, queued]), "pending")
        self.assertEqual(self.state([queued, failed]), "pending")
        # of two unstarted attempts the higher id is the newer
        later = run("ci / lint", "completed", "failure", started=None, id_=3)
        self.assertEqual(self.state([later, queued]), "failed")
        self.assertEqual(self.state([queued, later]), "failed")

    def test_a_status_only_context(self):
        def status(state):
            return {"context": "ci / lint", "state": state}

        self.assertEqual(self.state(statuses=[status("success")]), "green")
        self.assertEqual(self.state(statuses=[status("failure")]), "failed")
        self.assertEqual(self.state(statuses=[status("error")]), "failed")
        self.assertEqual(self.state(statuses=[status("pending")]), "pending")
        # a check run of the same name wins over the status
        self.assertEqual(
            self.state([run("ci / lint")], statuses=[status("failure")]), "green"
        )

    def test_no_combined_status_and_paginated_pages(self):
        self.gh.routes[("GET", self.RUNS)] = [
            {"total_count": 2, "check_runs": [run("ci / lint")]},
            {"total_count": 2, "check_runs": [run("ci / test", id_=2)]},
        ]
        self.assertEqual(
            github.check_state("o/r", self.SHA, ["ci / lint", "ci / test"]), "green"
        )
        self.assertIn(("GET", self.STATUS, None), self.gh.calls)

    def test_paginated_statuses(self):
        self.gh.routes[("GET", self.RUNS)] = {"total_count": 0, "check_runs": []}
        self.gh.routes[("GET", self.STATUS)] = [
            {
                "state": "pending",
                "statuses": [{"context": "ci / lint", "state": "success"}],
            },
            {
                "state": "pending",
                "statuses": [{"context": "ci / test", "state": "error"}],
            },
        ]
        contexts = ["ci / lint", "ci / test"]
        self.assertEqual(github.check_state("o/r", self.SHA, contexts), "failed")
        self.gh.routes[("GET", self.STATUS)][1]["statuses"][0]["state"] = "success"
        self.assertEqual(github.check_state("o/r", self.SHA, contexts), "green")


if __name__ == "__main__":
    unittest.main()
