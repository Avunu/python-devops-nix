"""Applying an auto-merge verdict under the safety rule (DESIGN §8.3).

Auto-merge is only safe where required checks protect the integration branch, and only
works where the repository allows it; a profile may turn ``dependabot.auto-merge`` on
but leave ``repo-policy`` off, so neither can be assumed. Before any ``gh pr merge
--auto`` the branch's rules and classic protection must require every enabled gate's
context (``ci.check_context``). If they don't, or the merge call fails (auto-merge
disabled for the repository), the PR is labelled ``needs-review`` with a notice naming
``<cli> repo doctor``, and the step still succeeds. Every label is created (``--force``,
idempotent) before it is applied.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from standards_core.ci import labels, notice, verdict
from standards_core.common import github

if TYPE_CHECKING:
    from standards_core.product import Product


def doctor(product: Product) -> str:
    return (
        f"run `{product.identity.cli} repo doctor` to see which repository settings"
        " it needs"
    )


def ensure_label(product: Product, name: str, repo: str = "") -> None:
    color, description, _module = labels(product)[name]
    args = [
        "label",
        "create",
        name,
        "--color",
        color,
        "--description",
        description,
        "--force",
    ]
    if repo:
        args += ["--repo", repo]
    github.cli(*args)


def label(product: Product, pr_url: str, name: str, repo: str = "") -> None:
    ensure_label(product, name, repo)
    res = github.cli("pr", "edit", pr_url, "--add-label", name)
    if res.code != 0:
        notice(f"could not label {pr_url} {name}: {res.stderr.strip()}")


def auto_merge_enabled(pr_url: str) -> bool:
    """Whether the PR already has auto-merge on, so a job that runs again (a weekly
    relock) leaves it as it is rather than asking twice."""
    res = github.cli("pr", "view", pr_url, "--json", "autoMergeRequest")
    if res.code != 0 or not res.stdout.strip():
        return False
    try:
        return bool(json.loads(res.stdout).get("autoMergeRequest"))
    except (json.JSONDecodeError, AttributeError):
        return False


def enable(
    product: Product,
    pr_url: str,
    repo: str,
    integration: str,
    gates: Sequence[str],
    merge_method: str,
) -> str:
    """``gh pr merge --auto`` when the gates protect ``integration``; else label
    ``needs-review``. Returns ``merging`` or ``needs-review``."""
    if not github.gates_protected(product, repo, integration, gates):
        label(product, pr_url, "needs-review", repo)
        contexts = ", ".join(github.gate_contexts(product, gates)) or "(no gates)"
        notice(
            f"not enabling auto-merge: {integration} does not require every enabled"
            f" gate ({contexts}); labelled needs-review. {doctor(product)}"
        )
        return "needs-review"
    if auto_merge_enabled(pr_url):
        print(f"auto-merge is already enabled on {pr_url}")
        return "merging"
    res = github.cli("pr", "merge", "--auto", f"--{merge_method}", pr_url)
    if res.code != 0:
        label(product, pr_url, "needs-review", repo)
        notice(
            f"`gh pr merge --auto` failed ({res.stderr.strip()}); labelled"
            f" needs-review. {doctor(product)}"
        )
        return "needs-review"
    print(f"auto-merge enabled ({merge_method}) on {pr_url}")
    return "merging"


def close(product: Product, pr_url: str, moved: Sequence[str]) -> None:
    cli = product.identity.cli
    what = " and ".join(moved) or " or ".join(product.rollout_inputs)
    body = (
        f"{what} moves through `{cli} repo rollout` (or a manual `nix flake update`"
        f" and `{product.identity.sync_command}` commit): its bump can rewrite"
        " workflow files, which this workflow cannot push. Closing."
    )
    github.cli("pr", "comment", pr_url, "--body", body)
    github.cli("pr", "close", pr_url)
    print(f"closed {pr_url}: {what} moves through {cli} repo rollout")


def apply(
    product: Product,
    decision: str,
    *,
    pr_url: str,
    repo: str,
    integration: str,
    gates: Sequence[str],
    merge_method: str,
    relock_state: str = "",
    moved: Sequence[str] | None = None,
) -> str:
    """Act on ``decision`` (a ``verdict`` value); returns what was done."""
    if decision == verdict.RELOCK:
        decision = verdict.nix_verdict(relock_state)
        if decision == "wait":
            print("nix: relock-push enables auto-merge after it pushes the relock")
            return "wait"
    if decision == verdict.CLOSE:
        close(product, pr_url, moved or [])
        return "closed"
    if decision == verdict.AUTO:
        return enable(product, pr_url, repo, integration, gates, merge_method)
    label(product, pr_url, "needs-review", repo)
    print(f"labelled {pr_url} needs-review")
    return "needs-review"
