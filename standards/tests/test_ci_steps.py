"""``<cli> ci``: the auto-merge decisions, the safety rule, the release dispatches, the
fast-forward guards and the drift issues (DESIGN §8), against a fake GitHub
(``common.github.use``)."""

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from support import KIT, STUB, run_cli
from support.checkouts import CheckoutCase, NestedConfigCase

from standards_core import cli
from standards_core.ci import automerge, drift, labels, release, verdict
from standards_core.ci.conventional import SEMVER
from standards_core.ci.verdict import DecisionRule
from standards_core.common import github
from standards_core.common.github import GitHubError, Result
from standards_core.product import CiConfig

REPO = "example/unit"
PR = "https://github.com/example/unit/pull/7"
GATES = ["pr-policy", "lint", "test"]
STUB_MAJOR = STUB.version_schemes["stub-major"]

# Recorded dependabot/fetch-metadata v3 outputs, one per row of the stub's table.
METADATA = [
    (
        {
            "package-ecosystem": "github_actions",
            "update-type": "version-update:semver-minor",
            "dependency-group": "actions",
            "dependency-names": "actions/checkout, actions/setup-node",
            "directory": "/",
        },
        "auto",
    ),
    (
        {
            "package-ecosystem": "github_actions",
            "update-type": "version-update:semver-patch",
            "dependency-group": "docs-actions",
            "dependency-names": "example/docs-tool/.github/workflows/docs.yml",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "github_actions",
            "update-type": "",
            "dependency-group": "pilot",
            "dependency-names": "example/pilot/.github/workflows/unit-assets.yml",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "github_actions",
            "update-type": "version-update:semver-major",
            "dependency-group": "",
            "dependency-names": "example/pilot/.github/workflows/unit-assets.yml",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "npm_and_yarn",
            "update-type": "version-update:semver-minor",
            "dependency-group": "toolchain",
            "dependency-names": "oxlint, oxfmt",
            "directory": "/",
        },
        "auto",
    ),
    (
        {
            "package-ecosystem": "npm_and_yarn",
            "update-type": "version-update:semver-patch",
            "dependency-group": "frontend",
            "dependency-names": "vue",
            "directory": "/frontend",
        },
        "auto",
    ),
    (
        {
            "package-ecosystem": "npm_and_yarn",
            "update-type": "version-update:semver-minor",
            "dependency-group": "docs-site-packages",
            "dependency-names": "vitepress",
            "directory": "/docs-site",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "npm_and_yarn",
            "update-type": "version-update:semver-major",
            "dependency-group": "",
            "dependency-names": "typescript",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "uv",
            "update-type": "version-update:semver-patch",
            "dependency-group": "tools",
            "dependency-names": "ruff",
            "directory": "/tools",
        },
        "auto",
    ),
    (
        {
            "package-ecosystem": "uv",
            "update-type": "version-update:semver-major",
            "dependency-group": "",
            "dependency-names": "semgrep",
            "directory": "/tools",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "pre_commit",
            "update-type": "",
            "dependency-group": "hooks",
            "dependency-names": "https://github.com/pre-commit/pre-commit-hooks",
            "directory": "/",
        },
        "auto",
    ),
    (
        {
            "package-ecosystem": "pre_commit",
            "update-type": "",
            "dependency-group": "test-utils",
            "dependency-names": "https://github.com/example/test_utils",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "pre_commit",
            "update-type": "",
            "dependency-group": "",
            "dependency-names": "https://github.com/example/test_utils",
            "directory": "/",
        },
        "review",
    ),
    (
        {
            "package-ecosystem": "nix",
            "update-type": "",
            "dependency-group": "flake-inputs",
            "dependency-names": "core-lib",
            "directory": "/",
        },
        "relock",
    ),
    (
        {
            "package-ecosystem": "nix",
            "update-type": "",
            "dependency-group": "",
            "dependency-names": "standards-profile",
            "directory": "/",
        },
        "close",
    ),
    (
        {
            "package-ecosystem": "submodules",
            "update-type": "",
            "dependency-group": "",
            "dependency-names": "vendor/lib",
            "directory": "/",
        },
        "auto",
    ),
]


def decide(meta, product=STUB, **kw):
    return verdict.decide(
        product,
        meta["package-ecosystem"],
        update_type=meta["update-type"],
        group=meta["dependency-group"],
        dependency_names=meta["dependency-names"],
        directory=meta["directory"],
        **kw,
    )


class Fake(github.Transport):
    """Answers REST calls from ``routes`` ((method, endpoint) → data, a function giving
    it, or a status to fail with) and ``gh`` subcommands from ``cli_answers`` (first argv
    words → Result). Any other call is a 404."""

    def __init__(self, routes=None, cli_answers=None):
        self.routes = dict(routes or {})
        self.cli_answers = dict(cli_answers or {})
        self.requests: list[tuple[str, str, object]] = []
        self.clis: list[tuple[str, ...]] = []

    def request(self, method, endpoint, body=None, paginate=False):
        self.requests.append((method, endpoint, body))
        key = (method, endpoint)
        if key not in self.routes:
            raise GitHubError(f"no route for {method} {endpoint}", 404)
        answer = self.routes[key]
        if callable(answer):
            answer = answer()
        if isinstance(answer, int):
            raise GitHubError(f"{method} {endpoint}: HTTP {answer}", answer)
        return 200, answer

    def cli(self, *args):
        self.clis.append(args)
        for prefix, result in self.cli_answers.items():
            if args[: len(prefix)] == prefix:
                return result
        return Result(0, "", "")

    def writes(self):
        return [r for r in self.requests if r[0] != "GET"]


def protected(contexts, branch="develop"):
    return {
        ("GET", f"repos/{REPO}/rules/branches/{branch}"): [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": c} for c in contexts]
                },
            }
        ],
        ("GET", f"repos/{REPO}/branches/{branch}"): {"protected": False},
    }


def printed(p):
    return " ".join(str(a) for call in p.call_args_list for a in call.args)


class FakeCase(unittest.TestCase):
    def use(self, fake):
        previous = github.use(fake)
        self.addCleanup(github.use, previous)
        return fake

    def output_file(self):
        out = Path(tempfile.mkdtemp()) / "out"
        patch = mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)})
        patch.start()
        self.addCleanup(patch.stop)
        return out


