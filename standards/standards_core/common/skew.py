"""Version skew: the caller workflows, the lock and the running tools name one engine
(DESIGN §9.4).

Three rows, each exit 3 (environment) and each skipped under ``<PREFIX>_ALLOW_SKEW=1``
(an engine's self-tests, ``audit --against latest``, drift):

1. ``--expect-rev``: the engine revision the product tools were installed from must be
   the one ``flake.lock`` pins;
2. the callers: every ``uses:`` of one of the engine's reusable workflows
   (``<owner>/<name>/.github/workflows/<engine.caller_stem>.yml@<sha> # v<version>``) in
   the tracked workflows must name the locked revision and the running version. The
   owner and name are the locked engine node's (the engine repository, or a fork);
3. the core: the python-devops-nix commit this copy of ``standards-core`` was built
   from (``standards_core.revision()``) must be the one the locked engine locks as its
   ``engine.base_input``. A path or editable install has no revision, so the row is
   skipped there.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import standards_core
from standards_core.common import flakelock
from standards_core.common.report import ENVIRONMENT, EnvError
from standards_core.scaffold import defaults, engine, globs
from standards_core.scaffold.engine import Item

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.engine import Plan


def caller_pattern(product: Product, owner: str, name: str) -> re.Pattern[str]:
    """A ``uses:`` of one of ``owner/name``'s reusable workflows, with its revision and
    optional version comment."""
    return re.compile(
        re.escape(f"{owner}/{name}")
        + r"/\.github/workflows/"
        + defaults.stem_regex(product.engine.caller_stem)
        + r"\.ya?ml@(?P<sha>[0-9a-f]{40})(?:[ \t]+#[ \t]*v?(?P<ver>\S+))?",
        re.IGNORECASE,
    )


def _skew(path: str, problem: str) -> Item:
    return Item(path, "skew", None, None, problem, ENVIRONMENT)


def expect_rev_problems(
    product: Product, rev: str, expect_rev: str | None
) -> list[Item]:
    """Row 1: ``--expect-rev`` against the locked engine revision."""
    if not expect_rev or not rev or expect_rev == rev:
        return []
    return [
        _skew(
            "flake.lock",
            f"version skew: this {product.identity.distribution} was installed from"
            f" {expect_rev}, flake.lock pins {product.engine.flake_input} {rev}",
        )
    ]


def caller_problems(product: Product, plan: Plan) -> list[Item]:
    """Row 2: one item per tracked workflow whose engine caller names another revision
    or version."""
    found = plan.ctx.engine
    rev = found.rev
    version = product.identity.version
    pattern = caller_pattern(product, found.owner, found.name)
    out = []
    for path in plan.checkout.tracked:
        if not globs.match(".github/workflows/*.y*ml", path):
            continue
        for m in pattern.finditer(engine.read(product, plan.root, path) or ""):
            if m["sha"] != rev or (m["ver"] or "") != version:
                out.append(
                    _skew(
                        path,
                        f"version skew: calls {product.engine.flake_input}"
                        f" @{m['sha'][:12]} # v{m['ver']}, but flake.lock pins"
                        f" {rev[:12] or '(nothing)'} and this"
                        f" {product.identity.distribution} is v{version}",
                    )
                )
                break
    return out


def core_problems(product: Product, plan: Plan) -> list[Item]:
    """Row 3: the core's own revision against the engine's ``base_input`` in the lock."""
    core_rev = standards_core.revision()
    if core_rev is None:
        return []
    path = plan.root / "flake.lock"
    if not path.is_file():
        return []
    try:
        locked = flakelock.base_rev(product, flakelock.load(path))
    except EnvError:
        return []  # lock_problems reports an unreadable lock
    if locked is None or locked == core_rev:
        return []
    base = product.engine.base_input
    return [
        _skew(
            "flake.lock",
            f"version skew: standards-core {core_rev[:12]} is not the {base}"
            f" {locked[:12]} that {product.engine.flake_input} locks",
        )
    ]


def problems(product: Product, plan: Plan, expect_rev: str | None) -> list[Item]:
    """Every skew row for ``plan`` (``[]`` under ``<PREFIX>_ALLOW_SKEW=1``)."""
    if engine.allow_skew(product):
        return []
    return [
        *expect_rev_problems(product, plan.ctx.engine.rev, expect_rev),
        *caller_problems(product, plan),
        *core_problems(product, plan),
    ]
