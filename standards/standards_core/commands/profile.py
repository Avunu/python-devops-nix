"""``<cli> profile show|list``: profiles, resolved and listed.

``show`` prints the checkout's resolved configuration and its module switches;
``--explain`` adds, per value, the layer that set it (``builtin:recommended@1.0``,
``org:<source>@<rev>``, ``app`` or ``default``), which answers "why is this gate on".

``list`` prints ``minimal`` and every ``recommended@<minor>`` snapshot with its
description, marking the one plain ``recommended`` means.

Exit codes as sync's: 0, 2 (invalid) or 3 (environment).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

import tomlkit

from standards_core.commands import help_text
from standards_core.commands.config import add_source_arguments, resolve_args
from standards_core.common import config
from standards_core.common.report import CLEAN, ConfigError

if TYPE_CHECKING:
    from standards_core.product import Product


def _plain(value: Any) -> Any:
    """``value`` without ``None`` (TOML has no null)."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_plain(v) for v in value if v is not None]
    return value


def _flat(value: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(value, dict) and value:
        out: dict[tuple[str, ...], Any] = {}
        for key, item in value.items():
            out.update(_flat(item, (*prefix, str(key))))
        return out
    return {prefix: value}


def _toml_key(path: tuple[str, ...]) -> str:
    return ".".join(tomlkit.key(part).as_string() for part in path)


def _toml_inline(value: Any) -> str:
    """``value`` on one line: tables inline, as TOML spells them."""
    if isinstance(value, dict):
        inner = ", ".join(
            f"{tomlkit.key(k).as_string()} = {_toml_inline(v)}"
            for k, v in value.items()
        )
        return "{ " + inner + " }" if inner else "{}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline(v) for v in value) + "]"
    return tomlkit.item(value).as_string()


def _explained(resolved: config.Resolved, fmt: str) -> str:
    """``show --explain``: what ``show`` prints, with the layer that set each value, in
    ``fmt``. TOML gives each ``[config]`` value as a dotted key with the layer as its
    comment (no ``None``: TOML has no null); JSON adds a ``sources`` table, keyed by the
    dotted name."""
    flat = _flat(resolved.cfg)
    if fmt == "json":
        doc = {
            "profile": resolved.profile,
            "modules": resolved.modules,
            "config": resolved.cfg,
            "sources": {".".join(k): resolved.source(".".join(k)) for k in flat},
        }
        return json.dumps(doc, indent=2) + "\n"
    lines = ["[profile]"]
    for key, value in _flat(dict(resolved.profile)).items():
        if value is not None:
            lines.append(f"{_toml_key(key)} = {_toml_inline(_plain(value))}")
    lines += ["", "[modules]"]
    for module, on in resolved.modules.items():
        lines.append(f"{tomlkit.key(module).as_string()} = {'true' if on else 'false'}")
    lines += ["", "[config]"]
    for key, value in flat.items():
        if value is not None:
            line = f"{_toml_key(key)} = {_toml_inline(_plain(value))}"
            lines.append(f"{line}  # {resolved.source('.'.join(key))}")
    return "\n".join(lines) + "\n"


def show(args: argparse.Namespace) -> int:
    resolved = resolve_args(args.product, args)
    if args.explain:
        sys.stdout.write(_explained(resolved, args.format))
        return CLEAN
    doc = {
        "profile": resolved.profile,
        "modules": resolved.modules,
        "config": resolved.cfg,
    }
    if args.format == "json":
        print(json.dumps(doc, indent=2, sort_keys=False))
    else:
        sys.stdout.write(tomlkit.dumps(_plain(doc)))
    return CLEAN


def list_profiles(args: argparse.Namespace) -> int:
    """Each built-in with its description, marking the one plain ``recommended`` means."""
    product = args.product
    names = config.builtin_names(product)
    try:
        newest = config.builtin_name(product, "recommended")
    except ConfigError:
        newest = None
    labels = {n: n + ("  (recommended)" if n == newest else "") for n in names}
    width = max(len(label) for label in labels.values())
    for name in names:
        description = config.builtin_profile(product, name)[1].get("description", "")
        print(f"{labels[name]:<{width}}  {description}".rstrip())
    return CLEAN


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    p = subparsers.add_parser(
        "profile",
        help="show the resolved profile, list the built-ins",
        description=help_text(product, "profile", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="profile_command", metavar="<show|list>", required=True)
    s = sub.add_parser(
        "show",
        help=help_text(
            product, "profile show", "the checkout's resolved configuration and modules"
        ),
    )
    s.add_argument(
        "--explain",
        action="store_true",
        help=help_text(
            product, "profile show --explain", "name the layer that set each value"
        ),
    )
    s.add_argument(
        "--format",
        choices=("toml", "json"),
        default="toml",
        help=help_text(product, "profile show --format", None),
    )
    add_source_arguments(product, s, "profile show")
    s.set_defaults(func=show)
    ls = sub.add_parser(
        "list", help=help_text(product, "profile list", "the built-in profiles")
    )
    ls.set_defaults(func=list_profiles)