class TestVerdict(FakeCase):
    def test_recorded_metadata(self):
        for meta, want in METADATA:
            with self.subTest(meta=meta):
                self.assertEqual(decide(meta), want)

    def test_docs_patterns_engine_and_unknown_ecosystems(self):
        self.assertEqual(
            verdict.decide(
                STUB,
                "github_actions",
                dependency_names="example/docs-tool",
                params={"docs-patterns": ["example/docs-tool*"]},
            ),
            "review",
        )
        self.assertEqual(
            verdict.decide(
                STUB,
                "github_actions",
                group="actions",
                dependency_names="example/stub-nix",
                engine_repo="Example/stub-nix",
            ),
            "review",
        )
        self.assertEqual(
            verdict.decide(
                STUB, "github_actions", group="actions", dependency_names="x/y"
            ),
            "auto",
        )
        self.assertEqual(verdict.decide(STUB, "cargo", dependency_names="x"), "review")

    def test_policy_all_merges_every_review(self):
        """``dependabot.auto-merge-policy = "all"``: every review row auto-merges;
        relock and close stay."""
        for meta, table in METADATA:
            with self.subTest(meta=meta):
                self.assertEqual(
                    decide(meta, policy="all"), "auto" if table == "review" else table
                )
        self.assertEqual(
            verdict.decide(STUB, "cargo", dependency_names="x", policy="all"), "auto"
        )

    def test_policy_all_never_merges_a_sticky_rule(self):
        """The engine pin moves only through `repo rollout`, whatever the policy."""
        got = verdict.decide(
            STUB,
            "github_actions",
            group="actions",
            dependency_names="example/stub-nix",
            engine_repo="Example/stub-nix",
            policy="all",
        )
        self.assertEqual(got, "review")

    def test_rule_conditions(self):
        product = dataclasses.replace(
            STUB,
            ci=CiConfig(
                decision_table=(
                    DecisionRule("uv", "close", update="major"),
                    DecisionRule("uv", "relock", directories=("tools/",)),
                    DecisionRule("uv", "auto", groups=("g",), names=("a*",)),
                )
            ),
        )

        def d(**kw):
            return verdict.decide(product, "uv", **kw)

        self.assertEqual(d(update_type=verdict.MAJOR), "close")
        self.assertEqual(d(directory="/tools"), "relock")
        self.assertEqual(d(directory="tools/"), "relock")
        self.assertEqual(d(group="g", dependency_names="abc"), "auto")
        self.assertEqual(d(group="g", dependency_names="b"), "review")
        self.assertEqual(d(group="h", dependency_names="abc"), "review")
        self.assertEqual(
            verdict.match(product, "uv", directory="/tools"),
            product.ci.decision_table[1],
        )
        self.assertIsNone(verdict.match(product, "npm_and_yarn"))

    def test_an_unset_param_matches_nothing(self):
        rule = STUB.ci.decision_table[2]
        self.assertEqual(verdict.patterns(STUB, rule, {}, ""), ["example/pilot*"])
        self.assertEqual(
            verdict.patterns(STUB, rule, {"docs-patterns": ["a", "b"]}, ""),
            ["a", "b", "example/pilot*"],
        )

    def test_the_kits_table(self):
        self.assertEqual(
            verdict.decide(KIT, "submodules", dependency_names="core"), "relock"
        )
        self.assertEqual(verdict.decide(KIT, "uv", dependency_names="ruff"), "review")
        rule = DecisionRule("nix", "close", names=("$rollout-inputs",))
        self.assertEqual(
            verdict.patterns(KIT, rule, {}, ""), ["kit-nix", "standards-profile"]
        )

    def test_nix_verdict(self):
        self.assertEqual(verdict.nix_verdict("empty"), "auto")
        self.assertEqual(verdict.nix_verdict("rollout-input-moved"), "close")
        self.assertEqual(verdict.nix_verdict("patch"), "wait")

    def test_problems(self):
        self.assertEqual(verdict.problems(STUB), [])
        product = dataclasses.replace(
            STUB,
            ci=CiConfig(
                decision_table=(
                    DecisionRule("uv", "merge"),
                    DecisionRule("uv", "auto", update="patch"),
                    DecisionRule("uv", "auto", names=("$nope", "$param:")),
                )
            ),
        )
        self.assertEqual(
            verdict.problems(product),
            [
                (
                    "ci.decision_table[0] (uv): verdict 'merge' is not one of auto,"
                    " review, relock, close"
                ),
                (
                    "ci.decision_table[1] (uv): update 'patch' is not one of any,"
                    " major, minor-patch"
                ),
                "ci.decision_table[2] (uv): unknown token '$nope'",
                "ci.decision_table[2] (uv): unknown token '$param:'",
            ],
        )

    def test_cli(self):
        out = self.output_file()
        code, stdout, _ = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "uv",
            "--update-type", "version-update:semver-minor", "--dependency-group", "tools",
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "auto"))
        self.assertEqual(out.read_text(), "verdict=auto\n")

    def test_policy_cli(self):
        code, stdout, _ = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "uv",
            "--update-type", "version-update:semver-major", "--policy", "all",
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "auto"))
        code, _, err = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "uv", "--policy", "everything"
        )
        self.assertEqual(code, 2, err)

    def test_params_and_engine_on_the_command_line(self):
        code, stdout, err = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "github_actions",
            "--dependency-names", "example/docs-tool",
            "--param", 'docs-patterns=["example/docs-*"]',
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "review"), err)
        code, stdout, err = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "github_actions",
            "--dependency-group", "actions", "--dependency-names", "example/stub-nix",
            "--engine-repo", "example/stub-nix", "--policy", "all",
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "review"), err)

    def test_bad_params_are_exit_2(self):
        for value, text in (
            ("docs-patterns=[", "--param docs-patterns: not JSON"),
            ('docs-patterns="x"', "--param docs-patterns must be a JSON list"),
            ("docs-patterns=[1]", "--param docs-patterns must be a JSON list"),
            ("no-equals", "give <key>=<JSON>"),
        ):
            with self.subTest(value=value):
                code, _, err = run_cli(
                    STUB, "ci", "verdict", "--ecosystem", "uv", "--param", value
                )
                self.assertEqual(code, 2, err)
                self.assertIn(text, err)


