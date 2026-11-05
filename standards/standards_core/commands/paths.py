"""``<cli> data-path`` and ``<cli> pin-path``: where a packaged or pinned file is."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.commands import help_text
from standards_core.common import repo
from standards_core.common.data import data_path
from standards_core.common.pins import pin_path
from standards_core.common.report import CLEAN

if TYPE_CHECKING:
    from standards_core.product import Product


def run_data_path(args: argparse.Namespace) -> int:
    print(data_path(args.product, args.rel))
    return CLEAN


def run_pin_path(args: argparse.Namespace) -> int:
    lock = Path(args.lock) if args.lock else repo.find_up("flake.lock")
    print(pin_path(args.product, args.input, lock.resolve(), writable=args.writable))
    return CLEAN


DATA_PATH = (
    "Print the absolute path of a data file: the product's data directory first, then"
    " the core's, e.g. `schema/core.schema.json`."
)

PIN_PATH = (
    "Print a directory holding the tree flake.lock pins for <input>: the checkout's own"
    " input (standards-profile, …) or one the engine pins. It is the Nix store path when"
    " present, else <pins dir>/<name>-<rev>/, fetched (github, gitlab and git inputs) and"
    " verified against the lock's narHash (exit 3 on a mismatch). <PREFIX>_FETCH_TOKEN"
    " authenticates the fetch of a private repository. --writable never prints the store"
    " path, which is read-only: the tree is copied from the store into the pins directory"
    " (or fetched there), for a caller that writes into it."
)


def register_data_path(
    subparsers: argparse._SubParsersAction, product: Product
) -> None:
    p = subparsers.add_parser(
        "data-path",
        help="print the absolute path of a file shipped in the product's data",
        description=help_text(product, "data-path", DATA_PATH),
    )
    p.add_argument(
        "rel",
        help=help_text(product, "data-path rel", "path relative to a data directory"),
    )
    p.set_defaults(func=run_data_path)


def register_pin_path(subparsers: argparse._SubParsersAction, product: Product) -> None:
    prefix = product.identity.env_prefix
    p = subparsers.add_parser(
        "pin-path",
        help="print a directory holding a flake.lock input, fetched and narHash-verified",
        description=help_text(
            product,
            "pin-path",
            PIN_PATH.replace("<PREFIX>", prefix).replace(
                "<pins dir>", product.pins.dir
            ),
        ),
    )
    p.add_argument(
        "input", help=help_text(product, "pin-path input", "the flake input name")
    )
    p.add_argument(
        "--lock",
        help=help_text(
            product,
            "pin-path --lock",
            "the flake.lock to read (default: the nearest flake.lock at or above the"
            " current directory, within the git work tree)",
        ),
    )
    p.add_argument(
        "--writable",
        action="store_true",
        help=help_text(
            product,
            "pin-path --writable",
            "a tree the caller may write into: the pins directory, never the store path",
        ),
    )
    p.set_defaults(func=run_pin_path)
