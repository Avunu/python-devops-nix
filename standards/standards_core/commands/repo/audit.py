"""``<cli> repo audit``: the scorecard (DESIGN §7.4).

For each repository: a shallow clone of the integration branch (resolved as
``repo apply`` resolves it), the checkout's configuration, and the rows below, each
``green``, ``amber``, ``red``, ``n/a`` (its module is off) or ``unknown`` (the data
couldn't be read, for example because the token lacks admin). The commands a row runs
on the clone (``sync --check``) run from the product's tools at the revision the
checkout's ``flake.lock`` pins, through ``uv tool run`` (``--against latest``: the
latest release's).

====  ============  =====================================================================
A1    repo-policy   the default branch is the integration branch
A2    releases      tags flow: the release branch equals the newest release tag;
                    post-merge-bump: it equals the integration head, or is behind it only
                    by commits whose checks are pending
A3    repo-policy   settings and rulesets match repo-policy/ as rendered for the checkout
A4    (always)      managed files present and ``sync --check`` clean
A5    ci            the gates green on the integration branch; pr-policy and the nightly
                    green when configured
A9    (always)      no legacy files (the retire rules)
A10   dependabot    the last nix bump merged by a bot with its relock; the engine and the
                    profile current
A11   releases      a release tag exists, released by a bot (tags flow only)
====  ============  =====================================================================

A product adds rows, or replaces one by id, through ``Product.audit_rows``; rows print
sorted by the number after ``A``.

Exit codes: 0 every row green, n/a or unknown; 1 any amber or red; 2 a configuration
error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from standards_core.ci import conventional
from standards_core.commands import help_text
from standards_core.commands.repo import apply as apply_cmd
from standards_core.commands.repo import targets
from standards_core.common import flakelock, github, repo_policy
from standards_core.common.github import GitHubError
from standards_core.common.report import CLEAN, DRIFT, ConfigError, StandardsError

if TYPE_CHECKING:
    from standards_core.product import Product

GREEN, AMBER, RED, NA, UNKNOWN = "green", "amber", "red", "n/a", "unknown"
STALE_DAYS = 14
# The subject the relock commit on a nix bump starts with (the products' relock steps).
RELOCK_SUBJECT = "chore(deps): relock"


@dataclass
class Row:
    id: str
    item: str
    status: str
    detail: str = ""


@dataclass
class Audit:
    """One repository's audit: the target, and what more than one row reads, read
    once."""

    t: targets.Target
    against: str = "pinned"
    cache: dict = field(default_factory=dict)

    @property
    def product(self) -> Product:
        if self.t.product is None:
            raise StandardsError(f"{self.t.repo} was loaded without its product")
        return self.t.product

    @property
    def r(self) -> str:
        return self.t.repo

    @property
    def root(self) -> Path:
        """The clone (every row that reads it runs only for an opted-in, cloned
        repository)."""
        if self.t.root is None:
            raise StandardsError(f"{self.t.repo} was not cloned")
        return self.t.root

    def once(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self.cache:
            try:
                self.cache[key] = fn()
            except GitHubError as e:
                self.cache[key] = e
        value = self.cache[key]
        if isinstance(value, GitHubError):
            raise value
        return value


@dataclass(frozen=True)
class RowSpec:
    item: str  # the scorecard text
    module: str | None  # n/a when off; None = always
    evaluate: Callable[[Audit], tuple[str, str]]  # (status, detail)


# --- the seam tests replace -----------------------------------------------------------


def run_tool(
    product: Product, root: Path, argv: list[str], spec: str | None
) -> tuple[int, str]:
    """``<cli> <argv>`` in ``root``, from the product's tools ``spec`` (a pip
    requirement), or the running one when ``spec`` is None. A pinned tool runs with
    ``<PREFIX>_ALLOW_SKEW=1``: it is not the engine the checkout's lock pins."""
    identity = product.identity
    env = dict(os.environ)
    if spec and "@" in spec:
        env[f"{identity.env_prefix}_ALLOW_SKEW"] = "1"
    cmd = (
        [
            "uv",
            "tool",
            "run",
            "--python",
            product.engine.tool_python,
            "--from",
            spec,
            identity.cli,
            *argv,
        ]
        if spec
        else [identity.cli, *argv]
    )
    try:
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, check=False, env=env
        )
    except FileNotFoundError as e:
        raise StandardsError(f"{cmd[0]} is not on PATH") from e
    return proc.returncode, proc.stdout