class TestArgAliases(FakeCase):
    """``ci.arg_aliases``: a product's old flags keep working (DESIGN §8.1)."""

    def test_param_alias(self):
        code, stdout, err = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "github_actions",
            "--dependency-names", "example/docs-tool",
            "--docs-patterns", '["example/docs-*"]',
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "review"), err)

    def test_plain_alias(self):
        code, stdout, err = run_cli(
            STUB, "ci", "verdict", "--ecosystem", "github_actions",
            "--dependency-group", "actions", "--dependency-names", "example/stub-nix",
            "--stub-nix-repo", "example/stub-nix",
        )  # fmt: skip
        self.assertEqual((code, stdout.strip()), (0, "review"), err)

    def test_aliases_only_where_the_target_exists(self):
        parser = cli.build_parser(STUB)
        ci = parser._subparsers._group_actions[0].choices["ci"]
        steps = ci._subparsers._group_actions[0].choices
        self.assertIn("--docs-patterns", steps["verdict"]._option_string_actions)
        self.assertIn("--docs-patterns", steps["automerge"]._option_string_actions)
        self.assertNotIn("--docs-patterns", steps["sweep"]._option_string_actions)
        self.assertIn("--stub-major", steps["fast-forward"]._option_string_actions)
        self.assertIn("--stub-major", steps["bump"]._option_string_actions)
        self.assertNotIn("--stub-major", steps["verdict"]._option_string_actions)

    def test_help_lists_aliases_with_product_text(self):
        product = dataclasses.replace(
            STUB,
            help=MappingProxyType(
                {"ci verdict --docs-patterns": "the docs patterns, as a JSON list"}
            ),
        )
        code, out, _ = run_cli(product, "ci", "verdict", "--help")
        self.assertEqual(code, 0)
        self.assertIn("the docs patterns, as a JSON list", out)
        code, out, _ = run_cli(STUB, "ci", "verdict", "--help")
        self.assertIn("same as --param docs-patterns", out)

    def test_version_scheme_choices(self):
        _, out, _ = run_cli(STUB, "ci", "fast-forward", "--help")
        self.assertIn("{semver,stub-major}", out)
        _, out, _ = run_cli(KIT, "ci", "fast-forward", "--help")
        self.assertIn("{semver,kit-series}", out)


class TestAutomerge(FakeCase):
    def apply(self, decision, product=STUB, **kw):
        return automerge.apply(
            product,
            decision,
            pr_url=PR,
            repo=REPO,
            integration="develop",
            gates=GATES,
            merge_method="squash",
            **kw,
        )

    def test_auto_when_protected(self):
        fake = self.use(Fake(protected([f"ci / {g}" for g in GATES])))
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("auto"), "merging")
        self.assertIn(("pr", "merge", "--auto", "--squash", PR), fake.clis)

    def test_unprotected_labels_with_notice(self):
        fake = self.use(Fake(protected(["ci / lint"])))  # lacks the other gates
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.apply("auto"), "needs-review")
        self.assertFalse(any(c[:2] == ("pr", "merge") for c in fake.clis))
        create = [
            i
            for i, c in enumerate(fake.clis)
            if c[:3] == ("label", "create", "needs-review")
        ]
        edit = [i for i, c in enumerate(fake.clis) if c[:2] == ("pr", "edit")]
        self.assertTrue(create and edit and create[0] < edit[0], fake.clis)
        self.assertIn("--force", fake.clis[create[0]])
        self.assertIn("d93f0b", fake.clis[create[0]])
        self.assertIn("::notice::", printed(p))
        self.assertIn("stub-nix repo doctor", printed(p))
        self.assertIn("ci / pr-policy, ci / lint, ci / test", printed(p))

    def test_no_rules_at_all(self):
        fake = self.use(
            Fake(
                {
                    ("GET", f"repos/{REPO}/rules/branches/develop"): [],
                    ("GET", f"repos/{REPO}/branches/develop"): {},
                }
            )
        )
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("auto"), "needs-review")
        self.assertFalse(any(c[:2] == ("pr", "merge") for c in fake.clis))

    def test_merge_auto_refused(self):
        fake = self.use(
            Fake(
                protected([f"ci / {g}" for g in GATES]),
                {
                    ("pr", "merge"): Result(
                        1, "", "GraphQL: Auto merge is not allowed for this repository"
                    )
                },
            )
        )
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.apply("auto"), "needs-review")
        self.assertTrue(
            any(c[:2] == ("pr", "edit") and "needs-review" in c for c in fake.clis)
        )
        self.assertIn("::notice::", printed(p))

    def test_a_failed_label_is_a_notice(self):
        self.use(Fake(cli_answers={("pr", "edit"): Result(1, "", "nope")}))
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.apply("review"), "needs-review")
        self.assertIn(f"could not label {PR} needs-review: nope", printed(p))

    def test_nix_never_auto_with_a_relock_patch(self):
        fake = self.use(Fake(protected([f"ci / {g}" for g in GATES])))
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("relock", relock_state="patch"), "wait")
            self.assertEqual(self.apply("relock", relock_state=""), "wait")
        self.assertEqual(fake.clis, [])
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("relock", relock_state="empty"), "merging")

    def test_nix_rollout_input_closes(self):
        fake = self.use(Fake())
        with mock.patch("builtins.print"):
            self.assertEqual(
                self.apply(
                    "relock", relock_state="rollout-input-moved", moved=["stub-nix"]
                ),
                "closed",
            )
        self.assertEqual(
            [c[:2] for c in fake.clis], [("pr", "comment"), ("pr", "close")]
        )
        self.assertIn(
            "stub-nix moves through `stub-nix repo rollout` (or a manual `nix flake"
            " update` and `stub-init --sync` commit)",
            fake.clis[0][-1],
        )

    def test_close_names_both_rollout_inputs_by_default(self):
        fake = self.use(Fake())
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("close", product=KIT), "closed")
        self.assertTrue(
            fake.clis[0][-1].startswith(
                "kit-nix or standards-profile moves through `kit-nix repo rollout`"
            )
        )

    def test_already_enabled_is_left_alone(self):
        """A job that runs again enables it again on the same PR."""
        fake = self.use(
            Fake(
                protected([f"ci / {g}" for g in GATES]),
                {
                    ("pr", "view"): Result(
                        0, json.dumps({"autoMergeRequest": {"mergeMethod": "SQUASH"}})
                    )
                },
            )
        )
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("auto"), "merging")
        self.assertFalse(any(c[:2] == ("pr", "merge") for c in fake.clis))

    def test_auto_merge_enabled_reads_garbage_as_off(self):
        self.use(Fake(cli_answers={("pr", "view"): Result(0, "not json")}))
        self.assertFalse(automerge.auto_merge_enabled(PR))

    def test_cli_policy_all_merges_a_major(self):
        fake = self.use(Fake(protected(["ci / lint", "ci / test"])))
        code, _, err = run_cli(
            STUB, "ci", "automerge", "--pr-url", PR, "--repo", REPO,
            "--integration", "develop", "--gates", "lint,test", "--ecosystem", "uv",
            "--update-type", "version-update:semver-major", "--policy", "all",
        )  # fmt: skip
        self.assertEqual(code, 0, err)
        self.assertIn(("pr", "merge", "--auto", "--squash", PR), fake.clis)

    def test_review_labels(self):
        fake = self.use(Fake())
        with mock.patch("builtins.print"):
            self.assertEqual(self.apply("review"), "needs-review")
        self.assertEqual(fake.clis[0][:3], ("label", "create", "needs-review"))

    def test_cli_exit_0_and_output(self):
        self.use(Fake(protected([])))
        out = self.output_file()
        code, _, err = run_cli(
            STUB, "ci", "automerge", "--pr-url", PR, "--repo", REPO,
            "--integration", "develop", "--gates", "lint,test", "--ecosystem", "uv",
            "--update-type", "version-update:semver-patch",
        )  # fmt: skip
        self.assertEqual(code, 0, err)
        self.assertEqual(out.read_text(), "result=needs-review\n")

    def test_cli_needs_a_verdict_or_metadata(self):
        self.use(Fake())
        code, _, err = run_cli(
            STUB, "ci", "automerge", "--pr-url", PR, "--repo", REPO,
            "--integration", "develop", "--gates", "lint",
        )  # fmt: skip
        self.assertEqual(code, 2, err)

    def test_cli_needs_a_repo(self):
        self.use(Fake())
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            code, _, err = run_cli(
                STUB, "ci", "enable-automerge", "--pr-url", PR,
                "--integration", "develop", "--gates", "lint",
            )  # fmt: skip
        self.assertEqual(code, 2, err)
        self.assertIn("give --repo", err)

    def test_labels_are_the_cores_and_the_products(self):
        self.assertEqual(set(labels(STUB)), {"dependencies", "needs-review", "extras"})
        self.assertEqual(set(labels(KIT)), {"dependencies", "needs-review"})


