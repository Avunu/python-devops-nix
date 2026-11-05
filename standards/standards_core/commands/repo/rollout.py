"""``<cli> repo rollout``: move repositories to an engine release or an org profile
release (DESIGN §7.2).

The one path by which an engine (or org profile) release reaches a checkout with CI
callers: the bump rewrites the caller workflows' ``uses:`` SHAs, which a
``GITHUB_TOKEN`` can't push, so it runs locally with the user's ``gh`` token (scope
``workflow``). For each repository, in a temporary clone of its integration branch::

    nix flake update <engine input>             # with --to
    nix flake update standards-profile          # with --profile-to (the ref in `profile` set first)
    <the product's rollout steps>               # its relock, say
    nix run --no-pure-eval .#<init app> -- --sync

then a commit ``chore(deps): <engine input> vX.Y.Z`` (or ``chore(deps): standards
profile <short rev>``, or both in one subject), a push of
``standards/<engine input>-vX.Y.Z`` (or ``standards/profile-<short rev>``), a pull
request against the integration branch, and, unless ``--no-auto-merge``,
``gh pr merge --auto --<first of repo-policy.merge-methods>``. A repository whose
``profile`` is a built-in is skipped by ``--profile-to``. ``--dry-run`` prints each
repository's plan and runs nothing.

``--to`` names the release the bump must reach: after the engine's ``nix flake update``
the locked revision must be that tag's commit (the release branch the checkout tracks
is fast-forwarded to each tag), else the repository fails and nothing is pushed. Exit 0
when every repository was rolled out or had nothing to do, 1 when one failed, 2 for a
usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.commands import help_text
from standards_core.commands.repo import targets
from standards_core.common import flakelock, github, pyproject, repoconfig
from standards_core.common.config import ORG_PROFILE_PREFIXES
from standards_core.common.report import (
    CLEAN,
    DRIFT,
    ConfigError,
    EnvError,
    StandardsError,
)

if TYPE_CHECKING:
    from standards_core.common.repoconfig import RepoConfig
    from standards_core.product import Product

_VERSION = re.compile(r"^v\d+\.\d+\.\d+$")
_HEADER = re.compile(r"^\s*\[")


def sh(cmd: list[str], cwd: Path) -> str:
    """Run ``cmd`` in ``cwd``; a failure is an ``EnvError`` naming it. Tests replace
    it."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise EnvError(f"{cmd[0]} is not on PATH") from e
    if proc.returncode != 0:
        raise EnvError(f"{' '.join(cmd)}: {proc.stderr.strip()[-2000:]}")
    return proc.stdout


def set_profile_ref(product: Product, path: Path, ref: str) -> bool:
    """Point ``[tool.<table>] profile`` in the config file at ``path`` to ``ref``
    (``latest`` keeps the current ref). Only that table's ``profile`` line changes."""
    if ref == "latest":
        return False
    lines = path.read_text().splitlines(keepends=True)
    header = re.compile(
        r"^\s*\[\s*tool\.\"?" + re.escape(product.identity.table) + r"\"?\s*\]"
    )
    key = re.compile(r'^(\s*profile\s*=\s*)"[^"]*"')
    inside = False
    for i, line in enumerate(lines):
        if _HEADER.match(line):
            inside = bool(header.match(line))
            continue
        if inside:
            m = key.match(line)
            if m:
                new = key.sub(lambda m: f'{m[1]}"{ref}"', line, count=1)
                if new == line:
                    return False
                lines[i] = new
                path.write_text("".join(lines))
                return True
    return False


def check_reached(product: Product, root: Path, to: str) -> None:
    """The lock's engine revision is ``to``'s commit."""
    name = product.engine.flake_input
    lock = flakelock.load(root / "flake.lock")
    found = flakelock.node_at(lock, [name])
    locked = (found[1].get("locked") or {}) if found else {}
    owner, repo = locked.get("owner"), locked.get("repo")
    tag = github.get_or_none(f"repos/{owner}/{repo}/commits/{to}") or {}
    if not tag.get("sha") or tag["sha"] != locked.get("rev"):
        raise EnvError(
            f"flake.lock locks {name} {str(locked.get('rev'))[:12]}, not {to}"
            f" ({str(tag.get('sha'))[:12]}): its release branch has not reached that"
            " release"
        )


def _org_profile(rc: RepoConfig) -> bool:
    return rc.resolved.profile.get("source", "builtin").startswith(ORG_PROFILE_PREFIXES)


def plan(
    product: Product, rc: RepoConfig, to: str | None, profile_to: str | None
) -> tuple[list[list[str]], str, str]:
    """The commands, the commit subject and the branch for one checkout."""
    engine = product.engine
    cmds: list[list[str]] = []
    subjects, branches = [], []
    if to:
        cmds.append(["nix", "flake", "update", engine.flake_input])
        subjects.append(f"{engine.flake_input} {to}")
        branches.append(f"{engine.flake_input}-{to}")
    if profile_to and _org_profile(rc):
        cmds.append(["nix", "flake", "update", flakelock.PROFILE_INPUT])
    cmds += [list(step) for step in product.sync.rollout_steps]
    cmds.append(
        ["nix", "run", "--no-pure-eval", f".#{engine.init_app}", "--", "--sync"]
    )
    return cmds, "; ".join(subjects), "-".join(branches)


