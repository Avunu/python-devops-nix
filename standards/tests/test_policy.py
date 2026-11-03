"""``<cli> policy``: the commit-msg hook and the pr-policy check (DESIGN §2.5, §6.2)."""

import dataclasses
import os
import subprocess
import unittest
from unittest import mock

from support import KIT, STUB, git
from support.checkouts import CheckoutCase, NestedConfigCase

from standards_core.ci.conventional import SEMVER
from standards_core.commands import policy
from standards_core.common.report import EnvError

STUB_MAJOR = '\n[tool.stub-nix.releases]\nversion-scheme = "stub-major"\n'


def rules(scheme=None, **cfg):
    return policy.Rules(scheme=scheme, cfg=cfg)


class TestMessageRules(unittest.TestCase):
    def setUp(self):
        self.major = rules(STUB.version_schemes["stub-major"], major=16)

    def test_stub_major_rejects(self):
        for msg in (
            "feat!: x",
            "fix(api)!: y",
            "fix: y\n\nBREAKING CHANGE: z",
            "fix: y\n\nBREAKING-CHANGE: z",
            "chore: x\n\nRelease-As: 17.0.0",
        ):
            self.assertTrue(policy.message_problems(STUB, msg, self.major), msg)

    def test_stub_major_accepts(self):
        for msg in (
            "chore(deps): bump foo from 1 to 2",
            "chore(develop): release 16.1.0",
            "feat: x\n\nRelease-As: 16.0.0",
        ):
            self.assertEqual(policy.message_problems(STUB, msg, self.major), [], msg)

    def test_semver_and_no_scheme_accept_bang(self):
        for r in (rules(SEMVER), rules()):
            self.assertEqual(policy.message_problems(STUB, "feat!: x", r), [])
            self.assertEqual(
                policy.message_problems(STUB, "feat: x\n\nBREAKING CHANGE: y", r), []
            )

    def test_comment_lines_are_ignored(self):
        self.assertEqual(
            policy.message_problems(STUB, "# feat!: in a comment\nfix: x", self.major),
            [],
        )

    def test_no_bang_names_the_product(self):
        found = policy.message_problems(STUB, "feat!: x", policy.Rules(no_bang=True))
        self.assertEqual(
            found,
            [
                (
                    "'feat!: x': `!` marks a major bump, and a stub-nix major is made"
                    " only with a `Release-As: <N>.0.0` footer"
                )
            ],
        )
        self.assertEqual(
            policy.message_problems(
                STUB, "chore: x\n\nRelease-As: 2.0.0", policy.Rules(no_bang=True)
            ),
            [],
        )


