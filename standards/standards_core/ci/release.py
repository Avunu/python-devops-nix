"""The release flow's dispatches and fast-forward, and the bot-PR sweep (DESIGN §8.2).

A ``GITHUB_TOKEN`` push or PR raises no workflow run, so the release PR (opened by
release-please) and bot PRs (Dependabot, relock) get their checks by dispatch:

- ``ensure_ci`` checks each required context on the head SHA on its own and dispatches
  the workflow that reports a missing one (``product.ci.contexts`` pairs a context with
  its workflow). A dispatch that failed or a concurrency group cancelled is therefore
  retried by the next run;
- ``dispatch_ci`` does that for the open release PR (``autorelease: pending``),
  accepting only release-please's own branch names;
- ``sweep`` does it for every open PR whose author is Dependabot or GitHub Actions;
- ``FastForward`` moves the release branch with a REST PATCH (or creates it on the first
  release), which GitHub refuses unless it is a fast-forward, then reads it back.

The fast-forward has two guards, one per flow:

- **tags**: the branch must equal the tag this run created, peeled (D8). The version
  scheme's ``tag_problem`` is asked first, before anything is written;
- **post-merge-bump** (no tag): the integration head's required checks must have passed
  (``wait_checks`` over ``github.check_state``), and the branch must then equal ``sha``.

``dry_run`` prints the REST calls instead of making them and checks the commit the write
would leave (``sha``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from standards_core.ci import error, notice
from standards_core.common import github

if TYPE_CHECKING:
    from standards_core.product import VersionScheme

# The core's default dispatch pairs (CiConfig.contexts' default).
CI_CONTEXTS: tuple[tuple[str, str], ...] = (
    ("ci / lint", "ci.yml"),
    ("ci / pr-policy", "pr-policy.yml"),
)
BOTS = {
    "app/dependabot",
    "dependabot[bot]",
    "app/github-actions",
    "github-actions[bot]",
}
# How often wait_checks reads the checks.
POLL_SECONDS = 15.0


def release_branches(integration: str, package: str = "") -> set[str]:
    """The head branch names release-please gives its PR."""
    names = {f"release-please--branches--{integration}"}
    if package:
        names.add(f"release-please--branches--{integration}--components--{package}")
    return names


def _has_check(repo: str, sha: str, context: str) -> bool:
    data = github.get(
        f"repos/{repo}/commits/{sha}/check-runs?check_name={context.replace(' ', '%20')}"
    )
    return bool((data or {}).get("total_count") or (data or {}).get("check_runs"))


def _workflow_exists(repo: str, workflow: str, sha: str) -> bool:
    return (
        github.get_or_none(
            f"repos/{repo}/contents/.github/workflows/{workflow}?ref={sha}"
        )
        is not None
    )


def ensure_ci(
    repo: str,
    ref: str,
    sha: str,
    contexts: Iterable[tuple[str, str]] = CI_CONTEXTS,
) -> list[str]:
    """Dispatch each workflow whose context is absent on ``sha``; returns what was
    dispatched."""
    out: list[str] = []
    for context, workflow in contexts:
        if _has_check(repo, sha, context):
            continue
        if workflow in out or not _workflow_exists(repo, workflow, sha):
            continue
        res = github.cli("workflow", "run", workflow, "--repo", repo, "--ref", ref)
        if res.code != 0:
            notice(f"could not dispatch {workflow} on {ref}: {res.stderr.strip()}")
            continue
        print(f"dispatched {workflow} on {ref} ({context} absent on {sha[:12]})")
        out.append(workflow)
    return out


def _pr_list(*args: str) -> list[dict]:
    res = github.cli("pr", "list", *args)
    if res.code != 0:
        raise github.GitHubError(f"gh pr list: {res.stderr.strip()}")
    return json.loads(res.stdout or "[]")


def dispatch_ci(
    repo: str,
    integration: str,
    package: str = "",
    contexts: Iterable[tuple[str, str]] = CI_CONTEXTS,
) -> list[str]:
    prs = _pr_list(
        "--repo", repo, "--base", integration, "--label", "autorelease: pending",
        "--state", "open", "--json", "number,headRefName,headRefOid",
    )  # fmt: skip
    allowed = release_branches(integration, package)
    out: list[str] = []
    found = False
    for pr in prs:
        if pr.get("headRefName") not in allowed:
            print(
                f"skipping #{pr.get('number')}: {pr.get('headRefName')} is not"
                " release-please's branch"
            )
            continue
        found = True
        out += ensure_ci(repo, pr["headRefName"], pr["headRefOid"], tuple(contexts))
    if not found:
        print("no open release PR")
    return out


def sweep(repo: str, contexts: Iterable[tuple[str, str]] = CI_CONTEXTS) -> list[str]:
    prs = _pr_list(
        "--repo", repo, "--state", "open", "--json",
        "number,author,headRefName,headRefOid",
    )  # fmt: skip
    out: list[str] = []
    for pr in prs:
        author = (pr.get("author") or {}).get("login", "")
        if author not in BOTS:
            continue
        out += ensure_ci(repo, pr["headRefName"], pr["headRefOid"], tuple(contexts))
    return out


def wait_checks(
    repo: str,
    sha: str,
    contexts: set[str],
    timeout: float,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """``github.check_state`` of ``sha``, read every 15 s until ``green`` or ``failed``,
    or ``pending`` once ``timeout`` seconds have passed (one read when it is 0)."""
    pause = sleep or time.sleep
    waited = 0.0
    while True:
        state = github.check_state(repo, sha, sorted(contexts))
        if state != "pending" or waited >= timeout:
            return state
        step = min(POLL_SECONDS, timeout - waited)
        pause(step)
        waited += step


@dataclass
class FastForward:
    repo: str
    branch: str
    sha: str
    tag: str = ""
    # the tags flow's guard: scheme.tag_problem(tag, cfg, facts) before any write
    scheme: VersionScheme | None = None
    cfg: Mapping[str, Any] = field(default_factory=dict)
    facts: Mapping[str, Any] = field(default_factory=dict)
    # the post-merge-bump flow: the branch whose required checks sha must pass
    integration: str = ""
    wait: float = 0.0
    sleep: Callable[[float], None] | None = None
    # workflows dispatched on the release branch once it moved
    dispatch_after: tuple[str, ...] = ()
    dry_run: bool = False
    calls: list[str] = field(default_factory=list)
    # "true", "pending" or "false" once run
    moved: str = "false"

    def _write(self, method: str, endpoint: str, body: dict) -> None:
        self.calls.append(f"{method} {endpoint} {json.dumps(body, sort_keys=True)}")
        if self.dry_run:
            print(f"dry run: {method} {endpoint} {json.dumps(body, sort_keys=True)}")
            return
        github.request(method, endpoint, body)

    def peeled(self, tag: str) -> str:
        obj = (github.get(f"repos/{self.repo}/git/ref/tags/{tag}") or {}).get(
            "object"
        ) or {}
        for _ in range(10):
            if obj.get("type") != "tag":
                return obj.get("sha", "")
            obj = (github.get(f"repos/{self.repo}/git/tags/{obj['sha']}") or {}).get(
                "object"
            ) or {}
        return ""

    def scheme_ok(self) -> bool:
        if not self.tag or self.scheme is None:
            return True
        problem = self.scheme.tag_problem(self.tag, self.cfg, self.facts)
        if problem:
            error(
                f"release tag {self.tag}: {problem}; under version-scheme ="
                f" {self.scheme.name} {self.branch} does not take it"
            )
            return False
        return True

    def checks_ok(self) -> bool | None:
        """The tagless guard: ``True`` when the required checks passed, ``None`` when
        they are still pending, ``False`` when they failed or nothing protects the
        integration branch."""
        if self.tag:
            return True
        contexts, _known = github.required_contexts(self.repo, self.integration)
        if not contexts:
            error(f"nothing protects {self.integration}: refusing to fast-forward")
            return False
        state = wait_checks(self.repo, self.sha, contexts, self.wait, self.sleep)
        if state == "failed":
            error(
                f"a required check failed on {self.sha[:12]}: {self.branch} stays"
                " where it is"
            )
            return False
        if state == "pending":
            print(
                f"the required checks on {self.sha[:12]} are still pending: {self.branch}"
                " moves on the next run"
            )
            return None
        return True

    def _move(self) -> int | None:
        endpoint = f"repos/{self.repo}/git/ref/heads/{self.branch}"
        try:
            if github.get_or_none(endpoint) is not None:
                self._write(
                    "PATCH",
                    f"repos/{self.repo}/git/refs/heads/{self.branch}",
                    {"sha": self.sha, "force": False},
                )
            else:
                self._write(
                    "POST",
                    f"repos/{self.repo}/git/refs",
                    {"ref": f"refs/heads/{self.branch}", "sha": self.sha},
                )
        except github.GitHubError as e:
            if e.status != 403:
                raise
            onto = self.tag or self.sha[:12]
            error(
                f"GitHub refused to move {self.branch} to {onto} (HTTP 403)."
                " GITHUB_TOKEN can't move a branch onto commits that change"
                " .github/workflows/; name a GitHub App with Workflows write in"
                " ci.github-app (its token then makes this move). To move it by hand,"
                " with a token that can change workflow files: gh api -X PATCH"
                f" repos/{self.repo}/git/refs/heads/{self.branch} -f sha={self.sha}"
                " -F force=false"
            )
            return 1
        return None

    def run(self) -> int:
        if not self.scheme_ok():
            return 1
        ready = self.checks_ok()
        if ready is None:
            self.moved = "pending"
            return 0
        if not ready:
            return 1
        failed = self._move()
        if failed is not None:
            return failed
        if self.dry_run:
            head = self.sha  # what the write would leave
        else:
            endpoint = f"repos/{self.repo}/git/ref/heads/{self.branch}"
            head = ((github.get_or_none(endpoint) or {}).get("object") or {}).get(
                "sha", ""
            )
        if self.tag:
            tagged = self.peeled(self.tag)
            if not head or head != tagged:
                error(
                    f"{self.branch} is at {head[:12] or '(missing)'} but {self.tag} is"
                    f" {tagged[:12] or '(missing)'}: the release branch must equal the"
                    " newest release tag (D8)"
                )
                return 1
            print(f"{self.branch} = {self.tag} = {head}")
        else:
            if head != self.sha:
                error(
                    f"{self.branch} is at {head[:12] or '(missing)'}, not"
                    f" {self.sha[:12]}: the release branch must equal the integration"
                    " head whose checks passed"
                )
                return 1
            print(f"{self.branch} = {head}")
        self.moved = "true"
        for workflow in self.dispatch_after:
            self.calls.append(f"workflow run {workflow} --ref {self.branch}")
            if self.dry_run:
                print(f"dry run: gh workflow run {workflow} --ref {self.branch}")
                continue
            res = github.cli(
                "workflow", "run", workflow, "--repo", self.repo, "--ref", self.branch
            )
            if res.code != 0:
                error(
                    f"could not dispatch {workflow} on {self.branch}:"
                    f" {res.stderr.strip()}"
                )
                return 1
        return 0
