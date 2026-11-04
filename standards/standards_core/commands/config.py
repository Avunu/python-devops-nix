"""``<cli> config <key>``: print one value of the checkout's resolved configuration.

The key is a dotted ``[tool.<table>]`` key with every layer and default applied
(``commits.subject-length``), ``modules.<module>`` (the effective switch, after the
module rules), or ``profile.name|source|rev``.

A list prints one item per line, so a shell can ``while read`` it; a table, or a list
item that is a table, prints as one line of JSON; a boolean prints as ``true``/``false``;
an unset key with no default prints nothing. ``--json`` prints the value as JSON
instead. An unknown key exits 2, and so does a checkout that has not opted in.

``--github-output modules`` appends ``<module>=true|false`` for every module to
``$GITHUB_OUTPUT`` (a CI step's module switches).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.commands import help_text
from standards_core.common import config, pyproject
from standards_core.common.report import CLEAN, ConfigError, EnvError, StandardsError

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import Checkout


def _line(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    return str(value)


def lines(value: Any) -> list[str]:
    """How ``value`` prints: one line per list item, otherwise one line (none when unset)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [_line(item) for item in value]
    return [_line(value)]


def checkout_or_none(product: Product, root: Path) -> Checkout | None:
    """The checkout at ``root``, or ``None`` when it is not one the product can load
    (outside git, say): the value commands then resolve without it, as for a table read
    on its own."""
    try:
        return product.hooks.load_checkout(root)
    except StandardsError:
        return None


def source(product: Product, pyproject_arg: str | None) -> tuple[Path, Path]:
    """The checkout root and its config file (DESIGN §15.CR1).

    Without ``--pyproject``, the root is ``pyproject.checkout_root`` from the current
    directory (the nearest directory at or above it, within the git work tree, whose
    config file exists, preferring one whose file opens the table when the file differs
    between checkouts; then the outermost directory up to the work tree's root whose
    config file that is; else the work tree's root) and the file is its config file.
    With ``--pyproject F``, the root is the outermost ancestor of ``F`` whose config file
    ``F`` is (``pyproject.root_for``), else ``F``'s directory."""
    if pyproject_arg:
        path = Path(pyproject_arg)
        return pyproject.root_for(product, path), path
    root = pyproject.checkout_root(product)
    return root, pyproject.config_path(product, root)


def resolve_args(product: Product, args: argparse.Namespace) -> config.Resolved:
    """The resolution ``--pyproject``, ``--lock`` and ``--profile-path`` ask for. The
    default lock is the root's ``flake.lock``, beside the checkout's ``flake.nix``."""
    root, path = source(product, args.pyproject)
    checkout = checkout_or_none(product, root) if path.is_file() else None
    resolved = config.resolve(
        product,
        path,
        root=checkout.root if checkout is not None else root,
        lock_path=Path(args.lock) if args.lock else None,
        profile_dir=Path(args.profile_path) if args.profile_path else None,
        checkout=checkout,
    )
    for notice in resolved.notices:
        print(f"notice: {notice}", file=sys.stderr)
    return resolved


def pyproject_help(product: Product) -> str:
    """``--pyproject``'s default help: the config file names, and how the checkout is
    found without the flag."""
    identity = product.identity
    paths = pyproject.config_paths(product)
    if len(paths) == 1 and product.hooks.config_file is None and "/" not in paths[0]:
        name = paths[0]
        return (
            f"the {name} to read (default: the nearest {name} at or above the current"
            " directory, within the git work tree)"
        )
    rule = (
        "whose file opens the table"
        if pyproject.per_checkout(product)
        else f"where {paths[0]} exists"
    )
    return (
        f"the config file holding [tool.{identity.table}] ({' or '.join(paths)} below"
        " the checkout's root) to read (default: the checkout's, its root the nearest"
        f" directory at or above the current one {rule}, within the git work tree)"
    )


def add_source_arguments(
    product: Product, parser: argparse.ArgumentParser, command: str
) -> None:
    """``--pyproject``, ``--lock`` and ``--profile-path``: where the configuration is."""
    parser.add_argument(
        "--pyproject",
        help=help_text(product, f"{command} --pyproject", pyproject_help(product)),
    )
    parser.add_argument(
        "--lock",
        help=help_text(
            product,
            f"{command} --lock",
            "the flake.lock an org profile is pinned in (default: the checkout root's,"
            " beside its flake.nix)",
        ),
    )
    parser.add_argument(
        "--profile-path",
        help=help_text(
            product,
            f"{command} --profile-path",
            "read the org profile from this directory instead of its locked input",
        ),
    )


def run(args: argparse.Namespace) -> int:
    resolved = resolve_args(args.product, args)
    if args.github_output:
        if args.github_output != "modules":
            raise ConfigError(
                f"--github-output takes `modules`, not {args.github_output!r}"
            )
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            raise EnvError("--github-output needs $GITHUB_OUTPUT")
        with open(out, "a") as f:
            f.writelines(
                f"{module}={'true' if on else 'false'}\n"
                for module, on in resolved.modules.items()
            )
        return CLEAN
    if not args.key:
        raise ConfigError("give a key, or --github-output modules")
    value = resolved.get(args.key)
    if args.json:
        print(json.dumps(value, sort_keys=True))
        return CLEAN
    for line in lines(value):
        print(line)
    return CLEAN


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    p = subparsers.add_parser(
        "config",
        help="print a resolved configuration value (lists one item per line)",
        description=help_text(product, "config", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "key",
        nargs="?",
        help=help_text(
            product,
            "config key",
            "a dotted key: commits.subject-length, modules.ci, profile.name",
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help=help_text(product, "config --json", "print the value as JSON"),
    )
    p.add_argument(
        "--github-output",
        metavar="modules",
        help=help_text(
            product,
            "config --github-output",
            "append every module switch to $GITHUB_OUTPUT",
        ),
    )
    add_source_arguments(product, p, "config")
    p.set_defaults(func=run)
