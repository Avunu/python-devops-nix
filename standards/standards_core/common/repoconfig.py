"""A checkout's resolved configuration, facts and gates (DESIGN §2.2).

The CI steps and the ``repo`` group need the same answers sync renders the callers from:
the resolved configuration (§4.4), the discovery facts (§4.7) and the list of enabled
gates (``hooks.gates``, in ``ci.gates`` order). They are computed exactly as sync
computes them, so a ruleset's required contexts and a rendered workflow can never
disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.common import config, pyproject
from standards_core.common.config import Resolved

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import Checkout


def release_branch(
    product: Product,
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
    facts: Mapping[str, Any],
) -> str:
    """The release branch, or "" when there is none.

    ``hooks.release_branch`` when the product has one; otherwise, with ``releases`` on,
    the ``releases.branching``'s rule over ``cfg`` and the checkout's facts.
    """
    if product.hooks.release_branch is not None:
        return product.hooks.release_branch(cfg, modules, facts)
    if not modules.get("releases"):
        return ""
    name = cfg.get("releases", {}).get("branching", "")
    branching = product.branching.get(name)
    return branching.release_branch(cfg, facts) if branching is not None else ""


@dataclass
class RepoConfig:
    root: Path
    name: str
    resolved: Resolved
    facts: dict
    gates: list[str]
    product: Product = field(repr=False, compare=False)

    @property
    def cfg(self) -> dict:
        return self.resolved.cfg

    @property
    def modules(self) -> dict[str, bool]:
        return self.resolved.modules

    @property
    def integration(self) -> str:
        return self.cfg["integration-branch"]

    @property
    def release_branch(self) -> str:
        return release_branch(self.product, self.cfg, self.modules, self.facts)


def facts_and_gates(
    product: Product, checkout: Checkout, resolved: Resolved
) -> tuple[dict, list[str]]:
    """The checkout's discovery facts and its enabled gates, as sync's context has them."""
    from standards_core.scaffold import discover

    facts = discover.facts(product, checkout, resolved.cfg, resolved.modules)
    gates = list(product.hooks.gates(resolved.modules, resolved.cfg, facts))
    return facts, gates


def load(
    product: Product,
    root: Path,
    *,
    lock_path: Path | None = None,
    profile_dir: Path | None = None,
) -> RepoConfig:
    """The checkout at ``root``: raises ``config.NotOptedIn`` (exit 2) without the table."""
    checkout = product.hooks.load_checkout(Path(root).resolve())
    resolved = config.resolve(
        product,
        pyproject.config_path(product, checkout.root),
        lock_path=lock_path,
        profile_dir=profile_dir,
        checkout=checkout,
    )
    facts, gates = facts_and_gates(product, checkout, resolved)
    return RepoConfig(checkout.root, checkout.name, resolved, facts, gates, product)
