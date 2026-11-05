"""The changed-defaults warning for a checkout on the floating ``recommended``.

Plain ``recommended`` means the newest ``recommended@<minor>`` snapshot of the running
product. When an engine bump moves it to a newer snapshot, sync (and so ``repo
rollout``) prints each value that changed and suggests pinning the old snapshot, so the
bump PR says in its own log which defaults moved. Exit codes don't change.

The snapshot the checkout resolved to at ``HEAD`` is the newest one at or below the
product version ``HEAD`` ran: the ``# vX.Y.Z`` comment of ``HEAD``'s caller workflows
(``<owner>/<repo>/.github/workflows/<engine.caller_stem>.yml@<sha> # v<version>``).
Without one (no CI yet) there is nothing to compare, and nothing is printed.
"""

from __future__ import annotations

import re
import sys
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from standards_core.common import config, repo
from standards_core.common.report import EnvError

if TYPE_CHECKING:
    from standards_core.product import Product


def stem_regex(stem: str) -> str:
    """A caller-workflow file stem glob (``kit-*``) as a regular expression."""
    return "".join("[A-Za-z0-9_-]+" if c == "*" else re.escape(c) for c in stem)


@cache
def caller(product: Product) -> re.Pattern[str]:
    """A ``uses:`` of one of the engine's reusable workflows, with its version comment."""
    return re.compile(
        r"/\.github/workflows/"
        + stem_regex(product.engine.caller_stem)
        + r"\.ya?ml@[0-9a-f]{40}[ \t]+#[ \t]*v(?P<ver>\S+)"
    )


def head_version(product: Product, root: Path) -> Version | None:
    """The product version ``HEAD``'s caller workflows name, when they name one."""
    try:
        names = repo.git(
            root, "ls-tree", "--name-only", "HEAD", ".github/workflows/"
        ).split()
    except EnvError:
        return None
    pattern = caller(product)
    for name in sorted(names):
        try:
            text = repo.git(root, "show", f"HEAD:./{name}")
        except EnvError:
            continue
        m = pattern.search(text)
        if m:
            try:
                return Version(m["ver"])
            except InvalidVersion:
                continue
    return None


def _snapshot_version(name: str) -> Version:
    return Version(name.removeprefix("recommended@"))


def snapshot_at(product: Product, version: Version) -> str | None:
    """The ``recommended@<minor>`` that plain ``recommended`` meant in ``version``."""
    names = [n for n in config.builtin_names(product) if n.startswith("recommended@")]
    cap = Version(f"{version.major}.{version.minor}")
    older = [n for n in names if _snapshot_version(n) <= cap]
    return max(older, key=_snapshot_version) if older else None


def _flat(doc: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(doc, dict):
        out: dict[str, Any] = {}
        for key, value in doc.items():
            if prefix == "" and key in ("name", "description", "schema"):
                continue
            out.update(_flat(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: doc}


def changes(product: Product, old: str, new: str) -> list[str]:
    """Each value that differs between two built-in snapshots, as ``key: old → new``."""
    a = _flat(config.builtin_profile(product, old)[1])
    b = _flat(config.builtin_profile(product, new)[1])
    return [
        f"{k}: {a.get(k)!r} → {b.get(k)!r}"
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k)
    ]


def warn_changed(product: Product, root: Path, plan: Any) -> list[str]:
    """Print (and return) the warning when ``recommended`` moved since ``HEAD``."""
    if plan.cfg.get("profile") != "recommended":
        return []
    at_head = head_version(product, root)
    if at_head is None:
        return []
    old = snapshot_at(product, at_head)
    new = config.builtin_name(product, "recommended")
    if old is None or old == new:
        return []
    identity = product.identity
    lines = [
        (
            f'profile = "recommended" now resolves to {new} (it was {old} at HEAD);'
            " changed defaults:"
        ),
        *(f"  {c}" for c in changes(product, old, new)),
        f'pin profile = "{old}" in [tool.{identity.table}] to keep the old defaults',
    ]
    for line in lines:
        print(f"{identity.name} sync: warning: {line}", file=sys.stderr)
    return lines
