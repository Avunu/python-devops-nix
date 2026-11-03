"""The steps a product's workflows run as ``<cli> ci <step>`` (DESIGN §8).

The workflows set up the job and call these; the decisions (what auto-merges, whether a
patch may be pushed, whether the release branch may move, which units a merge bumps)
live here, where they are unit tested, rather than in YAML. Every GitHub call goes
through ``common.github``.

- ``verdict``: the auto-merge decision, from fetch-metadata's outputs and the product's
  decision table, or the ``all`` policy;
- ``automerge``: applies a verdict under the auto-merge safety rule;
- ``relock``: the relock-plan input check, the relock patch and the workflow-path refusal;
- ``release``: ``dispatch-ci``, the sweep and the release-branch fast-forward with its
  guard (tag or required checks);
- ``conventional`` and ``bump``: conventional-commit levels and the post-merge bump;
- ``branches``: ``update-branches``, which keeps bot PRs up to date with the integration
  branch;
- ``drift``: the nightly reminders that the engine or the org profile moved.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from standards_core.product import Product

# The labels the core's steps apply: name → (color, description, module); "" = always.
# The auto-merge steps apply these two, on Dependabot's pull requests, so `repo apply`
# creates them and `repo doctor` checks them while `dependabot` is on. A product's
# `ci.labels` adds to them and may reword them.
CORE_LABELS: Mapping[str, tuple[str, str, str]] = {
    "dependencies": ("0366d6", "Dependency updates", "dependabot"),
    "needs-review": (
        "d93f0b",
        "Not merged automatically; a person reviews it",
        "dependabot",
    ),
}


def labels(product: Product) -> dict[str, tuple[str, str, str]]:
    """The core's labels with the product's (``ci.labels``) over them."""
    return {**CORE_LABELS, **product.ci.labels}


def output(**values: str) -> None:
    """Append ``key=value`` lines to ``$GITHUB_OUTPUT`` when it is set (a step's outputs)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        f.writelines(f"{key}={value}\n" for key, value in values.items())


def notice(message: str) -> None:
    print(f"::notice::{message}")


def error(message: str) -> None:
    print(f"::error::{message}")