class TestGatesFromTheCheckout(CheckoutCase):
    def test_enable_reads_the_gates(self):
        fake = Fake(protected(["ci / pr-policy", "ci / lint", "ci / test"]))
        previous = github.use(fake)
        self.addCleanup(github.use, previous)
        code, out, err = self.fn(
            "ci", "enable-automerge", "--pr-url", PR, "--repo", REPO,
            "--integration", "develop",
        )  # fmt: skip
        self.assertEqual(code, 0, out + err)
        self.assertIn(("pr", "merge", "--auto", "--squash", PR), fake.clis)


class TestDispatch(FakeCase):
    def routes(self, sha, have, contexts=("ci / lint", "ci / pr-policy")):
        routes = {}
        for ctx in contexts:
            endpoint = (
                f"repos/{REPO}/commits/{sha}/check-runs"
                f"?check_name={ctx.replace(' ', '%20')}"
            )
            routes[("GET", endpoint)] = {"total_count": 1 if ctx in have else 0}
        for wf in ("ci.yml", "pr-policy.yml"):
            routes[
                ("GET", f"repos/{REPO}/contents/.github/workflows/{wf}?ref={sha}")
            ] = {"name": wf}
        return routes

    def test_release_pr_each_context_independently(self):
        sha = "a" * 40
        prs = [
            {
                "number": 3,
                "headRefName": "release-please--branches--develop",
                "headRefOid": sha,
            },
            {"number": 4, "headRefName": "evil", "headRefOid": "b" * 40},
        ]
        fake = self.use(
            Fake(
                self.routes(sha, {"ci / lint"}),
                {("pr", "list"): Result(0, json.dumps(prs))},
            )
        )
        with mock.patch("builtins.print"):
            done = release.dispatch_ci(REPO, "develop")
        self.assertEqual(done, ["pr-policy.yml"])
        head = prs[0]["headRefName"]
        runs = [c for c in fake.clis if c[:2] == ("workflow", "run")]
        self.assertEqual(
            runs,
            [
                ("workflow", "run", "pr-policy.yml", "--repo", REPO, "--ref", head),
            ],
        )

    def test_an_engine_repositorys_own_contexts(self):
        """An engine's own release flow: `lint` from check.yml, `pr-policy` from
        pr-policy.yml."""
        sha = "a" * 40
        head = "release-please--branches--main--components--stub-nix"
        prs = [{"number": 9, "headRefName": head, "headRefOid": sha}]
        routes = {
            ("GET", f"repos/{REPO}/commits/{sha}/check-runs?check_name=lint"): {
                "total_count": 0
            },
            ("GET", f"repos/{REPO}/commits/{sha}/check-runs?check_name=pr-policy"): {
                "total_count": 1
            },
            ("GET", f"repos/{REPO}/contents/.github/workflows/check.yml?ref={sha}"): {
                "name": "check.yml"
            },
            (
                "GET",
                f"repos/{REPO}/contents/.github/workflows/pr-policy.yml?ref={sha}",
            ): {"name": "pr-policy.yml"},
        }
        fake = self.use(Fake(routes, {("pr", "list"): Result(0, json.dumps(prs))}))
        code, out, err = run_cli(
            STUB, "ci", "dispatch-ci", "--repo", REPO, "--integration", "main",
            "--package", "stub-nix", "--context", "lint=check.yml",
            "--context", "pr-policy=pr-policy.yml",
        )  # fmt: skip
        self.assertEqual(code, 0, out + err)
        runs = [c for c in fake.clis if c[:2] == ("workflow", "run")]
        self.assertEqual(
            runs, [("workflow", "run", "check.yml", "--repo", REPO, "--ref", head)]
        )

    def test_the_products_contexts_are_the_default(self):
        """The kit's two contexts come from one workflow: it is dispatched once."""
        sha = "a" * 40
        prs = [
            {
                "number": 3,
                "headRefName": "release-please--branches--develop",
                "headRefOid": sha,
            }
        ]
        fake = self.use(
            Fake(self.routes(sha, set()), {("pr", "list"): Result(0, json.dumps(prs))})
        )
        code, out, err = run_cli(
            KIT, "ci", "dispatch-ci", "--repo", REPO, "--integration", "develop"
        )
        self.assertEqual(code, 0, out + err)
        runs = [c[2] for c in fake.clis if c[:2] == ("workflow", "run")]
        self.assertEqual(runs, ["ci.yml"])

    def test_a_failed_dispatch_is_a_notice(self):
        sha = "a" * 40
        self.use(
            Fake(
                self.routes(sha, set()),
                {("workflow", "run"): Result(1, "", "HTTP 422")},
            )
        )
        with mock.patch("builtins.print") as p:
            self.assertEqual(release.ensure_ci(REPO, "x", sha), [])
        self.assertIn("could not dispatch ci.yml on x: HTTP 422", printed(p))

    def test_no_release_pr(self):
        self.use(Fake(cli_answers={("pr", "list"): Result(0, "[]")}))
        with mock.patch("builtins.print") as p:
            self.assertEqual(release.dispatch_ci(REPO, "develop"), [])
        self.assertIn("no open release PR", printed(p))

    def test_pr_list_failure_is_exit_3(self):
        self.use(Fake(cli_answers={("pr", "list"): Result(1, "", "boom")}))
        code, _, err = run_cli(STUB, "ci", "sweep", "--repo", REPO)
        self.assertEqual(code, 3, err)

    def test_bad_context_is_exit_2(self):
        self.use(Fake())
        code, _out, err = run_cli(
            STUB, "ci", "dispatch-ci", "--repo", REPO, "--integration", "main",
            "--context", "lint",
        )  # fmt: skip
        self.assertEqual(code, 2, err)

    def test_sweep_bots_only(self):
        sha = "c" * 40
        prs = [
            {
                "number": 1,
                "author": {"login": "app/dependabot"},
                "headRefName": "dependabot/uv/x",
                "headRefOid": sha,
            },
            {
                "number": 2,
                "author": {"login": "someone"},
                "headRefName": "feat",
                "headRefOid": "d" * 40,
            },
        ]
        fake = self.use(
            Fake(self.routes(sha, set()), {("pr", "list"): Result(0, json.dumps(prs))})
        )
        out = self.output_file()
        code, _, err = run_cli(STUB, "ci", "sweep", "--repo", REPO)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.read_text(), "dispatched=ci.yml,pr-policy.yml\n")
        self.assertTrue(
            all(
                "dependabot/uv/x" in c
                for c in fake.clis
                if c[:2] == ("workflow", "run")
            )
        )


