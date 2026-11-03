"""The template context (DESIGN §3.5) and the checkout it describes.

Every template, every manifest ``when`` and every entry ``path`` sees the same names:
the core keys below, plus the product's (``Product.hooks.template_globals``):

``name``, ``name_hyphen``, ``repo``, ``repo_host``, ``cfg``, ``modules``, ``org``,
``profile``, ``profile_input``, ``branches``, ``gates``, ``discover``, ``engine`` (also
under the product's ``report_key``), ``floors``, ``options``, ``first_commit_year`` and
``unmanaged_dirs``; ``engine.build`` adds ``previous`` and ``history``.

Nothing here reads the clock: the only date is the year of the first commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from standards_core.common import pyproject, repo
from standards_core.common.config import ORG_PROFILE_PREFIXES, Resolved
from standards_core.common.report import EnvError

if TYPE_CHECKING:
    from standards_core.product import Product

# The keys ``build`` makes; a product's ``template_globals`` may not shadow them.
CORE_KEYS = (
    "name",
    "name_hyphen",
    "repo",
    "repo_host",
    "cfg",
    "modules",
    "org",
    "profile",
    "profile_input",
    "branches",
    "gates",
    "discover",
    "engine",
    "floors",
    "options",
    "first_commit_year",
    "unmanaged_dirs",
    "previous",
    "history",
)


class NS(dict):
    """A dict whose keys also read as attributes, for ``when`` expressions and templates."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def ns(value: Any) -> Any:
    """``value`` with every dict turned into an ``NS``, recursively."""
    if isinstance(value, dict):
        return NS({k: ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [ns(v) for v in value]
    return value


@dataclass
class Checkout:
    """A checkout: its root, its name, its parsed config file and its tracked paths.

    ``extra`` holds what the product reads once per checkout (its own manifests, …).
    """

    root: Path
    name: str
    config: dict
    tracked: list[str]
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def hyphen(self) -> str:
        return self.name.replace("_", "-")


def _repo_name(value: object) -> str | None:
    """The last part of an ``<owner>/<repo>`` (or ``<host>/<group>/…/<repo>``) value."""
    if not isinstance(value, str) or "/" not in value:
        return None
    name = value.rstrip("/").rsplit("/", 1)[-1]
    return name or None


def default_load_checkout(product: Product, root: Path) -> Checkout:
    """The checkout at ``root`` (DESIGN §3.3); not a git work tree is an ``EnvError``.

    Its name is, in order: ``[project].name``; the name part of ``[tool.<table>] repo``;
    the name part of the origin remote; and only for a checkout with no origin remote
    (fixtures, throwaways), the directory's name. A product's ``load_checkout`` wraps
    this (``lambda root: default_load_checkout(PRODUCT, root)``).
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise EnvError(f"{root} is not a directory")
    tracked = repo.ls_files(root)
    doc = pyproject.load(product, root)
    table = pyproject.tool_table(product, doc) or {}
    origin = repo.origin_repo(root)
    name = (
        pyproject.project_name(doc)
        or _repo_name(table.get("repo"))
        or (_repo_name(origin[1]) if origin else None)
        or root.name
    )
    return Checkout(root=root, name=name, config=doc, tracked=tracked)


def engine_major(version: str) -> int:
    """The engine major a checkout's flake tracks (``release-<major>``): 1 before 1.0."""
    head = version.split(".", 1)[0]
    return max(1, int(head)) if head.isdigit() else 1


def default_engine_url(product: Product) -> str:
    """The flake URL of the engine's release branch for this product version."""
    engine = product.engine
    branch = engine.release_branch.format(major=engine_major(product.identity.version))
    return f"github:{engine.repo}/{branch}"


def engine_url(product: Product, cfg: Mapping[str, Any]) -> str:
    """The URL ``flake.nix`` gives the engine input: ``engine.url_key``'s value, else the
    release branch."""
    value: Any = cfg
    for part in product.engine.url_key.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value if isinstance(value, str) and value else default_engine_url(product)


class ShallowHistory:
    """``first_commit_year`` in a shallow clone, whose oldest commit is the shallow
    boundary rather than the first one: any use fails (exit 3) instead of rendering a
    wrong year that a full clone would then report as drift. Nothing reads it until a
    template does."""

    MESSAGE = (
        "first_commit_year needs the full git history, and this clone is shallow:"
        " fetch it with `git fetch --unshallow` (actions/checkout: fetch-depth: 0)"
    )

    def _fail(self, *_args: object) -> Any:
        raise EnvError(self.MESSAGE)

    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _fail
    __hash__ = object.__hash__

    __str__ = __int__ = __index__ = __bool__ = __format__ = _fail

    def __repr__(self) -> str:
        return "ShallowHistory()"


class History(list):
    """The configurations the checkout has committed since it opted in, newest first
    (``engine.history``): each table resolved against the org profile committed beside
    it. ``truncated`` when the walk ended at a shallow clone's boundary rather than at
    the commit before the opt-in: older tables are unknown there. ``unknown`` says why a
    committed state could not be read (its locked profile could not be fetched): what it
    held is unknown too."""

    truncated = False
    unknown = ""

    MESSAGE = (
        "retracting what a module that is off now left behind needs the history of the"
        " config file since the checkout opted in, and this clone is shallow: fetch it"
        " with `git fetch --unshallow` (actions/checkout: fetch-depth: 0)"
    )


def ever(history: Any, on: Any) -> bool:
    """Whether ``on(table)`` holds for a table of ``history`` (a ``History``).

    Found in the commits a shallow clone has, it holds in a full clone too. Not found
    there, the answer is in commits the clone lacks, so it fails (exit 3) rather than
    give a verdict a full clone of the same commit contradicts; the same for a state
    whose locked profile could not be fetched."""
    if any(on(h) for h in history or []):
        return True
    if getattr(history, "truncated", False):
        raise EnvError(History.MESSAGE)
    if getattr(history, "unknown", ""):
        raise EnvError(history.unknown)
    return False


def first_commit_year(root: Path) -> int | ShallowHistory | None:
    """The year of the first commit, which never changes; ``None`` before one exists."""
    try:
        if repo.git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
            return ShallowHistory()
        out = repo.git(root, "log", "--reverse", "--format=%cs")
    except EnvError:
        return None
    first = out.split("\n", 1)[0].strip()
    return int(first[:4]) if first[:4].isdigit() else None


def build(
    product: Product,
    checkout: Checkout,
    resolved: Resolved,
    *,
    lock: Mapping[str, Any] | None = None,
    floors: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
) -> NS:
    """The context for ``checkout`` with its resolved configuration.

    ``lock`` is the engine's ``locked`` node in ``flake.lock`` (``rev``, ``owner``,
    ``repo``), ``floors`` the manifest's, ``options`` the sync flags a ``when`` may read
    (``sync.options``). ``facts`` are the checkout's discovery facts when the caller
    has them already (``discover.facts`` otherwise).
    """
    from standards_core.common import repoconfig
    from standards_core.scaffold import discover

    cfg = resolved.cfg
    modules = resolved.modules
    if facts is None:
        facts = discover.facts(product, checkout, cfg, modules)
    facts = dict(facts)
    identity = product.identity
    lock = lock or {}
    owner, _, name = product.engine.repo.partition("/")
    source = cfg.get("profile", "minimal")
    release = repoconfig.release_branch(product, cfg, modules, facts) or None
    opts = dict(options or {})
    base = ns(
        {
            "name": checkout.name,
            "name_hyphen": checkout.hyphen,
            "repo": cfg.get("repo") or "",
            "repo_host": resolved.repo_host or "",
            "cfg": cfg,
            "modules": dict(modules),
            "org": dict(cfg.get("org", {})),
            "profile": dict(resolved.profile),
            # The org profile's flake URL when it is one: the standards-profile input.
            "profile_input": source if source.startswith(ORG_PROFILE_PREFIXES) else "",
            "branches": {"integration": cfg["integration-branch"], "release": release},
            "gates": list(product.hooks.gates(modules, cfg, facts)),
            "discover": facts,
            "engine": {
                "rev": lock.get("rev") or "",
                "version": identity.version,
                "major": engine_major(identity.version),
                "owner": lock.get("owner") or owner,
                "name": lock.get("repo") or name,
                "url": engine_url(product, cfg),
            },
            "floors": dict(floors),
            "options": {
                "force": bool(opts.pop("force", False)),
                # A fixture checkout's sync (profile validate, conformance): products
                # may accept there what only a real lock run would settle.
                "throwaway": bool(opts.pop("throwaway", False)),
                **opts,
            },
            "first_commit_year": first_commit_year(checkout.root),
            "unmanaged_dirs": list(product.paths.not_source),
        }
    )
    base[identity.report_key] = base["engine"]
    extra = product.hooks.template_globals(checkout, resolved, base)
    shadowed = sorted(set(extra) & (set(base) | set(CORE_KEYS)))
    if shadowed:
        raise RuntimeError(
            f"template_globals shadows the core context key(s) {', '.join(shadowed)}"
        )
    base.update(ns(dict(extra)))
    return base
