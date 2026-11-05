"""The pins check (``standards_core.testing.pins``, DESIGN §10.2-§10.3): the flake input,
the tools' URL dependency and the ``.github`` references name one commit, and with
``--reachable`` that commit is on the main branch or a release tag."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from standards_core.testing import pins
from standards_core.testing.fakes import git

OWNER = "example"
REV = "1" * 40
OTHER = "2" * 40


def lock(rev: str = REV, *, owner: str = OWNER, kind: str = "github") -> dict:
    node = {
        "locked": {
            "type": kind,
            "owner": owner,
            "repo": "python-devops-nix",
            "rev": rev,
            "narHash": "sha256-x",
        },
        "original": {"type": kind, "owner": owner, "repo": "python-devops-nix"},
    }
    return {
        "nodes": {
            "python-devops-nix": node,
            "root": {"inputs": {"python-devops-nix": "python-devops-nix"}},
        },
        "root": "root",
        "version": 7,
    }


def pyproject(url_rev: str = REV, *, owner: str = OWNER, extra: str = "") -> str:
    url = (
        f"git+https://github.com/{owner}/python-devops-nix@{url_rev}"
        f"#subdirectory=standards{extra}"
    )
    return (
        "[project]\n"
        'name = "kit-nix-tools"\n'
        'version = "1.0.0"\n'
        f'dependencies = ["jinja2", "standards-core @ {url}"]\n'
    )


WORKFLOW = """name: ci
on: push
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      # {owner}/python-devops-nix/.github/actions/standards-install@main is a comment
      - uses: {owner}/python-devops-nix/.github/actions/standards-install@{rev} # v1.3.0
        with:
          cli: kit-nix
  deps:
    uses: {owner}/python-devops-nix/.github/workflows/dependabot-auto-merge.yml@{rev2} # v1.3.0