class PolicyCase(CheckoutCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(policy, "run_committed", return_value=(True, ""))
        self.committed = patcher.start()
        self.addCleanup(patcher.stop)
        self.heads: list[dict] = []
        self.prs: list[dict] = []

        def gh_json(*args):
            if "--head" in args:
                return self.prs
            return self.heads

        patcher = mock.patch.object(policy, "gh_json", side_effect=gh_json)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("GITHUB_ACTIONS", "GITHUB_REF", "PR_NUMBER", "PR_AUTHOR"):
            os.environ.pop(key, None)

    def branch(self, name, subject):
        git(self.root, "checkout", "-q", "-b", name)
        self.write("demo_unit/x.py", f"# {name}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", subject)
        return git(self.root, "rev-parse", "HEAD").strip()

    def pr(self, base, title, head_sha, **env):
        values = {
            "EVENT_NAME": "pull_request",
            "BASE_REF": base,
            "PR_TITLE": title,
            "HEAD_SHA": head_sha,
            **env,
        }
        with mock.patch.dict(os.environ, values):
            return self.fn("policy", "--pr")


class TestCommitMsg(PolicyCase):
    def test_semver_hook_accepts_bang(self):
        self.write("msg", "feat!: x\n")
        self.assertEqual(
            self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 0
        )

    def test_not_opted_in_accepts(self):
        self.write("pyproject.toml", '[project]\nname = "demo_unit"\n')
        self.commit()
        self.write("msg", "feat!: x\n")
        code, out, err = self.fn("policy", "--commit-msg-file", str(self.root / "msg"))
        self.assertEqual(code, 0, out + err)

    def test_no_mode_is_exit_2(self):
        code, _out, err = self.fn("policy")
        self.assertEqual(code, 2, err)
        self.assertIn("give --commit-msg-file F or --pr", err)


class TestNestedConfigFile(NestedConfigCase):
    """``identity.config_file = "sub/pyproject.toml"``: the policy reads the checkout
    at the work tree's root, from the root and from below it (§15.CR1)."""

    extra_pyproject = STUB_MAJOR

    def test_the_hook_reads_the_nested_file(self):
        self.write("msg", "feat!: x\n")
        (self.root / "demo_unit").mkdir(exist_ok=True)
        for cwd in (self.root, self.root / "sub", self.root / "demo_unit"):
            code, out, err = self.fn(
                "policy", "--commit-msg-file", str(self.root / "msg"), cwd=cwd
            )
            self.assertEqual(code, 1, f"{cwd}: {out}{err}")
            self.assertIn("breaking change", out + err)
        self.write("msg", "fix: x\n")
        code, out, err = self.fn("policy", "--commit-msg-file", str(self.root / "msg"))
        self.assertEqual(code, 0, out + err)

    def test_checkout_root_is_the_work_tree(self):
        for cwd in (self.root, self.root / "sub"):
            with mock.patch.object(policy.Path, "cwd", return_value=cwd):
                self.assertEqual(
                    policy.checkout_root(self.product, False), self.root.resolve()
                )


class TestCommitMsgStubMajor(PolicyCase):
    extra_pyproject = STUB_MAJOR

    def test_rejects_bang_and_wrong_release_as(self):
        self.write("msg", "feat!: x\n")
        code, _out, err = self.fn("policy", "--commit-msg-file", str(self.root / "msg"))
        self.assertEqual(code, 1, err)
        self.write("msg", "chore: x\n\nRelease-As: 17.0.0\n")
        self.assertEqual(
            self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 1
        )
        self.write("msg", "chore(deps): bump x\n")
        self.assertEqual(
            self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 0
        )

    def test_releases_off_drops_the_scheme(self):
        self.write("pyproject.toml", self.read("pyproject.toml") + "enable = false\n")
        self.write("msg", "feat!: x\n")
        self.assertEqual(
            self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 0
        )

    def test_github_actions_annotations(self):
        self.write("msg", "feat!: x\n")
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            code, _out, err = self.fn(
                "policy", "--commit-msg-file", str(self.root / "msg")
            )
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("::error::"), err)

    def test_pr_commit_with_bang_fails(self):
        sha = self.branch("feature", "feat!: break it")
        code, _out, err = self.pr("develop", "feat: fine title", sha)
        self.assertEqual(code, 1)
        self.assertIn(f"commit {sha[:12]}: a breaking change", err)

    def test_pr_title_with_bang_fails(self):
        sha = self.branch("feature", "feat: fine")
        code, _out, err = self.pr("develop", "feat!: title", sha)
        self.assertEqual(code, 1)
        self.assertIn("title", err)


class TestPrBases(PolicyCase):
    def test_integration_base_passes(self):
        sha = self.branch("feature", "feat: x")
        code, out, err = self.pr("develop", "feat: x", sha)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.committed.called)
        self.assertIn("stub-nix policy: ok", out)

    def test_stacked_base_passes_when_allowed(self):
        git(self.root, "branch", "parent-branch")
        sha = self.branch("feature", "feat: x")
        self.heads = [{"headRefName": "parent-branch"}]
        self.assertEqual(self.pr("parent-branch", "feat: x", sha)[0], 0)

    def test_unknown_base_fails_with_retarget(self):
        sha = self.branch("feature", "feat: x")
        code, _out, err = self.pr(
            "elsewhere", "feat: x", sha, GITHUB_REF="refs/pull/7/merge"
        )
        self.assertEqual(code, 1)
        self.assertIn("retarget: gh pr edit 7 --base develop", err)

    def test_empty_base_and_head_are_exit_2(self):
        sha = self.branch("feature", "feat: x")
        self.assertEqual(self.pr("", "feat: x", sha)[0], 2)
        self.assertEqual(self.pr("develop", "feat: x", "")[0], 2)

    def test_unfetched_base(self):
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml")
            + '\n[tool.stub-nix.commits]\npr-allowed-bases = ["gone"]\n',
        )
        self.commit()
        sha = self.branch("feature", "feat: x")
        code, _out, err = self.pr("gone", "feat: x", sha)
        self.assertEqual(code, 1)
        self.assertIn("the base branch 'gone' is not fetched", err)


class TestPrNoStacking(PolicyCase):
    extra_pyproject = (
        "\n[tool.stub-nix.commits]\nallow-stacked = false\n"
        'pr-allowed-bases = ["version-*"]\n'
    )

    def test_stacked_base_fails(self):
        git(self.root, "branch", "parent-branch")
        sha = self.branch("feature", "feat: x")
        self.heads = [{"headRefName": "parent-branch"}]
        self.assertEqual(self.pr("parent-branch", "feat: x", sha)[0], 1)

    def test_allowed_base_glob_passes(self):
        git(self.root, "branch", "version-16")
        sha = self.branch("feature", "fix: x")
        self.assertEqual(self.pr("version-16", "fix: x", sha)[0], 0)