class TestFastForward(FakeCase):
    """The tags flow: the scheme's tag rule first, then the branch must equal the tag."""

    SHA = "e" * 40

    def routes(self, branch_at, tag="v1.3.0", tag_at=None, exists=True):
        routes = {
            ("GET", f"repos/{REPO}/git/ref/tags/{tag}"): {
                "object": {"type": "tag", "sha": "f" * 40}
            },
            ("GET", f"repos/{REPO}/git/tags/{'f' * 40}"): {
                "object": {"type": "commit", "sha": tag_at or self.SHA}
            },
            ("PATCH", f"repos/{REPO}/git/refs/heads/version-16"): {},
            ("POST", f"repos/{REPO}/git/refs"): {},
        }
        if exists:
            routes[("GET", f"repos/{REPO}/git/ref/heads/version-16")] = {
                "object": {"sha": branch_at}
            }
        return routes

    def ff(self, tag="v1.3.0", scheme=SEMVER, **kw):
        return release.FastForward(
            repo=REPO,
            branch="version-16",
            sha=self.SHA,
            tag=tag,
            scheme=scheme,
            cfg={"major": 16},
            **kw,
        )

    def test_semver_dry_run_passes(self):
        fake = self.use(Fake(self.routes(self.SHA)))
        with mock.patch("builtins.print"):
            ff = self.ff(dry_run=True)
            self.assertEqual(ff.run(), 0)
        self.assertEqual(fake.writes(), [])
        self.assertTrue(
            ff.calls[0].startswith("PATCH repos/example/unit/git/refs/heads/version-16")
        )
        self.assertEqual(ff.moved, "true")

    def test_semver_read_back_differs(self):
        self.use(Fake(self.routes("0" * 40)))
        with mock.patch("builtins.print") as p:
            ff = self.ff()
            self.assertEqual(ff.run(), 1)
        self.assertIn("::error::", printed(p))
        self.assertIn("(D8)", printed(p))
        self.assertEqual(ff.moved, "false")

    def test_scheme_refuses_before_any_call(self):
        fake = self.use(Fake(self.routes(self.SHA)))
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.ff(scheme=STUB_MAJOR, dry_run=True).run(), 1)
        self.assertEqual(fake.requests, [])
        self.assertIn(
            "release tag v1.3.0: 1.3.0 is not on major 16; under version-scheme ="
            " stub-major version-16 does not take it",
            printed(p),
        )

    def test_scheme_accepts_its_major(self):
        self.use(Fake(self.routes(self.SHA, tag="v16.1.0")))
        with mock.patch("builtins.print"):
            self.assertEqual(
                self.ff(tag="v16.1.0", scheme=STUB_MAJOR, dry_run=True).run(), 0
            )

    def test_first_release_creates_and_dispatches_after(self):
        state = {"created": False}
        routes = self.routes(self.SHA, exists=False)

        def ref():
            if not state["created"]:
                return 404
            return {"object": {"sha": self.SHA}}

        def create():
            state["created"] = True
            return {}

        routes[("GET", f"repos/{REPO}/git/ref/heads/version-16")] = ref
        routes[("POST", f"repos/{REPO}/git/refs")] = create
        fake = self.use(Fake(routes))
        with mock.patch("builtins.print"):
            self.assertEqual(self.ff(dispatch_after=("assets.yml",)).run(), 0)
        self.assertEqual(
            [w[:2] for w in fake.writes()], [("POST", f"repos/{REPO}/git/refs")]
        )
        self.assertIn(
            ("workflow", "run", "assets.yml", "--repo", REPO, "--ref", "version-16"),
            fake.clis,
        )

    def test_a_failed_dispatch_after_is_exit_1(self):
        self.use(
            Fake(
                self.routes(self.SHA),
                {("workflow", "run"): Result(1, "", "no such workflow")},
            )
        )
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.ff(dispatch_after=("assets.yml",)).run(), 1)
        self.assertIn("could not dispatch assets.yml", printed(p))

    def test_a_refused_move_names_the_workflow_file_cause(self):
        """GitHub refuses GITHUB_TOKEN's ref update onto commits that change
        .github/workflows/: exit 1 with the cause and the manual PATCH, not a
        traceback."""
        routes = self.routes("0" * 40)
        routes[("PATCH", f"repos/{REPO}/git/refs/heads/version-16")] = 403
        self.use(Fake(routes))
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.ff().run(), 1)
        out = printed(p)
        self.assertIn("::error::", out)
        self.assertIn(".github/workflows/", out)
        self.assertIn("GitHub App with Workflows write", out)
        self.assertIn(
            f"gh api -X PATCH repos/{REPO}/git/refs/heads/version-16 -f sha={self.SHA}"
            " -F force=false",
            out,
        )

    def test_another_http_error_is_raised(self):
        routes = self.routes("0" * 40)
        routes[("PATCH", f"repos/{REPO}/git/refs/heads/version-16")] = 422
        self.use(Fake(routes))
        with self.assertRaises(GitHubError):
            self.ff().run()

    def test_dry_run_refuses_a_commit_that_is_not_the_tag(self):
        fake = self.use(Fake(self.routes(self.SHA, tag_at="0" * 40)))
        with mock.patch("builtins.print"):
            self.assertEqual(self.ff(dry_run=True).run(), 1)
        self.assertEqual(fake.writes(), [])

    def test_an_engines_first_release_dry_run(self):
        """An engine's release.yml: release-1 does not exist yet, v1.0.0 creates it."""
        routes = {
            ("GET", f"repos/{REPO}/git/ref/tags/v1.0.0"): {
                "object": {"type": "commit", "sha": self.SHA}
            },
            ("GET", f"repos/{REPO}/git/ref/heads/release-1"): 404,
        }
        fake = self.use(Fake(routes))
        out_file = self.output_file()
        code, out, err = run_cli(
            STUB, "ci", "fast-forward", "--repo", REPO, "--release-branch", "release-1",
            "--sha", self.SHA, "--tag", "v1.0.0", "--version-scheme", "semver",
            "--tag-prefix", "v", "--dry-run",
        )  # fmt: skip
        self.assertEqual(code, 0, out + err)
        self.assertEqual(fake.writes(), [])
        self.assertIn(
            f'dry run: POST repos/{REPO}/git/refs {{"ref": "refs/heads/release-1",'
            f' "sha": "{self.SHA}"}}',
            out,
        )
        self.assertIn(f"release-1 = v1.0.0 = {self.SHA}", out)
        self.assertEqual(out_file.read_text(), "moved=true\n")

    def test_an_engines_next_release_dry_run_patches(self):
        routes = {
            ("GET", f"repos/{REPO}/git/ref/tags/v1.1.0"): {
                "object": {"type": "commit", "sha": self.SHA}
            },
            ("GET", f"repos/{REPO}/git/ref/heads/release-1"): {
                "object": {"sha": "1" * 40}
            },
        }
        self.use(Fake(routes))
        code, out, err = run_cli(
            STUB, "ci", "fast-forward", "--repo", REPO, "--release-branch", "release-1",
            "--sha", self.SHA, "--tag", "v1.1.0", "--version-scheme", "semver",
            "--tag-prefix", "v", "--dry-run",
        )  # fmt: skip
        self.assertEqual(code, 0, out + err)
        self.assertIn(
            f"dry run: PATCH repos/{REPO}/git/refs/heads/release-1"
            f' {{"force": false, "sha": "{self.SHA}"}}',
            out,
        )

    def test_cli(self):
        self.use(Fake(self.routes(self.SHA)))
        code, out, err = run_cli(
            STUB, "ci", "fast-forward", "--repo", REPO, "--release-branch",
            "version-16", "--sha", self.SHA, "--tag", "v1.3.0",
            "--version-scheme", "semver", "--tag-prefix", "v", "--stub-major", "16",
            "--dry-run",
        )  # fmt: skip
        self.assertEqual(code, 0, out + err)

    def test_empty_release_branch_is_exit_2(self):
        code, _, err = run_cli(
            STUB, "ci", "fast-forward", "--repo", REPO, "--release-branch", "",
            "--sha", self.SHA, "--tag", "v1.0.0", "--version-scheme", "semver",
        )  # fmt: skip
        self.assertEqual(code, 2, err)
        self.assertIn("no release branch", err)


