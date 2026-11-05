"""What the ``repo`` commands work on: the fleet list, clones and each target's
configuration (DESIGN §7.1); and ``<cli> repo targets``, which prints them.

The fleet list belongs to the organisation, not to the product: ``--all`` reads it from
``--fleet <file>``, validated against the composed ``fleet.schema.json``. Each target
is resolved from a shallow clone of its default branch (or of its integration branch,
when the default isn't it yet and that branch has opted in): an opted-in repository by
its own table, one that hasn't opted in by the profile ``--profile``, its fleet entry or
the fleet file names, with the app layer ``hooks.fleet_table`` gives.

``clone`` and ``fetch_profile`` are module-level functions, so tests replace them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.commands import help_text
from standards_core.common import config, pyproject, repoconfig, schema
from standards_core.common.report import CLEAN, ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.common.config import Resolved
    from standards_core.common.repoconfig import RepoConfig
    from standards_core.product import Product

_cleanup: list[Path] = []


@dataclass
class Target:
    """One repository and what is known about it."""

    repo: str
    entry: dict = field(default_factory=dict)
    root: Path | None = None
    opted_in: bool = False
    config: RepoConfig | None = None
    resolved: Resolved | None = None
    gates: list[str] = field(default_factory=list)
    profile_dir: Path | None = None
    product: Product | None = field(default=None, repr=False, compare=False)

    @property
    def cfg(self) -> dict:
        return self.resolved.cfg if self.resolved else {}

    @property
    def modules(self) -> dict:
        return self.resolved.modules if self.resolved else {}

    @property
    def facts(self) -> dict:
        """The clone's discovery facts (``{}`` without a configuration)."""
        return self.config.facts if self.config is not None else {}

    @property
    def integration(self) -> str:
        """The fleet entry's ``integration_branch``, else its first ``branches``, else
        the resolved ``integration-branch``."""
        return entry_integration(self.entry) or self.cfg.get("integration-branch", "")

    @property
    def release_branch(self) -> str:
        if self.product is None or self.resolved is None:
            return ""
        return repoconfig.release_branch(
            self.product, self.cfg, self.modules, self.facts
        )

    @property
    def branches(self) -> list[str]:
        """The fleet entry's ``branches``, else the integration branch alone."""
        listed = self.entry.get("branches")
        if listed:
            return list(listed)
        return [self.integration] if self.integration else []

    @property
    def private(self) -> bool:
        return bool(self.entry.get("private"))


def entry_integration(entry: dict) -> str:
    """A fleet entry's own integration branch: ``integration_branch``, else
    ``branches[0]``, else ``""``."""
    if entry.get("integration_branch"):
        return str(entry["integration_branch"])
    branches = entry.get("branches") or []
    return str(branches[0]) if branches else ""


def load_fleet(product: Product, path: str | None) -> dict:
    """The fleet file, validated; ``None`` is exit 2 (``--all`` needs ``--fleet``)."""
    if not path:
        raise ConfigError(
            "--all needs --fleet <file>: the fleet list lives in an org profile"
            " repository"
        )
    try:
        doc = json.loads(Path(path).read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"--fleet {path}: no such file") from e
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ConfigError(f"--fleet {path}: {e}") from e
    errors = schema.Validator(product, schema.FLEET, f"--fleet {path}").errors(doc)
    if errors:
        raise ConfigError("; ".join(errors))
    return doc


def entries(
    product: Product, repos: list[str], all_: bool, fleet_path: str | None
) -> tuple[list[tuple[str, dict]], dict]:
    """``[(repo, fleet entry)]`` and the fleet document (``{}`` without one)."""
    fleet = load_fleet(product, fleet_path) if all_ or fleet_path else {}
    listed = fleet.get(product.fleet_key, [])
    by_repo = {a["repo"].lower(): a for a in listed}
    if all_:
        if repos:
            raise ConfigError("give repositories or --all, not both")
        return [(a["repo"], a) for a in listed], fleet
    if not repos:
        raise ConfigError("give one or more <owner/repo>, or --all --fleet <file>")
    for r in repos:
        if r.count("/") != 1 or not all(r.split("/")):
            raise ConfigError(f"{r!r} is not <owner/repo>")
    return [(r, by_repo.get(r.lower(), {})) for r in repos], fleet


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise EnvError("git is not on PATH") from e


