"""The facts sync reads from the tracked tree instead of asking for them (DESIGN §4.7).

The core contributes only two, so it never guesses a checkout's shape:

- ``gitmodules``: ``.gitmodules`` is tracked;
- ``github_workflows``: any tracked ``.github/workflows/*``.

Everything else (projects or bundles, series, frontends, …) is the product's
``hooks.discover``. Every fact is computed from ``git ls-files`` and tracked file
contents, so the answer is the same on every machine, and ``--check`` recomputes them:
a newly tracked file can be drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import Checkout


def gitmodules(tracked: list[str]) -> bool:
    """Whether ``.gitmodules`` is tracked."""
    return ".gitmodules" in tracked


def github_workflows(tracked: list[str]) -> bool:
    """Whether any ``.github/workflows/*`` file is tracked."""
    return any(p.startswith(".github/workflows/") for p in tracked)


def core_facts(checkout: Checkout) -> dict[str, Any]:
    """The facts the core computes for every checkout."""
    tracked = checkout.tracked
    return {
        "gitmodules": gitmodules(tracked),
        "github_workflows": github_workflows(tracked),
    }


def facts(
    product: Product,
    checkout: Checkout,
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
) -> dict[str, Any]:
    """The core facts plus ``hooks.discover``'s; a product fact may not shadow a core one."""
    core = core_facts(checkout)
    extra = product.hooks.discover(checkout, cfg, modules)
    shadowed = sorted(set(core) & set(extra))
    if shadowed:
        raise RuntimeError(
            f"hooks.discover shadows the core fact(s) {', '.join(shadowed)}"
        )
    return {**core, **extra}