class TestFastForwardFromTheCheckout(CheckoutCase):
    """Without --version-scheme the checkout's scheme, cfg and facts apply; --set
    overrides a key for the step."""

    extra_pyproject = '\n[tool.stub-nix.releases]\nversion-scheme = "stub-major"\n'
    SHA = "e" * 40

    def setUp(self):
        super().setUp()
        self.fake = Fake(
            {
                ("GET", f"repos/{REPO}/git/ref/tags/v1.3.0"): {
                    "object": {"type": "commit", "sha": self.SHA}
                },
                ("GET", f"repos/{REPO}/git/ref/heads/version-16"): {
                    "object": {"sha": "1" * 40}
                },
            }
        )
        previous = github.use(self.fake)
        self.addCleanup(github.use, previous)

    def ff(self, *extra):
        return self.fn(
            "ci", "fast-forward", "--repo", REPO, "--release-branch", "version-16",
            "--sha", self.SHA, "--tag", "v1.3.0", "--dry-run", *extra,
        )  # fmt: skip

    def test_the_checkouts_scheme_refuses(self):
        code, out, _ = self.ff()
        self.assertEqual(code, 1)
        self.assertIn("1.3.0 is not on major 16", out)
        self.assertEqual(self.fake.requests, [])

    def test_set_overrides_the_checkouts_key(self):
        code, out, err = self.ff("--set", "major=1")
        self.assertEqual(code, 0, out + err)
        code, _, err = self.ff("--set", "major=[")
        self.assertEqual(code, 2, err)
        self.assertIn("--set major: not JSON", err)

    def test_set_alias(self):
        """``--stub-major N`` is ``--set major=N`` (``ci.arg_aliases``)."""
        code, out, err = self.ff("--stub-major", "1")
        self.assertEqual(code, 0, out + err)
        code, out, _ = self.ff("--stub-major", "16")
        self.assertEqual(code, 1)
        self.assertIn("1.3.0 is not on major 16", out)

    def test_an_explicit_scheme_wins(self):
        code, out, err = self.ff("--version-scheme", "semver")
        self.assertEqual(code, 0, out + err)


