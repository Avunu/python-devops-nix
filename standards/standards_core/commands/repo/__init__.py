"""``<cli> repo apply|audit|rollout|doctor|targets``: the repository-level tools
(DESIGN §7).

``apply`` sets a repository's settings, labels and rulesets from ``repo-policy/``
(module ``repo-policy``); ``doctor`` reports which settings the enabled modules rely
on; ``audit`` is the scorecard; ``rollout`` moves repositories to an engine or org
profile release; ``targets`` lists the fleet's repositories. Each takes
``<owner/repo>…`` or ``--all --fleet <file>``, the fleet list an org profile repository
keeps.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from standards_core.commands import help_text

if TYPE_CHECKING:
    from standards_core.product import Product


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    from standards_core.commands.repo import apply, audit, doctor, rollout, targets

    p = subparsers.add_parser(
        "repo",
        help="repository settings, rulesets, the scorecard, rollouts and the fleet",
        description=help_text(product, "repo", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="repo_command", metavar="<subcommand>", required=True)
    for module in (apply, audit, rollout, doctor, targets):
        module.register(sub, product)