class TestPrMainTags(PolicyCase):
    extra_pyproject = '\n[tool.stub-nix.releases]\nbranching = "main+tags"\n'

    def setUp(self):
        super().setUp()
        self.table('integration-branch = "main"\n')
        self.commit()
        git(self.root, "branch", "main")

    def test_develop_base_fails_naming_main(self):
        sha = self.branch("feature", "feat: x")
        code, _out, err = self.pr("develop", "feat: x", sha)
        self.assertEqual(code, 1)
        self.assertIn("--base main", err)


class TestDispatch(PolicyCase):
    def dispatch(self, sha):
        env = {
            "EVENT_NAME": "workflow_dispatch",
            "REF_NAME": "feature",
            "HEAD_SHA": sha,
            "BASE_REF": "",
            "PR_TITLE": "",
        }
        with mock.patch.dict(os.environ, env):
            return self.fn("policy", "--pr")

    def test_no_pr_passes(self):
        sha = self.branch("feature", "feat: x")
        code, out, _err = self.dispatch(sha)
        self.assertEqual(code, 0)
        self.assertIn("stub-nix policy: no open PR for 'feature'", out)

    def test_stale_dispatch_fails(self):
        sha = self.branch("feature", "feat: x")
        self.prs = [
            {
                "number": 3,
                "title": "feat: x",
                "baseRefName": "develop",
                "headRefOid": "0" * 40,
            }
        ]
        code, _out, err = self.dispatch(sha)
        self.assertEqual(code, 1)
        self.assertIn("stale dispatch", err)

    def test_dispatch_takes_title_and_base_from_the_pr(self):
        sha = self.branch("feature", "feat: x")
        self.prs = [
            {
                "number": 3,
                "title": "feat: x",
                "baseRefName": "develop",
                "headRefOid": sha,
            }
        ]
        self.assertEqual(self.dispatch(sha)[0], 0)

    def test_an_empty_lookup_is_refused(self):
        sha = self.branch("feature", "feat: x")
        self.prs = [{"number": 3, "title": "", "baseRefName": "", "headRefOid": sha}]
        code, _out, err = self.dispatch(sha)
        self.assertEqual(code, 1)
        self.assertIn("no title or base", err)


class TestMajorFree(PolicyCase):
    """An engine repository's own pr-policy: no table, `!` refused."""

    def setUp(self):
        super().setUp()
        git(self.root, "branch", "main")

    def major_free(self, title, sha):
        values = {
            "EVENT_NAME": "pull_request",
            "BASE_REF": "main",
            "PR_TITLE": title,
            "HEAD_SHA": sha,
        }
        with mock.patch.dict(os.environ, values):
            return self.fn("policy", "--pr", "--major-free")

    def test_major_free_uses_base_branch(self):
        sha = self.branch("feature", "feat: fine")
        # Without --major-free the checkout's integration branch is develop.
        self.assertEqual(self.pr("main", "feat: fine", sha)[0], 1)
        code, out, err = self.major_free("feat: fine", sha)
        self.assertEqual(code, 0, out + err)

    def test_bang_title_fails(self):
        sha = self.branch("feature", "feat: fine")
        code, _out, err = self.major_free("feat!: break it", sha)
        self.assertEqual(code, 1)
        self.assertIn("a stub-nix major is made only with a `Release-As", err)

    def test_bang_commit_and_breaking_footer_fail(self):
        sha = self.branch("feature", "fix(api)!: break it")
        self.assertEqual(self.major_free("fix: fine", sha)[0], 1)
        git(self.root, "checkout", "-q", "main")
        sha = self.branch("other", "fix: x\n\nBREAKING CHANGE: y")
        self.assertEqual(self.major_free("fix: x", sha)[0], 1)

    def test_release_as_any_major_passes(self):
        sha = self.branch("feature", "feat: first release\n\nRelease-As: 1.0.0")
        self.assertEqual(self.major_free("feat: first release", sha)[0], 0)

    def test_no_product_checks(self):
        product = dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(STUB.hooks, pr_checks=lambda pr: ["never"]),
        )
        self.product = product
        sha = self.branch("feature", "feat: fine")
        self.assertEqual(self.major_free("feat: fine", sha)[0], 0)
        self.assertEqual(self.pr("develop", "feat: fine", sha)[0], 1)