class TestNestedConfigFile(NestedConfigCase):
    """``identity.config_file = "sub/pyproject.toml"``: the steps that fall back to the
    checkout read it at the work tree's root, from the root and from below it
    (§15.CR1)."""

    extra_pyproject = TestFastForwardFromTheCheckout.extra_pyproject
    SHA = TestFastForwardFromTheCheckout.SHA

    def setUp(self):
        super().setUp()
        self.fake = Fake(
            {
                ("GET", f"repos/{REPO}/git/ref/tags/v1.3.0"): {
                    "object": {"type": "commit", "sha": self.SHA}
                },
                ("GET", f"repos/{REPO}/git/ref/heads/version-16"): {
                    "object": {"sha": "1" * 40}
                },
                **protected(["ci / pr-policy", "ci / lint", "ci / test"]),
            }
        )
        previous = github.use(self.fake)
        self.addCleanup(github.use, previous)

    def test_fast_forward_reads_the_checkouts_scheme(self):
        for cwd in (self.root, self.root / "sub", self.root / "demo_unit"):
            code, out, err = self.fn(
                "ci", "fast-forward", "--repo", REPO, "--release-branch", "version-16",
                "--sha", self.SHA, "--tag", "v1.3.0", "--dry-run", cwd=cwd,
            )  # fmt: skip
            self.assertEqual(code, 1, f"{cwd}: {out}{err}")
            self.assertIn("1.3.0 is not on major 16", out)

    def test_enable_reads_the_gates(self):
        for cwd in (self.root, self.root / "sub"):
            self.fake.clis.clear()
            code, out, err = self.fn(
                "ci", "enable-automerge", "--pr-url", PR, "--repo", REPO,
                "--integration", "develop", cwd=cwd,
            )  # fmt: skip
            self.assertEqual(code, 0, f"{cwd}: {out}{err}")
            self.assertIn(("pr", "merge", "--auto", "--squash", PR), self.fake.clis)


def runs(*specs):
    """Check runs: (name, status, conclusion[, started_at[, id]])."""
    out = []
    for i, spec in enumerate(specs):
        name, status, conclusion, *rest = spec
        started = rest[0] if rest else "2026-01-01T00:00:00Z"
        out.append(
            {
                "id": rest[1] if len(rest) > 1 else i + 1,
                "name": name,
                "status": status,
                "conclusion": conclusion,
                "started_at": started,
            }
        )
    return {"total_count": len(out), "check_runs": out}


class TestTaglessFastForward(FakeCase):
    """The post-merge-bump flow: the integration head's required checks guard the move."""

    SHA = "c" * 40
    CHECKS = ("GET", f"repos/{REPO}/commits/{'c' * 40}/check-runs?per_page=100")

    def routes(self, check_runs, required=("ci / lint", "ci / test")):
        routes = {
            **protected(required),
            self.CHECKS: check_runs,
            ("GET", f"repos/{REPO}/git/ref/heads/18.0"): {"object": {"sha": self.SHA}},
            ("PATCH", f"repos/{REPO}/git/refs/heads/18.0"): {},
        }
        return routes

    def ff(self, **kw):
        kw.setdefault("sleep", lambda _s: None)
        return release.FastForward(
            repo=REPO, branch="18.0", sha=self.SHA, integration="develop", **kw
        )

    def test_green_moves(self):
        fake = self.use(
            Fake(
                self.routes(
                    runs(
                        ("ci / lint", "completed", "success"),
                        ("ci / test", "completed", "skipped"),
                    )
                )
            )
        )
        with mock.patch("builtins.print") as p:
            ff = self.ff()
            self.assertEqual(ff.run(), 0)
        self.assertEqual(ff.moved, "true")
        self.assertEqual(
            fake.writes(),
            [
                (
                    "PATCH",
                    f"repos/{REPO}/git/refs/heads/18.0",
                    {"sha": self.SHA, "force": False},
                )
            ],
        )
        self.assertIn(f"18.0 = {self.SHA}", printed(p))

    def test_pending_is_exit_0_and_writes_nothing(self):
        fake = self.use(Fake(self.routes(runs(("ci / lint", "in_progress", None)))))
        with mock.patch("builtins.print"):
            ff = self.ff()
            self.assertEqual(ff.run(), 0)
        self.assertEqual(ff.moved, "pending")
        self.assertEqual(fake.writes(), [])

    def test_failed_is_exit_1(self):
        fake = self.use(
            Fake(
                self.routes(
                    runs(
                        ("ci / lint", "completed", "failure"),
                        ("ci / test", "completed", "success"),
                    )
                )
            )
        )
        with mock.patch("builtins.print") as p:
            ff = self.ff()
            self.assertEqual(ff.run(), 1)
        self.assertEqual((ff.moved, fake.writes()), ("false", []))
        self.assertIn("a required check failed", printed(p))

    def test_nothing_protects_the_integration_branch(self):
        fake = self.use(Fake(self.routes(runs(), required=())))
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.ff().run(), 1)
        self.assertIn("nothing protects develop: refusing to fast-forward", printed(p))
        self.assertEqual(fake.writes(), [])

    def test_the_read_back_must_be_the_sha(self):
        routes = self.routes(
            runs(
                ("ci / lint", "completed", "success"),
                ("ci / test", "completed", "success"),
            )
        )
        routes[("GET", f"repos/{REPO}/git/ref/heads/18.0")] = {
            "object": {"sha": "0" * 40}
        }
        self.use(Fake(routes))
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.ff().run(), 1)
        self.assertIn("the release branch must equal the integration head", printed(p))

    def test_wait_checks_polls_until_green(self):
        answers = iter(
            [
                runs(("ci / lint", "queued", None), ("ci / test", "queued", None)),
                runs(
                    ("ci / lint", "completed", "success"),
                    ("ci / test", "in_progress", None),
                ),
                runs(
                    ("ci / lint", "completed", "success"),
                    ("ci / test", "completed", "neutral"),
                ),
            ]
        )
        routes = self.routes(lambda: next(answers))
        self.use(Fake(routes))
        slept = []
        state = release.wait_checks(
            REPO, self.SHA, {"ci / lint", "ci / test"}, 60, sleep=slept.append
        )
        self.assertEqual((state, slept), ("green", [15.0, 15.0]))

    def test_wait_checks_times_out_pending(self):
        self.use(Fake(self.routes(runs(("ci / lint", "queued", None)))))
        slept = []
        state = release.wait_checks(
            REPO, self.SHA, {"ci / lint"}, 40, sleep=slept.append
        )
        self.assertEqual((state, slept), ("pending", [15.0, 15.0, 10.0]))
        slept.clear()
        self.assertEqual(
            release.wait_checks(REPO, self.SHA, {"ci / lint"}, 0, sleep=slept.append),
            "pending",
        )
        self.assertEqual(slept, [])

    def test_cli_pending_output(self):
        self.use(Fake(self.routes(runs(("ci / lint", "queued", None)))))
        out = self.output_file()
        code, stdout, err = run_cli(
            STUB, "ci", "fast-forward", "--repo", REPO, "--release-branch", "18.0",
            "--sha", self.SHA, "--integration", "develop", "--version-scheme", "semver",
        )  # fmt: skip
        self.assertEqual(code, 0, stdout + err)
        self.assertEqual(out.read_text(), "moved=pending\n")
        self.assertIn("still pending", stdout)


