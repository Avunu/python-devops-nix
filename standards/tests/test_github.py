"""``common/github.py`` directly: the transport seam, pagination and required contexts."""

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


if __name__ == "__main__":
    unittest.main()