# --- shared reads ------------------------------------------------------------------------


def engine_node(a: Audit) -> dict:
    """The ``locked`` part of the clone's engine node (``{}`` without one)."""
    path = a.root / "flake.lock"
    if not path.is_file():
        return {}
    lock = flakelock.load(path)
    found = flakelock.node_at(lock, [a.product.engine.flake_input])
    return (found[1].get("locked") or {}) if found else {}


def default_tool_spec(a: Audit) -> str | None:
    """The product tools the checkout pins (or the latest release's):
    ``engine.install_spec`` at the locked revision."""
    if a.t.root is None:
        return None
    locked = engine_node(a)
    owner, name, rev = locked.get("owner"), locked.get("repo"), locked.get("rev")
    if not (owner and name and rev):
        return None
    if a.against == "latest":
        rel = github.get_or_none(f"repos/{owner}/{name}/releases/latest") or {}
        rev = rel.get("tag_name") or rev
    return a.product.engine.install_spec.format(owner=owner, repo=name, rev=rev)


def tool_spec(a: Audit) -> str | None:
    hook = a.product.hooks.tool_spec
    return hook(a) if hook is not None else default_tool_spec(a)


def tool(a: Audit, *argv: str) -> tuple[int, str]:
    key = "tool:" + " ".join(argv)
    if key not in a.cache:
        spec = a.once("spec", lambda: tool_spec(a))
        a.cache[key] = run_tool(a.product, a.root, list(argv), spec)
    return a.cache[key]


def sync_report(a: Audit) -> dict | None:
    def read() -> dict | None:
        _, out = tool(a, "sync", "--check", "--format", "json")
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    return a.once("sync", read)


def tags(a: Audit) -> list[dict]:
    return a.once("tags", lambda: github.get(f"repos/{a.r}/tags", paginate=True) or [])


def release_tags(a: Audit) -> list[tuple[Version, dict]]:
    """The release tags, newest first: ``<prefix><version>`` whose version the
    checkout's ``releases.version-scheme`` takes."""
    rel = a.t.cfg.get("releases", {})
    prefix = rel.get("tag-prefix", "v")
    scheme = conventional.scheme(a.product, rel.get("version-scheme", "semver"))
    out = []
    for tag in tags(a):
        name = tag.get("name", "")
        if not name.startswith(prefix):
            continue
        version = name[len(prefix) :]
        try:
            v = Version(version)
        except InvalidVersion:
            continue
        if scheme.valid(version, a.t.cfg, a.t.facts) is not None:
            continue
        out.append((v, tag))
    return sorted(out, key=lambda x: x[0], reverse=True)


def latest_run(a: Audit, workflow: str, branch: str | None = None) -> dict | None:
    def read() -> dict | None:
        q = f"?status=completed&per_page=1{f'&branch={branch}' if branch else ''}"
        endpoint = f"repos/{a.r}/actions/workflows/{workflow}/runs{q}"
        runs = (github.get(endpoint) or {}).get("workflow_runs", [])
        return runs[0] if runs else None

    return a.once(f"run:{workflow}:{branch}", read)


def run_jobs(a: Audit, run_id: int) -> list[dict]:
    endpoint = f"repos/{a.r}/actions/runs/{run_id}/jobs?per_page=100"
    return a.once(
        f"jobs:{run_id}", lambda: (github.get(endpoint) or {}).get("jobs", [])
    )


