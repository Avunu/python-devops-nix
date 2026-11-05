"""``<cli> policy``: the commit-msg hook and the ``pr-policy`` check (DESIGN §2.5, §6.2).

``--commit-msg-file F`` is the commit-msg hook. With ``releases`` on, it applies the
rules of ``releases.version-scheme`` (``VersionScheme.message_problems``, given the
resolved configuration and the checkout's facts): ``semver`` rejects nothing, a scheme
whose major is fixed rejects ``type!:``, ``BREAKING CHANGE`` and a ``Release-As`` with
another major. With ``releases`` off, or in a checkout that has not opted in, it rejects
nothing (``committed`` checks the rest).

``--pr`` runs in CI, from the environment the workflow passes: ``INTEGRATION``,
``EVENT_NAME``, ``BASE_REF``, ``HEAD_SHA``, ``PR_TITLE``, ``REF_NAME``, ``PR_AUTHOR``
(and ``GH_TOKEN`` for ``gh``; ``HEAD_REF`` or ``GITHUB_HEAD_REF`` names the head branch
on a ``pull_request``). On a ``workflow_dispatch`` the PR is looked up with ``gh pr list
--head $REF_NAME``; its ``headRefOid`` must equal ``HEAD_SHA``, and its title, base and
author replace the empty ``PR_TITLE``, ``BASE_REF`` and ``PR_AUTHOR``. No PR passes.
Then:

1. the base is allowed: the integration branch, a ``commits.pr-allowed-bases`` glob, or
   (with ``commits.allow-stacked``) the head branch of another open PR;
2. the title passes ``committed --commit-file`` and the scheme's rules;
3. every commit in ``git rev-list --no-merges base..head`` passes ``committed <sha>``
   and the scheme's rules;
4. the product's own checks (``hooks.pr_checks``), which may read files at the base and
   the head.

``committed`` runs as ``hooks.committed_argv(root)`` gives it. A Dependabot PR
(``PR_AUTHOR`` is Dependabot) is exempt from ``committed``'s ``subject_length`` alone,
for its title and for its commits whose author is Dependabot: its generated subjects
name SHA pins and run past any length. Every other rule still applies.

``--major-free`` (an engine repository's own rules, which has no table) drops the scheme
and the product's checks, refuses ``!`` and ``BREAKING CHANGE`` (a major is made only
with a ``Release-As: <N>.0.0`` footer), and takes the integration branch from
``--base-branch``.

Exit 0 or 1; 2 for a bad argument.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.commands import help_text
from standards_core.common import config, pyproject, repo, repoconfig
from standards_core.common.report import CLEAN, DRIFT, ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Product, VersionScheme

_BANG = re.compile(r"^[a-z]+(\([^)]*\))?!:")
_BREAKING = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
_SUBJECT_LENGTH = re.compile(r"^subject_length\s*=.*$", re.MULTILINE)
# The PR author as `github.event.pull_request.user.login` names it, and as
# `gh --json author` does.
_DEPENDABOT_LOGINS = frozenset({"dependabot[bot]", "app/dependabot"})
_DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def is_dependabot(login: str) -> bool:
    return login in _DEPENDABOT_LOGINS


@dataclass
class Rules:
    """What the resolved configuration says the policy checks."""

    # releases.version-scheme's, with releases on; None: no scheme rules
    scheme: VersionScheme | None = None
    cfg: Mapping[str, Any] = field(default_factory=dict)
    facts: Mapping[str, Any] = field(default_factory=dict)
    # --major-free: `!` and BREAKING CHANGE refused on their own
    no_bang: bool = False
    integration: str = "main"
    allow_stacked: bool = True
    allowed_bases: list[str] = field(default_factory=list)
    # run hooks.pr_checks (not under --major-free)
    pr_checks: bool = False


@dataclass(frozen=True)
class PrContext:
    """What ``hooks.pr_checks`` gets: the pull request and read access to its files."""

    root: Path
    base_ref: str
    head_sha: str
    head_ref: str
    title: str
    author: str
    rules: Rules
    # a file's text at a revision, None when it is not there
    show: Callable[[str, str], str | None]
    # the paths the PR changes (base...head)
    changed: Callable[[], list[str]]


def message_problems(product: Product, text: str, rules: Rules) -> list[str]:
    """The rules a commit message or PR title breaks: the scheme's, and under
    ``no_bang`` the refusal of ``!`` and ``BREAKING CHANGE``."""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    body = "\n".join(lines)
    out: list[str] = []
    if rules.no_bang:
        subject = next((line for line in lines if line.strip()), "")
        why = (
            f"a {product.identity.name} major is made only with a"
            " `Release-As: <N>.0.0` footer"
        )
        if _BANG.match(subject):
            out.append(f"{subject!r}: `!` marks a major bump, and {why}")
        if _BREAKING.search(body):
            out.append(f"a BREAKING CHANGE footer marks a major bump, and {why}")
    if rules.scheme is not None:
        out += rules.scheme.message_problems(body, rules.cfg, rules.facts)
    return out


def rules_for(
    product: Product, root: Path, *, major_free: bool, base_branch: str
) -> Rules:
    """The rules for the checkout at ``root``; with ``major_free``, an engine
    repository's own (no table)."""
    if major_free:
        return Rules(integration=base_branch, no_bang=True)
    from standards_core.ci import conventional

    rc = repoconfig.load(product, root)
    cfg, modules = rc.cfg, rc.modules
    scheme = None
    if modules.get("releases"):
        name = cfg.get("releases", {}).get("version-scheme", "semver")
        scheme = conventional.scheme(product, name)
    commits = cfg.get("commits", {})
    return Rules(
        scheme=scheme,
        cfg=cfg,
        facts=rc.facts,
        integration=cfg["integration-branch"],
        allow_stacked=bool(commits.get("allow-stacked", True)),
        allowed_bases=list(commits.get("pr-allowed-bases", [])),
        pr_checks=True,
    )