class TestDrift(FakeCase):
    def test_open_and_close(self):
        root = Path(tempfile.mkdtemp())
        issues = []
        fake = self.use(
            Fake(
                {
                    ("GET", f"repos/{REPO}/issues?state=open&per_page=100"): lambda: (
                        issues
                    ),
                    ("POST", f"repos/{REPO}/issues"): {},
                    ("PATCH", f"repos/{REPO}/issues/9"): {},
                }
            )
        )
        from packaging.version import Version

        with mock.patch(
            "standards_core.scaffold.defaults.head_version",
            return_value=Version("1.0.0"),
        ):
            self.assertEqual(
                drift.run(STUB, root, REPO, "v1.2.0", check_profile=False),
                {"stub-nix": "opened"},
            )
            post = fake.writes()[-1]
            self.assertEqual(post[2]["title"], "stub-nix v1.2.0 available")
            self.assertIn(
                f"stub-nix repo rollout --to v1.2.0 --repo {REPO}", post[2]["body"]
            )
            self.assertIn("pin stub-nix v1.0.0; v1.2.0 is released", post[2]["body"])
            issues.append({"number": 9, "title": "stub-nix v1.2.0 available"})
            self.assertEqual(
                drift.run(STUB, root, REPO, "v1.3.0", "1 file drifted\n", False),
                {"stub-nix": "updated"},
            )
            self.assertIn(
                "Drift against the latest release", fake.writes()[-1][2]["body"]
            )
            self.assertEqual(
                drift.run(STUB, root, REPO, "v1.0.0", check_profile=False),
                {"stub-nix": "closed"},
            )
            self.assertEqual(
                fake.writes()[-1],
                ("PATCH", f"repos/{REPO}/issues/9", {"state": "closed"}),
            )

    def test_another_products_issue_is_not_ours(self):
        self.assertTrue(drift.engine_title(STUB).match("stub-nix v2.0.0 available"))
        self.assertFalse(drift.engine_title(STUB).match("kit-nix v2.0.0 available"))

    def test_a_bad_latest_is_exit_2(self):
        self.use(Fake())
        code, _, err = run_cli(
            STUB, "ci", "drift", "--repo", REPO, "--latest", "nope", "--no-profile"
        )
        self.assertEqual(code, 2, err)

    def test_profile_issue(self):
        root = Path(tempfile.mkdtemp())
        lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"standards-profile": "standards-profile"}},
                "standards-profile": {
                    "locked": {
                        "type": "github",
                        "owner": "example",
                        "repo": "profile",
                        "rev": "1" * 40,
                    },
                    "original": {
                        "type": "github",
                        "owner": "example",
                        "repo": "profile",
                        "ref": "v1",
                    },
                },
            },
        }
        (root / "flake.lock").write_text(json.dumps(lock))
        fake = self.use(
            Fake(
                {
                    ("GET", f"repos/{REPO}/issues?state=open&per_page=100"): [],
                    ("POST", f"repos/{REPO}/issues"): {},
                }
            )
        )
        with (
            mock.patch(
                "standards_core.scaffold.defaults.head_version", return_value=None
            ),
            mock.patch(
                "standards_core.ci.drift.ls_remote", return_value="2" * 40
            ) as ls,
        ):
            out = drift.run(STUB, root, REPO, "v1.0.0")
        ls.assert_called_with("https://github.com/example/profile", "v1")
        self.assertEqual(out["profile"], "opened")
        self.assertEqual(
            fake.writes()[-1][2]["title"], "standards profile update available"
        )
        self.assertIn(
            "stub-nix repo rollout --profile-to latest", fake.writes()[-1][2]["body"]
        )

    def test_profile_remote_kinds(self):
        def lock(original):
            return {
                "nodes": {
                    "root": {"inputs": {"standards-profile": "p"}},
                    "p": {"original": original, "locked": {"rev": "r"}},
                }
            }

        self.assertEqual(
            drift.profile_remote(lock({"type": "gitlab", "owner": "o", "repo": "r"})),
            ("https://gitlab.com/o/r", "HEAD", "r"),
        )
        self.assertEqual(
            drift.profile_remote(lock({"type": "git", "url": "https://x/y"})),
            ("https://x/y", "HEAD", "r"),
        )
        self.assertIsNone(drift.profile_remote(lock({"type": "path"})))
        self.assertIsNone(drift.profile_remote({"nodes": {}}))


if __name__ == "__main__":
    unittest.main()
