"""The core's subcommands, and how a product adds its own (DESIGN §6.1).

A command is a ``Command``: its name, a ``register(subparsers, product)`` function that
adds one or more subcommands (each with ``set_defaults(func=run)``, where
``run(args) -> int`` returns the exit code and ``args.product`` is the product), and a
``sort_key`` that places it in ``--help`` (default: its name). A command raises
``StandardsError`` for a one-line failure; ``standards_core.cli.main`` prints it and
exits with its code.

``CORE_COMMANDS`` lists the core's. Descriptions and flag help go through
``help_text(product, key, default)``, so a product rewords any of them through
``Product.help`` (``"<command>"`` for a command's description, ``"<command> <flag>"``
for a flag's help, ``"<command>:summary"`` for its line in the command list, and
``"<command> <subcommand>[ <flag>]"`` below a command group).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from standards_core.product import Product


@dataclass(frozen=True)
class Command:
    name: str
    # adds the subcommand(s); set_defaults(func=run)
    register: Callable[[argparse._SubParsersAction, Product], None]
    # "" → name; data-path and pin-path sort as "paths", where their module's name put them
    sort_key: str = ""


def help_text(product: Product, key: str, default: str | None) -> str | None:
    """``product.help[key]``, else ``default`` (``None``: argparse's no text)."""
    return product.help.get(key, default)


def _core_commands() -> tuple[Command, ...]:
    from standards_core.commands import ci, config, paths, policy, profile, repo, sync

    return (
        Command("ci", ci.register),
        Command("config", config.register),
        Command("data-path", paths.register_data_path, "paths"),
        Command("pin-path", paths.register_pin_path, "paths"),
        Command("policy", policy.register),
        Command("profile", profile.register),
        Command("repo", repo.register),
        Command("sync", sync.register),
    )


def __getattr__(name: str) -> Any:
    # CORE_COMMANDS is built on first use: the command modules import help_text from
    # this package, so they cannot be imported while it initialises.
    if name == "CORE_COMMANDS":
        return _core_commands()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
