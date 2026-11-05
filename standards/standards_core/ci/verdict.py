"""The auto-merge decision for a Dependabot PR, from ``dependabot/fetch-metadata``'s
outputs and the product's decision table (DESIGN §8.3).

The table is data: ``product.ci.decision_table``, a sequence of ``DecisionRule``. Rules
are tried in order, filtered by the PR's ecosystem; every condition a rule sets must hold,
and the first rule that matches gives the verdict. No match is ``review``: nothing merges
without a rule that says it may.

``names`` patterns are case-insensitive globs over the PR's dependency names, with three
tokens: ``$engine`` (the ``<owner>/<repo>`` the checkout locks the engine from),
``$rollout-inputs`` (the inputs that move only through ``repo rollout``) and
``$param:<key>`` (the list ``--param <key>=<JSON list>`` gives, such as a product's docs
action patterns).

``dependabot.auto-merge-policy`` chooses between the table (``decision-table``, the
default) and ``all``: every ``review`` becomes ``auto``, so the required checks alone
decide what merges, except where the matching rule is ``sticky`` (an engine pin bump,
which moves only through ``repo rollout``). ``relock`` and ``close`` stay as they are.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Literal

from standards_core.product import EMPTY

if TYPE_CHECKING:
    from standards_core.product import Product

AUTO = "auto"
REVIEW = "review"
RELOCK = "relock"
CLOSE = "close"
VERDICTS = (AUTO, REVIEW, RELOCK, CLOSE)
UPDATES = ("any", "major", "minor-patch")

DECISION_TABLE = "decision-table"
ALL = "all"
POLICIES = (DECISION_TABLE, ALL)

MAJOR = "version-update:semver-major"
MINOR_PATCH = ("version-update:semver-minor", "version-update:semver-patch")


@dataclass(frozen=True)
class DecisionRule:
    # fetch-metadata package-ecosystem: "github_actions", "nix", "submodules", …
    ecosystem: str
    verdict: Literal["auto", "review", "relock", "close"]
    # match when dependency-group is one of these
    groups: tuple[str, ...] = ()
    # fnmatch, case-insensitive, any dependency name; tokens $engine,
    # $rollout-inputs, $param:<key>
    names: tuple[str, ...] = ()
    # match when the "/"-normalised directory is one of these
    directories: tuple[str, ...] = ()
    # minor-patch includes a grouped PR without a type
    update: Literal["any", "major", "minor-patch"] = "any"
    # match only when there is no group and at least one name
    ungrouped_named: bool = False
    # policy "all" never lifts this verdict to auto
    sticky: bool = False


def names(raw: str) -> list[str]:
    """fetch-metadata's ``dependency-names``: comma-separated."""
    return [n.strip() for n in raw.split(",") if n.strip()]


def _matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(name.lower(), p.lower()) for p in patterns)


def _minor_or_patch(update_type: str, group: str) -> bool:
    """A minor or patch update; a grouped PR without an update type is one too, because
    the groups a product configures take only minor and patch updates."""
    if update_type == MAJOR:
        return False
    return update_type in MINOR_PATCH or (not update_type and bool(group))


def _directory(directory: str) -> str:
    return "/" + directory.strip("/") if directory else "/"


def patterns(
    product: Product,
    rule: DecisionRule,
    params: Mapping[str, Sequence[str]],
    engine_repo: str,
) -> list[str]:
    """``rule.names`` with its tokens expanded."""
    out: list[str] = []
    for pattern in rule.names:
        if pattern == "$engine":
            out += [engine_repo] if engine_repo else []
        elif pattern == "$rollout-inputs":
            out += list(product.rollout_inputs)
        elif pattern.startswith("$param:"):
            out += list(params.get(pattern.removeprefix("$param:"), ()))
        else:
            out.append(pattern)
    return out


def _holds(
    product: Product,
    rule: DecisionRule,
    *,
    update_type: str,
    group: str,
    deps: list[str],
    directory: str,
    params: Mapping[str, Sequence[str]],
    engine_repo: str,
) -> bool:
    if rule.groups and group not in rule.groups:
        return False
    if rule.names:
        found = patterns(product, rule, params, engine_repo)
        if not any(_matches(d, found) for d in deps):
            return False
    if rule.directories and directory not in {_directory(d) for d in rule.directories}:
        return False
    if rule.update == "major" and update_type != MAJOR:
        return False
    if rule.update == "minor-patch" and not _minor_or_patch(update_type, group):
        return False
    return not (rule.ungrouped_named and (group or not deps))


def match(
    product: Product,
    ecosystem: str,
    update_type: str = "",
    group: str = "",
    dependency_names: str = "",
    directory: str = "/",
    params: Mapping[str, Sequence[str]] = EMPTY,
    engine_repo: str = "",
) -> DecisionRule | None:
    """The first rule of the product's table that holds for the PR, or ``None``."""
    deps = names(dependency_names)
    where = _directory(directory)
    for rule in product.ci.decision_table:
        if rule.ecosystem != ecosystem:
            continue
        if _holds(
            product,
            rule,
            update_type=update_type,
            group=group,
            deps=deps,
            directory=where,
            params=params,
            engine_repo=engine_repo,
        ):
            return rule
    return None


def decide(
    product: Product,
    ecosystem: str,
    update_type: str = "",
    group: str = "",
    dependency_names: str = "",
    directory: str = "/",
    params: Mapping[str, Sequence[str]] = EMPTY,
    engine_repo: str = "",
    policy: str = DECISION_TABLE,
) -> str:
    """The verdict for one Dependabot PR: ``auto``, ``review``, ``relock`` or ``close``."""
    rule = match(
        product,
        ecosystem,
        update_type,
        group,
        dependency_names,
        directory,
        params,
        engine_repo,
    )
    decision = rule.verdict if rule is not None else REVIEW
    if policy == ALL and decision == REVIEW and not (rule is not None and rule.sticky):
        return AUTO
    return decision


def nix_verdict(relock_state: str) -> str:
    """A ``relock`` verdict once relock-plan has run: ``auto`` only when there was nothing
    to relock (``empty``); ``close`` when the PR moves a rollout input; otherwise the PR
    waits for relock-push, which enables auto-merge after its push (``patch``), so this
    job does nothing."""
    if relock_state == "empty":
        return AUTO
    if relock_state == "rollout-input-moved":
        return CLOSE
    return "wait"


def problems(product: Product) -> list[str]:
    """What is wrong with the product's decision table (conformance, DESIGN §11.4)."""
    out = []
    for i, rule in enumerate(product.ci.decision_table):
        where = f"ci.decision_table[{i}] ({rule.ecosystem})"
        if not isinstance(rule, DecisionRule):
            out.append(f"{where}: not a DecisionRule")
            continue
        if rule.verdict not in VERDICTS:
            out.append(
                f"{where}: verdict {rule.verdict!r} is not one of {', '.join(VERDICTS)}"
            )
        if rule.update not in UPDATES:
            out.append(
                f"{where}: update {rule.update!r} is not one of {', '.join(UPDATES)}"
            )
        for pattern in rule.names:
            if pattern.startswith("$") and not (
                pattern in ("$engine", "$rollout-inputs")
                or (pattern.startswith("$param:") and len(pattern) > len("$param:"))
            ):
                out.append(f"{where}: unknown token {pattern!r}")
    return out