def _tempdir(product: Product, kind: str) -> Path:
    dest = Path(tempfile.mkdtemp(prefix=f"{product.identity.name}-{kind}-"))
    _cleanup.append(dest)
    return dest


def clone(
    product: Product, repo: str, branch: str | None = None, *, depth: int | None = 1
) -> Path | None:
    """A clone of ``repo`` (its default branch, or ``branch``) in a temporary directory,
    at ``<tmp>/<repo name>``, or ``None`` when that branch doesn't exist.
    Authenticates through ``gh``'s credential helper."""
    dest = _tempdir(product, "repo") / repo.rsplit("/", 1)[-1]
    args = [
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
        "clone",
        "-q",
    ]
    if depth:
        args += ["--depth", str(depth)]
    if branch:
        args += ["--branch", branch]
    proc = _git(*args, f"https://github.com/{repo}.git", str(dest))
    if proc.returncode != 0:
        if branch and "not found in upstream" in proc.stderr:
            return None
        where = f" ({branch})" if branch else ""
        raise EnvError(f"git clone {repo}{where}: {proc.stderr.strip()}")
    return dest


def clone_branch(root: Path) -> str:
    """The branch a clone has checked out."""
    return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()


def fetch_profile(product: Product, ref: str) -> Path:
    """An org profile's tree for a ``github:<owner>/<repo>[/<ref>]`` (or ``gitlab:``,
    ``git+https:``) reference, cloned at that ref; a built-in name needs no tree."""
    if ref.startswith("github:"):
        parts = ref.removeprefix("github:").split("/")
        url = f"https://github.com/{parts[0]}/{parts[1]}.git"
        branch = "/".join(parts[2:]) or None
    elif ref.startswith("gitlab:"):
        parts = ref.removeprefix("gitlab:").split("/")
        url = f"https://gitlab.com/{parts[0]}/{parts[1]}.git"
        branch = "/".join(parts[2:]) or None
    elif ref.startswith("git+https://"):
        base, _, query = ref.removeprefix("git+").partition("?")
        url = base
        pairs = (p.split("=", 1) for p in query.split("&") if "=" in p)
        branch = dict(pairs).get("ref")
    else:
        raise ConfigError(
            f"profile {ref!r} is not a github:, gitlab: or git+https:// reference"
        )
    dest = _tempdir(product, "profile") / "profile"
    args = ["clone", "-q", "--depth", "1", *(["--branch", branch] if branch else [])]
    proc = _git(*args, url, str(dest))
    if proc.returncode != 0:
        raise EnvError(f"fetching profile {ref}: {proc.stderr.strip()}")
    return dest


def cleanup() -> None:
    while _cleanup:
        shutil.rmtree(_cleanup.pop(), ignore_errors=True)


def opted_in(product: Product, root: Path) -> bool:
    """Whether the checkout at ``root`` has the table in its config file."""
    path = pyproject.config_path(product, root)
    if not path.is_file():
        return False
    try:
        doc = pyproject.parse(pyproject.read(path), path)
    except (ConfigError, EnvError):
        return False
    return pyproject.tool_table(product, doc) is not None


def resolve_profile(
    product: Product, repo: str, ref: str, entry: dict
) -> tuple[Resolved, Path | None]:
    """A not-yet-opted-in repository's configuration: ``ref`` with the app layer
    ``hooks.fleet_table`` gives, on GitHub."""
    table: dict[str, Any] = {
        "schema": 1,
        "profile": ref,
        "repo": repo,
        **product.hooks.fleet_table(repo, entry, ref),
    }
    integration = entry_integration(entry)
    if integration:
        table["integration-branch"] = integration
    doc = {"tool": {product.identity.table: table}}
    if config.is_builtin(ref):
        return config.resolve_doc(product, doc, on_github=True), None
    directory = fetch_profile(product, ref)
    resolved = config.resolve_doc(product, doc, profile_dir=directory, on_github=True)
    return resolved, directory


