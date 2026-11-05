"""``<cli> repo doctor``: the repository settings the enabled modules rely on (DESIGN §7.2).

Read-only, and needs no admin. For the checkout in the current directory (or a cloned
``<owner/repo>``) it lists each setting as ``ok``, ``missing`` or ``unknown`` (not
readable with this token), with where to change it:

- ``releases``: Actions may create pull requests (a release tool's pull request); with a
  release branch, the ``repo-policy.release-bypass`` actor may move it (a ruleset
  bypass, or no ruleset restricting updates there);
- ``dependabot`` with ``auto-merge``: ``allow_auto_merge``, and every enabled gate's
  required context on the integration branch (otherwise the auto-merge step labels
  instead of merging);
- the labels the enabled modules use (``ci.labels``);
- an org profile in a private repository: the ``<PREFIX>_FETCH_TOKEN`` secret (Actions,
  and Dependabot with ``dependabot`` on).

``hooks.doctor_rows`` adds the product's rows and notices. Exit 0 when nothing is
``missing``, else 1.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.ci import labels as ci_labels
from standards_core.commands import help_text
from standards_core.commands.repo import targets
from standards_core.common import github, pyproject, repo, repo_policy, repoconfig
from standards_core.common.github import GitHubError
from standards_core.common.report import CLEAN, DRIFT, ConfigError

if TYPE_CHECKING:
    from standards_core.common.repoconfig import RepoConfig
    from standards_core.product import Product

OK, MISSING, UNKNOWN = "ok", "missing", "unknown"


@dataclass
class Row:
    module: str
    setting: str
    status: str
    where: str


def _status(fn: Callable[[], bool]) -> str:
    """``fn()``'s verdict, or ``unknown`` when GitHub refuses to say."""
    try:
        return OK if fn() else MISSING
    except GitHubError:
        return UNKNOWN


def _secret(r: str, name: str, kind: str = "actions") -> str:
    """Whether the repository (or its organisation, for this repository) has the
    secret."""
    try:
        github.get(f"repos/{r}/{kind}/secrets/{name}")
        return OK
    except GitHubError as e:
        if e.status != 404:
            return UNKNOWN
    if kind == "actions":
        try:
            org = github.get(f"repos/{r}/actions/organization-secrets") or {}
        except GitHubError:
            return UNKNOWN
        if any(s.get("name") == name for s in org.get("secrets", [])):
            return OK
    return MISSING


def _same_actor(a: dict, b: dict) -> bool:
    return a.get("actor_type") == b.get("actor_type") and a.get("actor_id") == b.get(
        "actor_id"
    )


def release_branch_movable(r: str, release: str, actors: list[dict]) -> str:
    """Whether one of ``actors`` may move ``release``: no ruleset restricts updates
    there, or each ruleset that does lets one of them bypass it."""
    try:
        rules = github.get(f"repos/{r}/rules/branches/{release}", paginate=True) or []
    except GitHubError:
        return UNKNOWN
    ids = {rule.get("ruleset_id") for rule in rules if rule.get("type") == "update"}
    ids.discard(None)
    if not ids:
        return OK
    for rid in sorted(ids, key=str):
        try:
            rs = github.get(f"repos/{r}/rulesets/{rid}") or {}
        except GitHubError:
            return UNKNOWN
        if "bypass_actors" not in rs:
            return UNKNOWN
        if not any(
            _same_actor(have, want) for have in rs["bypass_actors"] for want in actors
        ):
            return MISSING
    return OK


def _actor_name(release_bypass: str) -> str:
    if release_bypass == "github-actions":
        return "GitHub Actions"
    kind, _, ident = release_bypass.partition(":")
    return f"{'user' if kind == 'user' else 'App'} {ident}"


def _release_rows(product: Product, r: str, rc: RepoConfig, settings: str) -> list[Row]:
    rows: list[Row] = []
    try:
        perms = github.get(f"repos/{r}/actions/permissions/workflow") or {}
        status = OK if perms.get("can_approve_pull_request_reviews") else MISSING
    except GitHubError:
        status = UNKNOWN
    rows.append(
        Row(
            "releases",
            "Actions may create pull requests",
            status,
            f"{settings}/actions → Workflow permissions",
        )
    )
    release = rc.release_branch
    if release:
        bypass = rc.cfg.get("repo-policy", {}).get("release-bypass", "github-actions")
        try:
            actors = repo_policy.bypass_actors(bypass)
        except ConfigError:
            actors = []
        who = _actor_name(bypass)
        rows.append(
            Row(
                "releases",
                f"{who} may fast-forward {release}",
                release_branch_movable(r, release, actors) if actors else MISSING,
                f"{settings}/rules (a bypass for {who} on {release})",
            )
        )
    return rows


