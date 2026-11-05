"""Repo policy: the settings and rulesets ``<cli> repo apply`` wants for a checkout
(DESIGN §7.3).

The defaults are ``data/repo-policy/``: ``repo-settings.json`` and
``rulesets/{integration,integration-provisional,release-branch,tags}.json``, JSON with
``{placeholders}`` filled from the checkout's resolved configuration. Each is read from
the product's data root first, then the core's, so a product replaces any of them; an
org profile replaces any with ``templates/repo-policy/<same path>``. ``--policy-dir`` (an
org profile repository's own policy) and ``--self`` (the product's
``repo-policy/self/``) read another directory, whose rulesets are applied as they are.

A string value that is exactly ``"{name}"`` becomes the typed value (a list, a number, a
boolean); a placeholder inside a longer string is substituted as text. ``{<method> in
merge_methods}`` is whether ``repo-policy.merge-methods`` lists that method. A
placeholder nothing fills is exit 2.

The labels are the settings file's ``labels`` followed by the ``ci.labels`` it does not
name; each is applied while its module (``ci.labels``) is on, and a label with no module
always.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.common.data import product_roots
from standards_core.common.github import ACTIONS_APP_ID
from standards_core.common.report import ConfigError

if TYPE_CHECKING:
    from standards_core.product import Branching, Product

SETTINGS = "repo-settings.json"
INTEGRATION = "integration.json"
PROVISIONAL = "integration-provisional.json"
RELEASE_BRANCH = "release-branch.json"
TAGS = "tags.json"
_WHOLE = re.compile(r"^\{([a-z_]+(?: in [a-z_]+)?)\}$")
_PART = re.compile(r"\{([a-z_]+(?: in [a-z_]+)?)\}")


@dataclass
class Policy:
    """What a repository should look like: the settings parts and the rulesets, by name."""

    repository: dict = field(default_factory=dict)
    workflow_permissions: dict | None = None
    vulnerability_alerts: bool | None = None
    automated_security_fixes: bool | None = None
    labels: list[dict] = field(default_factory=list)
    rulesets: list[dict] = field(default_factory=list)


def self_dir(product: Product) -> Path:
    """The engine repository's own policy: ``repo-policy/self`` in the product's data
    root (the core ships none)."""
    path = Path(product.data_root) / "repo-policy" / "self"
    if not path.is_dir():
        name = product.identity.name
        raise ConfigError(
            f"this {name} has no repo-policy/self/ (the {name} repository's own policy)"
        )
    return path


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"{path} does not exist") from e
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e


def read_file(
    product: Product,
    rel: str,
    *,
    directory: Path | None = None,
    overrides: Path | None = None,
) -> Any:
    """``rel`` from ``overrides/repo-policy/`` when the org profile has it, else
    ``directory``, else ``repo-policy/<rel>`` from the data roots (product first)."""
    if overrides is not None:
        over = overrides / "repo-policy" / rel
        if over.is_symlink():
            raise ConfigError(f"the profile's templates/repo-policy/{rel} is a symlink")
        if over.is_file():
            return _read(over)
    if directory is not None:
        return _read(directory / rel)
    return _read(product_roots(product).path(f"repo-policy/{rel}"))


def ruleset_files(directory: Path) -> list[Path]:
    """A policy directory's rulesets: ``rulesets/*.json``, else every ``*.json`` beside
    ``repo-settings.json`` (an org profile repository's ``repo-policy/main.json``, …)."""
    sub = directory / "rulesets"
    if sub.is_dir():
        return sorted(sub.glob("*.json"))
    return sorted(p for p in directory.glob("*.json") if p.name != SETTINGS)


def fill(value: Any, values: Mapping[str, Any], where: str) -> Any:
    """``value`` with every placeholder replaced (module docstring)."""
    if isinstance(value, dict):
        return {k: fill(v, values, where) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, values, where) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(name: str) -> Any:
        method, _, listed = name.partition(" in ")
        if listed:
            if listed not in values:
                raise ConfigError(f"{where}: no value for {{{name}}}")
            return method in values[listed]
        if name not in values:
            raise ConfigError(f"{where}: no value for {{{name}}}")
        return values[name]

    whole = _WHOLE.match(value)
    if whole:
        return copy.deepcopy(lookup(whole[1]))
    return _PART.sub(lambda m: str(lookup(m[1])), value)


def required_checks(
    product: Product, cfg: Mapping[str, Any], gates: Sequence[str]
) -> list[dict]:
    """``{required_checks}``: one context per required gate, in ``ci.gates`` order."""
    want = cfg.get("repo-policy", {}).get("required-checks", "gates")
    if want == "gates":
        names = list(gates)
    else:
        extra = [g for g in want if g not in gates]
        if extra:
            raise ConfigError(
                f"repo-policy.required-checks names {extra}, which are not enabled"
                f" gates ({', '.join(gates) or 'none'})"
            )
        names = list(want)
    return [
        {
            "context": product.ci.check_context.format(gate=g),
            "integration_id": ACTIONS_APP_ID,
        }
        for g in product.ci.gates
        if g in names
    ]


def bypass_actors(release_bypass: str) -> list[dict]:
    """``repo-policy.release-bypass`` as ``bypass_actors``: GitHub Actions, a user, or a
    GitHub App (an integration) by id."""
    if release_bypass == "github-actions":
        return [
            {
                "actor_type": "Integration",
                "actor_id": ACTIONS_APP_ID,
                "bypass_mode": "always",
            }
        ]
    m = re.fullmatch(r"(user|integration):(\d+)", release_bypass or "")
    if m:
        kind = "User" if m[1] == "user" else "Integration"
        return [{"actor_type": kind, "actor_id": int(m[2]), "bypass_mode": "always"}]
    raise ConfigError(
        'repo-policy.release-bypass is "github-actions", "user:<id>" or'
        f' "integration:<id>", not {release_bypass!r}'
    )


def branching(product: Product, cfg: Mapping[str, Any]) -> Branching | None:
    """The ``Branching`` of ``releases.branching``, or ``None`` (a product with none)."""
    return product.branching.get(cfg.get("releases", {}).get("branching", ""))


def values(
    product: Product,
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
    gates: Sequence[str],
    documentation: str = "",
    release_branch: str = "",
) -> dict[str, Any]:
    """The placeholder values of a checkout's resolved configuration (DESIGN §7.3).

    ``integration_bypass`` lets the release-bypass actor past the integration ruleset
    only where ``ci bump --push`` writes to that branch: ``releases`` on, the flow
    ``post-merge-bump``, and a release branch to bump for (§15.CR3). Anything else
    renders ``[]``.
    """
    rp = cfg.get("repo-policy", {})
    rel = cfg.get("releases", {})
    prefix = rel.get("tag-prefix", "v")
    flow = branching(product, cfg)
    release_bypass = bypass_actors(rp.get("release-bypass", "github-actions"))
    bump_push = (
        bool(modules.get("releases"))
        and flow is not None
        and flow.flow == "post-merge-bump"
        and bool(release_branch)
    )
    out: dict[str, Any] = {
        "integration_branch": cfg["integration-branch"],
        "merge_methods": list(rp.get("merge-methods", ["squash"])),
        "required_approvals": rp.get("required-approvals", 1),
        "require_code_owner_review": rp.get("require-code-owner-review", False),
        "require_thread_resolution": rp.get("require-thread-resolution", True),
        "delete_branch_on_merge": rp.get("delete-branch-on-merge", True),
        "required_checks": required_checks(product, cfg, gates),
        "release_bypass": release_bypass,
        "tag_prefix": prefix,
        "tag_pattern": f"refs/tags/{prefix}*" if prefix else "refs/tags/[0-9]*",
        "documentation": documentation,
        "release_pattern": flow.release_pattern if flow is not None else "",
        "release_label": flow.release_label if flow is not None else "",
        "integration_bypass": copy.deepcopy(release_bypass) if bump_push else [],
        "strict_checks": False,
    }
    out.update(product.hooks.repo_policy_values(cfg, modules, list(gates)))
    return out


def labels(
    product: Product, listed: Sequence[dict], modules: Mapping[str, bool]
) -> list[dict]:
    """The labels to apply: ``listed`` (the settings file's), then the ``ci.labels`` it
    does not name, each kept while its module is on (no module: always)."""
    from standards_core.ci import labels as ci_labels

    known = ci_labels(product)
    named = {str(label.get("name", "")).lower() for label in listed}
    out = [dict(label) for label in listed]
    out += [
        {"name": name, "color": color, "description": description}
        for name, (color, description, _module) in known.items()
        if name.lower() not in named
    ]

    def on(label: dict) -> bool:
        module = known.get(label.get("name", ""), ("", "", ""))[2]
        return not module or bool(modules.get(module))

    return [label for label in out if on(label)]


def _settings(
    product: Product,
    doc: dict,
    vals: Mapping[str, Any],
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
    where: str,
) -> Policy:
    rp = cfg.get("repo-policy", {})
    repository = dict(doc.get("repository", {}))
    if not rp.get("manage-default-branch", True):
        repository.pop("default_branch", None)
    if not rp.get("manage-features", True):
        repository.pop("has_wiki", None)
        repository.pop("has_projects", None)
    if not vals.get("documentation"):
        repository.pop("homepage", None)
    if not rp.get("secret-scanning", True):
        repository.pop("security_and_analysis", None)
    return Policy(
        repository=fill(repository, vals, where),
        workflow_permissions=fill(doc.get("actions_workflow_permissions"), vals, where),
        vulnerability_alerts=doc.get("vulnerability_alerts"),
        automated_security_fixes=doc.get("automated_security_fixes"),
        labels=fill(labels(product, doc.get("labels", []), modules), vals, where),
    )


def _strip_status_checks(ruleset: dict) -> dict:
    """The ruleset without a ``required_status_checks`` rule that lists nothing (no
    gates)."""
    rules = [
        r
        for r in ruleset.get("rules", [])
        if not (
            r.get("type") == "required_status_checks"
            and not r["parameters"]["required_status_checks"]
        )
    ]
    return {**ruleset, "rules": rules}


def ruleset_names(
    product: Product,
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
    phase: str,
    release_branch: str,
) -> list[str]:
    """The ruleset files (under ``rulesets/``) for ``phase``: ``hooks.rulesets``, else
    the integration ruleset (provisional or full); under ``full`` the release-branch
    ruleset when there is a release branch and a ``release_pattern``, and the tags
    ruleset when ``releases`` is on and its branching tags."""
    if product.hooks.rulesets is not None:
        return list(product.hooks.rulesets(cfg, modules, phase, release_branch))
    names = [PROVISIONAL if phase == "provisional" else INTEGRATION]
    if phase == "full":
        flow = branching(product, cfg)
        if release_branch and flow is not None and flow.release_pattern:
            names.append(RELEASE_BRANCH)
        if modules.get("releases") and flow is not None and flow.tags:
            names.append(TAGS)
    return names


def for_target(
    product: Product,
    cfg: Mapping[str, Any],
    modules: Mapping[str, bool],
    gates: Sequence[str],
    *,
    phase: str,
    release_branch: str,
    documentation: str = "",
    overrides: Path | None = None,
) -> Policy:
    """The built-in (or org-overridden) policy for a checkout (DESIGN §7.3)."""
    vals = values(product, cfg, modules, gates, documentation, release_branch)
    policy = _settings(
        product,
        read_file(product, SETTINGS, overrides=overrides),
        vals,
        cfg,
        modules,
        SETTINGS,
    )
    for name in ruleset_names(product, cfg, modules, phase, release_branch):
        rel = f"rulesets/{name}"
        doc = read_file(product, rel, overrides=overrides)
        policy.rulesets.append(_strip_status_checks(fill(doc, vals, rel)))
    return policy


def from_dir(directory: Path, vals: Mapping[str, Any] | None = None) -> Policy:
    """A whole policy directory (``--policy-dir``, ``--self``), every ruleset in it
    applied."""
    vals = vals or {}
    settings = _read(directory / SETTINGS) if (directory / SETTINGS).is_file() else {}
    policy = Policy(
        repository=fill(settings.get("repository", {}), vals, SETTINGS),
        workflow_permissions=fill(
            settings.get("actions_workflow_permissions"), vals, SETTINGS
        ),
        vulnerability_alerts=settings.get("vulnerability_alerts"),
        automated_security_fixes=settings.get("automated_security_fixes"),
        labels=fill(settings.get("labels", []), vals, SETTINGS),
    )
    for path in ruleset_files(directory):
        policy.rulesets.append(_strip_status_checks(fill(_read(path), vals, path.name)))
    return policy