def rollout_one(product: Product, r: str, args: argparse.Namespace) -> str:
    cli = product.identity.cli
    root = targets.clone(product, r, depth=None)
    if root is None:
        raise EnvError(f"{r}: could not clone")
    try:
        rc = repoconfig.load(product, root)
    except ConfigError:
        return f"{r}: skipped, no [tool.{product.identity.table}]"
    integration = rc.integration
    if targets.clone_branch(root) != integration:
        sh(["git", "fetch", "-q", "origin", integration], root)
        sh(["git", "checkout", "-q", "-B", integration, f"origin/{integration}"], root)
        rc = repoconfig.load(product, root)
    org = _org_profile(rc)
    if args.profile_to and not args.to and not org:
        return f"{r}: skipped, its profile is the built-in {rc.cfg.get('profile')!r}"
    cmds, subject, branch = plan(product, rc, args.to, args.profile_to)
    method = (rc.cfg.get("repo-policy", {}).get("merge-methods") or ["squash"])[0]
    if args.dry_run:
        lines = [f"{r} ({integration}):"]
        if args.profile_to and args.profile_to != "latest" and org:
            lines.append(f"  set profile = {args.profile_to!r}")
        lines += [f"  {' '.join(c)}" for c in cmds]
        lines.append(
            f"  commit 'chore(deps): {subject or 'standards profile <rev>'}' on"
            f" standards/{branch or 'profile-<rev>'}"
        )
        lines.append(f"  gh pr create --base {integration}")
        if not args.no_auto_merge:
            lines.append(f"  gh pr merge --auto --{method}")
        return "\n".join(lines)
    if args.profile_to:
        set_profile_ref(product, pyproject.config_path(product, root), args.profile_to)
    for c in cmds:
        sh(c, root)
        if args.to and c == ["nix", "flake", "update", product.engine.flake_input]:
            check_reached(product, root, args.to)
    if args.profile_to and org:
        lock = flakelock.load(root / "flake.lock")
        rev = (flakelock.locked_rev(product, lock, flakelock.PROFILE_INPUT) or "")[:7]
        subject = "; ".join(s for s in (subject, f"standards profile {rev}") if s)
        branch = "-".join(b for b in (branch, f"profile-{rev}") if b)
    if not sh(["git", "status", "--porcelain"], root).strip():
        return f"{r}: already current"
    head = f"standards/{branch}"
    sh(["git", "checkout", "-q", "-B", head], root)
    sh(["git", "add", "-A"], root)
    sh(["git", "commit", "-q", "-m", f"chore(deps): {subject}"], root)
    sh(["git", "push", "-q", "--force", "origin", f"HEAD:refs/heads/{head}"], root)
    res = github.cli(
        "pr",
        "create",
        "-R",
        r,
        "--base",
        integration,
        "--head",
        head,
        "--title",
        f"chore(deps): {subject}",
        "--body",
        f"Rolled out with `{cli} repo rollout`.\n\n{subject}",
    )
    url = (
        res.stdout.strip().splitlines()[-1]
        if res.code == 0 and res.stdout.strip()
        else ""
    )
    if res.code != 0 and "already exists" not in res.stderr:
        raise EnvError(f"{r}: gh pr create: {res.stderr.strip()}")
    if not args.no_auto_merge:
        merge = github.cli("pr", "merge", "-R", r, "--auto", f"--{method}", url or head)
        if merge.code != 0:
            print(
                f"notice: {r}: auto-merge not enabled ({merge.stderr.strip()}); run"
                f" {cli} repo doctor",
                file=sys.stderr,
            )
    return f"{r}: {url or head}"


def run(args: argparse.Namespace) -> int:
    product: Product = args.product
    if not args.to and not args.profile_to:
        raise ConfigError("give --to vX.Y.Z, --profile-to <ref>|latest, or both")
    if args.to and not _VERSION.match(args.to):
        raise ConfigError(f"--to takes a release tag vX.Y.Z, not {args.to!r}")
    pairs, _fleet = targets.entries(product, args.repo or [], args.all, args.fleet)
    code = CLEAN
    try:
        for r, _entry in pairs:
            try:
                print(rollout_one(product, r, args))
            except StandardsError as e:
                code = DRIFT
                print(f"{product.identity.cli} repo rollout: {r}: {e}", file=sys.stderr)
    finally:
        targets.cleanup()
    return code


def register(sub: argparse._SubParsersAction, product: Product) -> None:
    p = sub.add_parser(
        "rollout",
        help=(
            f"move repositories to a {product.engine.flake_input} or org profile"
            " release (needs nix and a gh token with `workflow`)"
        ),
        description=help_text(product, "repo rollout", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    def add(flag: str, default_help: str | None, **kwargs: Any) -> None:
        p.add_argument(
            flag,
            help=help_text(product, f"repo rollout {flag}", default_help),
            **kwargs,
        )

    add("--to", None, metavar="vX.Y.Z")
    add("--profile-to", None, metavar="<ref>|latest")
    add("--repo", None, action="append", metavar="owner/repo")
    add("--all", None, action="store_true")
    add("--fleet", None)
    add("--no-auto-merge", None, action="store_true")
    add("--dry-run", None, action="store_true")
    p.set_defaults(func=run)