def _dependabot_rows(
    product: Product, r: str, rc: RepoConfig, info: dict, settings: str
) -> list[Row]:
    allowed = info.get("allow_auto_merge")
    rows = [
        Row(
            "dependabot",
            "auto-merge is allowed",
            UNKNOWN if allowed is None else (OK if allowed else MISSING),
            f"{settings} → Pull Requests → Allow auto-merge",
        )
    ]
    contexts, known = github.required_contexts(r, rc.integration)
    wanted = set(github.gate_contexts(product, rc.gates))
    if not known:
        status = UNKNOWN
    else:
        status = OK if wanted and wanted <= contexts else MISSING
    rows.append(
        Row(
            "dependabot",
            f"{rc.integration} requires {', '.join(sorted(wanted)) or 'the gate checks'}",
            status,
            f"{settings}/rules (or `{product.identity.cli} repo apply` with repo-policy"
            " on)",
        )
    )
    return rows


def _label_rows(product: Product, r: str, rc: RepoConfig, settings: str) -> list[Row]:
    names = [
        name
        for name, (_color, _description, module) in ci_labels(product).items()
        if not module or rc.modules.get(module)
    ]
    if not names:
        return []
    try:
        have = {
            lb["name"].lower()
            for lb in github.get(f"repos/{r}/labels", paginate=True) or []
        }
    except GitHubError:
        return [
            Row("labels", f"label {n}", UNKNOWN, f"{settings}/labels") for n in names
        ]
    where = (
        f"the CI steps create it on first use, or `{product.identity.cli} repo apply`"
    )
    return [
        Row("labels", f"label {name}", OK if name.lower() in have else MISSING, where)
        for name in names
    ]


def _profile_rows(product: Product, r: str, rc: RepoConfig, settings: str) -> list[Row]:
    source = rc.resolved.profile.get("source", "builtin")
    if not source.startswith("github:"):
        return []
    owner_repo = "/".join(source.removeprefix("github:").split("/")[:2])
    try:
        prof = github.get_or_none(f"repos/{owner_repo}")
        private = prof is None or bool(prof.get("private"))
    except GitHubError:
        private = True
    if not private:
        return []
    secret = f"{product.identity.env_prefix}_FETCH_TOKEN"
    rows = [
        Row(
            "ci",
            f"secret {secret} (private profile)",
            _secret(r, secret),
            f"{settings}/secrets/actions",
        )
    ]
    if rc.modules.get("dependabot"):
        rows.append(
            Row(
                "dependabot",
                f"Dependabot secret {secret}",
                _secret(r, secret, "dependabot"),
                f"{settings}/secrets/dependabot",
            )
        )
    return rows


def rows_for(product: Product, r: str, rc: RepoConfig) -> tuple[list[Row], list[str]]:
    """The core rows, then ``hooks.doctor_rows``."""
    cfg, modules = rc.cfg, rc.modules
    rows: list[Row] = []
    notices: list[str] = []
    try:
        info = github.get(f"repos/{r}") or {}
    except GitHubError:
        info = {}
    settings = f"https://github.com/{r}/settings"
    if modules.get("releases"):
        rows += _release_rows(product, r, rc, settings)
    if modules.get("dependabot") and cfg.get("dependabot", {}).get("auto-merge", True):
        rows += _dependabot_rows(product, r, rc, info, settings)
    rows += _label_rows(product, r, rc, settings)
    rows += _profile_rows(product, r, rc, settings)
    extra_rows, extra_notices = product.hooks.doctor_rows(r, rc)
    return rows + list(extra_rows), notices + list(extra_notices)


def repo_slug(product: Product, rc: RepoConfig) -> str:
    slug = rc.cfg.get("repo") or ""
    if slug:
        return slug
    origin = repo.origin_repo(rc.root)
    if origin and origin[0] == "github.com":
        return origin[1]
    raise ConfigError(
        "cannot tell which GitHub repository this is: set"
        f" [tool.{product.identity.table}] repo, or give <owner/repo>"
    )


def run(args: argparse.Namespace) -> int:
    product: Product = args.product
    table = f"[tool.{product.identity.table}]"
    try:
        if args.repo:
            t = targets.load(product, args.repo, {}, {})
            if not t.opted_in or t.config is None:
                raise ConfigError(f"{args.repo} has no {table}: nothing to check")
            rc, r = t.config, args.repo
        else:
            start = Path(args.root) if args.root else None
            rc = repoconfig.load(product, pyproject.checkout_root(product, start))
            r = repo_slug(product, rc)
        rows, notices = rows_for(product, r, rc)
    finally:
        targets.cleanup()
    if args.format == "json":
        doc: dict[str, Any] = {
            "repo": r,
            "rows": [row.__dict__ for row in rows],
            "notices": notices,
        }
        print(json.dumps(doc, indent=2))
    else:
        if not rows:
            print(f"{r}: no enabled module relies on a repository setting")
        for row in rows:
            print(f"{row.status:8} {row.module:11} {row.setting}  ({row.where})")
        for n in notices:
            print(f"notice: {n}")
    return DRIFT if any(row.status == MISSING for row in rows) else CLEAN


def register(sub: argparse._SubParsersAction, product: Product) -> None:
    p = sub.add_parser(
        "doctor",
        help="which repository settings the enabled modules rely on (read-only)",
        description=help_text(product, "repo doctor", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "repo",
        nargs="?",
        metavar="owner/repo",
        help=help_text(product, "repo doctor repo", None),
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help=help_text(product, "repo doctor --format", None),
    )
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.set_defaults(func=run)
