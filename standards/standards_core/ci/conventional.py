"""Conventional-commit parsing, bump levels and the core's ``semver`` scheme (DESIGN §8.2).

``parse`` reads a commit message's subject (``type(scope)!: subject``) and its footers;
``level`` is the highest bump a set of commits asks for: ``major`` for a breaking change
(``type!:`` or a ``BREAKING CHANGE:``/``BREAKING-CHANGE:`` footer), else the level its
type maps to (``feat`` minor, ``fix`` and ``perf`` patch), else none.

``SEMVER`` is the core's version scheme (``releases.version-scheme = "semver"``): plain
``<major>.<minor>.<patch>``, any message allowed, any tag allowed. ``scheme(product,
name)`` finds it or one of the product's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from standards_core.common.report import ConfigError

if TYPE_CHECKING:
    from standards_core.product import Facts, Level, Product, VersionScheme

DEFAULT_TYPES: Mapping[str, Level] = {"feat": "minor", "fix": "patch", "perf": "patch"}
_RANK = {"patch": 1, "minor": 2, "major": 3}

_SUBJECT = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:[ \t]*(?P<subject>.*)$"
)
_BREAKING = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


@dataclass(frozen=True)
class Commit:
    type: str
    scope: str | None
    breaking: bool
    subject: str


def parse(message: str) -> Commit | None:
    """The conventional commit ``message`` is, or ``None`` (not conventional: no level).

    Lines starting with ``#`` (a commit-msg file's comments) are ignored.
    """
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = next((line for line in lines if line.strip()), "").strip()
    m = _SUBJECT.match(subject)
    if not m:
        return None
    body = "\n".join(lines)
    return Commit(
        type=m["type"].lower(),
        scope=m["scope"],
        breaking=bool(m["bang"]) or bool(_BREAKING.search(body)),
        subject=m["subject"],
    )


def commit_level(
    commit: Commit | None, types: Mapping[str, Level] = DEFAULT_TYPES
) -> Level | None:
    """The bump one commit asks for."""
    if commit is None:
        return None
    if commit.breaking:
        return "major"
    return types.get(commit.type)


def level(
    commits: Iterable[Commit | None], types: Mapping[str, Level] = DEFAULT_TYPES
) -> Level | None:
    """The highest bump ``commits`` ask for, or ``None`` when none asks for one."""
    best: Level | None = None
    for commit in commits:
        found = commit_level(commit, types)
        if found is not None and (best is None or _RANK[found] > _RANK[best]):
            best = found
    return best


_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class Semver:
    """``semver``: ``<major>.<minor>.<patch>``; a major resets minor and patch."""

    name = "semver"

    def valid(self, version: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        if not _SEMVER.fullmatch(version):
            return f"{version} is not <major>.<minor>.<patch>"
        return None

    def bump(
        self, version: str, level: Level, cfg: Mapping[str, Any], facts: Facts
    ) -> str:
        m = _SEMVER.fullmatch(version)
        if not m:
            raise ConfigError(f"{version} is not <major>.<minor>.<patch>")
        major, minor, patch = (int(g) for g in m.groups())
        if level == "major":
            return f"{major + 1}.0.0"
        if level == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"

    def message_problems(
        self, text: str, cfg: Mapping[str, Any], facts: Facts
    ) -> list[str]:
        return []

    def tag_problem(self, tag: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        return None


SEMVER = Semver()


def schemes(product: Product) -> dict[str, VersionScheme]:
    """Every version scheme ``product`` has: ``semver`` and its own."""
    return {"semver": SEMVER, **product.version_schemes}


def scheme(product: Product, name: str) -> VersionScheme:
    """The version scheme ``name``; an unknown name is a ``ConfigError``."""
    found = schemes(product).get(name)
    if found is None:
        raise ConfigError(
            f"version scheme {name!r} is not one of {', '.join(schemes(product))}"
        )
    return found