"""


class PinsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "product"
        self.root.mkdir()

    def write(
        self,
        *,
        lock_doc: dict | None = None,
        project: str | None = None,
        workflow: str | None = None,
    ) -> None:
        (self.root / "flake.lock").write_text(json.dumps(lock_doc or lock()))
        tools = self.root / "py" / "kit_nix_tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "pyproject.toml").write_text(
            pyproject() if project is None else project
        )
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        text = WORKFLOW.format(owner=OWNER, rev=REV, rev2=REV)
        (workflows / "ci.yml").write_text(text if workflow is None else workflow)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pins.main(["--root", str(self.root), "--owner", OWNER, *argv])
        return code, out.getvalue(), err.getvalue()


class TestAgreement(PinsCase):
    def test_all_three_agree(self):
        self.write()
        report = pins.check(self.root, OWNER)
        self.assertEqual(report.problems, [])
        self.assertEqual(report.locked, REV)
        self.assertEqual(report.urls, [("py/kit_nix_tools/pyproject.toml", REV)])
        self.assertEqual(
            report.uses,
            [(".github/workflows/ci.yml:8", REV), (".github/workflows/ci.yml:12", REV)],
        )
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn(
            f"python-devops-nix {REV}: flake.lock, 1 standards-core URL(s)", out
        )
        self.assertIn("2 .github reference(s) agree", out)

    def test_owner_case_is_ignored(self):
        self.write(lock_doc=lock(owner="Example"), project=pyproject(owner="EXAMPLE"))
        self.assertEqual(pins.check(self.root, OWNER).problems, [])

    def test_no_actions_is_fine(self):
        self.write(workflow="name: ci\non: push\njobs: {}\n")
        report = pins.check(self.root, OWNER)
        self.assertEqual((report.problems, report.uses), ([], []))

    def test_extra_fragment_parts_are_kept(self):
        self.write(project=pyproject(extra="&egg=standards-core"))
        self.assertEqual(pins.check(self.root, OWNER).problems, [])

    def test_optional_dependencies_count(self):
        url = f"git+https://github.com/{OWNER}/python-devops-nix@{REV}#subdirectory=standards"
        self.write(
            project=(
                '[project]\nname = "x"\nversion = "1"\ndependencies = []\n'
                f'[project.optional-dependencies]\ncore = ["standards-core @ {url}"]\n'
            )
        )
        self.assertEqual(pins.check(self.root, OWNER).problems, [])

    def test_hidden_directories_are_not_read(self):
        self.write()
        venv = self.root / "py" / "kit_nix_tools" / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "pyproject.toml").write_text(pyproject(OTHER))
        self.assertEqual(pins.check(self.root, OWNER).problems, [])

    def test_pyproject_glob_argument(self):
        self.write()
        (self.root / "tools").mkdir()
        (self.root / "tools" / "pyproject.toml").write_text(pyproject(OTHER))
        report = pins.check(self.root, OWNER, pyprojects=("tools/pyproject.toml",))
        self.assertEqual(
            report.problems,
            [
                (
                    f"tools/pyproject.toml: pins {OTHER[:12]}, but flake.lock locks"
                    f" python-devops-nix {REV[:12]}"
                )
            ],
        )

    def test_other_input_and_repository_names(self):
        doc = lock()
        doc["nodes"]["base"] = doc["nodes"].pop("python-devops-nix")
        doc["nodes"]["base"]["locked"]["repo"] = "base-kit"
        doc["nodes"]["root"]["inputs"] = {"base": "base"}
        self.write(
            lock_doc=doc,
            project=pyproject().replace("python-devops-nix", "base-kit"),
            workflow=WORKFLOW.format(owner=OWNER, rev=REV, rev2=REV).replace(
                "python-devops-nix", "base-kit"
            ),
        )
        report = pins.check(self.root, OWNER, input_name="base", repo_name="base-kit")
        self.assertEqual((report.problems, len(report.uses)), ([], 2))


class TestDisagreement(PinsCase):
    def test_url_differs_from_the_lock(self):
        self.write(project=pyproject(OTHER))
        self.assertEqual(
            pins.check(self.root, OWNER).problems,
            [
                (
                    f"py/kit_nix_tools/pyproject.toml: pins {OTHER[:12]}, but flake.lock"
                    f" locks python-devops-nix {REV[:12]}"
                )
            ],
        )
        self.assertEqual(self.run_main()[0], 1)

    def test_action_differs_from_the_lock(self):
        self.write(workflow=WORKFLOW.format(owner=OWNER, rev=REV, rev2=OTHER))
        self.assertEqual(
            pins.check(self.root, OWNER).problems,
            [
                (
                    f".github/workflows/ci.yml:12: pins {OTHER[:12]}, but flake.lock locks"
                    f" python-devops-nix {REV[:12]}"
                )
            ],
        )

    def test_action_at_a_branch(self):
        self.write(workflow=WORKFLOW.format(owner=OWNER, rev="main", rev2=REV))
        self.assertEqual(
            pins.check(self.root, OWNER).problems,
            [
                (
                    ".github/workflows/ci.yml:8: example/python-devops-nix is used at"
                    " 'main', not a 40-hex commit"
                )
            ],
        )

    def test_composite_actions_are_read(self):
        self.write()
        action = self.root / ".github" / "actions" / "own" / "action.yml"
        action.parent.mkdir(parents=True)
        action.write_text(
            "runs:\n  using: composite\n  steps:\n"
            f"    - uses: {OWNER}/python-devops-nix/.github/actions/standards-pr-policy"
            f"@{OTHER} # v1.3.0\n"
        )
        self.assertEqual(
            pins.check(self.root, OWNER).problems,
            [
                (
                    f".github/actions/own/action.yml:4: pins {OTHER[:12]}, but flake.lock"
                    f" locks python-devops-nix {REV[:12]}"
                )
            ],
        )

    def test_no_url_dependency(self):
        self.write(project='[project]\nname = "x"\nversion = "1"\ndependencies = []\n')
        self.assertEqual(
            pins.check(self.root, OWNER).problems,
            [
                (
                    "no py/**/pyproject.toml declares standards-core @"
                    " git+https://github.com/example/python-devops-nix@<sha>"
                    "#subdirectory=standards"
                )
            ],
        )

    def test_bad_url_shapes(self):
        cases = {
            "standards-core @ git+https://gitlab.com/example/python-devops-nix@"
            + REV: "is not git+https://github.com/example/python-devops-nix@<sha>",
            "standards-core @ git+https://github.com/other/python-devops-nix@"
            + REV
            + "#subdirectory=standards": "comes from other/python-devops-nix",
            "standards-core @ git+https://github.com/example/python-devops-nix@"
            + REV: "lacks #subdirectory=standards",
            "standards-core @ git+https://github.com/example/python-devops-nix@v1.3.0"
            "#subdirectory=standards": "is pinned to 'v1.3.0', not a 40-hex commit",
        }
        for requirement, text in cases.items():
            with self.subTest(requirement=requirement):
                self.write(
                    project=(
                        '[project]\nname = "x"\nversion = "1"\n'
                        f"dependencies = [{json.dumps(requirement)}]\n"
                    )
                )
                problems = pins.check(self.root, OWNER).problems
                self.assertTrue(any(text in p for p in problems), problems)

    def test_lock_problems(self):
        no_input = lock()
        no_input["nodes"]["root"]["inputs"] = {}
        gitlab = lock(kind="gitlab")
        fork = lock(owner="fork")
        short = lock(rev="abc")
        cases = {
            "the root has no python-devops-nix input": no_input,
            "is locked as 'gitlab', not from github": gitlab,
            "locks fork/python-devops-nix, not example/python-devops-nix": fork,
            "has no 40-hex rev": short,
        }
        for text, doc in cases.items():
            with self.subTest(text=text):
                self.write(lock_doc=doc)
                problems = pins.check(self.root, OWNER).problems
                self.assertTrue(any(text in p for p in problems), problems)

    def test_unreadable_lock_is_exit_3(self):
        self.write()
        (self.root / "flake.lock").write_text("{")
        code, _, err = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("flake.lock is unreadable", err)

    def test_bad_arguments_are_exit_2(self):
        self.write()
        self.assertEqual(self.run_main("--owner", "a b")[0], 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(pins.main([]), 2)
            self.assertEqual(pins.main(["--root", "/nonexistent", "--owner", OWNER]), 2)


class TestReachable(PinsCase):
    def setUp(self):
        super().setUp()
        self.upstream = Path(self.tmp.name) / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q", "-b", "main")
        (self.upstream / "a").write_text("a\n")
        git(self.upstream, "add", "a")
        git(self.upstream, "commit", "-q", "-m", "feat: a")
        self.on_main = git(self.upstream, "rev-parse", "HEAD").strip()
        git(self.upstream, "checkout", "-q", "-b", "topic")
        (self.upstream / "b").write_text("b\n")
        git(self.upstream, "add", "b")
        git(self.upstream, "commit", "-q", "-m", "feat: b")
        self.on_topic = git(self.upstream, "rev-parse", "HEAD").strip()
        git(self.upstream, "checkout", "-q", "-b", "release", "main")
        (self.upstream / "c").write_text("c\n")
        git(self.upstream, "add", "c")
        git(self.upstream, "commit", "-q", "-m", "feat: c")
        self.tagged = git(self.upstream, "rev-parse", "HEAD").strip()
        git(self.upstream, "tag", "-a", "v1.3.0", "-m", "v1.3.0")
        git(self.upstream, "checkout", "-q", "main")
        self.url = self.upstream.as_uri()

    def pinned(self, rev: str) -> None:
        self.write(
            lock_doc=lock(rev),
            project=pyproject(rev),
            workflow=WORKFLOW.format(owner=OWNER, rev=rev, rev2=rev),
        )

    def test_on_main(self):
        self.assertIsNone(pins.reachable(self.url, self.on_main))
        self.pinned(self.on_main)
        code, out, _ = self.run_main("--reachable", self.url)
        self.assertEqual(code, 0, out)
        self.assertIn("(reachable)", out)

    def test_on_a_release_tag(self):
        self.assertIsNone(pins.reachable(self.url, self.tagged))

    def test_on_a_branch_only(self):
        self.assertEqual(
            pins.reachable(self.url, self.on_topic),
            f"{self.on_topic[:12]} is on neither main nor a v* tag of {self.url}",
        )
        self.pinned(self.on_topic)
        self.assertEqual(self.run_main("--reachable", self.url)[0], 1)
        self.assertEqual(
            self.run_main("--reachable", self.url, "--branch", "topic")[0], 0
        )

    def test_unknown_commit(self):
        self.assertEqual(
            pins.reachable(self.url, OTHER),
            f"{OTHER[:12]} is not a commit of {self.url}",
        )

    def test_unreachable_url_is_exit_3(self):
        self.pinned(self.on_main)
        missing = (Path(self.tmp.name) / "missing").as_uri()
        code, _, err = self.run_main("--reachable", missing)
        self.assertEqual(code, 3)
        self.assertIn("git clone", err)


if __name__ == "__main__":
    unittest.main()