def info(a: Audit) -> dict:
    return a.once("info", lambda: github.get(f"repos/{a.r}") or {})


def flow(a: Audit) -> str:
    """The ``Branching.flow`` of the checkout's ``releases.branching`` (``none``
    without one)."""
    found = repo_policy.branching(a.product, a.t.cfg)
    return found.flow if found is not None else "none"


def _days_since(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    when = dt.datetime.fromisoformat(stamp)
    return (dt.datetime.now(dt.UTC) - when).total_seconds() / 86400


# --- the rows --------------------------------------------------------------------------


def a1(a: Audit) -> tuple[str, str]:
    default = info(a).get("default_branch")
    if default == a.t.integration:
        return GREEN, default
    return RED, f"default {default}, integration {a.t.integration}"


def _ref_sha(a: Audit, branch: str) -> str | None:
    ref = github.get_or_none(f"repos/{a.r}/git/ref/heads/{branch}")
    return ref["object"]["sha"] if ref else None


def _a2_tags(a: Audit, branch: str) -> tuple[str, str]:
    newest = release_tags(a)
    if not newest:
        return AMBER, "no release tag yet"
    sha = _ref_sha(a, branch)
    if sha is None:
        return RED, f"{branch} does not exist"
    tag = newest[0][1]
    want = tag["commit"]["sha"]
    if sha == want:
        return GREEN, f"{branch} = {tag['name']}"
    return RED, f"{branch} is {sha[:12]}, {tag['name']} is {want[:12]}"


def _a2_bump(a: Audit, branch: str) -> tuple[str, str]:
    integration = a.t.integration
    sha = _ref_sha(a, branch)
    if sha is None:
        return RED, f"{branch} does not exist"
    head = _ref_sha(a, integration)
    if head is None:
        return RED, f"{integration} does not exist"
    if sha == head:
        return GREEN, f"{branch} = {integration}"
    compare = github.get(f"repos/{a.r}/compare/{sha}...{head}") or {}
    if compare.get("status") != "ahead":
        return RED, f"{branch} and {integration} have diverged"
    commits = [c.get("sha", "") for c in compare.get("commits", [])]
    contexts = github.gate_contexts(a.product, a.t.gates)
    states = {c: github.check_state(a.r, c, contexts) for c in commits}
    settled = [c[:12] for c, state in states.items() if state != "pending"]
    if settled:
        return RED, (
            f"{branch} is {len(commits)} commit(s) behind {integration}, and"
            f" {', '.join(settled)} finished its checks"
        )
    return (
        GREEN,
        f"{branch} is behind {integration} by {len(commits)} pending commit(s)",
    )


def a2(a: Audit) -> tuple[str, str]:
    branch = a.t.release_branch
    if not branch:
        return NA, "no release branch"
    kind = flow(a)
    if kind == "tags":
        return _a2_tags(a, branch)
    if kind == "post-merge-bump":
        return _a2_bump(a, branch)
    return NA, f"releases.branching moves no release branch (flow {kind})"


def a3(a: Audit) -> tuple[str, str]:
    product = a.product
    out = apply_cmd.Outcome(a.r)
    phase = "full" if a.t.opted_in else "provisional"
    documentation = apply_cmd.documentation_url(product, a.t)
    policy = apply_cmd.plan_policy(product, a.t, phase, documentation)
    apply_cmd.apply_settings(out, a.r, policy, True)
    if info(a).get("private") or a.t.private:
        detail = "rulesets n/a (private)"
    else:
        try:
            apply_cmd.apply_rulesets(out, a.r, policy, True, False)
            detail = ""
        except StandardsError:
            return UNKNOWN, "rulesets not readable"
    if out.changes:
        return RED, "; ".join(c.call for c in out.changes)
    return GREEN, detail


def a4(a: Audit) -> tuple[str, str]:
    report = sync_report(a)
    if report is None:
        return UNKNOWN, "sync --check gave no report"
    status = report.get("status")
    if status == "clean":
        return GREEN, ""
    paths = ", ".join(f["path"] for f in report.get("files", [])[:5])
    return RED, f"{status}: {paths}"


def a5(a: Audit) -> tuple[str, str]:
    product = a.product
    ci = product.ci
    run = latest_run(a, ci.ci_workflow, a.t.integration)
    if not run:
        return RED, f"no completed {ci.ci_workflow} run on {a.t.integration}"
    problems = []
    if run.get("conclusion") != "success":
        problems.append(f"{ci.ci_workflow} {run.get('conclusion')}")
    jobs = {j.get("name"): j.get("conclusion") for j in run_jobs(a, run["id"])}
    for gate in a.t.gates:
        if gate == "pr-policy":
            continue
        name = ci.check_context.format(gate=gate)
        if jobs.get(name) != "success":
            problems.append(f"{name} {jobs.get(name, 'absent')}")
    if "pr-policy" in a.t.gates:
        workflow = dict(ci.contexts).get(ci.check_context.format(gate="pr-policy"))
        if workflow and workflow != ci.ci_workflow:
            pr = latest_run(a, workflow)
            if pr and pr.get("conclusion") != "success":
                problems.append(f"pr-policy {pr.get('conclusion')}")
    if ci.nightly_workflow:
        nightly = latest_run(a, ci.nightly_workflow)
        if nightly is None or nightly.get("conclusion") != "success":
            problems.append(
                f"nightly {nightly.get('conclusion') if nightly else 'never ran'}"
            )
    return (RED, "; ".join(problems)) if problems else (GREEN, "")


def a9(a: Audit) -> tuple[str, str]:
    report = sync_report(a)
    if report is None:
        return UNKNOWN, "sync --check gave no report"
    legacy = [
        f["path"]
        for f in report.get("files", [])
        if "legacy" in (f.get("problem") or "")
    ]
    if legacy:
        return RED, f"legacy files: {', '.join(legacy)}"
    return GREEN, ""


def _nix_bump_notes(a: Audit) -> list[str]:
    endpoint = (
        f"repos/{a.r}/pulls?state=closed&per_page=100&sort=updated&direction=desc"
    )
    prs = github.get(endpoint) or []
    nix = [
        p
        for p in prs
        if p.get("merged_at")
        and p.get("head", {}).get("ref", "").startswith("dependabot/nix/")
    ]
    if not nix:
        return []
    pr = nix[0]
    commits = github.get(f"repos/{a.r}/pulls/{pr['number']}/commits") or []
    relocked = any(
        c.get("commit", {}).get("message", "").startswith(RELOCK_SUBJECT)
        for c in commits
    )
    merger = (pr.get("merged_by") or {}).get("login", "")
    # github-actions[bot], dependabot[bot], or the App of ci.github-app.
    if (relocked or len(commits) == 1) and (merger == "" or merger.endswith("[bot]")):
        return []
    without = "" if relocked else " without a relock"
    return [f"#{pr['number']} merged by {merger or '?'}{without}"]


def _engine_notes(a: Audit, lock: dict) -> list[str]:
    product = a.product
    found = flakelock.node_at(lock, [product.engine.flake_input])
    locked = (found[1].get("locked") or {}) if found else {}
    if not locked.get("owner"):
        return []
    owner, name = locked["owner"], locked["repo"]
    latest = github.get_or_none(f"repos/{owner}/{name}/releases/latest") or {}
    tag = latest.get("tag_name")
    if not tag:
        return []
    tag_sha = (github.get_or_none(f"repos/{owner}/{name}/commits/{tag}") or {}).get(
        "sha"
    )
    if (
        tag_sha
        and tag_sha != locked.get("rev")
        and _days_since(latest.get("published_at")) > STALE_DAYS
    ):
        return [
            (
                f"{product.engine.flake_input} {tag} available:"
                f" {product.identity.cli} repo rollout --to {tag} --repo {a.r}"
            )
        ]
    return []


def _profile_notes(a: Audit, lock: dict) -> list[str]:
    product = a.product
    prof = flakelock.input_node(product, lock, flakelock.PROFILE_INPUT)
    src = a.t.resolved.profile.get("source", "builtin") if a.t.resolved else "builtin"
    if not prof or not src.startswith("github:"):
        return []
    plocked = prof[1].get("locked") or {}
    parts = src.removeprefix("github:").split("/")
    ref = "/".join(parts[2:]) or "HEAD"
    head = github.get_or_none(f"repos/{parts[0]}/{parts[1]}/commits/{ref}") or {}
    if not head.get("sha") or head["sha"] == plocked.get("rev"):
        return []
    date = (head.get("commit") or {}).get("committer", {}).get("date")
    if _days_since(date) <= STALE_DAYS:
        return []
    return [
        (
            f"standards profile behind {ref}: {product.identity.cli} repo rollout"
            f" --profile-to latest --repo {a.r}"
        )
    ]


def a10(a: Audit) -> tuple[str, str]:
    notes = _nix_bump_notes(a)
    lock_path = a.root / "flake.lock"
    if lock_path.is_file():
        lock = flakelock.load(lock_path)
        notes += _engine_notes(a, lock) + _profile_notes(a, lock)
    return (AMBER if notes else GREEN), "; ".join(notes)


def a11(a: Audit) -> tuple[str, str]:
    if flow(a) != "tags":
        return NA, "no release tags under this branching"
    newest = release_tags(a)
    if not newest:
        return RED, "no release tag"
    name = newest[0][1]["name"]
    rel = github.get_or_none(f"repos/{a.r}/releases/tags/{name}")
    if rel is None:
        return RED, f"{name} has no GitHub release"
    author = (rel.get("author") or {}).get("login", "")
    # The release tool's: GITHUB_TOKEN's github-actions[bot], or the App of ci.github-app.
    if author.endswith("[bot]"):
        return GREEN, name
    return AMBER, f"{name} released by {author}"


CORE_ROWS: dict[str, RowSpec] = {
    "A1": RowSpec("default branch is the integration branch", "repo-policy", a1),
    "A2": RowSpec("release branch at the newest release", "releases", a2),
    "A3": RowSpec("settings and rulesets match repo-policy/", "repo-policy", a3),
    "A4": RowSpec("managed files present, sync --check clean", None, a4),
    "A5": RowSpec("gates green on the integration branch, nightly green", "ci", a5),
    "A9": RowSpec("no legacy files", None, a9),
    "A10": RowSpec(
        "dependency bumps merge with relock; the engine and profile current",
        "dependabot",
        a10,
    ),
    "A11": RowSpec("a release tag made by a bot", "releases", a11),
}


def _row_number(rid: str) -> tuple[int, str]:
    try:
        return int(rid.removeprefix("A")), rid
    except ValueError:
        return 1 << 30, rid


def row_specs(product: Product) -> list[tuple[str, RowSpec]]:
    """The core rows with ``product.audit_rows`` over them, by the number after A."""
    rows = {**CORE_ROWS, **product.audit_rows}
    return sorted(rows.items(), key=lambda kv: _row_number(kv[0]))


def audit(a: Audit) -> list[Row]:
    rows = []
    table = f"[tool.{a.product.identity.table}]"
    for rid, spec in row_specs(a.product):
        if a.t.resolved is None or not a.t.opted_in:
            if rid == "A4":
                rows.append(
                    Row(rid, spec.item, RED, f"no {table} on the integration branch")
                )
            else:
                status = UNKNOWN if a.t.resolved is None else NA
                rows.append(Row(rid, spec.item, status, "not opted in"))
            continue
        if spec.module is not None and not a.t.modules.get(spec.module):
            rows.append(Row(rid, spec.item, NA, f"{spec.module} is off"))
            continue
        try:
            status, detail = spec.evaluate(a)
        except StandardsError as e:
            status, detail = UNKNOWN, str(e).splitlines()[0] if str(e) else ""
        rows.append(Row(rid, spec.item, status, detail))
    return rows


def markdown(repo_name: str, rows: list[Row]) -> str:
    out = [
        f"## {repo_name}",
        "",
        "| Row | Item | Status | Detail |",
        "|---|---|---|---|",
    ]
    out += [
        f"| {r.id} | {r.item} | {r.status} | {r.detail.replace('|', '/')} |"
        for r in rows
    ]
    return "\n".join(out) + "\n"


def file_issue(product: Product, issue_repo: str, repo_name: str, body: str) -> None:
    """Open or update ``scorecard: <repo>``. ``<PREFIX>_ISSUE_TOKEN``, when set, is the
    token for these calls: the audit token is read-only, and the scorecard issues live
    in the repository that runs the audit (the fleet-audit action passes its own
    token)."""
    title = f"scorecard: {repo_name}"
    token = os.environ.get(f"{product.identity.env_prefix}_ISSUE_TOKEN")
    saved = os.environ.get("GH_TOKEN")
    if token:
        os.environ["GH_TOKEN"] = token
    try:
        endpoint = f"repos/{issue_repo}/issues?state=open&per_page=100"
        issues = github.get(endpoint, paginate=True) or []
        found = [i for i in issues if i.get("title") == title]
        if found:
            github.request(
                "PATCH",
                f"repos/{issue_repo}/issues/{found[0]['number']}",
                {"body": body},
            )
        else:
            github.request(
                "POST", f"repos/{issue_repo}/issues", {"title": title, "body": body}
            )
    finally:
        if token:
            if saved is None:
                os.environ.pop("GH_TOKEN", None)
            else:
                os.environ["GH_TOKEN"] = saved


def run(args: argparse.Namespace) -> int:
    product: Product = args.product
    if args.issue and not args.issue_repo:
        raise ConfigError("--issue needs --issue-repo <owner/repo>")
    pairs, fleet = targets.entries(product, args.repo or [], args.all, args.fleet)
    results = []
    code = CLEAN
    try:
        for r, entry in pairs:
            t = targets.load(product, r, entry, fleet)
            rows = audit(Audit(t, args.against))
            results.append((r, rows))
            if any(row.status in (AMBER, RED) for row in rows):
                code = DRIFT
            if args.issue:
                file_issue(product, args.issue_repo, r, markdown(r, rows))
    finally:
        targets.cleanup()
    if args.format == "json":
        doc = {
            "repos": [
                {"repo": r, "rows": [row.__dict__ for row in rows]}
                for r, rows in results
            ]
        }
        text = json.dumps(doc, indent=2)
    else:
        text = "\n".join(markdown(r, rows) for r, rows in results)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return code


def register(sub: argparse._SubParsersAction, product: Product) -> None:
    p = sub.add_parser(
        "audit",
        help="the scorecard for one or more repositories",
        description=help_text(product, "repo audit", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    def add(flag: str, default_help: str | None, **kwargs: Any) -> None:
        p.add_argument(
            flag, help=help_text(product, f"repo audit {flag}", default_help), **kwargs
        )

    add("--repo", None, action="append", metavar="owner/repo")
    add("--all", "every repository in --fleet", action="store_true")
    add("--fleet", "the fleet list (an org profile repository's fleet.json)")
    add("--format", None, choices=("md", "json"), default="md")
    add("--out", "write the scorecard to this file")
    add(
        "--issue",
        "open or update `scorecard: <repo>` in --issue-repo",
        action="store_true",
    )
    add("--issue-repo", None, metavar="owner/repo")
    add("--against", None, choices=("pinned", "latest"), default="pinned")
    p.set_defaults(func=run)
