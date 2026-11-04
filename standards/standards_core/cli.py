"""``<cli> <command>``: the dispatcher a product's console script calls (DESIGN §6.1).

A product's entry point is::

    def main() -> int:
        return standards_core.cli.main(PRODUCT, extra_commands=EXTRA)

The parser registers the core's commands and the product's, sorted by
``(sort_key or name, name)``. Every handler gets ``args.product``. A
``StandardsError`` prints ``<cli> <command>: <message>`` and exits with its code; any
other exception, import failures included, prints ``<cli> <command>: internal error:
<Type>: <message>`` and exits 3, never 1 (which means drift). ``<PREFIX>_DEBUG`` prints
the traceback.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from standards_core.common.report import ENVIRONMENT, INVALID, StandardsError

if TYPE_CHECKING:
    from standards_core.commands import Command
    from standards_core.product import Product


def description(product: Product) -> str:
    """The parser's description: ``identity.description``, else one built from the docs URL."""
    identity = product.identity
    return identity.description or (
        f"{identity.name} standards tools. See {identity.docs_url}."
    )


SUMMARY = ":summary"


def _help_keys(prefix: str, parser: argparse.ArgumentParser) -> Iterator[str]:
    """Every ``Product.help`` key ``parser`` (the command ``prefix``) can take."""
    yield prefix
    yield prefix + SUMMARY
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _help_keys(f"{prefix} {name}", sub)
            continue
        for flag in action.option_strings or [action.dest]:
            yield f"{prefix} {flag}"


def _summaries(product: Product, prefix: str, parser: argparse.ArgumentParser) -> None:
    """Each subcommand's line in its parent's command list from ``"<command>:summary"``
    (``prefix`` names ``parser``; ``""`` is the top level)."""
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for pseudo in action._choices_actions:
            path = f"{prefix} {pseudo.dest}".strip()
            if path + SUMMARY in product.help:
                pseudo.help = product.help[path + SUMMARY]
        for name, sub in action.choices.items():
            _summaries(product, f"{prefix} {name}".strip(), sub)


def build_parser(
    product: Product, extra_commands: Sequence[Command] = ()
) -> argparse.ArgumentParser:
    """The command line: the core's commands and ``extra_commands``.

    ``Product.help`` keys are ``"<command>"`` (its description), ``"<command> <flag>"``
    (a flag's help, a positional by its name) and ``"<command>:summary"`` (its line in
    the command list above it), where ``<command>`` may name a subcommand (``"repo
    audit"``). A duplicate command name, or a key that names no registered command or
    flag, is a ``RuntimeError`` (a product bug).
    """
    from standards_core.commands import CORE_COMMANDS

    parser = argparse.ArgumentParser(
        prog=product.identity.cli, description=description(product)
    )
    parser.add_argument("--version", action="version", version=product.identity.version)
    parser.set_defaults(product=product, func=None)
    subparsers = parser.add_subparsers(
        dest="command", metavar="<command>", title="commands"
    )
    commands = [*CORE_COMMANDS, *extra_commands]
    names = [c.name for c in commands]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise RuntimeError(f"command(s) registered twice: {', '.join(duplicates)}")
    for command in sorted(commands, key=lambda c: (c.sort_key or c.name, c.name)):
        before = set(subparsers.choices)
        command.register(subparsers, product)
        if command.name not in set(subparsers.choices) - before:
            raise RuntimeError(f"command {command.name!r} did not register itself")
    if product.help:
        known = {
            key
            for name, sub in subparsers.choices.items()
            for key in _help_keys(name, sub)
        }
        unknown = sorted(set(product.help) - known)
        if unknown:
            raise RuntimeError(
                f"Product.help names no command or flag: {', '.join(unknown)}"
            )
        _summaries(product, "", parser)
    return parser


def main(
    product: Product,
    argv: Sequence[str] | None = None,
    extra_commands: Sequence[Command] = (),
) -> int:
    # Building the parser imports every command module, so a broken module or a missing
    # dependency fails inside the try below too: exit 3, never Python's 1.
    prefix = product.identity.cli
    try:
        parser = build_parser(product, extra_commands)
        args = parser.parse_args(None if argv is None else list(argv))
        if args.command is None:
            parser.print_help(sys.stderr)
            return INVALID
        prefix = f"{product.identity.cli} {args.command}"
        args.product = product
        return args.func(args)
    except StandardsError as e:
        print(f"{prefix}: {e}", file=sys.stderr)
        return e.code
    except Exception as e:  # noqa: BLE001 — every failure must map to an exit code
        # A bug, or a failure no command anticipated: never exit 1, which means drift.
        # <PREFIX>_DEBUG=1 prints the traceback.
        if os.environ.get(f"{product.identity.env_prefix}_DEBUG"):
            traceback.print_exc()
        print(f"{prefix}: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return ENVIRONMENT