def any_subject_length(dumped: str) -> str:
    """``committed --dump-config`` output with ``subject_length`` off (0) and the rest
    kept."""
    if _SUBJECT_LENGTH.search(dumped):
        return _SUBJECT_LENGTH.sub("subject_length = 0", dumped)
    return f"subject_length = 0\n{dumped}"


def _committed(
    product: Product, root: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*product.hooks.committed_argv(root), *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise EnvError(f"committed is not available: {e}") from e


def run_committed(
    product: Product, root: Path, *args: str, long_subject: bool = False
) -> tuple[bool, str]:
    """``committed <args>``; with ``long_subject``, under the repository's own
    configuration with ``subject_length`` alone turned off (a Dependabot title or
    commit)."""
    if not long_subject:
        proc = _committed(product, root, *args)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    dump = _committed(product, root, "--dump-config", "-")
    if dump.returncode != 0:
        raise EnvError(
            f"committed --dump-config: {(dump.stdout + dump.stderr).strip()}"
        )
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(any_subject_length(dump.stdout))
        name = f.name
    try:
        proc = _committed(product, root, "--config", name, *args)
    finally:
        os.unlink(name)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def gh_json(*args: str) -> list[dict]:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise EnvError("gh is not on PATH") from e
    if proc.returncode != 0:
        raise EnvError(f"gh {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
    data = json.loads(proc.stdout or "[]")
    return data if isinstance(data, list) else []


def open_pr_heads() -> set[str]:
    return {
        pr.get("headRefName", "")
        for pr in gh_json(
            "pr", "list", "--state", "open", "--limit", "500", "--json", "headRefName"
        )
    }


def pr_number() -> str:
    """The PR's number on a ``pull_request`` run (``refs/pull/<n>/merge``), for the
    retarget hint."""
    m = re.match(r"refs/pull/(\d+)/", os.environ.get("GITHUB_REF", ""))
    return os.environ.get("PR_NUMBER") or (m[1] if m else "<n>")


def base_problem(base: str, rules: Rules, number: str) -> str | None:
    """Check 1."""
    if base == rules.integration or any(
        fnmatch.fnmatchcase(base, g) for g in rules.allowed_bases
    ):
        return None
    if rules.allow_stacked and base in open_pr_heads():
        return None
    return (
        f"the base is {base!r}, not {rules.integration!r}: retarget: gh pr edit"
        f" {number} --base {rules.integration}"
    )


def _base_ref(root: Path, base: str) -> str:
    for ref in (f"origin/{base}", base):
        try:
            repo.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            return ref
        except EnvError:
            continue
    raise EnvError(
        f"the base branch {base!r} is not fetched (actions/checkout: fetch-depth: 0)"
    )


def commit_problems(
    product: Product,
    root: Path,
    base: str,
    head: str,
    rules: Rules,
    *,
    dependabot: bool = False,
) -> list[str]:
    """Check 3. In a Dependabot PR, a commit Dependabot authored may have any subject
    length."""
    out = []
    try:
        ref = _base_ref(root, base)
    except EnvError as e:
        return [str(e)]
    shas = repo.git(root, "rev-list", "--no-merges", head, f"^{ref}").split()
    for sha in shas:
        long_subject = (
            dependabot
            and repo.git(root, "log", "-1", "--format=%ae", sha).strip()
            == _DEPENDABOT_EMAIL
        )
        ok, msg = run_committed(product, root, sha, long_subject=long_subject)
        if not ok:
            out.append(f"commit {sha[:12]}: committed: {msg}")
        text = repo.git(root, "log", "-1", "--format=%B", sha)
        out += [
            f"commit {sha[:12]}: {p}" for p in message_problems(product, text, rules)
        ]
    return out


def title_problems(
    product: Product, root: Path, title: str, rules: Rules, *, dependabot: bool = False
) -> list[str]:
    """Check 2. A Dependabot PR's title may have any subject length."""
    out = []
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(title + "\n")
        name = f.name
    try:
        ok, msg = run_committed(
            product, root, "--commit-file", name, long_subject=dependabot
        )
    finally:
        os.unlink(name)
    if not ok:
        out.append(f"title: committed: {msg}")
    out += [f"title: {p}" for p in message_problems(product, title, rules)]
    return out


def pr_context(
    root: Path,
    rules: Rules,
    *,
    base: str,
    head_sha: str,
    head_ref: str,
    title: str,
    author: str,
) -> PrContext:
    """The ``PrContext`` of a PR from ``base`` at ``head_sha``."""

    def show(rev: str, path: str) -> str | None:
        try:
            return repo.git(root, "show", f"{rev}:{path}")
        except EnvError:
            return None

    def changed() -> list[str]:
        ref = _base_ref(root, base)
        out = repo.git(root, "diff", "--name-only", f"{ref}...{head_sha}")
        return [p for p in out.split("\n") if p]

    return PrContext(
        root=root,
        base_ref=base,
        head_sha=head_sha,
        head_ref=head_ref,
        title=title,
        author=author,
        rules=rules,
        show=show,
        changed=changed,
    )


def check_pr(
    product: Product, root: Path, rules: Rules, env: Mapping[str, str]
) -> list[str]:
    event = env.get("EVENT_NAME", "")
    head_sha = env.get("HEAD_SHA", "")
    title = env.get("PR_TITLE", "")
    base = env.get("BASE_REF", "")
    head_ref = env.get("HEAD_REF") or env.get("GITHUB_HEAD_REF") or ""
    author = env.get("PR_AUTHOR", "")
    number = pr_number()
    if event == "workflow_dispatch":
        ref = env.get("REF_NAME", "")
        prs = gh_json(
            "pr",
            "list",
            "--head",
            ref,
            "--state",
            "open",
            "--json",
            "number,title,baseRefName,headRefOid,author",
        )
        if not prs:
            print(
                f"{product.identity.cli} policy: no open PR for {ref!r}; nothing to"
                " check"
            )
            return []
        pr = prs[0]
        if pr.get("headRefOid") != head_sha:
            stale = (
                f"stale dispatch: PR #{pr.get('number')} is at"
                f" {pr.get('headRefOid')}, this run at {head_sha}"
            )
            return [stale]
        title, base, number, head_ref, author = (
            pr.get("title") or "",
            pr.get("baseRefName") or "",
            str(pr.get("number")),
            ref,
            (pr.get("author") or {}).get("login") or "",
        )
        if not title or not base:
            empty = (
                f"PR #{number}: the lookup returned no title or base; refusing to"
                " check an empty value"
            )
            return [empty]
    if not base:
        raise ConfigError(
            "BASE_REF is empty (set it from github.event.pull_request.base.ref)"
        )
    if not head_sha:
        raise ConfigError("HEAD_SHA is empty")
    out = []
    problem = base_problem(base, rules, number)
    if problem:
        out.append(problem)
    dependabot = is_dependabot(author)
    out += title_problems(product, root, title, rules, dependabot=dependabot)
    out += commit_problems(product, root, base, head_sha, rules, dependabot=dependabot)
    if rules.pr_checks:
        out += product.hooks.pr_checks(
            pr_context(
                root,
                rules,
                base=base,
                head_sha=head_sha,
                head_ref=head_ref,
                title=title,
                author=author,
            )
        )
    return out


def checkout_root(product: Product, major_free: bool) -> Path:
    """The checkout the policy reads: the work tree with ``--major-free``, else the
    nearest directory holding the product's config file (``pyproject.checkout_root``;
    the work tree's root without one)."""
    if major_free:
        return repo.toplevel(Path.cwd())
    return pyproject.checkout_root(product)


def run(args: argparse.Namespace) -> int:
    product: Product = args.product
    root = checkout_root(product, args.major_free)
    if args.commit_msg_file:
        try:
            rules = rules_for(
                product, root, major_free=args.major_free, base_branch=args.base_branch
            )
        except config.NotOptedIn:
            return CLEAN
        found = message_problems(product, Path(args.commit_msg_file).read_text(), rules)
    elif args.pr:
        rules = rules_for(
            product, root, major_free=args.major_free, base_branch=args.base_branch
        )
        found = check_pr(product, root, rules, dict(os.environ))
    else:
        raise ConfigError("give --commit-msg-file F or --pr")
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for p in found:
        print(f"::error::{p}" if actions else p, file=sys.stderr)
    if not found:
        print(f"{product.identity.cli} policy: ok")
    return DRIFT if found else CLEAN


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    p = subparsers.add_parser(
        "policy",
        help="the commit-msg hook and the pr-policy check",
        description=help_text(product, "policy", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--commit-msg-file",
        metavar="F",
        help=help_text(
            product,
            "policy --commit-msg-file",
            "check one commit message (the commit-msg hook)",
        ),
    )
    mode.add_argument(
        "--pr",
        action="store_true",
        help=help_text(product, "policy --pr", "check the pull request (CI)"),
    )
    p.add_argument(
        "--major-free",
        action="store_true",
        help=help_text(
            product,
            "policy --major-free",
            "an engine repository's own rules: no version scheme and no product"
            " checks; `!` still refused",
        ),
    )
    p.add_argument(
        "--base-branch",
        default="main",
        help=help_text(
            product,
            "policy --base-branch",
            "the integration branch with --major-free (default: main)",
        ),
    )
    p.set_defaults(func=run)