def _profile_dir(product: Product, rc: RepoConfig) -> Path | None:
    source = rc.resolved.profile.get("source", "builtin")
    if source == "builtin":
        return None
    try:
        return config.org_profile_dir(product, source, rc.root)[0]
    except (ConfigError, EnvError):
        return None


def load(
    product: Product,
    repo: str,
    entry: dict,
    fleet: dict,
    profile: str | None = None,
) -> Target:
    """Clone ``repo`` and resolve it (DESIGN §7.1)."""
    t = Target(repo, dict(entry), product=product)
    root = clone(product, repo)
    if root is None:
        raise EnvError(f"{repo}: could not clone the default branch")
    if opted_in(product, root):
        rc = repoconfig.load(product, root)
        integration = entry_integration(entry) or rc.integration
        head = clone_branch(root)
        if integration and head and head != integration:
            other = clone(product, repo, integration)
            if other is not None and opted_in(product, other):
                root, rc = other, repoconfig.load(product, other)
        t.root, t.opted_in, t.config = root, True, rc
        t.resolved, t.gates = rc.resolved, list(rc.gates)
        t.profile_dir = _profile_dir(product, rc)
        return t
    t.root = root
    ref = profile or entry.get("profile") or fleet.get("profile")
    if ref:
        t.resolved, t.profile_dir = resolve_profile(product, repo, ref, entry)
    return t


# --- repo targets --------------------------------------------------------------------


def row(t: Target) -> dict[str, Any]:
    """One target as ``repo targets`` prints it."""
    return {
        "repo": t.repo,
        "branches": t.branches,
        "integration": t.integration,
        "opted_in": t.opted_in,
        "profile": t.cfg.get("profile", ""),
        "private": t.private,
    }


def run(args: argparse.Namespace) -> int:
    product: Product = args.product
    pairs, fleet = entries(product, args.repo or [], args.all, args.fleet)
    rows = []
    try:
        for r, entry in pairs:
            rows.append(row(load(product, r, entry, fleet, args.profile)))
    finally:
        cleanup()
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return CLEAN
    for item in rows:
        print(
            f"{item['repo']}: integration {item['integration'] or '-'}, branches"
            f" {', '.join(item['branches']) or '-'},"
            f" {'opted in' if item['opted_in'] else 'not opted in'}, profile"
            f" {item['profile'] or '-'}{', private' if item['private'] else ''}"
        )
    return CLEAN


def register(sub: argparse._SubParsersAction, product: Product) -> None:
    p = sub.add_parser(
        "targets",
        help="the fleet's repositories, their branches and opt-in state (read-only)",
        description=help_text(
            product,
            "repo targets",
            "Print each target's repository, branches, integration branch, opt-in"
            " state, profile and privacy: the matrix the fleet audit runs over"
            " (--format json).",
        ),
    )
    p.add_argument(
        "--repo",
        action="append",
        metavar="owner/repo",
        help=help_text(product, "repo targets --repo", None),
    )
    p.add_argument(
        "--all",
        action="store_true",
        help=help_text(product, "repo targets --all", "every repository in --fleet"),
    )
    p.add_argument(
        "--fleet",
        help=help_text(
            product,
            "repo targets --fleet",
            "the fleet list (an org profile repository's fleet.json)",
        ),
    )
    p.add_argument(
        "--profile",
        help=help_text(
            product,
            "repo targets --profile",
            "the profile for a repository that has not opted in",
        ),
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help=help_text(product, "repo targets --format", None),
    )
    p.set_defaults(func=run)