class TestPrChecksHook(PolicyCase):
    """``hooks.pr_checks`` gets the PR and read access to its files at base and head."""

    def setUp(self):
        super().setUp()
        self.seen = []

        def checks(pr):
            self.seen.append(pr)
            out = []
            before = pr.show(pr.base_ref, "demo_unit/__init__.py")
            after = pr.show(pr.head_sha, "demo_unit/__init__.py")
            if before != after:
                out.append(f"version moved by hand: {pr.changed()}")
            if pr.show(pr.head_sha, "nope") is not None:
                out.append("show found a missing file")
            return out

        self.product = dataclasses.replace(
            STUB, hooks=dataclasses.replace(STUB.hooks, pr_checks=checks)
        )

    def test_the_hook_sees_the_pr(self):
        sha = self.branch("feature", "feat: x")
        code, out, err = self.pr(
            "develop", "feat: x", sha, HEAD_REF="feature", PR_AUTHOR="someone"
        )
        self.assertEqual(code, 0, out + err)
        pr = self.seen[0]
        self.assertEqual(
            (pr.base_ref, pr.head_sha, pr.head_ref, pr.title, pr.author),
            ("develop", sha, "feature", "feat: x", "someone"),
        )
        self.assertEqual(pr.root, self.root.resolve())
        self.assertEqual(pr.changed(), ["demo_unit/x.py"])
        self.assertEqual(pr.rules.integration, "develop")

    def test_a_problem_fails_the_check(self):
        git(self.root, "checkout", "-q", "-b", "feature")
        self.write("demo_unit/__init__.py", '__version__ = "17.0.0"\n')
        self.commit("feat: x")
        sha = git(self.root, "rev-parse", "HEAD").strip()
        code, _out, err = self.pr("develop", "feat: x", sha)
        self.assertEqual(code, 1)
        self.assertIn("version moved by hand: ['demo_unit/__init__.py']", err)


class TestCommittedArgv(CheckoutCase):
    """``hooks.committed_argv``: how ``committed`` runs."""

    def test_default_and_kit(self):
        self.assertEqual(STUB.hooks.committed_argv(self.root), ["committed"])
        self.write("tools/pyproject.toml", "[project]\nname = 'tools'\n")
        self.assertEqual(
            STUB.hooks.committed_argv(self.root)[:4],
            ["uv", "run", "--frozen", "--project"],
        )
        self.assertEqual(KIT.hooks.committed_argv(self.root), ["committed"])

    def with_argv(self, argv):
        return dataclasses.replace(
            STUB,
            hooks=dataclasses.replace(STUB.hooks, committed_argv=lambda root: argv),
        )

    def test_run_committed_uses_the_hook(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "fine", "")

        product = self.with_argv(["fake-committed", "--x"])
        with mock.patch.object(policy.subprocess, "run", side_effect=run):
            self.assertEqual(
                policy.run_committed(product, self.root, "abc"), (True, "fine")
            )
        self.assertEqual(calls, [["fake-committed", "--x", "abc"]])

    def test_long_subject_dumps_the_config_first(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if "--dump-config" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "subject_length = 50\n", "")
            with open(cmd[cmd.index("--config") + 1]) as f:
                self.assertEqual(f.read(), "subject_length = 0\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        product = self.with_argv(["c"])
        with mock.patch.object(policy.subprocess, "run", side_effect=run):
            ok, _ = policy.run_committed(product, self.root, "abc", long_subject=True)
        self.assertTrue(ok)
        self.assertEqual(calls[0], ["c", "--dump-config", "-"])
        self.assertEqual(calls[1][-1], "abc")

    def test_missing_committed_is_exit_3(self):
        product = self.with_argv(["/nonexistent/committed"])
        with self.assertRaises(EnvError):
            policy.run_committed(product, self.root, "abc")


LONG = (
    "fix(deps): bump example/docs-tool/.github/workflows/check-docs.yml from "
    "06562bcc4dfe0081a7ad3838a7862b421d0e90eb to c9e636d8f316af89afc0c4d97ac9e5aef5b62f4e "
    "in the github-actions group"
)
TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)


class TestDependabotSubjectLength(PolicyCase):
    """A Dependabot PR's generated subjects (SHA pins) may exceed ``subject_length``;
    nothing else."""

    def setUp(self):
        super().setUp()
        self.committed.side_effect = self.fake_committed

    def fake_committed(self, product, root, *args, long_subject=False):
        """``committed`` under a conventional, subject_length = 100 configuration."""
        if args[0] == "--commit-file":
            with open(args[1]) as f:
                text = f.read()
        else:
            text = git(root, "log", "-1", "--format=%B", args[0])
        subject = text.splitlines()[0]
        if subject.split(":", 1)[0].split("(", 1)[0].rstrip("!") not in TYPES:
            return False, "Disallowed type"
        if len(subject) > 100 and not long_subject:
            message = (
                f"Commit subject is too long, {len(subject)} exceeds the max length"
                " of 100"
            )
            return False, message
        return True, ""

    def dependabot_branch(self, subject):
        git(self.root, "checkout", "-q", "-b", "dependabot/x")
        self.write("demo_unit/x.py", "# bump\n")
        git(self.root, "add", "-A")
        git(
            self.root,
            "-c",
            "user.name=dependabot[bot]",
            "-c",
            "user.email=49699333+dependabot[bot]@users.noreply.github.com",
            "commit",
            "-q",
            "-m",
            subject,
        )
        return git(self.root, "rev-parse", "HEAD").strip()

    def test_long_dependabot_title_and_commit_pass(self):
        self.assertGreater(len(LONG), 100)
        sha = self.dependabot_branch(LONG)
        code, out, err = self.pr("develop", LONG, sha, PR_AUTHOR="dependabot[bot]")
        self.assertEqual(code, 0, out + err)

    def test_long_title_from_a_human_fails(self):
        sha = self.branch("feature", "fix: short")
        code, _out, err = self.pr("develop", LONG, sha, PR_AUTHOR="someone")
        self.assertEqual(code, 1)
        self.assertIn("title: committed: Commit subject is too long", err)

    def test_long_human_commit_in_a_dependabot_pr_fails(self):
        sha = self.branch("feature", LONG)
        code, _out, err = self.pr(
            "develop", "fix(deps): bump x", sha, PR_AUTHOR="dependabot[bot]"
        )
        self.assertEqual(code, 1)
        self.assertIn("too long", err)

    def test_dependabot_bang_title_fails(self):
        git(self.root, "branch", "main")
        sha = self.dependabot_branch("fix(deps): bump x")
        values = {
            "EVENT_NAME": "pull_request",
            "BASE_REF": "main",
            "PR_TITLE": "fix(deps)!: bump x",
            "HEAD_SHA": sha,
            "PR_AUTHOR": "dependabot[bot]",
        }
        with mock.patch.dict(os.environ, values):
            code, _out, err = self.fn("policy", "--pr", "--major-free")
        self.assertEqual(code, 1)
        self.assertIn("`!` marks a major bump", err)

    def test_dependabot_commit_with_bad_type_fails(self):
        sha = self.dependabot_branch("bump: x from 1 to 2")
        code, _out, err = self.pr(
            "develop", "fix(deps): bump x", sha, PR_AUTHOR="dependabot[bot]"
        )
        self.assertEqual(code, 1)
        self.assertIn("Disallowed type", err)

    def test_dispatch_reads_the_author_from_the_lookup(self):
        sha = self.dependabot_branch(LONG)
        self.prs = [
            {
                "number": 3,
                "title": LONG,
                "baseRefName": "develop",
                "headRefOid": sha,
                "author": {"login": "app/dependabot", "is_bot": True},
            }
        ]
        env = {
            "EVENT_NAME": "workflow_dispatch",
            "REF_NAME": "dependabot/x",
            "HEAD_SHA": sha,
        }
        env |= {"BASE_REF": "", "PR_TITLE": "", "PR_AUTHOR": ""}
        with mock.patch.dict(os.environ, env):
            code, out, err = self.fn("policy", "--pr")
        self.assertEqual(code, 0, out + err)
        self.prs[0]["author"] = {"login": "someone"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.fn("policy", "--pr")[0], 1)


class TestAnySubjectLength(unittest.TestCase):
    def test_turns_off_subject_length_only(self):
        dumped = 'subject_length = 100\nstyle = "conventional"\nline_length = 0\n'
        self.assertEqual(
            policy.any_subject_length(dumped),
            'subject_length = 0\nstyle = "conventional"\nline_length = 0\n',
        )
        self.assertEqual(
            policy.any_subject_length('style = "x"\n'),
            'subject_length = 0\nstyle = "x"\n',
        )

    def test_dependabot_logins(self):
        self.assertTrue(policy.is_dependabot("dependabot[bot]"))
        self.assertTrue(policy.is_dependabot("app/dependabot"))
        self.assertFalse(policy.is_dependabot("dependabot-impostor"))
        self.assertFalse(policy.is_dependabot(""))


if __name__ == "__main__":
    unittest.main()
